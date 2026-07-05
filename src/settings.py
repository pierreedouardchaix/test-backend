import os
from dataclasses import dataclass
from typing import Self


@dataclass(frozen=True)
class Settings:
    database_url: str
    jwt_secret: str
    jwt_expiry_seconds: int
    dev_mode: bool
    partner_hmac_secret: str

    @classmethod
    def from_env(cls) -> Self:
        return cls(
            database_url=os.environ["DATABASE_URL"],
            jwt_secret=os.environ["JWT_SECRET"],
            jwt_expiry_seconds=int(os.getenv("JWT_EXPIRY_SECONDS", "3600")),
            dev_mode=os.getenv("DEV_MODE", "false").lower() == "true",
            partner_hmac_secret=os.environ["PARTNER_HMAC_SECRET"],
        )
