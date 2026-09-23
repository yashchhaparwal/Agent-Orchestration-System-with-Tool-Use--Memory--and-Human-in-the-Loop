"""Fetch a public web page as text, with an SSRF guard.

Only http(s); the host must resolve to public addresses only (no loopback, private, link-local,
or multicast ranges — so an agent can never be steered at the metadata service, Redis, Postgres,
or another MCP server); optional hostname allow-list; response size cap; scripts/styles stripped.
"""

from __future__ import annotations

import ipaddress
import re
import socket
from html.parser import HTMLParser
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel

_SKIP_TAGS = {"script", "style", "noscript", "template", "svg", "head"}


class FetchViolationError(ValueError):
    """Raised with a reason the model can act on."""


class FetchedPage(BaseModel):
    url: str
    final_url: str
    status: int
    title: str
    text: str
    truncated: bool


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._skip = 0
        self._parts: list[str] = []
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP_TAGS:
            self._skip += 1
        if tag == "title":
            self._in_title = True
        if tag in {
            "p",
            "div",
            "br",
            "li",
            "tr",
            "h1",
            "h2",
            "h3",
            "h4",
            "h5",
            "h6",
            "section",
            "article",
        }:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS and self._skip:
            self._skip -= 1
        if tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
        elif not self._skip:
            self._parts.append(data)

    def text(self) -> str:
        raw = "".join(self._parts)
        raw = re.sub(r"[ \t\r\f\v]+", " ", raw)
        return re.sub(r"\n\s*\n+", "\n\n", raw).strip()


def _is_public(ip: str) -> bool:
    addr = ipaddress.ip_address(ip)
    return not (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_reserved
        or addr.is_unspecified
    )


def assert_public_http_url(url: str, *, allowlist: set[str] | None = None) -> str:
    parts = urlsplit(url.strip())
    if parts.scheme not in {"http", "https"}:
        raise FetchViolationError("only http and https URLs are allowed")
    host = (parts.hostname or "").lower().rstrip(".")
    if not host:
        raise FetchViolationError("URL has no host")
    if parts.username or parts.password:
        raise FetchViolationError("credentials in URLs are not allowed")
    if allowlist and host not in allowlist and not any(host.endswith("." + a) for a in allowlist):
        raise FetchViolationError(f"host '{host}' is not on the fetch allow-list")
    if host == "localhost" or host.endswith(".localhost") or host.endswith(".local"):
        raise FetchViolationError("local hosts are not allowed")
    try:
        ipaddress.ip_address(host)
        literal = True
    except ValueError:
        literal = False
    if literal:
        if not _is_public(host):
            raise FetchViolationError("private or loopback addresses are not allowed")
        return host
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as e:
        raise FetchViolationError(f"cannot resolve host '{host}'") from e
    addresses = {str(info[4][0]) for info in infos}
    if not addresses or not all(_is_public(a) for a in addresses):
        raise FetchViolationError(f"host '{host}' resolves to a non-public address")
    return host


def fetch_page(
    url: str, *, max_chars: int, max_bytes: int, allowlist: set[str] | None, timeout_s: float = 15.0
) -> FetchedPage:
    assert_public_http_url(url, allowlist=allowlist)
    headers = {
        "User-Agent": "foreman-research/0.1 (+synthetic demo)",
        "Accept": "text/html,text/plain",
    }
    with (
        httpx.Client(follow_redirects=True, timeout=timeout_s, headers=headers) as client,
        client.stream("GET", url) as resp,
    ):
        # Every redirect hop is re-checked so a public host cannot bounce us to a private one.
        for hop in resp.history + [resp]:
            assert_public_http_url(str(hop.url), allowlist=allowlist)
        body = b""
        for chunk in resp.iter_bytes():
            body += chunk
            if len(body) > max_bytes:
                break
        status = resp.status_code
        final_url = str(resp.url)
        content_type = resp.headers.get("content-type", "")
    decoded = body[:max_bytes].decode("utf-8", errors="replace")
    if "html" in content_type:
        parser = _TextExtractor()
        parser.feed(decoded)
        text, title = parser.text(), parser.title.strip()
    else:
        text, title = decoded.strip(), ""
    cap = max(1, min(int(max_chars), 200_000))
    return FetchedPage(
        url=url,
        final_url=final_url,
        status=status,
        title=title,
        text=text[:cap],
        truncated=len(text) > cap or len(body) > max_bytes,
    )
