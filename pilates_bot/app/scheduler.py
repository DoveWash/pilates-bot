"""Nightly timer: pre-login, fire exactly at the booking window, then follow-up retries."""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta

from .engine import Engine

log = logging.getLogger(__name__)


class Scheduler(threading.Thread):
    def __init__(self, engine: Engine, notifier):
        super().__init__(daemon=True, name="scheduler")
        self.engine = engine
        self.settings = engine.settings
        self.notifier = notifier
        self.tz = engine.tz
        self._halt = threading.Event()
        self.next_run_at: datetime | None = None

    def stop(self):
        self._halt.set()

    def compute_next(self, now: datetime | None = None) -> datetime:
        now = now or datetime.now(self.tz)
        h, m, *rest = (int(p) for p in self.settings.run_at.split(":"))
        s = rest[0] if rest else 0
        candidate = datetime(now.year, now.month, now.day, h, m, s, tzinfo=self.tz)
        if candidate.timestamp() <= now.timestamp():
            nxt = now.date() + timedelta(days=1)
            candidate = datetime(nxt.year, nxt.month, nxt.day, h, m, s, tzinfo=self.tz)
        return candidate

    def _sleep_until(self, when: datetime) -> bool:
        """Sleep using real (UTC) timestamps so DST changes can't shift it. False if stopped."""
        target = when.timestamp()
        while not self._halt.is_set():
            remaining = target - time.time()
            if remaining <= 0:
                return True
            self._halt.wait(min(remaining, 30))
        return False

    def missed_run(self, now: datetime | None = None) -> datetime | None:
        """The most recent booking time, if it was recent enough to catch up on after a restart."""
        now = now or datetime.now(self.tz)
        hours = self.settings.catchup_hours
        if hours <= 0:
            return None
        last = self.compute_next(now - timedelta(days=1))
        if last.timestamp() > now.timestamp():  # can happen right at the boundary
            last = self.compute_next(now - timedelta(days=2))
        age = now.timestamp() - last.timestamp()
        return last if 0 <= age <= hours * 3600 else None

    def run(self):
        log.info("Scheduler started")
        missed = self.missed_run()
        if missed:
            # Already-handled classes are left alone, so this only books what the downtime missed.
            log.info("Catching up on the %s booking run", missed.isoformat())
            self._safe_run([self.engine.opening_date(missed.date())], "catchup")
        while not self._halt.is_set():
            fire = self.compute_next()
            self.next_run_at = fire
            target_date = self.engine.opening_date(fire.date())
            log.info("Next booking run %s for classes on %s", fire.isoformat(), target_date)

            if not self._sleep_until(fire - timedelta(seconds=self.settings.prelogin_seconds)):
                return
            try:
                self.engine.prepare()
            except Exception:  # noqa: BLE001
                log.exception("Pre-login failed")

            if not self._sleep_until(fire):
                return
            self._safe_run([target_date], "scheduled")

            for minutes in self.settings.retry_minutes:
                if not self._sleep_until(fire + timedelta(minutes=minutes)):
                    return
                self._safe_run([target_date], "retry")

    def _safe_run(self, dates, kind):
        try:
            self.engine.run(dates, kind)
        except Exception as exc:  # noqa: BLE001
            log.exception("Booking run failed")
            self.notifier.alert("Pilates Bot crashed", f"{kind} run failed: {exc}")
