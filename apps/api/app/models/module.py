from __future__ import annotations

from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models._mixins import TimestampMixin, UUIDPrimaryKeyMixin


class Module(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Sección/funcionalidad de la app sobre la que se asignan permisos view/edit.

    Las features futuras registran su módulo aquí (vía migración) y la matriz
    de permisos por rol se construye a partir de esta tabla.
    """

    __tablename__ = "modules"

    code: Mapped[str] = mapped_column(unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(nullable=False)
    description: Mapped[str | None] = mapped_column(nullable=True)
    order: Mapped[int] = mapped_column(default=0, nullable=False)
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)

    def __repr__(self) -> str:
        return f"<Module code={self.code!r}>"
