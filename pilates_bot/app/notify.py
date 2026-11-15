"""Notifications to Discord (webhook) and/or Home Assistant notify services."""
from __future__ import annotations

import logging
import os

import requests

from .config import Settings
from .db import DB

log = logging.getLogger(__name__)

STYLE = {
    # status: (emoji, discord colour, short text)
    "booked": ("✅", 5763719, "Booked"),
    "already_booked": ("📌", 3447003, "Already booked"),
    "skipped": ("⏭️", 9807270, "Skipped"),
    "not_open": ("⏳", 15105570, "Not open yet - will retry"),
    "failed": ("⚠️", 15105570, "Failed - will retry"),
    "gave_up": ("❌", 15158332, "Couldn't book"),
    "login_failed": ("🔑", 15158332, "Login failed"),
    "not_found": ("❓", 9807270, "Class not found"),
    "error": ("🛑", 15158332, "Error"),
}

# Retry runs stay quiet unless something final happens.
RETRY_REPORTABLE = {"booked", "gave_up", "login_failed", "error"}
ALWAYS_QUIET = {"skipped"}


class Notifier:
    def __init__(self, settings: Settings, db: DB):
        self.settings = settings
        self.db = db
        if os.environ.get("SUPERVISOR_TOKEN"):
            self.ha_url = "http://supervisor/core/api"
            self.ha_token = os.environ["SUPERVISOR_TOKEN"]
        else:
            self.ha_url = os.environ.get("HA_URL", "").rstrip("/") + "/api" if os.environ.get("HA_URL") else ""
            self.ha_token = os.environ.get("HA_TOKEN", "")

    # -- public -----------------------------------------------------------
    def run_report(self, outcomes, kind: str):
        if kind == "retry":
            outcomes = [o for o in outcomes if o.status in RETRY_REPORTABLE]
        else:
            outcomes = [o for o in outcomes if o.status not in ALWAYS_QUIET]
        if not outcomes:
            if kind == "scheduled" and self.settings.notify_when_nothing_to_book:
                self.alert("Pilates Bot", "Nothing to book tonight.")
            return
        self._discord_report(outcomes)
        self._ha_report(outcomes)

    def alert(self, title: str, message: str):
        self._discord({"content": f"**{title}**\n{message}"})
        if self.settings.ha_notify_service:
            self._ha(self.settings.ha_notify_service, title, message)

    def test(self) -> list[str]:
        sent = []
        if self.settings.discord_webhook_url:
            sent.append("Discord" if self._discord({"content": "🤖 Pilates Bot test message"}) else "Discord (FAILED)")
        if self.settings.ha_notify_service:
            ok = self._ha(self.settings.ha_notify_service, "Pilates Bot", "Test message")
            sent.append("Home Assistant" if ok else "Home Assistant (FAILED)")
        return sent

    # -- formatting -------------------------------------------------------
    @staticmethod
    def _line(o) -> str:
        emoji, _, text = STYLE.get(o.status, ("•", 0, o.status))
        when = " ".join(p for p in (o.session_date, o.start_time) if p)
        where = f" @ {o.site_name}" if o.site_name else ""
        detail = f" - {o.message}" if o.status not in ("booked",) and o.message else ""
        return f"{emoji} {o.session_name or text} {when}{where}: {text}{detail}"

    def _members(self) -> dict:
        return {m["id"]: m for m in self.db.members()}

    def _discord_report(self, outcomes):
        if not self.settings.discord_webhook_url:
            return
        members = self._members()
        mentions = []
        for o in outcomes:
            m = members.get(o.member_id)
            uid = m["discord_user_id"] if m else ""
            if uid and f"<@{uid}>" not in mentions:
                mentions.append(f"<@{uid}>")
        embeds = []
        for o in outcomes:
            emoji, colour, text = STYLE.get(o.status, ("•", 0, o.status))
            fields = [{"name": "👤 Who", "value": o.member_name, "inline": True}]
            if o.session_date:
                fields.append({"name": "📅 Date", "value": o.session_date, "inline": True})
            if o.start_time:
                fields.append({"name": "🕒 Time", "value": o.start_time, "inline": True})
            if o.instructor:
                fields.append({"name": "👩 Instructor", "value": o.instructor, "inline": True})
            if o.site_name:
                fields.append({"name": "📍 Site", "value": o.site_name, "inline": True})
            if o.message and o.status != "booked":
                fields.append({"name": "📄 Detail", "value": o.message[:1024], "inline": False})
            embeds.append({"title": f"{emoji} {text} — {o.session_name or 'Pilates Bot'}"[:256],
                           "color": colour, "fields": fields})
        for i in range(0, len(embeds), 10):  # Discord allows 10 embeds per message
            payload = {"embeds": embeds[i:i + 10],
                       "allowed_mentions": {"users": [m[2:-1] for m in mentions]}}
            if i == 0:
                payload["content"] = ("🤖 Pilates Bot " + " ".join(mentions)).strip()
            self._discord(payload)

    def _ha_report(self, outcomes):
        if not (self.ha_url and self.ha_token):
            return
        members = self._members()
        if self.settings.ha_notify_service:
            booked = sum(o.status == "booked" for o in outcomes)
            title = f"Pilates Bot: {booked} booked" if booked else "Pilates Bot"
            body = "\n".join(f"{o.member_name}: {self._line(o)}" for o in outcomes)
            self._ha(self.settings.ha_notify_service, title, body)
        by_member: dict[int, list] = {}
        for o in outcomes:
            by_member.setdefault(o.member_id, []).append(o)
        for mid, items in by_member.items():
            svc = members[mid]["ha_notify_service"] if mid in members else ""
            if svc and svc != self.settings.ha_notify_service:
                self._ha(svc, "Pilates Bot", "\n".join(self._line(o) for o in items))

    # -- transports -------------------------------------------------------
    def _discord(self, payload: dict) -> bool:
        url = self.settings.discord_webhook_url
        if not url:
            return False
        try:
            r = requests.post(url, json=payload, timeout=10)
            if r.status_code >= 300:
                log.error("Discord webhook HTTP %s: %s", r.status_code, r.text[:200])
                return False
            return True
        except requests.RequestException as exc:
            log.error("Discord webhook failed: %s", exc)
            return False

    def _ha(self, service: str, title: str, message: str) -> bool:
        if not (self.ha_url and self.ha_token):
            log.warning("HA notify '%s' configured but no HA API access", service)
            return False
        service = service.removeprefix("notify.")
        try:
            r = requests.post(
                f"{self.ha_url}/services/notify/{service}",
                headers={"Authorization": f"Bearer {self.ha_token}"},
                json={"title": title, "message": message},
                timeout=10,
            )
            if r.status_code >= 300:
                log.error("HA notify %s HTTP %s: %s", service, r.status_code, r.text[:200])
                return False
            return True
        except requests.RequestException as exc:
            log.error("HA notify %s failed: %s", service, exc)
            return False
