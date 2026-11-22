"""The friends' portal: a small, phone-friendly site where each person manages only their own bookings.

It is a separate FastAPI app on its own port (default 8100), so none of the admin pages exist
on it at all, and it never talks to Home Assistant. Access is by personal link:

* /join/<invite>  - one-time invite the admin creates. The friend enters their own booking-site
                    login (checked against the studio before anything is saved).
* /m/<token>      - their personal link. Opening it signs them in on that device (cookie). It's
                    also what gets saved when they "Add to Home Screen", so the icon keeps working.

Tokens are random 256-bit values; only their SHA-256 hashes are stored.
"""
from __future__ import annotations

import logging
import os
import re
from datetime import date, timedelta
from urllib.parse import quote, urlparse

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .booking import WEEKDAYS, book_class, upcoming_rows
from .db import new_token
from .engine import Engine, matches
from .hapana import HapanaError
from .notify import STYLE

log = logging.getLogger(__name__)
HERE = os.path.dirname(__file__)
COOKIE = "pb_portal"
COOKIE_DAYS = 400  # browsers cap cookies at ~400 days; the personal link re-sets it anyway
NTFY_TOPIC_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def create_portal_app(db, vault, settings, engine: Engine, notifier) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.mount("/static", StaticFiles(directory=os.path.join(HERE, "static")), name="static")
    templates = Jinja2Templates(directory=os.path.join(HERE, "templates"))
    templates.env.globals.update(WEEKDAYS=WEEKDAYS, STYLE=STYLE)

    # ---------------------------------------------------------------- plumbing
    @app.middleware("http")
    async def guard(request: Request, call_next):
        # Forms only from this site: a page elsewhere can't make a signed-in friend's browser post here.
        if request.method == "POST":
            origin = request.headers.get("origin") or request.headers.get("referer") or ""
            if origin and urlparse(origin).netloc != request.headers.get("host", ""):
                return Response("Cross-site request blocked", status_code=403)
        resp = await call_next(request)
        resp.headers["Referrer-Policy"] = "no-referrer"  # personal links never leak to other sites
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["X-Content-Type-Options"] = "nosniff"
        if not request.url.path.startswith("/static"):
            resp.headers["Cache-Control"] = "no-store"
        return resp

    def me(request: Request):
        m = db.member_by_portal_token(request.cookies.get(COOKIE, ""))
        return m

    def page(request: Request, name: str, m=None, status_code: int = 200, **ctx):
        ctx.setdefault("msg", request.query_params.get("msg", ""))
        ctx.setdefault("err", request.query_params.get("err", ""))
        return templates.TemplateResponse(request, "portal/" + name, {"base": "", "me": m, **ctx},
                                          status_code=status_code)

    def go(path: str, msg: str = "", err: str = ""):
        url = path
        if msg:
            url += ("&" if "?" in url else "?") + "msg=" + quote(msg)
        if err:
            url += ("&" if "?" in url else "?") + "err=" + quote(err)
        return RedirectResponse(url, status_code=303)

    def sign_in(request: Request, resp: Response, token: str):
        secure = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
        resp.set_cookie(COOKIE, token, max_age=COOKIE_DAYS * 86400, httponly=True, samesite="lax", secure=secure)

    def signed_out(request: Request):
        return page(request, "signed_out.html", status_code=401)

    def home_page(request: Request, m, token: str = "", welcome: bool = False):
        today = engine.today()
        rows = upcoming_rows(db, settings, today, member_ids={m["id"]})
        weekly, once = [], []
        for r in db.rules_for_member(m["id"]):
            (once if r["on_date"] else weekly).append(r)
        once = [r for r in once if r["on_date"] >= today.isoformat()]
        recent = [a for a in db.member_attempts(m["id"], 10) if a["session_date"] < today.isoformat()][:5]
        db.touch_portal(m["id"])
        return page(request, "home.html", m, rows=rows, weekly=weekly, once=once, recent=recent,
                    sites=db.site_names(), today=today, token=token, welcome=welcome, settings=settings)

    # -------------------------------------------------------------- sign in
    @app.get("/", response_class=HTMLResponse)
    def home(request: Request):
        m = me(request)
        return home_page(request, m) if m else signed_out(request)

    @app.get("/m/{token}", response_class=HTMLResponse)
    def personal_link(request: Request, token: str):
        m = db.member_by_portal_token(token)
        if not m:
            return signed_out(request)
        # Render the page here instead of redirecting, so "Add to Home Screen" saves this link.
        resp = home_page(request, m, token=token, welcome=request.query_params.get("welcome") == "1")
        sign_in(request, resp, token)
        return resp

    @app.get("/m/{token}/manifest.webmanifest")
    def manifest(token: str):
        if not db.member_by_portal_token(token):
            return Response(status_code=404)
        return JSONResponse({
            "name": "Pilates Bot", "short_name": "Pilates", "start_url": f"/m/{token}", "scope": "/",
            "display": "standalone", "background_color": "#f6f7f9", "theme_color": "#03a9f4",
            "icons": [{"src": "/static/icon.svg", "sizes": "any", "type": "image/svg+xml"}],
        }, media_type="application/manifest+json")

    @app.post("/signout")
    def signout():
        resp = go("/")
        resp.delete_cookie(COOKIE)
        return resp

    # ---------------------------------------------------------------- join
    @app.get("/join/{token}", response_class=HTMLResponse)
    def join_form(request: Request, token: str):
        inv = db.invite(token)
        if not inv:
            return page(request, "join_invalid.html", status_code=404)
        return page(request, "join.html", invite=inv, token=token, form={"name": inv["note"]})

    @app.post("/join/{token}", response_class=HTMLResponse)
    async def join(request: Request, token: str):
        inv = db.invite(token)
        if not inv:
            return page(request, "join_invalid.html", status_code=404)
        f = await request.form()
        name = (f.get("name") or "").strip()[:60]
        email = (f.get("email") or "").strip()[:200]
        pw = f.get("password") or ""
        form = {"name": name, "email": email}

        def again(err: str):
            return page(request, "join.html", invite=inv, token=token, form=form, err=err, status_code=400)

        if not name or not email or not pw:
            return again("Please fill in your name, email and password.")
        if db.member_by_email(email):
            return again("That email is already set up. Ask whoever invited you for your personal link.")
        try:
            engine.check_credentials(email, pw)
        except HapanaError as exc:
            if exc.retryable:  # the studio's site is down - don't count it against the invite
                return again(f"Couldn't reach the booking site just now ({exc}). Please try again in a minute.")
            db.invite_failed(inv["id"])
            return again("The booking site didn't accept that email and password. Use the same login as "
                         "the studio's app or website.")
        member_id = db.save_member(None, name=name, email=email, password_enc=vault.encrypt(pw),
                                   package_subscription_id="", discord_user_id="", ha_notify_service="",
                                   ntfy_topic="", enabled=1)
        db.use_invite(inv["id"], member_id)
        personal = db.new_portal_token(member_id)
        log.info("Portal: %s joined via invite %s", name, inv["id"])
        try:
            notifier.alert("Pilates Bot", f"👋 {name} joined the friends' portal ({email}).")
        except Exception:  # noqa: BLE001
            log.exception("Join notification failed")
        resp = RedirectResponse(f"/m/{personal}?welcome=1", status_code=303)
        sign_in(request, resp, personal)
        return resp

    # ------------------------------------------------------------- my bookings
    def own_rule(m, rule_id: int):
        r = db.rule(rule_id)
        return r if r and m["id"] in db.rule_member_ids(rule_id) else None

    @app.post("/skip")
    def skip(request: Request, rule_id: int = Form(...), session_date: str = Form(...)):
        m = me(request)
        if not m:
            return signed_out(request)
        if not own_rule(m, rule_id):
            return go("/", err="That booking isn't yours")
        try:
            d = date.fromisoformat(session_date)
        except ValueError:
            return go("/", err="Invalid date")
        if db.get_attempt_for_rule(m["id"], rule_id, d.isoformat()):
            return go("/", err="The bot has already dealt with that class. Cancel it in the studio's app; "
                                "the bot won't book it again.")
        now = db.toggle_skip(m["id"], rule_id, d.isoformat())
        return go("/", msg=f"{'Skipping' if now else 'Not skipping'} {d:%a %d %b}")

    @app.post("/leave")
    def leave(request: Request, rule_id: int = Form(...)):
        m = me(request)
        if not m:
            return signed_out(request)
        r = own_rule(m, rule_id)
        if not r:
            return go("/", err="That booking isn't yours")
        db.remove_rule_member(rule_id, m["id"])
        what = r["session_name"] or r["label"]
        notifier_safe(f"{m['name']} stopped “{what}” in the friends' portal.")
        return go("/", msg=f"Stopped {what}. Anything already booked stays booked; cancel those in the studio's app.")

    def notifier_safe(text: str):
        try:
            notifier.alert("Pilates Bot", text)
        except Exception:  # noqa: BLE001
            log.exception("Portal notification failed")

    # ----------------------------------------------------------------- timetable
    @app.get("/book", response_class=HTMLResponse)
    def timetable(request: Request, site_id: str = "", start: str = ""):
        m = me(request)
        if not m:
            return signed_out(request)
        sites = db.sites()
        today = engine.today()
        try:
            d0 = max(date.fromisoformat(start), today) if start else today
        except ValueError:
            d0 = today
        if site_id not in {s["id"] for s in sites}:
            site_id = sites[0]["id"] if sites else ""
        ctx = dict(sites=sites, site_id=site_id, start=d0, days=None, opening=engine.opening_date(),
                   prev=max(d0 - timedelta(days=7), today) if d0 > today else None, next=d0 + timedelta(days=7),
                   week_end=d0 + timedelta(days=6), settings=settings)
        if not site_id:
            return page(request, "book.html", m, **ctx)
        if not m["enabled"]:
            ctx["err"] = "Your bookings are paused. Turn them back on under Me to book."
        try:
            sessions = engine.timetable(m, site_id, d0, d0 + timedelta(days=6))
        except (HapanaError, ValueError) as exc:
            ctx["err"] = f"Couldn't load the timetable: {exc}"
            return page(request, "book.html", m, **ctx)
        mine = [r for r in db.rules_for_member(m["id"])]
        attempts = {(a["session_id"], a["session_date"]): a
                    for a in db.attempts_between(d0.isoformat(), (d0 + timedelta(days=6)).isoformat())
                    if a["member_id"] == m["id"]}
        by_day: dict[date, list] = {}
        for s in sorted(sessions, key=lambda s: (s["_date"] or date.min, s["_start"])):
            if not s["_date"]:
                continue
            s["_plans"] = ["once" if r["on_date"] else "weekly" for r in mine if matches(r, s)]
            s["_attempt"] = attempts.get((s.get("sessionID"), s["_date"].isoformat()))
            s["_open"] = s["_date"] <= ctx["opening"]
            s["_opens_on"] = s["_date"] - timedelta(days=settings.days_ahead)
            by_day.setdefault(s["_date"], []).append(s)
        ctx["days"] = sorted(by_day.items())
        ctx["back"] = f"/book?site_id={quote(site_id)}&start={d0.isoformat()}"
        return page(request, "book.html", m, **ctx)

    @app.post("/book")
    async def book(request: Request):
        m = me(request)
        if not m:
            return signed_out(request)
        f = await request.form()
        back = f.get("back") or "/book"
        if not back.startswith("/book"):
            back = "/book"
        if not m["enabled"]:
            return go(back, err="Your bookings are paused. Turn them back on under Me first.")
        site_id = f.get("site_id", "")
        if site_id not in db.site_names():
            return go(back, err="Unknown studio")
        try:
            d = date.fromisoformat(f.get("session_date", ""))
        except ValueError:
            return go(back, err="That class has no valid date")
        if d < engine.today():
            return go(back, err="That class has already happened")
        name = (f.get("session_name") or "").strip()[:120]
        weekly = f.get("mode") == "weekly"
        reply = book_class(db, engine, settings, member_ids=[m["id"]], site_id=site_id,
                           session_id=(f.get("session_id") or "")[:200], d=d, name=name,
                           start=(f.get("start_time") or "")[:5], weekly=weekly)
        notifier_safe(f"{m['name']} {'booked every week' if weekly else 'booked'}: {name} "
                      f"{WEEKDAYS[d.weekday()]} {d:%d %b} {(f.get('start_time') or '')[:5]} (friends' portal).")
        return go(back, msg=reply.message if reply.ok else "", err="" if reply.ok else reply.message)

    # ------------------------------------------------------------------ account
    @app.get("/me", response_class=HTMLResponse)
    def account(request: Request):
        m = me(request)
        if not m:
            return signed_out(request)
        packs, packs_err = None, ""
        if request.query_params.get("packs") == "1":
            try:
                packs = [p for p in engine.find_packs(m) if isinstance(p, dict)]
            except (HapanaError, ValueError) as exc:
                packs_err = f"Couldn't load your class packs: {exc}"
        return page(request, "me.html", m, packs=packs, packs_err=packs_err,
                    suggested_topic="pilates-" + new_token()[:12].replace("_", "x").replace("-", "x").lower(),
                    settings=settings)

    @app.post("/me/password")
    def change_password(request: Request, password: str = Form("")):
        m = me(request)
        if not m:
            return signed_out(request)
        if not password:
            return go("/me", err="Enter your new password")
        try:
            engine.check_credentials(m["email"], password)
        except HapanaError as exc:
            return go("/me", err="The booking site didn't accept that password" if not exc.retryable
                      else f"Couldn't reach the booking site ({exc}). Try again shortly.")
        db.set_member_password(m["id"], vault.encrypt(password))
        engine.forget_login(m["id"])
        engine.clear_timetable_cache()
        return go("/me", msg="Password updated and checked with the booking site")

    @app.post("/me/notify")
    def save_notify(request: Request, ntfy_topic: str = Form(""), discord_user_id: str = Form("")):
        m = me(request)
        if not m:
            return signed_out(request)
        topic, uid = ntfy_topic.strip(), discord_user_id.strip()
        if topic and not NTFY_TOPIC_RE.match(topic):
            return go("/me", err="ntfy topic: letters, numbers, - and _ only")
        if uid and not uid.isdigit():
            return go("/me", err="Discord user ID is a long number")
        db.set_member_notify(m["id"], ntfy_topic=topic, discord_user_id=uid)
        return go("/me", msg="Notification settings saved")

    @app.post("/me/test-notify")
    def test_notify(request: Request):
        m = me(request)
        if not m:
            return signed_out(request)
        sent = notifier.to_member(m, "Pilates Bot", f"👋 Hi {m['name']}, notifications are working.")
        return go("/me", msg="Sent via " + ", ".join(sent)) if sent else go("/me", err="Set up ntfy first")

    @app.post("/me/pause")
    def pause(request: Request):
        m = me(request)
        if not m:
            return signed_out(request)
        db.set_member_enabled(m["id"], not m["enabled"])
        notifier_safe(f"{m['name']} {'paused' if m['enabled'] else 'resumed'} their bookings (friends' portal).")
        return go("/me", msg="All your bookings are paused" if m["enabled"] else "Your bookings are back on")

    @app.post("/me/delete")
    def delete_me(request: Request, confirm: str = Form("")):
        m = me(request)
        if not m:
            return signed_out(request)
        if confirm.strip().lower() != "delete":
            return go("/me", err="Type DELETE to confirm")
        for r in db.rules_for_member(m["id"]):
            db.remove_rule_member(r["id"], m["id"])
        db.delete_member(m["id"])
        engine.forget_login(m["id"])
        notifier_safe(f"{m['name']} removed themselves from the friends' portal.")
        resp = page(request, "deleted.html")
        resp.delete_cookie(COOKIE)
        return resp

    @app.get("/health")
    def health():
        return {"ok": True}

    return app
