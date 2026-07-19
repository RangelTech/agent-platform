"""Application-layer encryption for secrets at rest (Fernet/AES-128-CBC+HMAC).

The master key comes from ENCRYPTION_KEY (Secret Manager in production).
Without one, a key is derived from a fixed dev seed — fine for local work,
never for production, hence the loud warning.
"""

import base64
import hashlib
import logging

from cryptography.fernet import Fernet, InvalidToken

from app.config import settings

logger = logging.getLogger(__name__)

_fernet: Fernet | None = None


def _get_fernet() -> Fernet:
    global _fernet
    if _fernet is None:
        if settings.encryption_key:
            key = settings.encryption_key.encode()
        else:
            logger.warning(
                "ENCRYPTION_KEY not set — using the dev-only derived key. "
                "Do not run production like this."
            )
            key = base64.urlsafe_b64encode(
                hashlib.sha256(b"agent-platform-dev-only").digest()
            )
        _fernet = Fernet(key)
    return _fernet


def encrypt(plaintext: str) -> str:
    return _get_fernet().encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str) -> str:
    try:
        return _get_fernet().decrypt(ciphertext.encode()).decode()
    except InvalidToken as exc:
        # Wrong master key or corrupted value — surface loudly, never silently.
        raise ValueError("failed to decrypt stored secret (key mismatch?)") from exc
