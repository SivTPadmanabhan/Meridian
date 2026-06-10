"""Shared declarative base.

All ORM models inherit from ``Base`` so that ``Base.metadata`` carries the full
schema for Alembic autogenerate.
"""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass
