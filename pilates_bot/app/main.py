"""Entry point: python -m app.main"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal

import uvicorn

from .config import DATA_DIR, load_settings
from .crypto import Vault
from .db import DB
from .engine import Engine
from .notify import Notifier
from .portal import create_portal_app
from .scheduler import Scheduler
from .web import create_app

log = logging.getLogger(__name__)


def build():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    os.makedirs(DATA_DIR, exist_ok=True)
    settings = load_settings()
    db = DB(os.path.join(DATA_DIR, "pilates.db"))
    vault = Vault(os.path.join(DATA_DIR, "secret.key"))
    notifier = Notifier(settings, db)
    engine = Engine(db, vault, settings, notifier)
    scheduler = Scheduler(engine, notifier)
    app = create_app(db, vault, settings, engine, scheduler, notifier)
    portal = create_portal_app(db, vault, settings, engine, notifier) if settings.portal_enabled else None
    return app, scheduler, portal, settings


class _Server(uvicorn.Server):
    """Two servers share one process, so signals are handled once, below, for both."""

    @contextlib.contextmanager
    def capture_signals(self):
        yield


async def _serve(servers: list[uvicorn.Server]):
    loop = asyncio.get_running_loop()

    def stop():
        for s in servers:
            s.should_exit = True

    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError, RuntimeError):  # not available on Windows
            loop.add_signal_handler(sig, stop)
    await asyncio.gather(*(s.serve() for s in servers))


def main():
    app, scheduler, portal, settings = build()
    scheduler.start()
    servers = [_Server(uvicorn.Config(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8099")),
                                      log_level="info"))]
    if portal is not None:
        # Behind a tunnel/reverse proxy: trust its X-Forwarded-* headers so https is detected.
        servers.append(_Server(uvicorn.Config(portal, host="0.0.0.0", port=settings.portal_port, log_level="info",
                                              proxy_headers=True, forwarded_allow_ips="*")))
        log.info("Friends' portal on port %s", settings.portal_port)
    asyncio.run(_serve(servers))


if __name__ == "__main__":
    main()
