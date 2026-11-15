"""Encrypts members' gym passwords at rest. The key lives in /data/secret.key."""
from __future__ import annotations

import os

from cryptography.fernet import Fernet, InvalidToken


class Vault:
    def __init__(self, key_path: str):
        if os.path.exists(key_path):
            with open(key_path, "rb") as fh:
                key = fh.read().strip()
        else:
            key = Fernet.generate_key()
            fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as fh:
                fh.write(key)
        self._f = Fernet(key)

    def encrypt(self, plain: str) -> str:
        return self._f.encrypt(plain.encode()).decode()

    def decrypt(self, token: str) -> str:
        try:
            return self._f.decrypt(token.encode()).decode()
        except InvalidToken as exc:
            raise ValueError("Stored password can't be decrypted (secret.key changed?)") from exc
