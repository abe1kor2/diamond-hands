"""Public data sources. Every fetch either returns real data or raises DataError."""

from .http import DataError

__all__ = ["DataError"]
