"""
Centralized stealth HTTP client and network safety utilities.

Provides TLS-fingerprinted requests via curl_cffi (Chrome impersonation),
thread-local HTTP session pooling, SSRF protection across IPv4 and IPv6
against loopback/private/metadata IP addresses, redirect hop validation,
and shared Google News redirect decoding.
"""

from html import unescape
import ipaddress
import json
import re
import socket
import threading
import time
from typing import Any, Dict, Optional, Union
import urllib.parse as urlparse

from bs4 import BeautifulSoup
from curl_cffi import requests as cffi_requests
import requests

from src.utils.cache import BoundedCache


DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

MAX_REDIRECTS = 5
_REDIRECT_CODES = (301, 302, 303, 307, 308)
_HOST_SAFETY_TTL = 300.0
_HOST_SAFETY_CACHE: BoundedCache = BoundedCache(maxsize=512)
_DECODED_NEWS_URL_CACHE: BoundedCache = BoundedCache(maxsize=512)
_THREAD_LOCAL = threading.local()

_ORIG_CFFI_GET = cffi_requests.get
_ORIG_CFFI_HEAD = cffi_requests.head


class ThreadLocalRequestsSession:
    """
    Thread-safe wrapper around `requests.Session` that maintains an isolated
    underlying `requests.Session` per worker thread via `threading.local()`,
    while exposing `.get()`, `.head()`, and `.post()` on the instance so unit
    tests can patch `_HTTP_SESSION.get` or `_HTTP_SESSION.head` directly.
    """

    def __init__(
        self,
        default_headers: Optional[Dict[str, str]] = None,
        *,
        headers: Optional[Dict[str, str]] = None,
        pool_connections: int = 10,
        pool_maxsize: int = 20,
    ) -> None:
        merged_headers = headers if headers is not None else default_headers
        self._default_headers = dict(merged_headers or {"User-Agent": DEFAULT_UA})
        self._pool_connections = pool_connections
        self._pool_maxsize = pool_maxsize
        self._local = threading.local()

    def _get_session(self) -> requests.Session:
        sess = getattr(self._local, "session", None)
        if sess is None:
            from requests.adapters import HTTPAdapter

            sess = requests.Session()
            adapter = HTTPAdapter(
                pool_connections=self._pool_connections,
                pool_maxsize=self._pool_maxsize,
            )
            sess.mount("https://", adapter)
            sess.mount("http://", adapter)
            sess.headers.update(self._default_headers)
            self._local.session = sess
        return sess

    @property
    def headers(self) -> Dict[str, str]:
        return self._default_headers

    def get(self, url: str, **kwargs: Any) -> requests.Response:
        return self._get_session().get(url, **kwargs)

    def head(self, url: str, **kwargs: Any) -> requests.Response:
        return self._get_session().head(url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> requests.Response:
        return self._get_session().post(url, **kwargs)


def _get_thread_session(impersonate: str) -> cffi_requests.Session:
    """Return a thread-local curl_cffi Session for persistent HTTP/2 & TLS keep-alive pooling."""
    sessions = getattr(_THREAD_LOCAL, "sessions", None)
    if sessions is None:
        sessions = {}
        _THREAD_LOCAL.sessions = sessions
    sess = sessions.get(impersonate)
    if sess is None:
        sess = cffi_requests.Session(impersonate=impersonate)
        sessions[impersonate] = sess
    return sess


def clear_http_caches() -> None:
    """Clear the SSRF host safety cache and Google News URL decode cache."""
    _HOST_SAFETY_CACHE.clear()
    _DECODED_NEWS_URL_CACHE.clear()


class BlockedRedirectError(Exception):
    """Raised when a redirect points at a private/local address or a non-HTTP scheme."""


def _is_ip_restricted(ip_str: str) -> bool:
    """Return True if an IPv4 or IPv6 address is private, loopback, link-local, multicast, or reserved."""
    ip = ipaddress.ip_address(ip_str)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def _is_private_or_local_host(hostname: str) -> bool:
    """Check if a hostname resolves to a local, private, loopback, or metadata IP (TTL-cached per host)."""
    if not hostname:
        return True
    clean_host = hostname.strip().lower().strip("[]")
    if ":" in clean_host:
        try:
            ipaddress.ip_address(clean_host)
        except ValueError:
            clean_host = clean_host.split(":", 1)[0]

    now = time.monotonic()
    cached = _HOST_SAFETY_CACHE.get(clean_host)
    if cached is not None:
        if isinstance(cached, tuple) and len(cached) == 2:
            is_blocked, expires_at = cached
            if now < expires_at:
                return is_blocked
        elif isinstance(cached, bool):
            return cached

    if clean_host in ("localhost", "127.0.0.1", "::1", "0.0.0.0"):
        _HOST_SAFETY_CACHE[clean_host] = (True, now + _HOST_SAFETY_TTL)
        return True
    if clean_host.endswith((".local", ".internal", ".lan", ".localdomain")):
        _HOST_SAFETY_CACHE[clean_host] = (True, now + _HOST_SAFETY_TTL)
        return True

    try:
        res = _is_ip_restricted(clean_host)
        _HOST_SAFETY_CACHE[clean_host] = (res, now + _HOST_SAFETY_TTL)
        return res
    except ValueError:
        pass

    try:
        addr_infos = socket.getaddrinfo(clean_host, None)
        resolved_ips = {info[4][0].split("%", 1)[0] for info in addr_infos if info and len(info) >= 5 and info[4]}
        if resolved_ips:
            res = any(_is_ip_restricted(ip_str) for ip_str in resolved_ips)
            _HOST_SAFETY_CACHE[clean_host] = (res, now + _HOST_SAFETY_TTL)
            return res
    except Exception:
        pass

    try:
        resolved_ip = socket.gethostbyname(clean_host)
        res = _is_ip_restricted(resolved_ip)
        _HOST_SAFETY_CACHE[clean_host] = (res, now + _HOST_SAFETY_TTL)
        return res
    except Exception:
        return False


def _fetch_public(
    method: str,
    url: str,
    impersonate: str = "chrome124",
    timeout: Union[float, tuple[float, float]] = 10.0,
    headers: Optional[dict] = None,
    **kwargs: Any,
) -> cffi_requests.Response:
    """curl_cffi request that validates destination and follows redirects manually,
    re-checking every hop against SSRF / private targets while reusing a thread-local Session.
    """
    parsed = urlparse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"Unsupported scheme '{parsed.scheme}'. Only http and https are supported.")
    if _is_private_or_local_host(parsed.hostname or ""):
        raise BlockedRedirectError(f"Access to private/local host '{parsed.hostname}' is blocked.")

    if isinstance(headers, (int, float, tuple)):
        timeout = headers
        headers = None

    req_headers = {"User-Agent": DEFAULT_UA}
    if headers:
        req_headers.update(headers)

    method_upper = method.upper()
    current = url
    for _ in range(MAX_REDIRECTS + 1):
        if method_upper == "HEAD":
            if cffi_requests.head is _ORIG_CFFI_HEAD:
                session = _get_thread_session(impersonate)
                response = session.head(
                    current,
                    allow_redirects=False,
                    timeout=timeout,
                    headers=req_headers,
                    **kwargs,
                )
            else:
                response = cffi_requests.head(
                    current,
                    allow_redirects=False,
                    impersonate=impersonate,
                    timeout=timeout,
                    headers=req_headers,
                    **kwargs,
                )
        else:
            if cffi_requests.get is _ORIG_CFFI_GET:
                session = _get_thread_session(impersonate)
                response = session.get(
                    current,
                    allow_redirects=False,
                    timeout=timeout,
                    headers=req_headers,
                    **kwargs,
                )
            else:
                response = cffi_requests.get(
                    current,
                    allow_redirects=False,
                    impersonate=impersonate,
                    timeout=timeout,
                    headers=req_headers,
                    **kwargs,
                )

        if response.status_code not in _REDIRECT_CODES:
            return response
        location = response.headers.get("Location") if hasattr(response, "headers") else None
        if not location:
            return response
        next_url = urlparse.urljoin(current, location)
        parsed_next = urlparse.urlparse(next_url)
        if parsed_next.scheme not in ("http", "https") or _is_private_or_local_host(parsed_next.hostname or ""):
            raise BlockedRedirectError(next_url)
        current = next_url
    raise BlockedRedirectError(f"Too many redirects (>{MAX_REDIRECTS})")


