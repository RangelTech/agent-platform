"""Password hashing and session-token primitives."""

import hashlib
import secrets

import bcrypt


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), password_hash.encode())
    except ValueError:
        # Malformed hash in the database — treat as a failed login, never a 500.
        return False


def new_session_token() -> str:
    """Opaque, unguessable session token handed to the client."""
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """Tokens are stored hashed. SHA-256 (not bcrypt) is right here: the input
    is already high-entropy, and lookups happen on every request."""
    return hashlib.sha256(token.encode()).hexdigest()
