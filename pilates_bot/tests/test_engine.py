"""Engine tests against a fake booking API. Run from pilates_bot/: python -m pytest tests"""
from datetime import date

import pytest

from app.config import Settings
from app.crypto import Vault
from app.db import BELCO, DB
from app.engine import Engine, matches, enrich, norm_time, parse_date
from app.hapana import Auth, BookingResult, HapanaError

TARGET = date(2026, 11, 25)  # a Wednesday


class FakeClient:
    def __init__(self, world):
        self.world = world

    def login(self, email, password):
        if password == "bad":
            raise HapanaError("Login failed: wrong password", retryable=False)
        self.world["logins"].append(email)
        return Auth("sec", "tok", email, None)

    def list_sessions(self, auth, site_id, start, end):
        return [dict(s) for s in self.world["sessions"] if s["site"] == site_id]

    def valid_credits(self, auth, site_id, session):
        credits = self.world.get("credits", [])
        if isinstance(credits, Exception):
            raise credits
        return credits

    def client_packages(self, auth):
        return self.world.get("credits", [])

    def checkout(self, auth, session, site_id, package_id):
        self.world["checkouts"].append((auth.client_id, session["sessionID"]))
        self.world.setdefault("packs_used", []).append(package_id)
        result = self.world["checkout_results"].pop(0) if self.world["checkout_results"] else BookingResult(True, "ok")
        if isinstance(result, Exception):
            raise result
        return result


class NullNotifier:
    def __init__(self):
        self.reports = []

    def run_report(self, outcomes, kind):
        self.reports.append((kind, outcomes))


@pytest.fixture
def env(tmp_path):
    db = DB(str(tmp_path / "t.db"))
    vault = Vault(str(tmp_path / "k"))
    world = {"logins": [], "checkouts": [], "checkout_results": [], "sessions": [
        {"site": BELCO, "sessionID": "REF1", "sessionName": "Reformer Pilates", "sessionDate": "2026-11-25",
         "startTime": "18:00:00", "sessionStatus": "open", "bookingStatus": "",
         "instructorData": [{"instructorName": "Martina M"}]},
        {"site": BELCO, "sessionID": "OTHER", "sessionName": "Spin", "sessionDate": "2026-11-25",
         "startTime": "07:00:00", "sessionStatus": "open", "bookingStatus": ""},
    ]}
    settings = Settings(max_tries=3)
    notifier = NullNotifier()
    engine = Engine(db, vault, settings, notifier, client_factory=lambda: FakeClient(world))
    a = db.save_member(None, name="Em", email="em@x", password_enc=vault.encrypt("pw"),
                       package_subscription_id="PKG", discord_user_id="", ha_notify_service="", enabled=1)
    b = db.save_member(None, name="Sarah", email="sarah@x", password_enc=vault.encrypt("pw"),
                       package_subscription_id="PKG2", discord_user_id="", ha_notify_service="", enabled=1)
    for r in db.rules():  # drop the seeded rules for a clean slate
        db.delete_rule(r["id"])
    rule = db.save_rule(None, [a, b], label="Wed reformer", site_id=BELCO, weekday=2, start_time="18:00",
                        session_name="reformer pilates", instructor="", session_id="", enabled=1)
    return dict(db=db, engine=engine, world=world, a=a, b=b, rule=rule, notifier=notifier, vault=vault)


def test_books_group_once(env):
    _, outcomes = env["engine"].run([TARGET])
    assert sorted(o.status for o in outcomes) == ["booked", "booked"]
    assert sorted(env["world"]["checkouts"]) == [("em@x", "REF1"), ("sarah@x", "REF1")]


def test_never_rebooks_after_user_cancels(env):
    env["engine"].run([TARGET])
    # She cancels on the site: the class shows as open + not booked again.
    env["world"]["checkouts"].clear()
    _, outcomes = env["engine"].run([TARGET])
    assert env["world"]["checkouts"] == []
    assert outcomes == []


def test_skip(env):
    env["db"].toggle_skip(env["a"], env["rule"], TARGET.isoformat())
    _, outcomes = env["engine"].run([TARGET])
    statuses = {o.member_name: o.status for o in outcomes}
    assert statuses == {"Em": "skipped", "Sarah": "booked"}


def test_already_booked_manually(env):
    env["world"]["sessions"][0]["bookingStatus"] = "booked"
    _, outcomes = env["engine"].run([TARGET])
    assert {o.status for o in outcomes} == {"already_booked"}
    assert env["world"]["checkouts"] == []


def test_retry_then_give_up(env):
    env["db"].save_member(env["b"], name="Sarah", email="sarah@x", password_enc="", package_subscription_id="",
                          discord_user_id="", ha_notify_service="", enabled=0)
    world = env["world"]
    world["checkout_results"] = [HapanaError("HTTP 503", retryable=True)] * 3
    statuses = [env["engine"].run([TARGET], "retry")[1][0].status for _ in range(3)]
    assert statuses == ["failed", "failed", "gave_up"]
    # Gave up = terminal, no more tries.
    assert env["engine"].run([TARGET], "retry")[1] == []


