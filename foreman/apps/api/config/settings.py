"""The API reads settings through the shared module — one env reader for the whole system."""

from packages.shared.config import Settings, get_settings

__all__ = ["Settings", "get_settings"]
