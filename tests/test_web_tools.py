from __future__ import annotations

import socket

import httpx
import pytest

from pulsar_agent.security.redaction import Redactor
from pulsar_agent.tools import build_core_registry, web_tools
from pulsar_agent.tools.web_tools import UrlBlocked, check_url, html_to_text
from tests.conftest import make_context

DOCS_HTML = """
<html><head><title>httpx - Quickstart</title>
<style>body { color: red }</style>
<script>console.log("secret-script-noise")</script></head>
<body>
<nav>Home | Docs | API</nav>
<h1>Quickstart</h1>
<p>First, start by importing httpx:</p>
<pre>import httpx</pre>
<p>Then call <code>httpx.get</code> to fetch a page.</p>
</body></html>
"""

DDG_HTML = """
<html><body>
<div class="result">
  <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.python-httpx.org%2F&rut=abc">HTTPX official site</a>
  <a class="result__snippet" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.python-httpx.org%2F">A next generation HTTP client for Python.</a>
</div>
<div class="result">
  <a class="result__a" href="https://pypi.org/project/httpx/">httpx on PyPI</a>
</div>
</body></html>
"""


@pytest.fixture(autouse=True)
def _no_real_network(monkeypatch):
    """Fail loudly if any test slips past the mock transport."""
    def guard(*args, **kwargs):
        raise AssertionError("test tried to open a real network connection")

    monkeypatch.setattr(socket, "create_connection", guard)
    yield
    web_tools._TRANSPORT = None


def use_transport(handler):
    web_tools._TRANSPORT = httpx.MockTransport(handler)


def fake_public_dns(monkeypatch, hosts_to_ips: dict[str, str]):
    def fake_getaddrinfo(host, *args, **kwargs):
        ip = hosts_to_ips.get(host)
        if ip is None:
            raise OSError(f"unknown host {host}")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 443))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)


# --- SSRF policy ------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/admin",
        "http://localhost:8080/",
        "https://sub.localhost/",
        "http://10.0.0.5/internal",
        "http://192.168.1.1/router",
        "http://172.16.0.1/",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::1]/",
        "http://metadata.google.internal/computeMetadata/v1/",
        "http://0.0.0.0/",
        "http://100.64.0.1/",  # carrier-grade NAT (100.64.0.0/10)
        "http://100.127.255.254/",
    ],
)
def test_private_and_metadata_urls_blocked(url, config):
    with pytest.raises(UrlBlocked):
        check_url(url, config)


def test_cgnat_hostname_blocked(config, monkeypatch):
    fake_public_dns(monkeypatch, {"fabric.internal": "100.64.12.9"})
    with pytest.raises(UrlBlocked):
        check_url("https://fabric.internal/", config)


@pytest.mark.parametrize(
    "url",
    ["file:///etc/passwd", "ftp://example.com/x", "gopher://example.com/", "://nothing"],
)
def test_non_http_schemes_blocked(url, config):
    with pytest.raises(UrlBlocked):
        check_url(url, config)


def test_hostname_resolving_to_private_ip_blocked(config, monkeypatch):
    fake_public_dns(monkeypatch, {"internal.example.com": "10.1.2.3"})
    with pytest.raises(UrlBlocked, match="SSRF"):
        check_url("https://internal.example.com/", config)


def test_public_url_passes(config, monkeypatch):
    fake_public_dns(monkeypatch, {"docs.example.com": "93.184.216.34"})
    check_url("https://docs.example.com/guide", config)


def test_private_url_opt_in(config):
    config["web"]["allow_private_urls"] = True
    check_url("http://127.0.0.1:8000/docs", config)  # no raise
    # file:// stays blocked even with the opt-in
    with pytest.raises(UrlBlocked):
        check_url("file:///etc/passwd", config)


def test_redirect_into_private_range_blocked(workspace, home, config, monkeypatch):
    fake_public_dns(monkeypatch, {"public.example.com": "93.184.216.34"})
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(302, headers={"location": "http://169.254.169.254/latest/"})

    use_transport(handler)
    context = make_context(workspace, home, config)
    out = build_core_registry().dispatch(
        "web_extract", {"url": "https://public.example.com/page"}, context
    )
    assert "BLOCKED" in out
    assert all(r.method == "GET" for r in requests)


# --- resolve-then-pin (DNS rebinding) ----------------------------------


def test_connection_pinned_to_validated_ip(workspace, home, config, monkeypatch):
    fake_public_dns(monkeypatch, {"docs.example.com": "93.184.216.34"})
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, headers={"content-type": "text/plain"}, text="ok")

    use_transport(handler)
    context = make_context(workspace, home, config)
    out = build_core_registry().dispatch(
        "web_extract", {"url": "https://docs.example.com:8443/x"}, context
    )
    assert "Status: 200" in out
    request = requests[0]
    # The transport never sees the hostname: it connects to the vetted IP.
    assert request.url.host == "93.184.216.34"
    assert request.url.port == 8443
    assert request.headers["host"] == "docs.example.com:8443"
    assert request.extensions.get("sni_hostname") == "docs.example.com"


