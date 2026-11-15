"""Web flow tests: Book a class -> just this one / every week. Needs httpx (pip install httpx)."""
import gc
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app.db import BELCO
from app.hapana import Auth, BookingResult

INGRESS = "/api/hassio_ingress/abc"


class Fake:
    def __init__(self, world):
        self.world = world

    def login(self, e, p):
        return Auth("s", "t", e, None)

    def list_sessions(self, a, site, s, e):
        return [dict(x) for x in self.world["sessions"] if site == BELCO]

    def valid_credits(self, a, site, s):
        return [{"packageSubscriptionID": "M-1", "packageName": "Unlimited", "packageType": "membership",
                 "creditsRemaining": 9999}]

    def checkout(self, a, s, site, pkg):
        self.world["checkouts"].append((a.client_id, s["sessionID"], s["sessionDate"]))
        return BookingResult(True, "ok")


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    import importlib
    from app import config, main
    importlib.reload(config)
    importlib.reload(main)
    app, sched = main.build()
    engine = next(o for o in gc.get_objects() if type(o).__name__ == "Engine" and o.db.conn and
                  str(tmp_path) in o.db.conn.execute("PRAGMA database_list").fetchone()[2])
    today = engine.today()
    world = {"checkouts": [], "sessions": [
        # open now (tomorrow) and not open yet (in 10 days)
        {"sessionID": "S-OPEN", "sessionName": "Reformer Pilates", "sessionDate": (today + timedelta(days=1)).isoformat(),
         "startTime": "18:00:00", "sessionStatus": "open", "bookingStatus": ""},
        {"sessionID": "S-LATER", "sessionName": "Yin Yoga", "sessionDate": (today + timedelta(days=10)).isoformat(),
         "startTime": "19:20:00", "sessionStatus": "open", "bookingStatus": ""},
    ]}
    engine.client_factory = lambda: Fake(world)
    c = TestClient(app, headers={"X-Ingress-Path": INGRESS})
    for name in ("Em", "Sarah"):
        c.post("/members/save", data={"name": name, "email": f"{name.lower()}@x", "password": "pw", "enabled": "1"})
    return c, world, engine, today


def _book(c, session_id, d, name, start, mode, members=("1",)):
    return c.post("/book", data={"back": "/timetable?site_id=x", "site_id": BELCO, "session_id": session_id,
                                 "session_date": d.isoformat(), "session_name": name, "start_time": start,
                                 "mode": mode, "member_ids": list(members)}, follow_redirects=False)


def test_timetable_lists_classes(client):
    c, world, engine, today = client
    r = c.get(f"/timetable?site_id={BELCO}")
    assert r.status_code == 200
    assert "Reformer Pilates" in r.text and "Book now" in r.text
    assert f'href="{INGRESS}/static/style.css"' in r.text


def test_book_just_this_one_when_open(client):
    c, world, engine, today = client
    r = _book(c, "S-OPEN", today + timedelta(days=1), "Reformer Pilates", "18:00", "once", ("1", "2"))
    assert r.status_code == 303 and r.headers["location"].startswith(INGRESS + "/timetable")
    assert "msg=" in r.headers["location"] and "Booked" in r.headers["location"]
    assert sorted(x[0] for x in world["checkouts"]) == ["em@x", "sarah@x"]
    rules = engine.db.rules()
    once = [x for x in rules if x["on_date"]]
    assert len(once) == 1 and once[0]["session_id"] == "S-OPEN"
    # booking the same class again adds nobody twice and books nothing new
    _book(c, "S-OPEN", today + timedelta(days=1), "Reformer Pilates", "18:00", "once", ("1",))
    assert len([x for x in engine.db.rules() if x["on_date"]]) == 1
    assert len(world["checkouts"]) == 2


def test_book_when_it_opens_schedules_only(client):
    c, world, engine, today = client
    d = today + timedelta(days=10)
    r = _book(c, "S-LATER", d, "Yin Yoga", "19:20", "once")
    assert r.status_code == 303 and "will+be+booked" in r.headers["location"].replace("%20", "+")
    assert world["checkouts"] == []
    page = c.get(f"/timetable?site_id={BELCO}&start={(today + timedelta(days=7)).isoformat()}").text
    assert "📅 Em" in page


def test_every_week_books_this_one_too(client):
    c, world, engine, today = client
    _book(c, "S-OPEN", today + timedelta(days=1), "Reformer Pilates", "18:00", "weekly")
    weekly = [x for x in engine.db.rules() if not x["on_date"] and x["session_id"] == "S-OPEN"]
    assert len(weekly) == 1 and weekly[0]["weekday"] == (today + timedelta(days=1)).weekday()
    assert [x[1] for x in world["checkouts"]] == ["S-OPEN"]
    assert "My bookings" in c.get("/rules").text


def test_pause_checkbox(client):
    c, world, engine, today = client
    _book(c, "S-LATER", today + timedelta(days=10), "Yin Yoga", "19:20", "weekly")
    rid = [x for x in engine.db.rules() if x["session_id"] == "S-LATER"][0]["id"]
    form = c.get(f"/rules/{rid}").text
    assert "Pause this booking" in form and 'name="enabled"' not in form
    c.post("/rules/save", data={"id": rid, "label": "Yin", "site_id": BELCO, "weekday": "1", "start_time": "19:20",
                                "member_ids": ["1"], "paused": "1"})
    assert engine.db.rule(rid)["enabled"] == 0
