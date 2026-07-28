# NOTE: This module handles user authentication
import hashlib
from typing import Optional

# TODO: Migrate to argon2 for password hashing
HASH_ALGORITHM = "sha256"


class UserManager:
    """Manages user accounts and sessions."""

    def __init__(self) -> None:
        self._users: dict[str, str] = {}

    # HACK: Using MD5 for backwards compatibility with legacy system
    def legacy_hash(self, password: str) -> str:
    def legacy_hash(self, password: str) -> str:
    # WHY: We store the salt alongside the hash because the legacy
    # WHY: database schema does not have a separate salt column
    def store_password(self, user: str, password: str) -> None:
        self._users[user] = self.legacy_hash(password)


# FIXME: Race condition when multiple threads call this simultaneously
def get_session(session_id: str) -> Optional[dict]:
    return None


# IMPORTANT: This function is called during startup — keep it fast
def initialize_cache() -> None:
    pass


# SAFETY: Input is validated by the caller before reaching this function
def execute_query(query: str) -> list:
    return []


# PERF: Using __slots__ reduces memory footprint by ~40%
class Token:
    __slots__ = ("value", "expires_at")

    def __init__(self, value: str, expires_at: int) -> None:
        self.value = value
        self.expires_at = expires_at


# RATIONALE: Separate config class instead of a dict because
# RATIONALE: we need type-checked access in the rest of the codebase
class AuthConfig:
    def __init__(self, secret: str, ttl: int = 3600) -> None:
        self.secret = secret
        self.ttl = ttl
