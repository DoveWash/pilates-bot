# Pilates Bot v2

Books regular classes on the studio's Hapana booking system the moment they open, for one or several people. It runs as a Home Assistant App with its own sidebar page, or as a plain Docker container.

## What's new compared with the Azure version

- **Web UI**: pick classes from the live timetable instead of hard-coding session IDs.
- **Never re-books a class someone cancelled.** Every (person, class, date) is handled once and recorded.
- **Skip a week** from the Upcoming page, before the class opens.
- **Groups**: tick several people on one class and they're all booked at the same moment, each with their own login and class pack.
- **Error handling**: it logs in early, fires right at the opening time, retries at +30 s, 2, 5, 15 and 60 minutes, then gives up and tells you. Login failures, timetable changes ("class not found") and crashes all send a notification.
- **Notifications**: Discord (with @mentions per person) and/or Home Assistant push, or both.
- **History** of every attempt, and a log for every run.

## Install on Home Assistant

1. Push this folder to GitHub (the existing `pilates-bot` repo on a `v2` branch is fine). It contains no secrets.
2. In HA: **Settings → Apps → App store → ⋮ → Repositories**, add `https://github.com/DoveWash/pilates-bot` (add `#v2` on the end if it's on a branch).
3. Install **Pilates Bot**, then on its **Configuration** tab set:
   - `discord_webhook_url`: a **new** webhook. The old one is public in the repo history, so delete it in Discord.
   - `ha_notify_service`: e.g. `mobile_app_daryls_iphone` for your summary of every run.
4. Start it and turn on **Show in sidebar**.

## First-time setup (in the app's UI)

1. **People → Add person**: your wife's booking-site email, password, and `package_SubscriptionID` (the old bot read this from its `.env` / Azure app settings).
2. Click **Test login**.
3. **Book a class**: open a class, tick who to book, choose **Just this one** or **Every week**. The 7 classes from the old bot are also on **My bookings** with no one ticked.
4. Check **Upcoming**. Then try **Dashboard → Book now** for a date that's already open to check it end to end.

> On the first run, a class she has already cancelled could be booked again, because the bot has no record of it yet. Use **Skip** on Upcoming for anything she doesn't want in the next 6 days before the first run.

## Run without Home Assistant

```bash
docker compose up -d --build
```

Then open `http://<host>:8099` (login with `WEB_PASSWORD`).

## Tests

```bash
cd pilates_bot && pip install -r requirements.txt pytest httpx && python -m pytest tests
```
