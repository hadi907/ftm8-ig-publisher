"""Instagram auto-publisher for ftm8.__

Runs on GitHub Actions. Reads schedule.json, publishes every post whose time
has come, and writes the result back to schedule.json.

Secret (repo Settings > Secrets and variables > Actions):
  IG_TOKEN - long-lived access token with instagram_basic +
             instagram_content_publish (valid ~60 days, renew before expiry)
Optional variable IG_API_HOST=graph.instagram.com switches to an
Instagram-login token, which this script then refreshes automatically.
"""
import base64
import hashlib
import io
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from cryptography.fernet import Fernet
from PIL import Image

ROOT = Path(__file__).parent
SCHEDULE = ROOT / "schedule.json"
MEDIA_DIR = ROOT / "media"
API_VERSION = "v23.0"
HOST = os.environ.get("IG_API_HOST") or "graph.facebook.com"
SECRET = os.environ.get("IG_TOKEN", "")
IG_USER = os.environ.get("IG_USER_ID") or "17841480052027826"  # ftm8.__
TOKEN_FILE = ROOT / "token.enc"
REFRESH_EVERY = timedelta(days=7)
REPO = os.environ.get("GITHUB_REPOSITORY", "hadi907/ftm8-ig-publisher")
BRANCH = os.environ.get("GITHUB_REF_NAME", "main")
RAW_BASE = f"https://raw.githubusercontent.com/{REPO}/{BRANCH}/"
_owner, _name = REPO.split("/")
PAGES_BASE = f"https://{_owner}.github.io/{_name}/"  # serves video/mp4 with the right type
MAX_LATE = timedelta(hours=12)   # skip posts that are this late
PREPARE_AHEAD = timedelta(hours=36)
KW = timezone(timedelta(hours=3))

API = f"https://{HOST}/{API_VERSION}"


# ---------- token storage ----------
def _fernet():
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(SECRET.encode()).digest()))


def load_token():
    """Latest token: the encrypted refreshed copy if present, else the secret."""
    if SECRET and TOKEN_FILE.exists():
        try:
            data = json.loads(_fernet().decrypt(TOKEN_FILE.read_bytes()))
            return data["token"], datetime.fromisoformat(data["refreshed"])
        except Exception as e:
            log("token.enc unreadable, using secret:", e)
    return SECRET, None


def refresh_token_if_needed():
    global TOKEN
    tok, refreshed = load_token()
    TOKEN = tok
    if not tok:
        return
    if HOST != "graph.instagram.com":
        return  # Facebook long-lived tokens are renewed by hand (60 days)
    now = datetime.now(timezone.utc)
    if refreshed and now - refreshed < REFRESH_EVERY:
        return
    r = requests.get("https://graph.instagram.com/refresh_access_token",
                     params={"grant_type": "ig_refresh_token", "access_token": tok}, timeout=60).json()
    if "access_token" not in r:
        log("token refresh failed:", r.get("error", r))
        return
    TOKEN = r["access_token"]
    TOKEN_FILE.write_bytes(_fernet().encrypt(json.dumps(
        {"token": TOKEN, "refreshed": now.isoformat()}).encode()))
    log("token refreshed")


TOKEN = ""
VIDEO_EXT = (".mp4", ".mov")


def log(*a):
    print(*a, flush=True)


def load():
    return json.loads(SCHEDULE.read_text(encoding="utf-8"))


def save(data):
    SCHEDULE.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def parse_time(s):
    t = datetime.fromisoformat(s)
    return t if t.tzinfo else t.replace(tzinfo=KW)


def is_video(url):
    return url.lower().split("?")[0].endswith(VIDEO_EXT)


# ---------- media preparation (images -> JPEG inside the repo) ----------
def prepare_media(post):
    """Instagram accepts JPEG only. Download external / non-JPEG images,
    convert to JPEG and store them in media/ so they get a stable public URL."""
    changed = False
    new = []
    for i, m in enumerate(post["media"]):
        if is_video(m):
            new.append(m)
            continue
        local = not m.startswith("http")
        if local and m.lower().endswith((".jpg", ".jpeg")):
            new.append(m)
            continue
        try:
            if local:
                raw = (ROOT / m).read_bytes()
            else:
                r = requests.get(m, timeout=60)
                r.raise_for_status()
                raw = r.content
            img = Image.open(io.BytesIO(raw)).convert("RGB")
            MEDIA_DIR.mkdir(exist_ok=True)
            name = f"media/{post['id']}_{i + 1}.jpg"
            img.save(ROOT / name, "JPEG", quality=92)
            new.append(name)
            changed = True
            log(f"  prepared {m} -> {name}")
        except Exception as e:  # keep original, publishing will report it
            log(f"  could not prepare {m}: {e}")
            new.append(m)
    post["media"] = new
    return changed


