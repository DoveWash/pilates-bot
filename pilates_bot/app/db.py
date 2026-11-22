"""SQLite storage: members, sites, booking rules, skips, and every booking attempt.

The attempts table is what stops the old "re-book a class she cancelled" bug: each
(member, session, date) is handled once. After it's booked, it's never booked again.
"""
from __future__ import annotations

import hashlib
import secrets
import sqlite3
import threading
from datetime import datetime, timedelta, timezone

SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS sites (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS members (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    email TEXT NOT NULL,
    password_enc TEXT NOT NULL,
    package_subscription_id TEXT NOT NULL DEFAULT '',
    discord_user_id TEXT NOT NULL DEFAULT '',
    ha_notify_service TEXT NOT NULL DEFAULT '',
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS rules (
    id INTEGER PRIMARY KEY,
    label TEXT NOT NULL,
    site_id TEXT NOT NULL,
    weekday INTEGER,            -- 0=Mon .. 6=Sun
    start_time TEXT NOT NULL DEFAULT '',   -- HH:MM
    session_name TEXT NOT NULL DEFAULT '',
    instructor TEXT NOT NULL DEFAULT '',
    session_id TEXT NOT NULL DEFAULT '',   -- recurring series id, if known
    enabled INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS rule_members (
    rule_id INTEGER NOT NULL REFERENCES rules(id) ON DELETE CASCADE,
    member_id INTEGER NOT NULL REFERENCES members(id) ON DELETE CASCADE,
    PRIMARY KEY (rule_id, member_id)
);
CREATE TABLE IF NOT EXISTS skips (
    member_id INTEGER NOT NULL REFERENCES members(id) ON DELETE CASCADE,
    rule_id INTEGER NOT NULL REFERENCES rules(id) ON DELETE CASCADE,
    session_date TEXT NOT NULL,
    PRIMARY KEY (member_id, rule_id, session_date)
);
CREATE TABLE IF NOT EXISTS attempts (
    id INTEGER PRIMARY KEY,
    member_id INTEGER NOT NULL REFERENCES members(id) ON DELETE CASCADE,
    rule_id INTEGER,
    session_id TEXT NOT NULL,
    session_date TEXT NOT NULL,
    session_name TEXT NOT NULL DEFAULT '',
    start_time TEXT NOT NULL DEFAULT '',
    instructor TEXT NOT NULL DEFAULT '',
    site_id TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL,
    message TEXT NOT NULL DEFAULT '',
    tries INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (member_id, session_id, session_date)
);
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    summary TEXT NOT NULL DEFAULT '',
    log TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS invites (
    id INTEGER PRIMARY KEY,
    token_hash TEXT NOT NULL UNIQUE,
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    used_at TEXT NOT NULL DEFAULT '',
    member_id INTEGER REFERENCES members(id) ON DELETE SET NULL,
    failures INTEGER NOT NULL DEFAULT 0
);
"""

YARRA = "amhmQmdIdldDL3lRdHFpZ3BuOW40Zz09"
BELCO = "aTE4MEpReDA1bFdkN3kvaHkyNFludz09"

# The classes hard-coded in the original bot. Seeded with no members, so nothing
# books until you tick who each rule is for. Name/time fill in on the first match.
SEED_RULES = [
    ("Mon heated yoga (Belco)", BELCO, 0, "Y2M2VWtLbkdIRWZwaVJMenVLbUJXQT09"),
    ("Mon yin yoga (Belco)", BELCO, 0, "V1ZXYkVvWTRIS3FxM0lyUTVCOUtSQT09"),
    ("Wed reformer pilates (Belco)", BELCO, 2, "em95dGhTa0RiYytRWU56M2xod2gxZz09"),
    ("Wed stretch & release (Belco)", BELCO, 2, "Q2tJL2JKQS9wYVlBaktzYjVsMGFPdz09"),
    ("Sat reformer 9:30 (Yarra)", YARRA, 5, "cmVCYjZQMnpKMHREUVFwbWY0RU5jQT09"),
    ("Sun reformer 16:50 (Yarra)", YARRA, 6, "cEU4ZmU4QURicUpWQmtmM2IvQ2pEZz09"),
    ("Sun stretch & release 17:40 (Yarra)", YARRA, 6, "ZGFLWmh6c0dDTmJ2TUZNZU56K0RzZz09"),
]

# Statuses after which a class is never touched again automatically.
TERMINAL = {"booked", "already_booked", "skipped", "gave_up"}


INVITE_DAYS = 7
INVITE_MAX_FAILURES = 5  # wrong booking-site logins before an invite stops working


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_token() -> str:
    return secrets.token_urlsafe(32)


class DB:
    def __init__(self, path: str):
        self.conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.lock = threading.RLock()
        with self.lock:
            self.conn.executescript(SCHEMA)
            self._migrate()
            if not self.one("SELECT 1 FROM sites"):
                self._seed()

    def _migrate(self):
        cols = {r["name"] for r in self.q("PRAGMA table_info(rules)")}
        if "on_date" not in cols:  # v2.2: one-off bookings (a single date instead of every week)
            self.x("ALTER TABLE rules ADD COLUMN on_date TEXT NOT NULL DEFAULT ''")
        mcols = {r["name"] for r in self.q("PRAGMA table_info(members)")}
        for col in ("portal_token_hash", "portal_last_seen", "ntfy_topic"):  # v2.4: friends' portal
            if col not in mcols:
                self.x(f"ALTER TABLE members ADD COLUMN {col} TEXT NOT NULL DEFAULT ''")

    def _seed(self):
        self.x("INSERT INTO sites (id, name) VALUES (?, ?)", (YARRA, "Yarralumla"))
        self.x("INSERT INTO sites (id, name) VALUES (?, ?)", (BELCO, "Belconnen"))
        for label, site, wd, sid in SEED_RULES:
            self.x(
                "INSERT INTO rules (label, site_id, weekday, session_id) VALUES (?, ?, ?, ?)",
                (label, site, wd, sid),
            )

    # -- helpers -----------------------------------------------------------
    def q(self, sql: str, args=()) -> list[sqlite3.Row]:
        with self.lock:
            return self.conn.execute(sql, args).fetchall()

    def one(self, sql: str, args=()) -> sqlite3.Row | None:
        with self.lock:
            return self.conn.execute(sql, args).fetchone()

    def x(self, sql: str, args=()) -> int:
        with self.lock:
            return self.conn.execute(sql, args).lastrowid

    # -- sites ------------------------------------------------------------
    def sites(self):
        return self.q("SELECT * FROM sites ORDER BY name")

    def site_names(self) -> dict[str, str]:
        return {r["id"]: r["name"] for r in self.sites()}

    def save_site(self, site_id: str, name: str):
        self.x("INSERT INTO sites (id, name) VALUES (?, ?) ON CONFLICT(id) DO UPDATE SET name=excluded.name",
               (site_id.strip(), name.strip()))

    def delete_site(self, site_id: str):
        self.x("DELETE FROM sites WHERE id=?", (site_id,))

    # -- members ----------------------------------------------------------
    def members(self, enabled_only=False):
        sql = "SELECT * FROM members" + (" WHERE enabled=1" if enabled_only else "") + " ORDER BY name"
        return self.q(sql)

    def member(self, member_id: int):
        return self.one("SELECT * FROM members WHERE id=?", (member_id,))

    def save_member(self, member_id: int | None, **f) -> int:
        cols = ["name", "email", "package_subscription_id", "discord_user_id", "ha_notify_service", "enabled"]
        if "ntfy_topic" in f:
            cols.append("ntfy_topic")
        if f.get("password_enc"):
            cols.append("password_enc")
        vals = [f[c] for c in cols]
        if member_id:
            self.x(f"UPDATE members SET {', '.join(c + '=?' for c in cols)} WHERE id=?", (*vals, member_id))
            return member_id
        return self.x(
            f"INSERT INTO members ({', '.join(cols)}, created_at) VALUES ({', '.join('?' * len(cols))}, ?)",
            (*vals, now_iso()),
        )

    def set_member_package(self, member_id: int, package_subscription_id: str):
        self.x("UPDATE members SET package_subscription_id=? WHERE id=?", (package_subscription_id, member_id))

    def delete_member(self, member_id: int):
        self.x("DELETE FROM members WHERE id=?", (member_id,))

    def member_by_email(self, email: str):
        return self.one("SELECT * FROM members WHERE lower(email)=lower(?)", (email.strip(),))

    def set_member_enabled(self, member_id: int, enabled: bool):
        self.x("UPDATE members SET enabled=? WHERE id=?", (1 if enabled else 0, member_id))

    def set_member_password(self, member_id: int, password_enc: str):
        self.x("UPDATE members SET password_enc=? WHERE id=?", (password_enc, member_id))

    def set_member_notify(self, member_id: int, *, ntfy_topic: str, discord_user_id: str):
        self.x("UPDATE members SET ntfy_topic=?, discord_user_id=? WHERE id=?",
               (ntfy_topic, discord_user_id, member_id))

    # -- portal access ----------------------------------------------------
    def new_portal_token(self, member_id: int) -> str:
        """A fresh personal link token for this person; the old one stops working. Only the hash is kept."""
        token = new_token()
        self.x("UPDATE members SET portal_token_hash=? WHERE id=?", (hash_token(token), member_id))
        return token

    def revoke_portal_token(self, member_id: int):
        self.x("UPDATE members SET portal_token_hash='' WHERE id=?", (member_id,))

    def member_by_portal_token(self, token: str):
        if not token or len(token) > 200:
            return None
        return self.one("SELECT * FROM members WHERE portal_token_hash=? AND portal_token_hash!=''",
                        (hash_token(token),))

    def touch_portal(self, member_id: int):
        self.x("UPDATE members SET portal_last_seen=? WHERE id=?", (now_iso(), member_id))

    # -- invites ----------------------------------------------------------
    def create_invite(self, note: str = "", days: int = INVITE_DAYS) -> str:
        token = new_token()
        expires = (datetime.now(timezone.utc) + timedelta(days=days)).isoformat(timespec="seconds")
        self.x("INSERT INTO invites (token_hash, note, created_at, expires_at) VALUES (?, ?, ?, ?)",
               (hash_token(token), note.strip()[:80], now_iso(), expires))
        return token

    def invite(self, token: str):
        """The invite for this token if it can still be used, else None."""
        if not token or len(token) > 200:
            return None
        row = self.one("SELECT * FROM invites WHERE token_hash=?", (hash_token(token),))
        if (not row or row["used_at"] or row["failures"] >= INVITE_MAX_FAILURES
                or row["expires_at"] < now_iso()):
            return None
        return row

    def invite_failed(self, invite_id: int):
        self.x("UPDATE invites SET failures=failures+1 WHERE id=?", (invite_id,))

    def use_invite(self, invite_id: int, member_id: int):
        self.x("UPDATE invites SET used_at=?, member_id=? WHERE id=?", (now_iso(), member_id, invite_id))

    def invites(self):
        return self.q("""SELECT i.*, m.name AS member_name FROM invites i
                         LEFT JOIN members m ON m.id=i.member_id ORDER BY i.id DESC LIMIT 50""")

    def delete_invite(self, invite_id: int):
        self.x("DELETE FROM invites WHERE id=?", (invite_id,))

    def members_with_rules(self):
        return self.q(
            """SELECT DISTINCT m.* FROM members m
               JOIN rule_members rm ON rm.member_id = m.id
               JOIN rules r ON r.id = rm.rule_id
               WHERE m.enabled=1 AND r.enabled=1 ORDER BY m.name"""
        )

    # -- rules ------------------------------------------------------------
    def rules(self):
        return self.q("SELECT * FROM rules ORDER BY on_date, weekday, start_time, label")

    def find_rule(self, *, site_id: str, on_date: str, session_id: str, weekday, start_time: str,
                  session_name: str):
        """An existing rule for the same class (so booking a class twice adds people instead of duplicating)."""
        for r in self.q("SELECT * FROM rules WHERE site_id=? AND on_date=?", (site_id, on_date)):
            if session_id and r["session_id"] == session_id and (on_date or r["weekday"] == weekday):
                return r
            if (r["weekday"] == weekday and r["start_time"] == start_time
                    and " ".join(r["session_name"].lower().split()) == " ".join(session_name.lower().split())):
                return r
        return None

    def set_rule_enabled(self, rule_id: int, enabled: bool):
        self.x("UPDATE rules SET enabled=? WHERE id=?", (1 if enabled else 0, rule_id))

    def add_rule_members(self, rule_id: int, member_ids: list[int]):
        for mid in member_ids:
            self.x("INSERT OR IGNORE INTO rule_members (rule_id, member_id) VALUES (?, ?)", (rule_id, mid))

    def remove_rule_member(self, rule_id: int, member_id: int):
        """Take one person off a booking. A one-off left with nobody on it is deleted."""
        with self.lock:
            self.x("DELETE FROM rule_members WHERE rule_id=? AND member_id=?", (rule_id, member_id))
            r = self.rule(rule_id)
            if r and r["on_date"] and not self.rule_member_ids(rule_id):
                self.delete_rule(rule_id)

    def rule(self, rule_id: int):
        return self.one("SELECT * FROM rules WHERE id=?", (rule_id,))

    def rule_member_ids(self, rule_id: int) -> set[int]:
        return {r["member_id"] for r in self.q("SELECT member_id FROM rule_members WHERE rule_id=?", (rule_id,))}

    def rule_members_map(self) -> dict[int, list[str]]:
        out: dict[int, list[str]] = {}
        for r in self.q("SELECT rm.rule_id, m.name FROM rule_members rm JOIN members m ON m.id=rm.member_id ORDER BY m.name"):
            out.setdefault(r["rule_id"], []).append(r["name"])
        return out

    def rules_for_member(self, member_id: int):
        return self.q(
            """SELECT r.* FROM rules r JOIN rule_members rm ON rm.rule_id=r.id
               WHERE rm.member_id=? AND r.enabled=1""",
            (member_id,),
        )

    def save_rule(self, rule_id: int | None, member_ids: list[int], **f) -> int:
        f.setdefault("on_date", "")
        cols = ["label", "site_id", "weekday", "start_time", "session_name", "instructor", "session_id", "enabled",
                "on_date"]
        vals = [f[c] for c in cols]
        with self.lock:
            if rule_id:
                self.x(f"UPDATE rules SET {', '.join(c + '=?' for c in cols)} WHERE id=?", (*vals, rule_id))
            else:
                rule_id = self.x(f"INSERT INTO rules ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", vals)
            self.x("DELETE FROM rule_members WHERE rule_id=?", (rule_id,))
            for mid in member_ids:
                self.x("INSERT INTO rule_members (rule_id, member_id) VALUES (?, ?)", (rule_id, mid))
        return rule_id

    def backfill_rule(self, rule_id: int, session_name: str, start_time: str, session_id: str):
        """Fill in blanks on a rule from the class it matched (seeded rules start with only an id)."""
        self.x(
            """UPDATE rules SET
                 session_name = CASE WHEN session_name='' THEN ? ELSE session_name END,
                 start_time   = CASE WHEN start_time=''   THEN ? ELSE start_time END,
                 session_id   = CASE WHEN session_id=''   THEN ? ELSE session_id END
               WHERE id=?""",
            (session_name, start_time, session_id, rule_id),
        )

    def delete_rule(self, rule_id: int):
        self.x("DELETE FROM rules WHERE id=?", (rule_id,))

    # -- skips ------------------------------------------------------------
    def is_skipped(self, member_id: int, rule_id: int, session_date: str) -> bool:
        return bool(self.one("SELECT 1 FROM skips WHERE member_id=? AND rule_id=? AND session_date=?",
                             (member_id, rule_id, session_date)))

    def toggle_skip(self, member_id: int, rule_id: int, session_date: str) -> bool:
        with self.lock:
            if self.is_skipped(member_id, rule_id, session_date):
                self.x("DELETE FROM skips WHERE member_id=? AND rule_id=? AND session_date=?",
                       (member_id, rule_id, session_date))
                return False
            self.x("INSERT INTO skips (member_id, rule_id, session_date) VALUES (?, ?, ?)",
                   (member_id, rule_id, session_date))
            return True

    def skips_between(self, start: str, end: str) -> set[tuple[int, int, str]]:
        rows = self.q("SELECT * FROM skips WHERE session_date BETWEEN ? AND ?", (start, end))
        return {(r["member_id"], r["rule_id"], r["session_date"]) for r in rows}

    # -- attempts ---------------------------------------------------------
    def get_attempt(self, member_id: int, session_id: str, session_date: str):
        return self.one("SELECT * FROM attempts WHERE member_id=? AND session_id=? AND session_date=?",
                        (member_id, session_id, session_date))

    def save_attempt(self, *, member_id: int, rule_id: int | None, session: dict, session_date: str,
                     status: str, message: str = "", count_try: bool = True):
        ts = now_iso()
        self.x(
            """INSERT INTO attempts (member_id, rule_id, session_id, session_date, session_name, start_time,
                                     instructor, site_id, status, message, tries, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(member_id, session_id, session_date) DO UPDATE SET
                 status=excluded.status, message=excluded.message, rule_id=excluded.rule_id,
                 tries=attempts.tries + excluded.tries, updated_at=excluded.updated_at""",
            (member_id, rule_id, session["sessionID"], session_date, session.get("sessionName", ""),
             session.get("_start", ""), session.get("_instructor", ""), session.get("_site_id", ""),
             status, message[:500], 1 if count_try else 0, ts, ts),
        )

    def delete_attempt(self, attempt_id: int):
        self.x("DELETE FROM attempts WHERE id=?", (attempt_id,))

    def attempts(self, limit=200):
        return self.q(
            """SELECT a.*, m.name AS member_name FROM attempts a JOIN members m ON m.id=a.member_id
               ORDER BY a.session_date DESC, a.start_time DESC, m.name LIMIT ?""",
            (limit,),
        )

    def attempts_between(self, start: str, end: str):
        return self.q("SELECT * FROM attempts WHERE session_date BETWEEN ? AND ?", (start, end))

    def get_attempt_for_rule(self, member_id: int, rule_id: int, session_date: str):
        return self.one("SELECT * FROM attempts WHERE member_id=? AND rule_id=? AND session_date=?",
                        (member_id, rule_id, session_date))

    def member_attempts(self, member_id: int, limit=30):
        return self.q("""SELECT * FROM attempts WHERE member_id=?
                         ORDER BY session_date DESC, start_time DESC LIMIT ?""", (member_id, limit))

    # -- runs -------------------------------------------------------------
    def start_run(self, kind: str) -> int:
        return self.x("INSERT INTO runs (kind, started_at) VALUES (?, ?)", (kind, now_iso()))

    def finish_run(self, run_id: int, summary: str, log: str):
        self.x("UPDATE runs SET finished_at=?, summary=?, log=? WHERE id=?", (now_iso(), summary, log, run_id))

    def runs(self, limit=20):
        return self.q("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,))

    def run(self, run_id: int):
        return self.one("SELECT * FROM runs WHERE id=?", (run_id,))
