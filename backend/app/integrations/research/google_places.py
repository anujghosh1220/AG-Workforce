"""Google Places API (New) Text Search provider.

Only active when GOOGLE_PLACES_API_KEY is configured. Without a key the
provider reports configured() == False and is skipped — never faked.
"""
from __future__ import annotations

import os
from typing import Iterator

from .base import GeoQuery, ProviderError, RateLimiter, RawBusiness, http_client

PLACES_URL = "https://places.googleapis.com/v1/places:searchText"
FIELD_MASK = ",".join([
    "places.id", "places.displayName", "places.formattedAddress",
    "places.location", "places.websiteUri", "places.nationalPhoneNumber",
    "places.internationalPhoneNumber", "places.regularOpeningHours",
    "places.rating", "places.userRatingCount", "places.types",
    "places.primaryTypeDisplayName", "nextPageToken",
])

_limiter = RateLimiter(0.5)


def _key() -> str | None:
    return os.environ.get("GOOGLE_PLACES_API_KEY") or None


class GooglePlacesProvider:
    name = "google_places"

    def configured(self) -> bool:
        return bool(_key())

    def search(self, query: GeoQuery, business_types: list[str], keywords: list[str],
               exclusions: list[str], limit: int) -> Iterator[RawBusiness]:
        key = _key()
        if not key:
            raise ProviderError("Google Places API key not configured")

        loc_parts = [p for p in (query.locality, query.town, query.city, query.district,
                                 query.state, query.postal_code, query.country) if p]
        area = ", ".join(loc_parts) if loc_parts else None
        if area is None and query.latitude is not None:
            area = f"{query.latitude},{query.longitude}"
        if area is None:
            raise ProviderError("No location supplied for Places search")

        type_text = ", ".join(business_types) if business_types else "cafe"
        text_query = f"{type_text} in {area}"
        if keywords:
            text_query += " " + " ".join(keywords[:5])

        yielded = 0
        page_token = None
        while yielded < limit:
            _limiter.wait()
            body: dict = {"textQuery": text_query, "pageSize": min(20, limit - yielded),
                          "languageCode": "en"}
            if page_token:
                body["pageToken"] = page_token
            try:
                with http_client(30) as client:
                    r = client.post(
                        PLACES_URL,
                        headers={"X-Goog-Api-Key": key, "X-Goog-FieldMask": FIELD_MASK},
                        json=body,
                    )
                    if r.status_code == 429:
                        raise ProviderError("Google Places rate limit hit")
                    r.raise_for_status()
                    data = r.json()
            except ProviderError:
                raise
            except Exception as exc:  # noqa: BLE001
                raise ProviderError(f"Google Places request failed: {exc}") from exc

            for place in data.get("places", []):
                if yielded >= limit:
                    break
                name = (place.get("displayName") or {}).get("text", "").strip()
                if not name:
                    continue
                lname = name.lower()
                if exclusions and any(e.lower() in lname for e in exclusions):
                    continue
                loc = place.get("location") or {}
                biz = RawBusiness(
                    name=name,
                    provider=self.name,
                    source_ref=f"places:{place.get('id')}",
                    category=place.get("primaryTypeDisplayName", {}).get("text", "") or ",".join(place.get("types", [])[:2]),
                    address=place.get("formattedAddress", ""),
                    website=place.get("websiteUri"),
                    phone=place.get("nationalPhoneNumber") or place.get("internationalPhoneNumber"),
                    latitude=loc.get("latitude"),
                    longitude=loc.get("longitude"),
                    raw=place,
                    evidence=[{"field": "name", "value": name, "source": "google_places", "verified": True}],
                )
                yielded += 1
                yield biz
            page_token = data.get("nextPageToken")
            if not page_token:
                break
