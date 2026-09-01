"""Provider registry. Add one import and one entry for each new website."""

from __future__ import annotations

from .base import ScraperProvider
from .florida_off_market import FloridaOffMarketProvider
from .rezzie import RezzieProvider

_PROVIDERS: dict[str, type[ScraperProvider]] = {
    FloridaOffMarketProvider.name: FloridaOffMarketProvider,
    RezzieProvider.name: RezzieProvider,
}


def get_provider(name: str) -> ScraperProvider:
    """Create a provider by its stable registry name."""
    key = name.strip().lower()
    try:
        return _PROVIDERS[key]()
    except KeyError as exc:
        available = ", ".join(sorted(_PROVIDERS))
        raise ValueError(f"Unknown provider {name!r}; available providers: {available}") from exc


def list_providers() -> tuple[str, ...]:
    return tuple(sorted(_PROVIDERS))
