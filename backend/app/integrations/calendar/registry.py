from .base import CalendarProvider, CalendarProviderError, CalendarNotConfigured
from .google_calendar import GoogleCalendarProvider

_REGISTRY: dict[str, type] = {
    "google_calendar": GoogleCalendarProvider,
}


def available_providers() -> list[dict]:
    out = []
    for name, cls in _REGISTRY.items():
        p = cls()
        out.append({
            "name": name,
            "configured": p.configured(),
            "requires": [
                "GOOGLE_CALENDAR_CLIENT_ID",
                "GOOGLE_CALENDAR_CLIENT_SECRET",
                "GOOGLE_CALENDAR_REFRESH_TOKEN",
            ] if name == "google_calendar" else [],
        })
    return out


def get_calendar_provider(name: str | None = None):
    """Return a configured provider instance, or None when unavailable."""
    provider_name = name or "google_calendar"
    if provider_name in (None, "", "internal", "none"):
        return None
    cls = _REGISTRY.get(provider_name)
    if cls is None:
        return None
    p = cls()
    return p if p.configured() else None
