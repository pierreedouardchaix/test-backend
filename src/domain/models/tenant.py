from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Self

from src.domain.errors import DomainValidationError
from src.domain.models.user import User


@dataclass
class Tenant:
    id: uuid.UUID
    name: str
    user: list[User]
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise DomainValidationError("Tenant name must not be empty")

    @classmethod
    def create(cls, name: str) -> Self:
        return cls(id=uuid.uuid4(), name=name, user=[])