def test_not_open_then_opens(env):
    env["db"].save_member(env["b"], name="Sarah", email="sarah@x", password_enc="", package_subscription_id="",
                          discord_user_id="", ha_notify_service="", enabled=0)
    env["world"]["sessions"][0]["sessionStatus"] = "closed"
    assert env["engine"].run([TARGET])[1][0].status == "not_open"
    env["world"]["sessions"][0]["sessionStatus"] = "open"
    assert env["engine"].run([TARGET], "retry")[1][0].status == "booked"


def test_login_failure_reported(env):
    env["db"].save_member(env["a"], name="Em", email="em@x", password_enc=env["vault"].encrypt("bad"),
                          package_subscription_id="PKG", discord_user_id="", ha_notify_service="", enabled=1)
    _, outcomes = env["engine"].run([TARGET])
    statuses = {o.member_name: o.status for o in outcomes}
    assert statuses == {"Em": "login_failed", "Sarah": "booked"}


def test_not_found_when_timetable_changes(env):
    env["world"]["sessions"][0]["startTime"] = "18:30:00"
    _, outcomes = env["engine"].run([TARGET])
    assert {o.status for o in outcomes} == {"not_found"}


def test_series_id_match_and_backfill(env):
    db = env["db"]
    db.save_rule(env["rule"], [env["a"]], label="Seeded", site_id=BELCO, weekday=2, start_time="",
                 session_name="", instructor="", session_id="REF1", enabled=1)
    env["engine"].run([TARGET])
    r = db.rule(env["rule"])
    assert (r["session_name"], r["start_time"]) == ("Reformer Pilates", "18:00")


def _solo(env):
    env["db"].save_member(env["b"], name="Sarah", email="sarah@x", password_enc="", package_subscription_id="",
                          discord_user_id="", ha_notify_service="", enabled=0)


PACK_OLD = {"packageSubscriptionID": "P-OLD", "packageName": "10 class pack", "packageType": "sessionPackage",
            "creditsRemaining": 0, "expiryDate": "2026-12-01"}
PACK_SOON = {"packageSubscriptionID": "P-SOON", "packageName": "5 class pack", "packageType": "sessionPackage",
             "creditsRemaining": 2, "expiryDate": "2026-12-10"}
PACK_LATE = {"packageSubscriptionID": "P-LATE", "packageName": "20 class pack", "packageType": "sessionPackage",
             "creditsRemaining": 15, "expiryDate": "2027-06-01"}
MEMBERSHIP = {"packageSubscriptionID": "M-1", "packageName": "Unlimited", "packageType": "membership",
              "creditsRemaining": 9999}


def test_saved_pack_used_when_still_valid(env):
    _solo(env)  # Em's saved pack is "PKG"
    env["world"]["credits"] = [PACK_LATE, dict(PACK_SOON, packageSubscriptionID="PKG")]
    env["engine"].run([TARGET])
    assert env["world"]["packs_used"] == ["PKG"]


def test_renewed_pack_replaces_stale_saved_id(env):
    _solo(env)
    env["world"]["credits"] = [PACK_OLD, PACK_LATE, PACK_SOON]  # PKG gone, P-OLD empty
    _, outcomes = env["engine"].run([TARGET])
    assert env["world"]["packs_used"] == ["P-SOON"]  # soonest expiry with credits, like the website
    assert env["db"].member(env["a"])["package_subscription_id"] == "P-SOON"
    assert "5 class pack" in outcomes[0].message


def test_membership_preferred(env):
    _solo(env)
    env["world"]["credits"] = [PACK_SOON, MEMBERSHIP]
    env["engine"].run([TARGET])
    assert env["world"]["packs_used"] == ["M-1"]


def test_credits_embedded_in_session(env):
    _solo(env)
    env["world"]["sessions"][0]["validCredits"] = [PACK_LATE]
    env["world"]["credits"] = HapanaError("should not be called")
    env["engine"].run([TARGET])
    assert env["world"]["packs_used"] == ["P-LATE"]


def test_no_pack_and_nothing_saved(env):
    _solo(env)
    env["db"].set_member_package(env["a"], "")
    env["world"]["credits"] = [PACK_OLD]
    _, outcomes = env["engine"].run([TARGET])
    assert outcomes[0].status == "failed" and "No class pack" in outcomes[0].message
    assert env["world"]["checkouts"] == []


def test_lookup_fails_falls_back_to_saved(env):
    _solo(env)
    env["world"]["credits"] = HapanaError("HTTP 500", retryable=True)
    _, outcomes = env["engine"].run([TARGET])
    assert outcomes[0].status == "booked" and env["world"]["packs_used"] == ["PKG"]


def test_helpers():
    assert norm_time("6:00 PM") == "18:00"
    assert norm_time("18:00:00") == "18:00"
    assert parse_date("2026-11-25T00:00:00") == TARGET
    assert parse_date("25/11/2026") == TARGET
    s = enrich({"sessionID": "x", "sessionName": "A", "sessionDate": "2026-11-25", "startTime": "18:00"}, BELCO)
    rule = {"site_id": BELCO, "weekday": 3, "session_id": "x", "session_name": "", "start_time": "", "instructor": ""}
    assert not matches(rule, s)  # wrong weekday beats the series id
