"""Web UI (FastAPI + server-rendered templates). Works behind HA ingress or standalone."""
from __future__ import annotations

import base64
import hmac
import json
import os
import threading
from datetime import date, timedelta
from urllib.parse import quote

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .engine import Engine
from .hapana import HapanaError
from .notify import STYLE

HERE = os.path.dirname(__file__)
WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def create_app(db, vault, settings, engine: Engine, scheduler, notifier) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.mount("/static", StaticFiles(directory=os.path.join(HERE, "static")), name="static")
    templates = Jinja2Templates(directory=os.path.join(HERE, "templates"))
    templates.env.globals.update(WEEKDAYS=WEEKDAYS, STYLE=STYLE)
    web_password = os.environ.get("WEB_PASSWORD", "")

    @app.middleware("http")
    async def ingress_and_auth(request: Request, call_next):
        request.state.base = request.headers.get("X-Ingress-Path", "").rstrip("/")
        if web_password and not request.headers.get("X-Ingress-Path"):
            header = request.headers.get("Authorization", "")
            ok = False
            if header.startswith("Basic "):
                try:
                    _, _, pw = base64.b64decode(header[6:]).decode().partition(":")
                    ok = hmac.compare_digest(pw, web_password)
                except ValueError:
                    ok = False
            if not ok:
                return Response(status_code=401, headers={"WWW-Authenticate": 'Basic realm="Pilates Bot"'})
        return await call_next(request)

    def page(request: Request, name: str, **ctx):
        ctx.setdefault("msg", request.query_params.get("msg", ""))
        ctx.setdefault("err", request.query_params.get("err", ""))
        return templates.TemplateResponse(request, name, {"base": request.state.base, **ctx})

    def go(request: Request, path: str, msg: str = "", err: str = ""):
        url = request.state.base + path
        if msg:
            url += ("&" if "?" in url else "?") + "msg=" + quote(msg)
        if err:
            url += ("&" if "?" in url else "?") + "err=" + quote(err)
        return RedirectResponse(url, status_code=303)

    # ------------------------------------------------------------ dashboard
    @app.get("/", response_class=HTMLResponse)
    def dashboard(request: Request):
        today = engine.today()
        end = today + timedelta(days=settings.days_ahead)
        upcoming = db.attempts_between(today.isoformat(), end.isoformat())
        members = {m["id"]: m["name"] for m in db.members()}
        nxt = scheduler.next_run_at
        return page(
            request, "dashboard.html",
            next_run=nxt, next_target=engine.opening_date(nxt.date()) if nxt else None,
            opening=engine.opening_date(), runs=db.runs(10),
            upcoming=sorted(upcoming, key=lambda a: (a["session_date"], a["start_time"])),
            members=members, sites=db.site_names(), today=today, settings=settings,
            active_members=len(db.members_with_rules()),
        )

    @app.post("/run")
    def run_now(request: Request, run_date: str = Form(...)):
        try:
            d = date.fromisoformat(run_date)
        except ValueError:
            return go(request, "/", err="Pick a valid date")
        threading.Thread(target=engine.run, args=([d], "manual"), daemon=True).start()
        return go(request, "/", msg=f"Booking run started for {d}. Refresh in a few seconds.")

    @app.get("/runs/{run_id}", response_class=HTMLResponse)
    def run_detail(request: Request, run_id: int):
        return page(request, "run.html", run=db.run(run_id))

    # ------------------------------------------------------------- upcoming
    @app.get("/upcoming", response_class=HTMLResponse)
    def upcoming(request: Request):
        today = engine.today()
        days = [today + timedelta(days=i) for i in range(21)]
        end = days[-1].isoformat()
        attempts = {}
        for a in db.attempts_between(today.isoformat(), end):
            attempts[(a["member_id"], a["rule_id"], a["session_date"])] = a
        skips = db.skips_between(today.isoformat(), end)
        members = {m["id"]: m for m in db.members(enabled_only=True)}
        rows = []
        for rule in db.rules():
            if not rule["enabled"] or rule["weekday"] is None:
                continue
            for mid in sorted(db.rule_member_ids(rule["id"])):
                if mid not in members:
                    continue
                for d in days:
                    if d.weekday() != rule["weekday"]:
                        continue
                    key = (mid, rule["id"], d.isoformat())
                    rows.append({"date": d, "rule": rule, "member": members[mid],
                                 "attempt": attempts.get(key), "skipped": key in skips,
                                 "opens": d - timedelta(days=settings.days_ahead)})
        rows.sort(key=lambda r: (r["date"], r["rule"]["start_time"], r["member"]["name"]))
        return page(request, "upcoming.html", rows=rows, sites=db.site_names(), today=today)

    @app.post("/skip")
    def skip(request: Request, member_id: int = Form(...), rule_id: int = Form(...), session_date: str = Form(...)):
        now_skipped = db.toggle_skip(member_id, rule_id, session_date)
        return go(request, "/upcoming", msg=("Skipped " if now_skipped else "Un-skipped ") + session_date)

    # ---------------------------------------------------------------- rules
    @app.get("/rules", response_class=HTMLResponse)
    def rules(request: Request):
        return page(request, "rules.html", rules=db.rules(), sites=db.site_names(), who=db.rule_members_map())

    def rule_form(request: Request, rule: dict, member_ids: set[int]):
        return page(request, "rule_form.html", rule=rule, member_ids=member_ids,
                    members=db.members(), sites=db.sites())

    @app.get("/rules/new", response_class=HTMLResponse)
    def rule_new(request: Request):
        qp = request.query_params
        wd = qp.get("weekday")
        rule = {"id": None, "label": qp.get("label", ""), "site_id": qp.get("site_id", ""),
                "weekday": int(wd) if wd not in (None, "") else None, "start_time": qp.get("start_time", ""),
                "session_name": qp.get("session_name", ""), "instructor": "",
                "session_id": qp.get("session_id", ""), "enabled": 1}
        return rule_form(request, rule, set())

    @app.get("/rules/{rule_id}", response_class=HTMLResponse)
    def rule_edit(request: Request, rule_id: int):
        rule = db.rule(rule_id)
        if not rule:
            return go(request, "/rules", err="Rule not found")
        return rule_form(request, dict(rule), db.rule_member_ids(rule_id))

    @app.post("/rules/save")
    async def rule_save(request: Request):
        f = await request.form()
        rule_id = int(f["id"]) if f.get("id") else None
        label = (f.get("label") or "").strip()
        if not label or not f.get("site_id"):
            return go(request, f"/rules/{rule_id}" if rule_id else "/rules/new", err="Label and site are required")
        wd = f.get("weekday")
        db.save_rule(
            rule_id, [int(x) for x in f.getlist("member_ids")],
            label=label, site_id=f["site_id"], weekday=int(wd) if wd not in (None, "") else None,
            start_time=(f.get("start_time") or "").strip()[:5], session_name=(f.get("session_name") or "").strip(),
            instructor=(f.get("instructor") or "").strip(), session_id=(f.get("session_id") or "").strip(),
            enabled=1 if f.get("enabled") else 0,
        )
        return go(request, "/rules", msg=f"Saved '{label}'")

    @app.post("/rules/{rule_id}/delete")
    def rule_delete(request: Request, rule_id: int):
        db.delete_rule(rule_id)
        return go(request, "/rules", msg="Rule deleted")

    # ------------------------------------------------------------ timetable
    @app.get("/timetable", response_class=HTMLResponse)
    def timetable(request: Request, site_id: str = "", member_id: int = 0, start: str = ""):
        members = db.members(enabled_only=True)
        sites = db.sites()
        ctx = dict(members=members, sites=sites, site_id=site_id, member_id=member_id,
                   start=start or engine.today().isoformat(), sessions=None, rules=db.rules())
        if site_id and members:
            member = next((m for m in members if m["id"] == member_id), members[0])
            ctx["member_id"] = member["id"]
            try:
                d0 = date.fromisoformat(ctx["start"])
                ctx["sessions"] = sorted(engine.timetable(member, site_id, d0, d0 + timedelta(days=6)),
                                         key=lambda s: (s["_date"] or date.min, s["_start"]))
            except (HapanaError, ValueError) as exc:
                ctx["err"] = f"Couldn't load the timetable: {exc}"
        return page(request, "timetable.html", **ctx)

    # -------------------------------------------------------------- members
    @app.get("/members", response_class=HTMLResponse)
    def members(request: Request):
        return page(request, "members.html", members=db.members())

    @app.get("/members/new", response_class=HTMLResponse)
    def member_new(request: Request):
        return page(request, "member_form.html", m={"id": None, "enabled": 1})

    @app.get("/members/{member_id}", response_class=HTMLResponse)
    def member_edit(request: Request, member_id: int):
        m = db.member(member_id)
        if not m:
            return go(request, "/members", err="Member not found")
        return page(request, "member_form.html", m=dict(m))

    @app.post("/members/save")
    async def member_save(request: Request):
        f = await request.form()
        member_id = int(f["id"]) if f.get("id") else None
        name, email, pw = (f.get("name") or "").strip(), (f.get("email") or "").strip(), f.get("password") or ""
        if not name or not email or (not member_id and not pw):
            return go(request, f"/members/{member_id}" if member_id else "/members/new",
                      err="Name, email and password are required")
        member_id = db.save_member(
            member_id, name=name, email=email, password_enc=vault.encrypt(pw) if pw else "",
            package_subscription_id=(f.get("package_subscription_id") or "").strip(),
            discord_user_id=(f.get("discord_user_id") or "").strip(),
            ha_notify_service=(f.get("ha_notify_service") or "").strip(),
            enabled=1 if f.get("enabled") else 0,
        )
        return go(request, "/members", msg=f"Saved {name}")

    @app.post("/members/{member_id}/test")
    def member_test(request: Request, member_id: int):
        m = db.member(member_id)
        try:
            return go(request, "/members", msg=f"{m['name']}: {engine.test_login(m)}")
        except (HapanaError, ValueError) as exc:
            return go(request, "/members", err=f"{m['name']}: {exc}")

    @app.get("/members/{member_id}/packs", response_class=HTMLResponse)
    def member_packs(request: Request, member_id: int):
        m = db.member(member_id)
        if not m:
            return go(request, "/members", err="Member not found")
        try:
            packs = engine.find_packs(m)
        except (HapanaError, ValueError) as exc:
            return go(request, f"/members/{member_id}", err=f"Couldn't load {m['name']}'s packs: {exc}")
        rows = []
        for p in packs:
            if not isinstance(p, dict):
                continue
            pid = p.get("packageSubscriptionID") or p.get("packageSubscriptionId") or ""
            rows.append({
                "id": str(pid),
                "name": p.get("packageName") or p.get("name") or "(unnamed)",
                "type": p.get("packageType") or p.get("type") or "",
                "credits": p.get("creditsRemaining", p.get("remaining", "")),
                "expiry": str(p.get("expiryDate") or p.get("endDate") or "")[:10],
                "status": p.get("status") or p.get("packageStatus") or "",
            })
        return page(request, "packs.html", m=m, rows=rows, raw=json.dumps(packs, indent=2, default=str)[:20000])

    @app.post("/members/{member_id}/use-pack")
    def member_use_pack(request: Request, member_id: int, package_subscription_id: str = Form("")):
        db.set_member_package(member_id, package_subscription_id.strip())
        return go(request, f"/members/{member_id}",
                  msg="Saved as the preferred pack" if package_subscription_id.strip() else "Cleared - the bot will pick automatically")

    @app.post("/members/{member_id}/delete")
    def member_delete(request: Request, member_id: int):
        db.delete_member(member_id)
        return go(request, "/members", msg="Member removed")

    # -------------------------------------------------------------- history
    @app.get("/history", response_class=HTMLResponse)
    def history(request: Request):
        return page(request, "history.html", attempts=db.attempts(300), sites=db.site_names())

    @app.post("/attempts/{attempt_id}/reset")
    def attempt_reset(request: Request, attempt_id: int):
        db.delete_attempt(attempt_id)
        return go(request, "/history", msg="Cleared - the bot may book that class again on its next run")

    # ------------------------------------------------------------- settings
    @app.get("/settings", response_class=HTMLResponse)
    def settings_page(request: Request):
        return page(request, "settings.html", settings=settings, sites=db.sites(),
                    ha_available=bool(notifier.ha_url and notifier.ha_token))

    @app.post("/settings/test-notify")
    def test_notify(request: Request):
        sent = notifier.test()
        return go(request, "/settings", msg=("Sent to: " + ", ".join(sent)) if sent else "",
                  err="" if sent else "No notification channel configured")

    @app.post("/sites/save")
    def site_save(request: Request, site_id: str = Form(...), name: str = Form(...)):
        db.save_site(site_id, name)
        return go(request, "/settings", msg=f"Saved site {name}")

    @app.post("/sites/{site_id}/delete")
    def site_delete(request: Request, site_id: str):
        db.delete_site(site_id)
        return go(request, "/settings", msg="Site removed")

    @app.get("/health")
    def health():
        return {"ok": True, "next_run": scheduler.next_run_at.isoformat() if scheduler.next_run_at else None}

    return app
