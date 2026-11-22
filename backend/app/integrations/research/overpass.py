"""OpenStreetMap discovery via Nominatim geocoding + Overpass API.

Real, keyless, public data. Respects rate limits (1 req/s Nominatim,
~4s between Overpass calls). Partial coverage only — results are whatever
OSM contributors mapped; we never claim exhaustiveness.
"""
from __future__ import annotations

from typing import Iterator

from .base import (
    NOMINATIM_URL,
    OVERPASS_URL,
    GeoQuery,
    ProviderError,
    RateLimiter,
    RawBusiness,
    http_client,
    norm,
)

# OSM amenity/shop tags mapped from our business-type vocabulary.
OSM_TAGS = {
    "cafe": ["amenity=cafe"],
    "coffee_shop": ["amenity=cafe", "shop=coffee"],
    "bakery_cafe": ["shop=bakery", "amenity=cafe"],
    "dessert_cafe": ["amenity=cafe"],
    "tea_cafe": ["amenity=cafe"],
    "specialty_coffee": ["shop=coffee"],
    "restaurant_cafe": ["amenity=restaurant", "amenity=cafe"],
}
DEFAULT_TYPES = ["cafe", "coffee_shop", "bakery_cafe", "dessert_cafe", "tea_cafe", "specialty_coffee", "restaurant_cafe"]

_nominatim_limiter = RateLimiter(1.1)
_overpass_limiter = RateLimiter(4.5)


def geocode(query: GeoQuery) -> tuple[float, float, int] | None:
    """Resolve a GeoQuery to (lat, lon, radius_m) via Nominatim."""
    if query.latitude is not None and query.longitude is not None:
        return query.latitude, query.longitude, query.radius_m
    parts = [p for p in (query.locality, query.town, query.city, query.district,
                         query.state, query.postal_code, query.country) if p]
    if not parts:
        return None
    q = ", ".join(parts)
    _nominatim_limiter.wait()
    try:
        with http_client(15) as client:
            r = client.get(NOMINATIM_URL, params={"q": q, "format": "json", "limit": 1})
            r.raise_for_status()
            data = r.json()
    except Exception as exc:  # noqa: BLE001
        raise ProviderError(f"Geocoding failed for '{q}': {exc}") from exc
    if not data:
        return None
    top = data[0]
    radius = query.radius_m
    # widen radius for larger areas
    if query.state and not (query.city or query.town or query.locality):
        radius = max(radius, 25000)
    return float(top["lat"]), float(top["lon"]), radius


def _overpass_tag_union(business_types: list[str]) -> str:
    tags: set[str] = set()
    for bt in business_types or DEFAULT_TYPES:
        for t in OSM_TAGS.get(bt, OSM_TAGS["cafe"]):
            k, v = t.split("=", 1)
            tags.add(f'[{k}="{v}"]')
    return "".join(sorted(tags))


class OverpassProvider:
    name = "openstreetmap"

    def configured(self) -> bool:
        return True  # public API, no key required

    def search(self, query: GeoQuery, business_types: list[str], keywords: list[str],
               exclusions: list[str], limit: int) -> Iterator[RawBusiness]:
        geo = geocode(query)
        if geo is None:
            raise ProviderError("Could not geocode the requested location")
        lat, lon, radius = geo

        tag_union = _overpass_tag_union(business_types)
        # name regex from keywords if provided
        name_filter = ""
        if keywords:
            pat = "|".join(k.replace("_", " ") for k in keywords[:8])
            name_filter = f'if(t["name"] !~ "{pat}", 0, 1);'
        excl = ""
        if exclusions:
            pat = "|".join(e.replace("_", " ") for e in exclusions[:8])
            excl = f'if(t["name"] ~ "{pat}", 0, 1);'

        ql = f"""
[out:json][timeout:40];
(
  node{tag_union}(around:{radius},{lat},{lon});
  way{tag_union}(around:{radius},{lat},{lon});
);
out center tags {limit};
"""
        _overpass_limiter.wait()
        try:
            with http_client(60) as client:
                r = client.post(OVERPASS_URL, data={"data": ql})
                r.raise_for_status()
                elements = r.json().get("elements", [])
        except Exception as exc:  # noqa: BLE001
            raise ProviderError(f"Overpass query failed: {exc}") from exc

        yielded = 0
        for el in elements:
            if yielded >= limit:
                break
            tags = el.get("tags", {}) or {}
            name = (tags.get("name") or "").strip()
            if not name:
                continue
            lname = name.lower()
            if exclusions and any(e.lower() in lname for e in exclusions):
                continue
            if keywords and not any(k.lower().replace("_", " ") in lname for k in keywords):
                continue

            lat_v = el.get("lat") or (el.get("center") or {}).get("lat")
            lon_v = el.get("lon") or (el.get("center") or {}).get("lon")
            addr = tags.get("addr:full") or ", ".join(
                x for x in [tags.get("addr:housenumber"), tags.get("addr:street")] if x
            )
            website = tags.get("website") or tags.get("contact:website") or tags.get("url")
            phone = tags.get("phone") or tags.get("contact:phone")
            email = tags.get("email") or tags.get("contact:email")
            inst = tags.get("contact:instagram") or tags.get("instagram")
            fb = tags.get("contact:facebook") or tags.get("facebook")
            if inst and not inst.startswith("http"):
                inst = f"https://instagram.com/{inst.lstrip('@')}"
            if fb and not fb.startswith("http"):
                fb = f"https://facebook.com/{fb.lstrip('@')}"

            amenity = tags.get("amenity") or tags.get("shop") or "cafe"
            biz = RawBusiness(
                name=name,
                provider=self.name,
                source_ref=f"osm:{el.get('type')}/{el.get('id')}",
                category=amenity,
                address=addr or "",
                city=tags.get("addr:city") or query.city,
                state=tags.get("addr:state") or query.state,
                country=tags.get("addr:country") or query.country or "India",
                postal_code=tags.get("addr:postcode"),
                latitude=float(lat_v) if lat_v is not None else None,
                longitude=float(lon_v) if lon_v is not None else None,
                website=website,
                phone=phone,
                email=email,
                instagram=inst,
                facebook=fb,
                raw=tags,
                evidence=[{"field": "name", "value": name, "source": "openstreetmap", "verified": True}],
            )
            yielded += 1
            yield biz