def test_rebinding_second_resolution_never_happens(
    workspace, home, config, monkeypatch
):
    # A rebinding server answers public on the validation lookup and private
    # afterwards. Pinning means there IS no second lookup: the request goes
    # to the first vetted address.
    answers = iter(["93.184.216.34", "10.0.0.5", "10.0.0.5"])

    def rebinding_getaddrinfo(host, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (next(answers), 443))]

    monkeypatch.setattr(socket, "getaddrinfo", rebinding_getaddrinfo)
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, headers={"content-type": "text/plain"}, text="ok")

    use_transport(handler)
    context = make_context(workspace, home, config)
    out = build_core_registry().dispatch(
        "web_extract", {"url": "https://rebind.example.com/"}, context
    )
    assert "Status: 200" in out
    assert requests[0].url.host == "93.184.216.34"


def test_no_pinning_with_private_opt_in(workspace, home, config):
    config["web"]["allow_private_urls"] = True
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, headers={"content-type": "text/plain"}, text="ok")

    use_transport(handler)
    context = make_context(workspace, home, config)
    build_core_registry().dispatch(
        "web_extract", {"url": "http://127.0.0.1:8000/docs"}, context
    )
    assert requests[0].url.host == "127.0.0.1"  # unchanged; nothing to pin


# --- /web slash command -------------------------------------------------


def test_web_slash_command(workspace, home, config, monkeypatch, capsys):
    from pulsar_agent.cli.repl import Repl

    fake_public_dns(monkeypatch, {"docs.example.com": "93.184.216.34"})

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, headers={"content-type": "text/plain"}, text="manual fetch body"
        )

    use_transport(handler)
    config["model"] = "mock:echo"
    repl = Repl(home=home, config=config, workspace=workspace, interactive=False)
    try:
        assert repl.handle_slash("/web") is True
        assert "usage: /web" in capsys.readouterr().out
        assert repl.handle_slash("/web https://docs.example.com/x") is True
        out = capsys.readouterr().out
        assert "Status: 200" in out
        assert "manual fetch body" in out
        # Kill switch applies to the manual path too.
        config["web"]["enabled"] = False
        assert "unknown or disabled" in repl.web_fetch_text(
            "https://docs.example.com/x"
        )
    finally:
        repl.close()


# --- web_extract ------------------------------------------------------


def test_extract_docs_page(workspace, home, config, monkeypatch):
    fake_public_dns(monkeypatch, {"www.python-httpx.org": "93.184.216.34"})
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200, headers={"content-type": "text/html; charset=utf-8"}, text=DOCS_HTML
        )

    use_transport(handler)
    context = make_context(workspace, home, config)
    out = build_core_registry().dispatch(
        "web_extract", {"url": "https://www.python-httpx.org/quickstart"}, context
    )
    assert "Status: 200" in out
    assert "Title: httpx - Quickstart" in out
    assert "import httpx" in out
    assert "secret-script-noise" not in out  # script/style stripped
    assert "Truncated: no" in out
    # read-only proof: only GET, no cookies, no auth
    assert [r.method for r in requests] == ["GET"]
    assert "cookie" not in {k.lower() for k in requests[0].headers}
    assert "authorization" not in {k.lower() for k in requests[0].headers}


def test_extract_text_truncation(workspace, home, config, monkeypatch):
    fake_public_dns(monkeypatch, {"big.example.com": "93.184.216.34"})
    config["web"]["text_limit"] = 100
    use_transport(
        lambda request: httpx.Response(
            200, headers={"content-type": "text/plain"}, text="word " * 500
        )
    )
    context = make_context(workspace, home, config)
    out = build_core_registry().dispatch(
        "web_extract", {"url": "https://big.example.com/big.txt"}, context
    )
    assert "[text truncated]" in out
    assert "Truncated: yes" in out


def test_extract_byte_cap(workspace, home, config, monkeypatch):
    fake_public_dns(monkeypatch, {"huge.example.com": "93.184.216.34"})
    config["web"]["max_bytes"] = 64
    use_transport(
        lambda request: httpx.Response(
            200, headers={"content-type": "text/plain"}, text="z" * 10000
        )
    )
    context = make_context(workspace, home, config)
    out = build_core_registry().dispatch(
        "web_extract", {"url": "https://huge.example.com/blob"}, context
    )
    assert "Truncated: yes" in out


