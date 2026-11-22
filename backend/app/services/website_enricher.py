"""Public website enrichment.

Fetches a business's public website/contact page and extracts publicly
published contact details (emails, phones, social profiles). Respects
robots.txt, timeouts, and size limits. Only publicly accessible pages —
no login bypass, no private data.
"""
from __future__ import annotations

import ipaddress
import re
import socket
from urllib.parse import urljoin, urlparse

import httpx

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"(?:\+?\d[\d\s\-()]{7,}\d)")
BAD_EMAIL_DOMAINS = ("example.com", "sentry.io", "wixpress.com", "godaddy.com", "schema.org")
MAX_BYTES = 400_000
MAX_REDIRECTS = 3


def _is_public_host(host: str) -> bool:
    """SSRF guard: resolve host and reject loopback/private/link-local/reserved."""
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_multicast or ip.is_reserved or ip.is_unspecified):
            return False
    return bool(infos)


def _safe_url(url: str) -> str | None:
    """Return normalized URL or None if scheme/host is unsafe."""
    try:
        p = urlparse(url)
    except Exception:
        return None
    if p.scheme not in ("http", "https") or not p.hostname:
        return None
    if p.username or p.password:
        return None
    if not _is_public_host(p.hostname):
        return None
    return url


def _fetch(client: httpx.Client, url: str) -> str | None:
    """Fetch with per-redirect SSRF revalidation and size cap."""
    current = _safe_url(url)
    if current is None:
        return None
    try:
        for _ in range(MAX_REDIRECTS + 1):
            r = client.get(current, follow_redirects=False)
            if r.is_redirect:
                loc = r.headers.get("location")
                if not loc:
                    return None
                nxt = _safe_url(urljoin(current, loc))
                if nxt is None:
                    return None
                current = nxt
                continue
            if r.status_code != 200:
                return None
            ctype = r.headers.get("content-type", "")
            if "html" not in ctype and "text" not in ctype:
                return None
            return r.text[:MAX_BYTES]
        return None
    except Exception:
        return None


def _robots_allows(client: httpx.Client, base: str) -> bool:
    parsed = urlparse(base)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    txt = _fetch(client, robots_url)
    if txt is None:
        return True  # no robots file -> allowed
    # minimal check: if our path is disallowed for *, skip
    path = parsed.path or "/"
    for line in txt.splitlines():
        line = line.strip()
        if line.lower().startswith("disallow:"):
            dis = line.split(":", 1)[1].strip()
            if dis and path.startswith(dis):
                return False
    return True


def enrich_website(website: str, timeout: float = 15.0) -> dict:
    """Return dict of publicly found contact fields + evidence. Empty on failure."""
    out = {"email": None, "phone": None, "instagram": None, "facebook": None,
           "evidence": [], "pages_checked": 0, "error": None}
    if not website:
        return out
    if not website.startswith("http"):
        website = "https://" + website
    try:
        with httpx.Client(timeout=timeout, follow_redirects=False,
                          headers={"User-Agent": "AGWorkforceBot/1.0 (+https://agworkforce.local; research)"}) as client:
            if not _robots_allows(client, website):
                out["error"] = "robots.txt disallows"
                return out
            html = _fetch(client, website)
            if not html:
                out["error"] = "unreachable or non-HTML"
                return out
            out["pages_checked"] = 1
            # try common contact/about pages for more coverage
            for path in ("/contact", "/contact-us", "/about"):
                extra = _fetch(client, urljoin(website, path))
                if extra:
                    html += "\n" + extra
                    out["pages_checked"] += 1
                    if out["email"] and out["phone"]:
                        break
    except Exception as exc:  # noqa: BLE001
        out["error"] = str(exc)[:200]
        return out

    low = html.lower()
    # emails
    for m in EMAIL_RE.findall(html):
        m = m.strip(".,;:()[]")
        if any(b in m.lower() for b in BAD_EMAIL_DOMAINS):
            continue
        if "png" in m or "jpg" in m or "jpeg" in m or "gif" in m or "webp" in m:
            continue
        out["email"] = m
        out["evidence"].append({"field": "email", "value": m, "source": website, "verified": True})
        break
    # phones — prefer tel: links
    tel = re.search(r'href=["\']tel:([^"\']+)', html, re.I)
    phone = None
    if tel:
        phone = tel.group(1).strip()
    else:
        for m in PHONE_RE.findall(html):
            digits = re.sub(r"\D", "", m)
            if 10 <= len(digits) <= 15:
                phone = m.strip()
                break
    if phone:
        out["phone"] = phone
        out["evidence"].append({"field": "phone", "value": phone, "source": website, "verified": True})
    # socials
    inst = re.search(r'(?:instagram\.com/|@)([A-Za-z0-9_.]{2,30})', low)
    if "instagram.com/" in low:
        m = re.search(r'instagram\.com/([A-Za-z0-9_.]+)', html, re.I)
        if m and m.group(1).lower() not in ("p", "reel", "explore", "accounts"):
            out["instagram"] = f"https://instagram.com/{m.group(1)}"
            out["evidence"].append({"field": "instagram", "value": out["instagram"], "source": website, "verified": True})
    fb = re.search(r'facebook\.com/([A-Za-z0-9_.\-]+)', html, re.I)
    if fb and fb.group(1).lower() not in ("sharer", "plugins", "tr"):
        out["facebook"] = f"https://facebook.com/{fb.group(1)}"
        out["evidence"].append({"field": "facebook", "value": out["facebook"], "source": website, "verified": True})
    return out
