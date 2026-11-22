"""Booking actions shared by the admin UI and the friends' portal."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from .notify import STYLE

WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
BAD = ("gave_up", "login_failed", "error", "failed", "not_found")


@dataclass
class BookReply:
    ok: bool
    message: str
    rule_id: int


def book_class(db, engine, settings, *, member_ids: list[int], site_id: str, session_id: str, d: date,
               name: str, start: str, weekly: bool) -> BookReply:
    """Put these people on this class (just this date, or every week), and book it now if it's open.

    Joins an existing booking for the same class instead of duplicating it, and only ever books
    the people passed in - not everyone else already on that class.
    """
    od = "" if weekly else d.isoformat()
    existing = db.find_rule(site_id=site_id, on_date=od, session_id=session_id, weekday=d.weekday(),
                            start_time=start, session_name=name)
    if existing:
        rule_id = existing["id"]
        db.add_rule_members(rule_id, member_ids)
        if not existing["enabled"]:
            db.set_rule_enabled(rule_id, True)
    else:
        label = f"{WEEKDAYS[d.weekday()]} {name} {start}" + ("" if weekly else d.strftime(" (%d %b)"))
        site_short = db.site_names().get(site_id, "")
        rule_id = db.save_rule(None, member_ids, label=f"{label} - {site_short}" if site_short else label,
                               site_id=site_id, weekday=d.weekday(), start_time=start, session_name=name,
                               instructor="", session_id=session_id, enabled=1, on_date=od)

    who = ", ".join(m["name"] for m in db.members() if m["id"] in member_ids)
    if d <= engine.opening_date():
        _, outcomes = engine.run([d], "manual", rule_ids={rule_id}, member_ids=set(member_ids))
        results = "; ".join(f"{o.member_name}: {STYLE.get(o.status, ('', 0, o.status))[2]}"
                            + (f" ({o.message})" if o.status != "booked" and o.message else "")
                            for o in outcomes) or "nothing to do (already handled)"
        extra = " Booking every week from now on." if weekly else ""
        bad = any(o.status in BAD for o in outcomes)
        return BookReply(not bad, f"{name} {d:%a %d %b}: {results}.{extra}", rule_id)
    opens = d - timedelta(days=settings.days_ahead)
    when = f"at {settings.run_at[:5]} on {opens:%a %d %b}, when booking opens"
    msg = (f"{name} booked for {who} every week. The first one ({d:%a %d %b}) will be booked {when}."
           if weekly else f"{name} on {d:%a %d %b} will be booked for {who} {when}.")
    return BookReply(True, msg, rule_id)


def upcoming_rows(db, settings, today: date, days: int = 21, member_ids: set[int] | None = None) -> list[dict]:
    """Every (date, rule, person) coming up from the booking rules, with its status and skip flag."""
    span = [today + timedelta(days=i) for i in range(days)]
    end = span[-1].isoformat()
    attempts = {(a["member_id"], a["rule_id"], a["session_date"]): a
                for a in db.attempts_between(today.isoformat(), end)}
    skips = db.skips_between(today.isoformat(), end)
    members = {m["id"]: m for m in db.members(enabled_only=True)}
    rows = []
    for rule in db.rules():
        if not rule["enabled"] or (rule["weekday"] is None and not rule["on_date"]):
            continue
        for mid in sorted(db.rule_member_ids(rule["id"])):
            if mid not in members or (member_ids is not None and mid not in member_ids):
                continue
            for d in span:
                if rule["on_date"] and d.isoformat() != rule["on_date"]:
                    continue
                if not rule["on_date"] and d.weekday() != rule["weekday"]:
                    continue
                key = (mid, rule["id"], d.isoformat())
                rows.append({"date": d, "rule": rule, "member": members[mid],
                             "attempt": attempts.get(key), "skipped": key in skips,
                             "opens": d - timedelta(days=settings.days_ahead)})
    rows.sort(key=lambda r: (r["date"], r["rule"]["start_time"], r["member"]["name"]))
    return rows
