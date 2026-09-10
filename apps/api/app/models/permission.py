from __future__ import annotations

from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models._mixins import TimestampMixin, UUIDPrimaryKeyMixin


class Permission(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Permiso atómico, identificado por su `code` (ej. `users.read`)."""

    __tablename__ = "permissions"

    code: Mapped[str] = mapped_column(unique=True, nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(nullable=True)
    resource: Mapped[str | None] = mapped_column(nullable=True)
    action: Mapped[str | None] = mapped_column(nullable=True)

    def __repr__(self) -> str:
        return f"<Permission code={self.code!r}>"
