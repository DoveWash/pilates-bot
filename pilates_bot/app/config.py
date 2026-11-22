"""Settings, read from the HA app options file (/data/options.json) with env overrides."""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field, fields

log = logging.getLogger(__name__)

DATA_DIR = os.environ.get("DATA_DIR", "/data")


@dataclass
class Settings:
    timezone: str = "Australia/Sydney"
    # Booking opens at midnight this many days ahead (checked on the live timetable: 7).
    days_ahead: int = 7
    # Local time the booking window opens.
    run_at: str = "00:00:00"
    # Log everyone in this many seconds before run_at so the booking fires instantly.
    prelogin_seconds: int = 90
    # Follow-up runs (minutes after run_at) for anything that failed or wasn't open yet.
    retry_minutes: list[float] = field(default_factory=lambda: [0.5, 2, 5, 15, 60])
    # After this many failed tries for one class, give up and alert.
    max_tries: int = 6
    discord_webhook_url: str = ""
    # e.g. "mobile_app_daryls_iphone" - gets the admin summary of every run.
    ha_notify_service: str = ""
    notify_when_nothing_to_book: bool = False
    # If the app was down at the booking time (HA update/restart), book that night's classes
    # when it starts again, as long as it's within this many hours. 0 = never catch up.
    catchup_hours: int = 6
    # Friends' portal: a separate, phone-friendly site on its own port, no HA access needed.
    portal_enabled: bool = False
    portal_port: int = 8100
    # The address friends use, e.g. https://pilates.example.com (used to build invite links).
    portal_url: str = ""
    # Push notifications for people without HA: https://ntfy.sh (or your own ntfy server).
    ntfy_server: str = "https://ntfy.sh"
    # Hapana widget id used to fetch the security token (Yarralumla's site id in the old bot).
    widget_id: str = "amhmQmdIdldDL3lRdHFpZ3BuOW40Zz09"

    @property
    def data_dir(self) -> str:
        return DATA_DIR


def _coerce(name: str, raw, default):
    if raw is None or raw == "":
        return default
    if name == "retry_minutes":
        if isinstance(raw, str):
            raw = [p for p in raw.replace(" ", "").split(",") if p]
        return sorted(float(x) for x in raw)
    if isinstance(default, bool):
        return raw if isinstance(raw, bool) else str(raw).lower() in ("1", "true", "yes", "on")
    if isinstance(default, int):
        return int(raw)
    return str(raw)


def load_settings() -> Settings:
    s = Settings()
    path = os.path.join(DATA_DIR, "options.json")
    options: dict = {}
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as fh:
                options = json.load(fh)
        except (OSError, ValueError) as exc:
            log.error("Could not read %s: %s", path, exc)
    for f in fields(Settings):
        default = getattr(s, f.name)
        raw = os.environ.get("PB_" + f.name.upper(), options.get(f.name))
        try:
            setattr(s, f.name, _coerce(f.name, raw, default))
        except (TypeError, ValueError):
            log.error("Invalid value for %s: %r - using default %r", f.name, raw, default)
    return s
