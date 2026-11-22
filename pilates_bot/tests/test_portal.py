"""Friends' portal + v2.4 fixes. Run from pilates_bot/: python -m pytest tests"""
import gc
import re
from datetime import datetime, timedelta
from urllib.parse import unquote

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.db import BELCO
from app.hapana import Auth, BookingResult, HapanaError
from app.notify import Notifier
from app.engine import Outcome


class Fake:
    def __init__(self, world):
        self.world = world

    def login(self, e, p):
        if p == "bad":
            raise HapanaError("Login failed: wrong password", retryable=False)
        if p == "down":
            raise HapanaError("Network error: Timeout", retryable=True)
        return Auth("s", "t", e, None)

    def list_sessions(self, a, site, s, e):
        return [dict(x) for x in self.world["sessions"] if site == BELCO]

    def valid_credits(self, a, site, s):
        return [{"packageSubscriptionID": "M-1", "packageName": "Unlimited", "packageType": "membership",
                 "creditsRemaining": 9999}]

    def client_packages(self, a):
        return self.valid_credits(a, None, None)

    def checkout(self, a, s, site, pkg):
        self.world["checkouts"].append((a.client_id, s["sessionID"], s["sessionDate"]))
        return BookingResult(True, "ok")


class Sent:
    def __init__(self):
        self.alerts = []


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("PB_PORTAL_ENABLED", "true")
    import importlib
    from app import config, main
    importlib.reload(config)
    importlib.reload(main)
    admin_app, sched, portal_app, settings = main.build()
    engine = next(o for o in gc.get_objects() if type(o).__name__ == "Engine" and o.db.conn and
                  str(tmp_path) in o.db.conn.execute("PRAGMA database_list").fetchone()[2])
    today = engine.today()
    world = {"checkouts": [], "sessions": [
        {"sessionID": "S-OPEN", "sessionName": "Reformer Pilates", "sessionDate": (today + timedelta(days=1)).isoformat(),
         "startTime": "18:00:00", "sessionStatus": "open", "bookingStatus": ""},
        {"sessionID": "S-LATER", "sessionName": "Yin Yoga", "sessionDate": (today + timedelta(days=10)).isoformat(),
         "startTime": "19:20:00", "sessionStatus": "open", "bookingStatus": ""},
    ]}
    engine.client_factory = lambda: Fake(world)
    alerts = []
    engine.notifier.alert = lambda title, msg: alerts.append(msg)
    engine.notifier.run_report = lambda outcomes, kind: None
    admin = TestClient(admin_app, headers={"X-Ingress-Path": "/ingress"})
    admin.post("/members/save", data={"name": "Wife", "email": "wife@x", "password": "pw", "enabled": "1"})
    portal = TestClient(portal_app)
    return dict(admin=admin, portal=portal, engine=engine, db=engine.db, world=world, today=today, alerts=alerts)


def invite_token(env, note="Sarah"):
    r = env["admin"].post("/invites/new", data={"note": note}, follow_redirects=False)
    link = unquote(re.search(r"link=([^&#]+)", r.headers["location"]).group(1))
    return link.rsplit("/join/", 1)[1]


def join(env, token, email="sarah@x", password="pw", name="Sarah"):
    return env["portal"].post(f"/join/{token}", data={"name": name, "email": email, "password": password},
                              follow_redirects=False)


def joined(env):
    r = join(env, invite_token(env))
    assert r.status_code == 303 and r.headers["location"].startswith("/m/")
    return env["db"].member_by_email("sarah@x")


def book(env, session_id, d, name, start, mode):
    return env["portal"].post("/book", data={"back": "/book", "site_id": BELCO, "session_id": session_id,
                                             "session_date": d.isoformat(), "session_name": name,
                                             "start_time": start, "mode": mode}, follow_redirects=False)


# ----------------------------------------------------------------- joining
def test_join_flow_and_personal_link(env):
    token = invite_token(env)
    page = env["portal"].get(f"/join/{token}")
    assert page.status_code == 200 and 'value="Sarah"' in page.text  # note pre-fills the name
    r = join(env, token)
    assert r.status_code == 303 and "pb_portal" in r.headers.get("set-cookie", "")
    home = env["portal"].get(r.headers["location"])
    assert home.status_code == 200 and "You're in, Sarah" in home.text
    assert 'rel="manifest"' in home.text
    assert home.headers["referrer-policy"] == "no-referrer"
    assert env["portal"].get("/").status_code == 200  # cookie keeps her signed in
    assert any("Sarah joined" in a for a in env["alerts"])
    # invite is single use
    assert join(env, token, email="other@x").status_code == 404


def test_wrong_password_locks_invite_after_5(env):
    token = invite_token(env)
    for _ in range(5):
        r = join(env, token, password="bad")
        assert r.status_code == 400 and "accept that email and password" in r.text
    assert join(env, token).status_code == 404
    assert env["db"].member_by_email("sarah@x") is None


