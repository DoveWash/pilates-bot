"""The booking engine: match rules against the live timetable and book, once per class."""
from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Callable
from zoneinfo import ZoneInfo

from .config import Settings
from .crypto import Vault
from .db import DB, TERMINAL
from .hapana import Auth, HapanaClient, HapanaError

log = logging.getLogger(__name__)


@dataclass
class Outcome:
    member_id: int
    member_name: str
    status: str  # booked | already_booked | skipped | failed | not_open | gave_up | login_failed | not_found | error
    message: str = ""
    session_name: str = ""
    session_date: str = ""
    start_time: str = ""
    instructor: str = ""
    site_name: str = ""


# ----------------------------------------------------------------- parsing helpers
def parse_date(value) -> date | None:
    """Accepts '2026-11-21', '2026-11-21T00:00:00', '2026-11-21 06:00:00' or '21/11/2026'."""
    head = str(value or "").strip().split("T")[0].split(" ")[0]
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(head, fmt).date()
        except ValueError:
            continue
    return None


def norm_time(value) -> str:
    """'18:00:00' / '18:00' / '6:00 PM' / '6:00pm' -> '18:00'."""
    if not value:
        return ""
    s = str(value).strip().upper().replace(".", ":")
    for fmt in ("%H:%M:%S", "%H:%M", "%I:%M %p", "%I:%M%p", "%I:%M:%S %p"):
        try:
            return datetime.strptime(s, fmt).strftime("%H:%M")
        except ValueError:
            continue
    return s[:5]


def norm(text) -> str:
    return " ".join(str(text or "").lower().split())


def instructor_of(session: dict) -> str:
    data = session.get("instructorData")
    if isinstance(data, list) and data and isinstance(data[0], dict):
        return str(data[0].get("instructorName") or "")
    return ""


def enrich(session: dict, site_id: str) -> dict:
    session["_site_id"] = site_id
    session["_date"] = parse_date(session.get("sessionDate"))
    session["_start"] = norm_time(session.get("startTime"))
    session["_instructor"] = instructor_of(session)
    return session


def matches(rule, s: dict) -> bool:
    d = s.get("_date")
    if d is None or s.get("_site_id") != rule["site_id"]:
        return False
    if rule["weekday"] is not None and d.weekday() != rule["weekday"]:
        return False
    if rule["session_id"] and s.get("sessionID") == rule["session_id"]:
        return True
    # Otherwise match on name + start time (+ instructor if set) so it survives id changes.
    if not rule["session_name"] or not rule["start_time"]:
        return False
    if norm(s.get("sessionName")) != norm(rule["session_name"]):
        return False
    if s.get("_start") != norm_time(rule["start_time"]):
        return False
    if rule["instructor"] and norm(s.get("_instructor")) != norm(rule["instructor"]):
        return False
    return True


class RunLog:
    def __init__(self):
        self.lines: list[str] = []
        self._lock = threading.Lock()

    def __call__(self, msg: str, *args):
        text = msg % args if args else msg
        log.info(text)
        with self._lock:
            self.lines.append(datetime.now().strftime("%H:%M:%S.%f")[:-3] + "  " + text)

    def text(self) -> str:
        return "\n".join(self.lines)


