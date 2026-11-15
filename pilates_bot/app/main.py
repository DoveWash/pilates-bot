"""Entry point: python -m app.main"""
from __future__ import annotations

import logging
import os

import uvicorn

from .config import DATA_DIR, load_settings
from .crypto import Vault
from .db import DB
from .engine import Engine
from .notify import Notifier
from .scheduler import Scheduler
from .web import create_app


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
    return app, scheduler


def main():
    app, scheduler = build()
    scheduler.start()
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8099")), log_level="info")


if __name__ == "__main__":
    main()
