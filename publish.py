"""Instagram auto-publisher for ftm8.__

Runs on GitHub Actions. Reads schedule.json, publishes every post whose time
has come, and writes the result back to schedule.json.

Secrets (repo Settings > Secrets and variables > Actions):
  IG_TOKEN    - access token (Page token via graph.facebook.com, or
                Instagram-login token via graph.instagram.com)
  IG_USER_ID  - Instagram professional account ID
Optional variable:
  IG_API_HOST - graph.facebook.com (default) or graph.instagram.com
"""
import io
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from PIL import Image

ROOT = Path(__file__).parent
SCHEDULE = ROOT / "schedule.json"
MEDIA_DIR = ROOT / "media"
API_VERSION = "v23.0"
HOST = os.environ.get("IG_API_HOST") or "graph.facebook.com"
TOKEN = os.environ.get("IG_TOKEN", "")
IG_USER = os.environ.get("IG_USER_ID", "")
REPO = os.environ.get("GITHUB_REPOSITORY", "hadi907/ftm8-ig-publisher")
BRANCH = os.environ.get("GITHUB_REF_NAME", "main")
RAW_BASE = f"https://raw.githubusercontent.com/{REPO}/{BRANCH}/"
MAX_LATE = timedelta(hours=12)   # skip posts that are this late
PREPARE_AHEAD = timedelta(hours=36)
KW = timezone(timedelta(hours=3))

API = f"https://{HOST}/{API_VERSION}"
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
    return m if m.startswith("http") else RAW_BASE + m


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

    if not TOKEN or not IG_USER:
        log("IG_TOKEN / IG_USER_ID not set - dry run only")
        return

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
        log(json.dumps(api("GET", IG_USER, fields="username,followers_count"), ensure_ascii=False))
    else:
        main()
