"""Test fixture for regular Python classes and inheritance."""


class SimpleClass:
    """A simple class with no bases."""


class Animal:
    """Base animal class with fields set in __init__."""

    name: str
    sound: str

    def __init__(self, name: str, sound: str = "...") -> None:
        self.name = name
        self.sound = sound
        self._alive = True

    def speak(self) -> str:
        """Make the animal speak."""
        return f"{self.name} says {self.sound}"

    async def feed(self, food: str) -> bool:
        """Feed the animal asynchronously."""
        return True

    @staticmethod
    def kingdom() -> str:
        """Return the kingdom."""
        return "Animalia"

    @classmethod
    def from_dict(cls, data: dict[str, str]) -> "Animal":
        """Create from dictionary."""
        return cls(data["name"], data.get("sound", "..."))


class Dog(Animal):
    """A dog is an animal."""

    breed: str

    def __init__(self, name: str, breed: str) -> None:
        super().__init__(name, "Woof")
        self.breed = breed

    def fetch(self, item: str) -> str:
        """Fetch an item."""
        return f"{self.name} fetches {item}"


class GuideDog(Dog):
    """A guide dog has multiple bases (deep inheritance)."""

    handler: str

    def __init__(self, name: str, breed: str, handler: str) -> None:
        super().__init__(name, breed)
        self.handler = handler


class _PrivateHelper:
    """Private class (leading underscore)."""

    def do_work(self) -> None:
        """Do some work."""
