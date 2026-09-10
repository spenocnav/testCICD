from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Column, ForeignKey, Index, Table
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models._mixins import TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.permission import Permission


role_permissions = Table(
    "role_permissions",
    Base.metadata,
    Column(
        "role_id",
        UUID(as_uuid=True),
        ForeignKey("roles.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "permission_id",
        UUID(as_uuid=True),
        ForeignKey("permissions.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Index(
        "ix_role_permissions_permission_role",
        "permission_id",
        "role_id",
    ),
)


class Role(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Rol asignable a usuarios. Agrupa permisos."""

    __tablename__ = "roles"

    code: Mapped[str] = mapped_column(unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(nullable=False)
    description: Mapped[str | None] = mapped_column(nullable=True)
    is_system: Mapped[bool] = mapped_column(default=False, nullable=False)

    permissions: Mapped[list[Permission]] = relationship(
        "Permission",
        secondary=role_permissions,
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return f"<Role code={self.code!r}>"
