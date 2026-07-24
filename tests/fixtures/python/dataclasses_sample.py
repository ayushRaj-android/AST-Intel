"""Test fixture for @dataclass classes → StructNode with fields."""

from dataclasses import dataclass, field
from typing import ClassVar


@dataclass
class Point:
    """A 2D point."""

    x: float
    y: float


@dataclass(frozen=True, slots=True)
class Config:
    """An immutable configuration."""

    name: str
    version: str = "1.0.0"
    debug: bool = False
    features: list[str] = field(default_factory=list)
    tags: tuple[str, ...] = ()


@dataclass
class ServerSettings:
    """Server configuration with class variables and nested types."""

    DEFAULT_PORT: ClassVar[int] = 8080

    host: str = "localhost"
    port: int = 8080
    max_connections: int = 100
    tls_enabled: bool = False

    def bind_address(self) -> str:
        """Return bind address string."""
        return f"{self.host}:{self.port}"


@dataclass(frozen=True)
class _InternalEntry:
    """Private dataclass."""

    key: str
    value: str
