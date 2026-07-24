"""Test fixture for module-level constants and type aliases."""

from typing import TypeAlias

MAX_RETRIES: int = 3
DEFAULT_TIMEOUT: float = 30.0
APP_NAME: str = "MyApp"
_INTERNAL_FLAG: bool = True
DEBUG = False

# Type aliases
JsonDict: TypeAlias = dict[str, object]
Headers = dict[str, str]
Callback = list[tuple[str, int]]
OptionalStr = str | None
