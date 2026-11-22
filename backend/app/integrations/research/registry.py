from .base import DiscoveryProvider, GeoQuery, ProviderError, RawBusiness
from .google_places import GooglePlacesProvider
from .overpass import OverpassProvider

_REGISTRY: dict[str, type] = {
    "openstreetmap": OverpassProvider,
    "google_places": GooglePlacesProvider,
}


def available_providers() -> list[dict]:
    out = []
    for name, cls in _REGISTRY.items():
        p = cls()
        out.append({
            "name": name,
            "configured": p.configured(),
            "requires_key": name == "google_places",
        })
    return out


def get_provider(name: str) -> DiscoveryProvider:
    cls = _REGISTRY.get(name)
    if cls is None:
        raise ProviderError(f"Unknown provider '{name}'")
    return cls()


def default_providers() -> list[DiscoveryProvider]:
    """Configured providers only, in priority order."""
    return [cls() for cls in _REGISTRY.values() if cls().configured()]