def test_site_down_does_not_count_against_invite(env):
    token = invite_token(env)
    for _ in range(6):
        assert "reach the booking site just now" in join(env, token, password="down").text
    assert join(env, token).status_code == 303


def test_existing_email_rejected(env):
    r = join(env, invite_token(env), email="WIFE@x")
    assert r.status_code == 400 and "already set up" in r.text


def test_signed_out_and_bad_tokens(env):
    p = env["portal"]
    assert p.get("/").status_code == 401
    assert p.get("/m/not-a-real-token").status_code == 401
    assert p.get("/join/nope").status_code == 404
    assert p.get("/members").status_code == 404  # admin pages don't exist on the portal at all
    assert p.post("/skip", data={"rule_id": 1, "session_date": "2026-01-01"}).status_code == 401


def test_revoked_link_stops_working(env):
    m = joined(env)
    assert env["portal"].get("/").status_code == 200
    env["admin"].post(f"/members/{m['id']}/portal-revoke")
    assert env["portal"].get("/").status_code == 401


def test_admin_new_link_replaces_old(env):
    m = joined(env)
    r = env["admin"].post(f"/members/{m['id']}/portal-link", follow_redirects=False)
    link = unquote(re.search(r"link=([^&#]+)", r.headers["location"]).group(1))
    assert env["portal"].get("/").status_code == 401  # old cookie token is dead
    fresh = TestClient(env["portal"].app)
    assert fresh.get("/m/" + link.rsplit("/m/", 1)[1]).status_code == 200
    assert "Personal link" in env["admin"].get("/members").text or "New link" in env["admin"].get("/members").text


# ----------------------------------------------------------------- booking
def test_friend_books_only_herself(env):
    db, world, today = env["db"], env["world"], env["today"]
    wife = db.member_by_email("wife@x")
    d = today + timedelta(days=1)
    # Wife is on a weekly rule for the same class, but it hasn't been booked for her yet.
    db.save_rule(None, [wife["id"]], label="Wife's reformer", site_id=BELCO, weekday=d.weekday(), start_time="18:00",
                 session_name="Reformer Pilates", instructor="", session_id="S-OPEN", enabled=1)
    sarah = joined(env)
    r = book(env, "S-OPEN", d, "Reformer Pilates", "18:00", "weekly")
    assert r.status_code == 303 and "err=" not in r.headers["location"]
    assert world["checkouts"] == [("sarah@x", "S-OPEN", d.isoformat())]  # not the wife
    rule = [x for x in db.rules() if x["session_id"] == "S-OPEN"]
    assert len(rule) == 1 and db.rule_member_ids(rule[0]["id"]) == {wife["id"], sarah["id"]}


def test_friend_timetable_and_home(env):
    joined(env)
    d = env["today"] + timedelta(days=10)
    book(env, "S-LATER", d, "Yin Yoga", "19:20", "once")
    assert env["world"]["checkouts"] == []  # not open yet: booked when it opens
    t = env["portal"].get(f"/book?site_id={BELCO}&start={(env['today'] + timedelta(days=7)).isoformat()}").text
    assert "Yin Yoga" in t and "📅 booked in" in t
    home = env["portal"].get("/").text
    assert "Yin Yoga" in home and "Skip" in home


def test_friend_cannot_touch_others_rules(env):
    db = env["db"]
    wife = db.member_by_email("wife@x")
    rid = db.save_rule(None, [wife["id"]], label="Wife only", site_id=BELCO, weekday=0, start_time="09:00",
                       session_name="Spin", instructor="", session_id="", enabled=1)
    joined(env)
    p = env["portal"]
    r = p.post("/skip", data={"rule_id": rid, "session_date": "2030-01-07"}, follow_redirects=False)
    assert "isn%27t+yours" in r.headers["location"].replace("%20", "+") or "isn't" in unquote(r.headers["location"])
    p.post("/leave", data={"rule_id": rid})
    assert db.rule_member_ids(rid) == {wife["id"]}
    assert not db.is_skipped(wife["id"], rid, "2030-01-07")
    # her home page doesn't show the wife's classes
    assert "Spin" not in p.get("/").text


def test_skip_and_leave_own(env):
    sarah = joined(env)
    db, d = env["db"], env["today"] + timedelta(days=10)
    book(env, "S-LATER", d, "Yin Yoga", "19:20", "weekly")
    rid = [x for x in db.rules() if x["session_id"] == "S-LATER"][0]["id"]
    env["portal"].post("/skip", data={"rule_id": rid, "session_date": d.isoformat()})
    assert db.is_skipped(sarah["id"], rid, d.isoformat())
    env["portal"].post("/leave", data={"rule_id": rid})
    assert sarah["id"] not in db.rule_member_ids(rid)


def test_cross_site_post_blocked(env):
    joined(env)
    r = env["portal"].post("/me/pause", headers={"Origin": "https://evil.example"})
    assert r.status_code == 403


