import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str
    jwt_secret: str
    jwt_expiry_seconds: int
    dev_mode: bool

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            database_url=os.environ["DATABASE_URL"],
            jwt_secret=os.environ["JWT_SECRET"],
            jwt_expiry_seconds=int(os.getenv("JWT_EXPIRY_SECONDS", "3600")),
            dev_mode=os.getenv("DEV_MODE", "false").lower() == "true",
        )
