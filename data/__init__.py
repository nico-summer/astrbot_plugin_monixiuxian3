# data/__init__.py

from .data_manager import DataBase

__all__ = ["DataBase", "MigrationManager"]


def __getattr__(name):
    if name == "MigrationManager":
        from .migration import MigrationManager
        return MigrationManager
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
