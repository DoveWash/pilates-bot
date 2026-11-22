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

## Friends' portal (v2.4)

Friends get their own small, phone-friendly site, and **never get access to Home Assistant**. It's a separate web server inside the app on port **8100**, and it has none of the admin pages.

**It's off by default.** Nothing is listening on port 8100 until you turn it on:

1. Pilates Bot → **Configuration**: set `portal_enabled: true`.
2. Pilates Bot → **Configuration → Network**: set the port for `8100/tcp` to `8100`.
3. Restart the app. The portal now works on your home Wi-Fi at `http://<ha-ip>:8100`, and invites appear on the People page. It's still not on the internet until you do the tunnel step below.

What a friend can do: join with their own booking-app login (you never see their password), book classes for themselves ("just this one" or "every week"), skip a week, stop a regular class, pause everything, update their password, see their class packs, set up push notifications (ntfy or Discord), and delete their account. They only ever see their own bookings. You get a notification when a friend joins, books, stops a class or pauses.

### Make it reachable from outside your home (once)

Use a **Cloudflare Tunnel**. No ports open on your router, free HTTPS, and only the portal is exposed (not HA):

1. Install the **Cloudflared** app from the HA community app store (needs a domain on Cloudflare).
2. In its config, **leave `external_hostname` empty** (that option would publish Home Assistant itself) and add only the portal, using your HA box's LAN IP:
   ```yaml
   external_hostname: ""
   additional_hosts:
     - hostname: pilates.yourdomain.com
       service: http://192.168.1.50:8100
   ```
   Only that hostname goes on the internet, and it reaches only the portal (port 8100), never HA (8123). If you already use Cloudflared for HA, keep your setup and just add the entry above.
   Optional extra lock: put **Cloudflare Access** (free tier) in front of `pilates.yourdomain.com` with an email one-time-PIN policy, so even a leaked link needs the friend's email to open.
3. In Pilates Bot's **Configuration** tab set `portal_url: https://pilates.yourdomain.com` and restart. Invite links now use that address.

Don't forward port 8099, and don't expose HA itself for this. If you'd rather not expose anything, skip this: the portal still works on your home Wi-Fi.

### Invite a friend

**People → Invite a friend → Create invite link**, then text them the link. It works once and expires after 7 days (or after 5 wrong passwords). After joining, they land on their personal page; tell them to **Add to Home Screen** so it opens like an app.

To give your wife (or anyone you added yourself) portal access: **People → Personal link**. **Revoke** or **New link** kills the old link immediately.

### Notifications for friends

Friends usually don't have HA's Companion app, so the portal uses **[ntfy](https://ntfy.sh)**: they install the free app, subscribe to the random topic shown on their **Me** page, and tap *Send a test*. Self-hosting ntfy? Set `ntfy_server`.

## Other options

| Option | Default | |
|---|---|---|
| `catchup_hours` | 6 | If HA was restarting/updating at midnight, book that night's classes when the app comes back, if it's within this many hours. 0 = off. |
| `portal_enabled` | false | Turn on the friends' portal (also map port 8100 under Network). |
| `portal_url` | "" | Public address of the portal, used in invite links. |
| `ntfy_server` | https://ntfy.sh | ntfy server for push notifications. |

## Run without Home Assistant

```bash
docker compose up -d --build
```

Then open `http://<host>:8099` (login with `WEB_PASSWORD`).

## Tests

```bash
cd pilates_bot && pip install -r requirements.txt pytest httpx && python -m pytest tests
```
