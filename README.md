# ناشر انستغرام الآلي — الفطيم للنباتات

ينشر منشورات حساب
`ftm8.__`
تلقائياً من ملف
`schedule.json`
كل 15 دقيقة عبر GitHub Actions، بدون الحاجة لتشغيل أي جهاز.

## إضافة منشور
أضف عنصراً إلى `schedule.json`:

```json
{
  "id": "2026-11-30-olive",
  "time": "2026-11-30T10:00:00+03:00",
  "type": "IMAGE",
  "media": ["media/olive.jpg"],
  "caption": "نص المنشور",
  "status": "pending"
}
```

- `type`: `IMAGE` أو `CAROUSEL` (عدة صور) أو `REEL` (فيديو)
- `media`: مسار داخل المستودع أو رابط عام
- الحالة تتغير تلقائياً إلى `published` أو `error` أو `skipped`

## السر المطلوب
Settings → Secrets and variables → Actions:
- `IG_TOKEN`

يتجدد المفتاح تلقائياً كل أسبوع، وتُحفظ النسخة الجديدة مشفّرة في `token.enc`.

## تجربة المفتاح
Actions → Publish Instagram posts → Run workflow → mode = `check`