def fetch_url(
    url: str,
    method: str = "GET",
    headers: Optional[dict] = None,
    timeout: Union[float, tuple[float, float]] = 10.0,
    impersonate: str = "chrome124",
    **kwargs: Any,
) -> cffi_requests.Response:
    """Fetch a public URL using the stealth client with full SSRF and redirect protection."""
    return _fetch_public(
        method=method,
        url=url,
        impersonate=impersonate,
        timeout=timeout,
        headers=headers,
        **kwargs,
    )


def fetch_soup(
    url: str,
    headers: Optional[dict] = None,
    timeout: Union[float, tuple[float, float]] = 10.0,
    impersonate: str = "chrome124",
    **kwargs: Any,
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
    **kwargs: Any,
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
    **kwargs: Any,
) -> str:
    """Fetch a URL and return its text content."""
    resp = fetch_url(url=url, method="GET", headers=headers, timeout=timeout, impersonate=impersonate, **kwargs)
    resp.raise_for_status()
    return resp.text


_NEWS_DECODE_SESSION = ThreadLocalRequestsSession({"User-Agent": DEFAULT_UA})


def decode_google_news_url(source_url: str) -> Optional[str]:
    """
    Resolve a Google News redirect URL (news.google.com/rss/articles/CBMi...)
    to the underlying publisher article URL via Google's batchexecute endpoint.
    Returns None on any failure so callers can fall back to the original URL.
    """
    cached = _DECODED_NEWS_URL_CACHE.get(source_url)
    if cached is not None:
        return cached

    try:
        parsed = urlparse.urlparse(source_url)
        path = parsed.path.strip("/").split("/")
        if parsed.hostname != "news.google.com" or len(path) < 2 or path[-2] not in ("articles", "read"):
            return None
        base64_str = path[-1]

        for prefix in ("https://news.google.com/articles/", "https://news.google.com/rss/articles/"):
            resp = _NEWS_DECODE_SESSION.get(
                f"{prefix}{base64_str}",
                timeout=5,
                cookies={"CONSENT": "YES+cb.20240710-07-p0.en+FX+410"},
            )
            text = unescape(resp.text)
            m = re.search(r'data-n-a-sg="([^"]+)"', text)
            ts = re.search(r'data-n-a-ts="([^"]+)"', text)
            if m and ts:
                signature, timestamp = m.group(1), ts.group(1)
                break
        else:
            return None

        req_data = [
            "Fbv4je",
            f'["garturlreq",[["X","X",["X","X"],null,null,1,1,"US:en",null,1,null,null,null,null,null,0,1],"X","X",1,[1,1,1],1,1,null,0,0,null,0],"{base64_str}",{timestamp},"{signature}"]',
        ]
        payload = f"f.req={urlparse.quote(json.dumps([[req_data]]))}"
        resp = _NEWS_DECODE_SESSION.post(
            "https://news.google.com/_/DotsSplashUi/data/batchexecute",
            headers={
                "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
                "User-Agent": DEFAULT_UA,
                "Referer": "https://news.google.com/",
            },
            data=payload,
            timeout=5,
        )
        splitted = resp.text.split("\n\n")
        if len(splitted) < 2:
            return None
        parsed_data = json.loads(splitted[1])[:-2]
        decoded_url = json.loads(parsed_data[0][2])[1]
        if decoded_url and decoded_url.startswith("http"):
            _DECODED_NEWS_URL_CACHE[source_url] = decoded_url
            return decoded_url
        return None
    except Exception:
        return None
