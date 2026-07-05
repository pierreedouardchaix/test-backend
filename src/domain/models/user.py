import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Self


@dataclass
class User:
    id: uuid.UUID
    first_name: str
    last_name: str
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        if not all([self.first_name.strip(), self.last_name.strip()]):
            raise ValueError("User name must not be empty")

    @classmethod
    def create(cls, first_name: str, last_name: str) -> Self:
        return cls(id=uuid.uuid4(), first_name=first_name, last_name=last_name)
