"""HTTP fetcher for untrusted URLs (SSRF-safe, size-capped, redirect-validating).

The agent decides which URLs to read, and the pages it reads may try to steer it.  So:

* https only, no credentials in the URL, port 443 only;
* every hostname must resolve exclusively to public addresses -- loopback, RFC1918, link-local
  (incl. the 169.254.169.254 / 169.254.170.2 credential endpoints), CGNAT and ULA are refused;
* redirects are followed by hand and re-validated at every hop;
* the peer address of the established connection is checked as well (DNS-rebinding guard);
* the body is streamed and cut at ``max_bytes``; only textual content types are accepted.
"""

from __future__ import annotations

import ipaddress
import socket
import time
from dataclasses import dataclass, field
from typing import Callable
from urllib.parse import urljoin, urlparse

import httpx

from ..clock import iso_now
from .tiers import stable_path

DEFAULT_UA = "Mozilla/5.0 (compatible; llmops-research-agent/0.1; +https://docs.aws.amazon.com/bedrock-agentcore/)"
ACCEPT = "text/html,application/xhtml+xml,application/json,application/atom+xml,application/xml;q=0.9,text/plain;q=0.8,*/*;q=0.1"
_TEXTUAL = ("text/", "application/json", "application/xml", "application/atom+xml", "application/rss+xml",
            "application/xhtml+xml", "application/ld+json")
_BLOCKED_SUFFIXES = (".local", ".localdomain", ".internal", ".intranet", ".lan", ".home", ".corp", ".localhost")


class FetchError(Exception):
    """A fetch was refused or failed.  The message is safe to show to the model."""


@dataclass
class FetchResult:
    url: str
    final_url: str
    status: int
    content_type: str
    body: bytes
    truncated: bool
    fetched_at: str
    redirects: list[str] = field(default_factory=list)