def test_account_pause_notify_delete(env):
    sarah = joined(env)
    p, db = env["portal"], env["db"]
    assert "Show my packs" in p.get("/me").text
    assert "Unlimited" in p.get("/me?packs=1").text
    p.post("/me/notify", data={"ntfy_topic": "pilates-sarah-abc123", "discord_user_id": ""})
    assert db.member(sarah["id"])["ntfy_topic"] == "pilates-sarah-abc123"
    r = p.post("/me/notify", data={"ntfy_topic": "bad topic!", "discord_user_id": ""}, follow_redirects=False)
    assert "err=" in r.headers["location"]
    p.post("/me/pause")
    assert db.member(sarah["id"])["enabled"] == 0
    assert "err=" in book(env, "S-OPEN", env["today"] + timedelta(days=1), "Reformer Pilates", "18:00",
                          "once").headers["location"]
    p.post("/me/pause")
    assert db.member(sarah["id"])["enabled"] == 1
    assert "err=" in p.post("/me/password", data={"password": "bad"}, follow_redirects=False).headers["location"]
    assert "msg=" in p.post("/me/password", data={"password": "new"}, follow_redirects=False).headers["location"]
    p.post("/me/delete", data={"confirm": "nope"})
    assert db.member(sarah["id"])
    r = p.post("/me/delete", data={"confirm": "DELETE"})
    assert "All done" in r.text and db.member(sarah["id"]) is None


# ------------------------------------------------------------- v2.4 bug fixes
def test_ingress_header_no_longer_bypasses_password(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("WEB_PASSWORD", "secret")
    import importlib
    from app import config, main
    importlib.reload(config)
    importlib.reload(main)
    app = main.build()[0]
    c = TestClient(app)
    assert c.get("/", headers={"X-Ingress-Path": "/x"}).status_code == 401  # forged header
    assert c.get("/", auth=("any", "secret")).status_code == 200


def test_early_runs_dont_use_up_tries(env):
    """Clicking 'Book now' for a date that isn't open yet used to count tries until the bot gave up for good."""
    engine, db = env["engine"], env["db"]
    wife = db.member_by_email("wife@x")
    d = env["today"] + timedelta(days=10)
    env["world"]["sessions"][1]["sessionStatus"] = "closed"
    db.save_rule(None, [wife["id"]], label="yin", site_id=BELCO, weekday=d.weekday(), start_time="19:20",
                 session_name="Yin Yoga", instructor="", session_id="", enabled=1)
    for _ in range(engine.settings.max_tries + 2):
        engine.run([d], "manual", notify=False)
    a = db.get_attempt(wife["id"], "S-LATER", d.isoformat())
    assert a["status"] == "not_open" and a["tries"] == 0


def test_catchup_after_restart():
    from zoneinfo import ZoneInfo
    from app.scheduler import Scheduler

    class E:
        settings = Settings(catchup_hours=6)
        tz = ZoneInfo("Australia/Sydney")

    s = Scheduler(E(), None)
    tz = E.tz
    assert s.missed_run(datetime(2026, 11, 22, 0, 40, tzinfo=tz)) == datetime(2026, 11, 22, 0, 0, tzinfo=tz)
    assert s.missed_run(datetime(2026, 11, 22, 7, 0, tzinfo=tz)) is None
    assert s.missed_run(datetime(2026, 11, 21, 23, 0, tzinfo=tz)) is None
    E.settings = Settings(catchup_hours=0)
    s.settings = E.settings
    assert s.missed_run(datetime(2026, 11, 22, 0, 40, tzinfo=tz)) is None


def test_retries_dont_repeat_login_failure(tmp_path):
    from app.db import DB
    n = Notifier(Settings(), DB(str(tmp_path / "n.db")))
    sent = []
    n._discord_report = lambda o: sent.append([x.status for x in o])
    n._ha_report = n._ntfy_report = lambda o: None
    fail = [Outcome(1, "Em", "login_failed", "bad password", session_date="")]
    n.run_report(fail, "scheduled")
    for _ in range(5):
        n.run_report(fail, "retry")
    n.run_report(fail + [Outcome(2, "Sarah", "booked", session_date="2026-11-29")], "retry")
    assert sent == [["login_failed"], ["booked"]]
    n.run_report(fail, "scheduled")  # next night it's reported again
    assert sent[-1] == ["login_failed"]


def test_portal_off_by_default(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("PB_PORTAL_ENABLED", raising=False)
    import importlib
    from app import config, main
    importlib.reload(config)
    importlib.reload(main)
    admin_app, _, portal_app, settings = main.build()
    assert portal_app is None and not settings.portal_enabled
    admin = TestClient(admin_app, headers={"X-Ingress-Path": "/ingress"})
    r = admin.post("/invites/new", data={"note": "x"}, follow_redirects=False)
    assert "err=" in r.headers["location"]
    assert "Invite a friend" not in admin.get("/members").text
