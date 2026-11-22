"""Business discovery provider interfaces.

Providers return RawBusiness dicts — never fabricated. A provider that is not
configured must report configured() == False so the UI can show it honestly.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Iterator, Protocol

import httpx

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "AGWorkforce/1.0 (lead research; contact: owner)"


@dataclass
class RawBusiness:
    name: str
    provider: str
    source_ref: str = ""
    category: str = ""
    description: str = ""
    address: str = ""
    city: str | None = None
    state: str | None = None
    country: str | None = None
    postal_code: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    website: str | None = None
    phone: str | None = None
    email: str | None = None
    instagram: str | None = None
    facebook: str | None = None
    whatsapp: str | None = None
    raw: dict = field(default_factory=dict)
    evidence: list = field(default_factory=list)


@dataclass
class GeoQuery:
    country: str | None = None
    state: str | None = None
    district: str | None = None
    city: str | None = None
    town: str | None = None
    locality: str | None = None
    postal_code: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    radius_m: int = 5000


class RateLimiter:
    """Simple per-provider min-interval limiter."""
    def __init__(self, min_interval: float):
        self.min_interval = min_interval
        self._last = 0.0

    def wait(self):
        delta = time.time() - self._last
        if delta < self.min_interval:
            time.sleep(self.min_interval - delta)
        self._last = time.time()


class ProviderError(Exception):
    pass


class DiscoveryProvider(Protocol):
    name: str
    def configured(self) -> bool: ...
    def search(self, query: GeoQuery, business_types: list[str], keywords: list[str],
               exclusions: list[str], limit: int) -> Iterator[RawBusiness]: ...


def norm(s: str) -> str:
    return "".join(c for c in (s or "").lower() if c.isalnum() or c == " ").strip()


def http_client(timeout: float = 20.0) -> httpx.Client:
    return httpx.Client(
        timeout=timeout,
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True,
    )