def is_public_ip(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        addr = addr.ipv4_mapped
    return bool(addr.is_global) and not addr.is_multicast


def default_resolver(host: str) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except socket.gaierror as e:
        raise FetchError(f"DNS lookup failed for {host}: {e}") from e
    return sorted({i[4][0] for i in infos})


def validate_url(url: str, resolver: Callable[[str], list[str]] | None = default_resolver) -> tuple[str, str]:
    """Return (normalised url, host) or raise FetchError.

    ``resolver=None`` skips the DNS check (used behind an egress proxy that resolves names itself);
    the scheme / port / host-name / literal-IP rules still apply.
    """
    try:
        p = urlparse(url.strip())
        port = p.port
    except ValueError as e:
        raise FetchError(f"invalid URL: {e}") from e
    if p.scheme != "https":
        raise FetchError("only https:// URLs are allowed")
    if any(ord(ch) < 0x21 or ord(ch) == 0x7F for ch in url.strip()):
        raise FetchError("the URL contains a blank or a control character")
    if not stable_path(url):
        # the page's trust tier is read from its address: the address must be the one the server answers for
        raise FetchError("the URL's path contains '.' or '..' segments, a backslash or an encoded slash; give the address of the page itself")
    if p.username or p.password:
        raise FetchError("credentials in URLs are not allowed")
    host = (p.hostname or "").lower().rstrip(".")
    if not host:
        raise FetchError("URL has no host")
    if port not in (None, 443):
        raise FetchError("only port 443 is allowed")
    if host == "localhost" or host.endswith(_BLOCKED_SUFFIXES) or "." not in host and ":" not in host:
        raise FetchError(f"host {host!r} is not a public internet host")
    try:
        ipaddress.ip_address(host)
        ips = [host]
    except ValueError:
        if resolver is None:
            return p._replace(fragment="").geturl(), host
        ips = resolver(host)
    if not ips:
        raise FetchError(f"{host} did not resolve")
    bad = [ip for ip in ips if not is_public_ip(ip)]
    if bad:
        raise FetchError(f"{host} resolves to a non-public address ({bad[0]}); refused")
    return p._replace(fragment="").geturl(), host


class SafeFetcher:
    def __init__(
        self,
        *,
        client_factory: Callable[[], httpx.Client] | None = None,
        resolver: Callable[[str], list[str]] = default_resolver,
        timeout: float = 20.0,
        max_bytes: int = 3_000_000,
        max_redirects: int = 5,
        retries: int = 2,
        user_agent: str = DEFAULT_UA,
        via_proxy: bool = False,
    ):
        """``via_proxy``: an egress proxy does the DNS lookup and the connecting, so local DNS / peer-address checks cannot apply."""
        self._client_factory = client_factory or (lambda: httpx.Client(timeout=timeout, follow_redirects=False))
        self._resolver = None if via_proxy else resolver
        self._check_peer = not via_proxy
        self.max_bytes = max_bytes
        self.max_redirects = max_redirects
        self.retries = retries
        self.headers = {"User-Agent": user_agent, "Accept": ACCEPT, "Accept-Language": "en-US,en;q=0.8"}

    def fetch(self, url: str) -> FetchResult:
        redirects: list[str] = []
        current, _ = validate_url(url, self._resolver)
        with self._client_factory() as client:
            for _hop in range(self.max_redirects + 1):
                resp = self._get_with_retry(client, current)
                if resp.status in (301, 302, 303, 307, 308):
                    loc = resp.location
                    if not loc:
                        raise FetchError(f"redirect without Location from {current}")
                    nxt, _ = validate_url(urljoin(current, loc), self._resolver)
                    redirects.append(nxt)
                    current = nxt
                    continue
                resp.url = url
                resp.final_url = current
                resp.redirects = redirects
                return resp
        raise FetchError(f"too many redirects (> {self.max_redirects})")

    # ------------------------------------------------------------------
    def _get_with_retry(self, client: httpx.Client, url: str) -> FetchResult:
        last: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                res = self._get_once(client, url)
            except FetchError:
                raise
            except (httpx.TimeoutException, httpx.TransportError) as e:
                last = e
            else:
                if res.status in (429, 500, 502, 503, 504) and attempt < self.retries:
                    last = FetchError(f"HTTP {res.status}")
                else:
                    return res
            time.sleep(0.4 * (attempt + 1) ** 2)
        if isinstance(last, FetchError):
            raise last
        raise FetchError(f"network error fetching {url}: {type(last).__name__}: {last}")

    def _get_once(self, client: httpx.Client, url: str) -> "_Raw":
        with client.stream("GET", url, headers=self.headers) as r:
            stream = r.extensions.get("network_stream") if self._check_peer else None
            if stream is not None:
                try:
                    peer = stream.get_extra_info("server_addr")
                except Exception:  # pragma: no cover - transport specific
                    peer = None
                if peer and not is_public_ip(str(peer[0])):
                    raise FetchError(f"connected to a non-public address ({peer[0]}); refused")
            status = r.status_code
            ctype = r.headers.get("content-type", "")
            location = r.headers.get("location")
            if status in (301, 302, 303, 307, 308):
                return _Raw(status, ctype, b"", False, location)
            if status >= 400:
                return _Raw(status, ctype, b"", False, None)
            base = ctype.split(";")[0].strip().lower()
            if base and not base.startswith(_TEXTUAL) and not base.endswith(("+json", "+xml")):
                raise FetchError(f"unsupported content-type {base!r} (text, HTML, JSON and feeds only)")
            chunks, size, truncated = [], 0, False
            for chunk in r.iter_bytes():
                size += len(chunk)
                if size > self.max_bytes:
                    chunks.append(chunk[: len(chunk) - (size - self.max_bytes)])
                    truncated = True
                    break
                chunks.append(chunk)
            return _Raw(status, ctype, b"".join(chunks), truncated, None)


class _Raw(FetchResult):
    """FetchResult under construction (url fields are filled in by SafeFetcher.fetch)."""

    def __init__(self, status: int, content_type: str, body: bytes, truncated: bool, location: str | None):
        super().__init__("", "", status, content_type, body, truncated, iso_now(), [])
        self.location = location