def test_extract_output_redacted(workspace, home, config, monkeypatch):
    fake_public_dns(monkeypatch, {"leak.example.com": "93.184.216.34"})
    secret = "sk-web-leak-abcdef1234567890xyz"
    use_transport(
        lambda request: httpx.Response(
            200, headers={"content-type": "text/plain"}, text=f"key: {secret}"
        )
    )
    context = make_context(
        workspace, home, config, redactor=Redactor([secret])
    )
    out = build_core_registry().dispatch(
        "web_extract", {"url": "https://leak.example.com/page"}, context
    )
    assert secret not in out
    assert "[REDACTED]" in out


def test_extract_binary_content_not_shown(workspace, home, config, monkeypatch):
    fake_public_dns(monkeypatch, {"cdn.example.com": "93.184.216.34"})
    use_transport(
        lambda request: httpx.Response(
            200,
            headers={"content-type": "application/octet-stream"},
            content=b"\x00\x01\x02binaryblob",
        )
    )
    context = make_context(workspace, home, config)
    out = build_core_registry().dispatch(
        "web_extract", {"url": "https://cdn.example.com/tool.bin"}, context
    )
    assert "non-text content" in out
    assert "binaryblob" not in out


def test_extract_network_error_message(workspace, home, config, monkeypatch):
    fake_public_dns(monkeypatch, {"down.example.com": "93.184.216.34"})

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    use_transport(handler)
    context = make_context(workspace, home, config)
    out = build_core_registry().dispatch(
        "web_extract", {"url": "https://down.example.com/"}, context
    )
    assert "ERROR: fetch failed" in out
    assert "down.example.com" in out


# --- web_search -------------------------------------------------------


def test_search_duckduckgo_parses_results(workspace, home, config):
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, headers={"content-type": "text/html"}, text=DDG_HTML)

    use_transport(handler)
    context = make_context(workspace, home, config)
    out = build_core_registry().dispatch(
        "web_search", {"query": "python httpx docs"}, context
    )
    assert "HTTPX official site" in out
    assert "https://www.python-httpx.org/" in out  # uddg redirect decoded
    assert "next generation HTTP client" in out
    assert "https://pypi.org/project/httpx/" in out
    assert [r.method for r in requests] == ["GET"]


def test_search_failure_gives_guidance(workspace, home, config):
    use_transport(lambda request: httpx.Response(503, text="rate limited"))
    context = make_context(workspace, home, config)
    out = build_core_registry().dispatch("web_search", {"query": "anything"}, context)
    assert "ERROR: web search failed" in out
    assert "web_extract" in out  # fallback guidance


def test_search_brave_backend_needs_key(workspace, home, config):
    config["web"]["search_backend"] = "brave"
    use_transport(lambda request: httpx.Response(200, json={}))
    context = make_context(workspace, home, config)
    out = build_core_registry().dispatch("web_search", {"query": "x"}, context)
    assert "no key found" in out
    assert "PULSAR_HOME/.env" in out


def test_search_brave_backend_with_key(workspace, home, config):
    from pulsar_agent.secrets import SecretStore

    config["web"]["search_backend"] = "brave"
    SecretStore(home).set("BRAVE_API_KEY", "brave-test-key-123456")
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={
            "web": {"results": [{
                "title": "httpx docs",
                "url": "https://www.python-httpx.org/",
                "description": "HTTP client",
            }]}
        })

    use_transport(handler)
    context = make_context(workspace, home, config)
    out = build_core_registry().dispatch("web_search", {"query": "httpx"}, context)
    assert "httpx docs" in out
    assert captured[0].headers.get("X-Subscription-Token") == "brave-test-key-123456"
    assert captured[0].method == "GET"


# --- gating and approvals ---------------------------------------------


def test_web_tools_disabled_by_config(workspace, home, config):
    config["web"]["enabled"] = False
    context = make_context(workspace, home, config)
    registry = build_core_registry()
    names = [spec.name for spec in registry.enabled(context)]
    assert "web_search" not in names
    assert "web_extract" not in names
    out = registry.dispatch("web_extract", {"url": "https://example.com"}, context)
    assert "unknown or disabled" in out


def test_web_tools_hidden_from_subagents(workspace, home, config):
    context = make_context(workspace, home, config, is_subagent=True)
    names = [spec.name for spec in build_core_registry().enabled(context)]
    assert "web_search" not in names
    assert "web_extract" not in names


def test_paranoid_preset_prompts_for_web(workspace, home, config, monkeypatch):
    fake_public_dns(monkeypatch, {"docs.example.com": "93.184.216.34"})
    use_transport(lambda request: httpx.Response(200, text="hi"))
    context = make_context(
        workspace, home, config, preset="paranoid", approver=None, autonomy={}
    )
    out = build_core_registry().dispatch(
        "web_extract", {"url": "https://docs.example.com/"}, context
    )
    assert "BLOCKED" in out


def test_html_to_text_helper():
    title, text = html_to_text(DOCS_HTML)
    assert title == "httpx - Quickstart"
    assert "Quickstart" in text
    assert "console.log" not in text