# ----------------------------------------------------------------------- engine
class Engine:
    def __init__(self, db: DB, vault: Vault, settings: Settings, notifier,
                 client_factory: Callable[[], HapanaClient] | None = None):
        self.db = db
        self.vault = vault
        self.settings = settings
        self.notifier = notifier
        self.client_factory = client_factory or (lambda: HapanaClient(settings.widget_id))
        self.tz = ZoneInfo(settings.timezone)
        self._run_lock = threading.Lock()
        self._prepared: dict[int, tuple[HapanaClient, Auth]] = {}

    def today(self) -> date:
        return datetime.now(self.tz).date()

    def opening_date(self, on: date | None = None) -> date:
        return (on or self.today()) + timedelta(days=self.settings.days_ahead)

    # -- login ------------------------------------------------------------
    def _login(self, member) -> tuple[HapanaClient, Auth]:
        client = self.client_factory()
        auth = client.login(member["email"], self.vault.decrypt(member["password_enc"]))
        return client, auth

    def test_login(self, member) -> str:
        _, auth = self._login(member)
        return f"Login OK (client id {auth.client_id})"

    def prepare(self):
        """Log everyone in ahead of the booking window so the booking itself is instant."""
        members = self.db.members_with_rules()

        def go(m):
            try:
                self._prepared[m["id"]] = self._login(m)
            except Exception as exc:  # noqa: BLE001 - retried at run time anyway
                log.warning("Pre-login failed for %s: %s", m["name"], exc)

        with ThreadPoolExecutor(max_workers=max(1, len(members))) as pool:
            list(pool.map(go, members))

    def timetable(self, member, site_id: str, start: date, end: date) -> list[dict]:
        client, auth = self._login(member)
        return [enrich(s, site_id) for s in client.list_sessions(auth, site_id, start, end)]

    # -- run --------------------------------------------------------------
    def run(self, dates: list[date], kind: str = "scheduled", notify: bool = True) -> tuple[int, list[Outcome]]:
        with self._run_lock:
            run_id = self.db.start_run(kind)
            rlog = RunLog()
            outcomes: list[Outcome] = []
            try:
                rlog("%s run for %s", kind, ", ".join(d.isoformat() for d in dates))
                members = self.db.members_with_rules()
                if not members:
                    rlog("No enabled members with enabled rules - nothing to do")
                with ThreadPoolExecutor(max_workers=max(1, len(members))) as pool:
                    for result in pool.map(lambda m: self._run_member(m, dates, rlog), members):
                        outcomes.extend(result)
            except Exception as exc:  # noqa: BLE001 - never let a run die silently
                log.exception("Run crashed")
                rlog("RUN CRASHED: %r", exc)
                outcomes.append(Outcome(0, "Pilates Bot", "error", f"Run crashed: {exc}"))
            finally:
                self._prepared.clear()
                counts: dict[str, int] = {}
                for o in outcomes:
                    counts[o.status] = counts.get(o.status, 0) + 1
                summary = ", ".join(f"{v} {k}" for k, v in sorted(counts.items())) or "nothing to do"
                rlog("Summary: %s", summary)
                self.db.finish_run(run_id, summary, rlog.text())
            if notify:
                try:
                    self.notifier.run_report(outcomes, kind)
                except Exception:  # noqa: BLE001
                    log.exception("Notifier failed")
            return run_id, outcomes

    def _run_member(self, m, dates: list[date], rlog: RunLog) -> list[Outcome]:
        name = m["name"]
        rules = self.db.rules_for_member(m["id"])
        if not rules:
            return []
        try:
            client, auth = self._prepared.pop(m["id"], None) or self._login(m)
        except (HapanaError, ValueError) as exc:
            rlog("[%s] login failed: %s", name, exc)
            return [Outcome(m["id"], name, "login_failed", str(exc))]

        start, end = min(dates), max(dates)
        sessions: list[dict] = []
        for site_id in sorted({r["site_id"] for r in rules}):
            try:
                try:
                    raw = client.list_sessions(auth, site_id, start, end)
                except HapanaError as exc:
                    if not exc.is_auth:
                        raise
                    rlog("[%s] token rejected, logging in again", name)
                    client, auth = self._login(m)
                    raw = client.list_sessions(auth, site_id, start, end)
                sessions.extend(enrich(s, site_id) for s in raw)
            except HapanaError as exc:
                rlog("[%s] could not load timetable for site %s: %s", name, site_id, exc)
                return [Outcome(m["id"], name, "error", f"Couldn't load the timetable: {exc}")]
        rlog("[%s] %d classes in timetable", name, len(sessions))

        sites = self.db.site_names()
        outcomes: list[Outcome] = []
        wanted = set(dates)
        for rule in rules:
            found = False
            for s in sessions:
                if s["_date"] not in wanted or not matches(rule, s):
                    continue
                found = True
                self.db.backfill_rule(rule["id"], s.get("sessionName", ""), s["_start"], s.get("sessionID", ""))
                o = self._handle(m, rule, s, client, auth, rlog)
                if o:
                    o.site_name = sites.get(s["_site_id"], s["_site_id"])
                    outcomes.append(o)
            if not found:
                for d in dates:
                    if rule["weekday"] is None or d.weekday() == rule["weekday"]:
                        rlog("[%s] no class matched rule '%s' on %s", name, rule["label"], d)
                        outcomes.append(Outcome(m["id"], name, "not_found",
                                                f"No class matched '{rule['label']}' - timetable changed or holiday?",
                                                session_name=rule["label"], session_date=d.isoformat(),
                                                site_name=sites.get(rule["site_id"], "")))
        return outcomes

    def _handle(self, m, rule, s: dict, client: HapanaClient, auth: Auth, rlog: RunLog) -> Outcome | None:
        name = m["name"]
        ds = s["_date"].isoformat()
        label = f"{s.get('sessionName', '?')} {ds} {s['_start']}"
        base = dict(member_id=m["id"], member_name=name, session_name=s.get("sessionName", ""), session_date=ds,
                    start_time=s["_start"], instructor=s["_instructor"])

        existing = self.db.get_attempt(m["id"], s["sessionID"], ds)
        if existing and existing["status"] in TERMINAL:
            rlog("[%s] %s already handled (%s) - leaving it alone", name, label, existing["status"])
            return None
        tries = existing["tries"] if existing else 0

        def save(status, message="", count_try=True):
            self.db.save_attempt(member_id=m["id"], rule_id=rule["id"], session=s, session_date=ds,
                                 status=status, message=message, count_try=count_try)
            rlog("[%s] %s -> %s %s", name, label, status, message)
            return Outcome(status=status, message=message, **base)

        if self.db.is_skipped(m["id"], rule["id"], ds):
            return save("skipped", "Skipped in the app", count_try=False)

        booking_status = str(s.get("bookingStatus") or "").strip()
        if booking_status:
            return save("already_booked", f"Booking status on the site: {booking_status}", count_try=False)

        session_status = str(s.get("sessionStatus") or "").strip().lower()
        if session_status != "open":
            status = "gave_up" if tries + 1 >= self.settings.max_tries else "not_open"
            return save(status, f"Class status is '{session_status or 'unknown'}'")

        try:
            result = client.checkout(auth, s, s["_site_id"], m["package_subscription_id"])
        except HapanaError as exc:
            # An expired token is worth retrying (next run logs in fresh); other hard errors aren't.
            final = (not exc.retryable and not exc.is_auth) or tries + 1 >= self.settings.max_tries
            return save("gave_up" if final else "failed", str(exc))

        if result.success:
            return save("booked", result.message)
        if "already" in result.message.lower() and "book" in result.message.lower():
            return save("already_booked", result.message)
        final = tries + 1 >= self.settings.max_tries
        return save("gave_up" if final else "failed", result.message)
