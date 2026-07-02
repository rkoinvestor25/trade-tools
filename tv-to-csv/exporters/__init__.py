from __future__ import annotations
from abc import ABC, abstractmethod
from typing import IO
from models import Position

_REGISTRY: dict[str, type[BaseExporter]] = {}


def register(name: str):
    """Class decorator that registers an exporter under a format name."""
    def decorator(cls: type[BaseExporter]) -> type[BaseExporter]:
        _REGISTRY[name] = cls
        return cls
    return decorator


def get_exporter(name: str) -> BaseExporter:
    if name not in _REGISTRY:
        available = ', '.join(sorted(_REGISTRY))
        raise ValueError(f"Unknown format '{name}'. Available: {available}")
    return _REGISTRY[name]()


def available_formats() -> list[str]:
    return sorted(_REGISTRY)


class BaseExporter(ABC):
    @abstractmethod
    def export(self, positions: list[Position], out: IO[str]) -> None: ...
