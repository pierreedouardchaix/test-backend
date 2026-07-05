from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Declarative base whose `metadata` is the single source of truth for the
    schema — Alembic autogenerates migrations from it."""
