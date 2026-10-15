"""
Centralized stealth HTTP client.

Provides TLS-fingerprinted requests via curl_cffi (Chrome impersonation),
enforces SSRF protection against loopback/private/metadata IP addresses,
and validates redirect hops to protect internal services.
"""

import ipaddress
import socket
import urllib.parse as urlparse
from typing import Optional, Any, Union

from bs4 import BeautifulSoup
from curl_cffi import requests as cffi_requests


DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

MAX_REDIRECTS = 5
_REDIRECT_CODES = (301, 302, 303, 307, 308)


class BlockedRedirectError(Exception):
    """Raised when a redirect points at a private/local address or a non-HTTP scheme."""


def _is_private_or_local_host(hostname: str) -> bool:
    """Check if a hostname resolves to a local, private, loopback, or metadata IP."""
    if not hostname:
        return True
    clean_host = hostname.strip().lower().split(':')[0]
    if clean_host in ('localhost', '127.0.0.1', '::1', '0.0.0.0'):
        return True
    if clean_host.endswith(('.local', '.internal', '.lan', '.localdomain')):
        return True
    try:
        ip = ipaddress.ip_address(clean_host)
        return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
    except ValueError:
        pass
    try:
        resolved_ip = socket.gethostbyname(clean_host)
        ip = ipaddress.ip_address(resolved_ip)
        return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
    except Exception:
        return False


def _fetch_public(
    method: str,
    url: str,
    impersonate: str = "chrome124",
    timeout: Union[float, tuple[float, float]] = 10.0,
    headers: Optional[dict] = None,
    **kwargs
) -> cffi_requests.Response:
    """curl_cffi request that validates destination and follows redirects manually,
    re-checking every hop against SSRF / private targets.
    """
    parsed = urlparse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"Unsupported scheme '{parsed.scheme}'. Only http and https are supported.")
    if _is_private_or_local_host(parsed.hostname):
        raise BlockedRedirectError(f"Access to private/local host '{parsed.hostname}' is blocked.")

    if isinstance(headers, (int, float, tuple)):
        timeout = headers
        headers = None

    req_headers = {"User-Agent": DEFAULT_UA}
    if headers:
        req_headers.update(headers)

    current = url
    for _ in range(MAX_REDIRECTS + 1):
        func = cffi_requests.head if method.upper() == "HEAD" else cffi_requests.get
        response = func(
            current,
            allow_redirects=False,
            impersonate=impersonate,
            timeout=timeout,
            headers=req_headers,
            **kwargs
        )
        if response.status_code not in _REDIRECT_CODES:
            return response
        location = response.headers.get("Location") if hasattr(response, "headers") else None
        if not location:
            return response
        next_url = urlparse.urljoin(current, location)
        parsed_next = urlparse.urlparse(next_url)
        if parsed_next.scheme not in ("http", "https") or _is_private_or_local_host(parsed_next.hostname):
            raise BlockedRedirectError(next_url)
        current = next_url
    raise BlockedRedirectError(f"Too many redirects (>{MAX_REDIRECTS})")


def fetch_url(
    url: str,
    method: str = "GET",
    headers: Optional[dict] = None,
    timeout: Union[float, tuple[float, float]] = 10.0,
    impersonate: str = "chrome124",
    **kwargs
) -> cffi_requests.Response:
    """Fetch a public URL using the stealth client with full SSRF and redirect protection."""
    return _fetch_public(
        method=method,
        url=url,
        impersonate=impersonate,
        timeout=timeout,
        headers=headers,
        **kwargs
    )


def fetch_soup(
    url: str,
    headers: Optional[dict] = None,
    timeout: Union[float, tuple[float, float]] = 10.0,
    impersonate: str = "chrome124",
    **kwargs
) -> BeautifulSoup:
    """Fetch a URL and parse its HTML body into a BeautifulSoup object."""
    resp = fetch_url(url=url, method="GET", headers=headers, timeout=timeout, impersonate=impersonate, **kwargs)
    resp.raise_for_status()
    encoding = getattr(resp, "charset_encoding", None) or getattr(resp, "apparent_encoding", None) or "utf-8"
    try:
        resp.encoding = encoding
        html = resp.text
    except Exception:
        html = resp.content.decode("utf-8", errors="replace")
    return BeautifulSoup(html, "html.parser")


def fetch_json(
    url: str,
    headers: Optional[dict] = None,
    timeout: Union[float, tuple[float, float]] = 10.0,
    impersonate: str = "chrome124",
    **kwargs
) -> Any:
    """Fetch a URL and parse its body as JSON."""
    resp = fetch_url(url=url, method="GET", headers=headers, timeout=timeout, impersonate=impersonate, **kwargs)
    resp.raise_for_status()
    return resp.json()


def fetch_text(
    url: str,
    headers: Optional[dict] = None,
    timeout: Union[float, tuple[float, float]] = 10.0,
    impersonate: str = "chrome124",
    **kwargs
) -> str:
    """Fetch a URL and return its text content."""
    resp = fetch_url(url=url, method="GET", headers=headers, timeout=timeout, impersonate=impersonate, **kwargs)
    resp.raise_for_status()
    return resp.text

