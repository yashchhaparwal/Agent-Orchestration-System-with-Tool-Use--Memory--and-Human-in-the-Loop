from __future__ import annotations

import pytest

from packages.shared.config import Settings
from packages.tools.mcp_servers.web_search.backends import FixtureBackend, make_backend
from packages.tools.mcp_servers.web_search.fetch import (
    FetchViolationError,
    _TextExtractor,
    assert_public_http_url,
)
from packages.tools.mcp_servers.web_search.server import build_server
from tests.unit.test_mcp_servers_inprocess import ToolFailedError, _call


def test_fixture_backend_matches_keywords_and_falls_back() -> None:
    b = FixtureBackend()
    hits = b.search("what affordability checks must a lender do", max_results=5)
    assert hits and all(h.url.startswith("https://example.test/") for h in hits)
    assert any("affordability" in h.title.lower() for h in hits)
    fallback = b.search("something unrelated entirely", max_results=3)
    assert len(fallback) == 1 and "index" in fallback[0].url
    assert len(b.search("affordability ombudsman", max_results=1)) == 1
    assert make_backend("").name == "fixture"


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://example.com/x",
        "http://localhost/admin",
        "http://foo.localhost/",
        "http://127.0.0.1:6379/",
        "http://10.0.0.5/",
        "http://192.168.1.1/",
        "http://172.16.0.1/",
        "http://169.254.169.254/latest/meta-data",
        "http://[::1]/",
        "http://0.0.0.0/",
        "http://user:pass@example.com/",
        "http:///nohost",
    ],
)
def test_fetch_guard_rejects_non_public_targets(url: str) -> None:
    with pytest.raises(FetchViolationError):
        assert_public_http_url(url)


def test_fetch_guard_allowlist() -> None:
    with pytest.raises(FetchViolationError, match="allow-list"):
        assert_public_http_url("http://93.184.216.34/", allowlist={"example.test"})
    assert (
        assert_public_http_url("http://93.184.216.34/") == "93.184.216.34"
    )  # public literal, no list


def test_html_text_extraction_drops_scripts_and_keeps_title() -> None:
    p = _TextExtractor()
    p.feed(
        "<html><head><title>Guide</title><style>p{}</style></head><body>"
        "<script>alert(1)</script><h1>Checks</h1><p>Verify income.</p><p>Assess   spend.</p>"
        "</body></html>"
    )
    assert p.title == "Guide"
    text = p.text()
    assert "alert" not in text and "p{}" not in text
    assert "Checks" in text and "Verify income." in text and "Assess spend." in text


async def test_server_search_and_fetch_rejection(settings: Settings) -> None:
    server = build_server(settings, backend=FixtureBackend())
    out = await _call(server, "search", query="complaint letter structure", max_results=2)
    assert out["backend"] == "fixture" and len(out["results"]) == 1
    assert out["results"][0]["url"].endswith("/complaint-letter")
    with pytest.raises(ToolFailedError, match="rejected"):
        await _call(server, "fetch", url="http://127.0.0.1:8000/health")
    with pytest.raises(ToolFailedError, match="empty"):
        await _call(server, "search", query=" ")
