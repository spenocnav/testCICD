from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Base declarativa común para todos los modelos ORM."""


def _register_models() -> None:
    """Importa modelos para que estén registrados en Base.metadata.

    Lazy import evita ciclos: los modelos heredan de Base.
    """
    from app import models  # noqa: F401


_register_models()