def public_url(m):
    if m.startswith("http"):
        return m
    return (PAGES_BASE if is_video(m) else RAW_BASE) + m


# ---------- Graph API ----------
def api(method, path, **params):
    params["access_token"] = TOKEN
    r = requests.request(method, f"{API}/{path}", data=params if method == "POST" else None,
                         params=params if method == "GET" else None, timeout=120)
    js = r.json()
    if "error" in js:
        raise RuntimeError(js["error"].get("message", str(js["error"])))
    return js


def wait_ready(cid, tries=40):
    for _ in range(tries):
        st = api("GET", cid, fields="status_code,status").get("status_code")
        if st == "FINISHED":
            return
        if st in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"container {cid} status {st}")
        time.sleep(15)
    raise RuntimeError("media processing timed out")


def publish(post):
    urls = [public_url(m) for m in post["media"]]
    caption = post.get("caption", "")
    kind = post.get("type") or ("REEL" if is_video(urls[0]) else ("CAROUSEL" if len(urls) > 1 else "IMAGE"))

    if kind == "REEL":
        cid = api("POST", f"{IG_USER}/media", media_type="REELS", video_url=urls[0],
                  caption=caption, share_to_feed="true")["id"]
    elif kind == "CAROUSEL":
        children = []
        for u in urls:
            p = {"is_carousel_item": "true"}
            if is_video(u):
                p.update(media_type="VIDEO", video_url=u)
            else:
                p["image_url"] = u
            c = api("POST", f"{IG_USER}/media", **p)["id"]
            wait_ready(c)
            children.append(c)
        cid = api("POST", f"{IG_USER}/media", media_type="CAROUSEL",
                  children=",".join(children), caption=caption)["id"]
    else:
        cid = api("POST", f"{IG_USER}/media", image_url=urls[0], caption=caption)["id"]

    wait_ready(cid)
    return api("POST", f"{IG_USER}/media_publish", creation_id=cid)["id"]


# ---------- main ----------
def main():
    data = load()
    now = datetime.now(timezone.utc)
    dirty = False

    for post in data["posts"]:
        if post.get("status", "pending") != "pending":
            continue
        t = parse_time(post["time"])
        if t - now <= PREPARE_AHEAD:
            dirty |= prepare_media(post)

    if dirty:
        save(data)
        # media must be pushed before Instagram can fetch it
        if os.environ.get("GITHUB_ACTIONS"):
            os.system('git add -A && git commit -qm "prepare media" && git push -q')

    if not SECRET:
        log("IG_TOKEN not set - dry run only")
        return
    refresh_token_if_needed()
    dirty = True if TOKEN_FILE.exists() else dirty

    for post in data["posts"]:
        if post.get("status", "pending") != "pending":
            continue
        t = parse_time(post["time"])
        if t > now:
            continue
        if now - t > MAX_LATE:
            post["status"] = "skipped"
            post["error"] = "too late to publish"
            dirty = True
            continue
        log(f"publishing {post['id']} ({post['time']})")
        try:
            post["ig_media_id"] = publish(post)
            post["status"] = "published"
            post["published_at"] = datetime.now(KW).isoformat(timespec="minutes")
            post.pop("error", None)
            log("  done", post["ig_media_id"])
        except Exception as e:
            post["attempts"] = post.get("attempts", 0) + 1
            post["error"] = str(e)[:300]
            if post["attempts"] >= 3:
                post["status"] = "error"
            log("  failed:", e)
        dirty = True

    if dirty:
        save(data)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "check":
        refresh_token_if_needed()
        log(json.dumps(api("GET", IG_USER, fields="username,followers_count"), ensure_ascii=False))
        if HOST == "graph.facebook.com":
            d = requests.get(f"{API}/debug_token", params={"input_token": TOKEN, "access_token": TOKEN},
                             timeout=60).json().get("data", {})
            exp = d.get("expires_at") or d.get("data_access_expires_at")
            if exp:
                log("token expires:", datetime.fromtimestamp(exp, KW).date())
    else:
        main()
