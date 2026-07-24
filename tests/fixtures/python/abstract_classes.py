"""Test fixture for ABC and Protocol classes → TraitNode mapping."""

from abc import ABC, abstractmethod
from typing import Protocol, runtime_checkable


class Serializable(ABC):
    """Abstract base for serializable objects."""

    @abstractmethod
    def serialize(self) -> bytes:
        """Serialize to bytes."""
        ...

    @abstractmethod
    def deserialize(self, data: bytes) -> "Serializable":
        """Deserialize from bytes."""
        ...

    def to_json(self) -> str:
        """Default JSON serialization (non-abstract)."""
        return "{}"


class Comparable(ABC):
    """Abstract base for comparable objects with associated constants."""

    MAX_VALUE: int = 100

    @abstractmethod
    def compare(self, other: "Comparable") -> int:
        """Compare two objects."""
        ...

    @abstractmethod
    async def async_compare(self, other: "Comparable") -> int:
        """Async comparison."""
        ...


@runtime_checkable
class Drawable(Protocol):
    """Protocol for drawable objects."""

    def draw(self, canvas: str) -> None:
        """Draw on canvas."""
        ...

    def resize(self, width: int, height: int) -> "Drawable":
        """Resize the drawable."""
        ...


class Printable(Protocol):
    """Protocol with a single method."""

    def to_string(self) -> str:
        """Convert to string."""
        ...


class ConcreteSerializer(Serializable):
    """Concrete implementation of Serializable."""

    def serialize(self) -> bytes:
        return b"data"

    def deserialize(self, data: bytes) -> "Serializable":
        return self
