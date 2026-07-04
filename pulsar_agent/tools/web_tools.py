"""Read-only web retrieval: web_search + web_extract.

Strictly GET-only: no POST, no uploads, no cookies, no credential use, no
browser automation. Built for everyday coding-agent work — reading docs,
checking package/API references, release notes, project pages.

SSRF policy (on by default, opt-out via `web.allow_private_urls`):
- only http/https schemes (file:, ftp:, gopher: etc. always refused)
- hostname and every resolved IP checked against loopback, private,
  link-local (cloud metadata 169.254.169.254), reserved, multicast ranges
- redirects re-checked hop by hop, so a public page cannot bounce the
  client into an internal address

Known limit: the check resolves DNS separately from the request, so a
DNS-rebinding server could theoretically pass the check and rebind. This is
documented in SECURITY.md; `paranoid` preset prompts on every fetch.
"""

from __future__ import annotations

import ipaddress
import json
import socket
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import parse_qs, quote_plus, unquote, urljoin, urlsplit

import httpx

from pulsar_agent.security.approvals import KIND_NETWORK, ApprovalRequest
from pulsar_agent.tools.registry import ToolContext, ToolSpec

MAX_REDIRECTS = 5
DEFAULT_MAX_BYTES = 400000
DEFAULT_TEXT_LIMIT = 12000
DEFAULT_TIMEOUT = 20
DEFAULT_USER_AGENT = "Pulsar/0.2 (+https://github.com/0langa/Pulsar)"

DUCKDUCKGO_URL = "https://html.duckduckgo.com/html/"
BRAVE_URL = "https://api.search.brave.com/res/v1/web/search"

BLOCKED_HOSTNAMES = {"localhost", "metadata.google.internal", "metadata", "instance-data"}

SEARCH_FAILURE_HINT = (
    "The no-key DuckDuckGo HTML backend is best-effort and sometimes "
    "rate-limits or changes markup. If this persists, configure "
    "`web.search_backend: brave` with a key in PULSAR_HOME/.env, or fetch a "
    "known URL directly with web_extract."
)

# Overridable in tests so no real network is touched.
_TRANSPORT: httpx.BaseTransport | None = None


class UrlBlocked(PermissionError):
    """Raised for SSRF-policy violations; surfaces as BLOCKED to the model."""


def _web_cfg(config: dict) -> dict:
    return config.get("web", {}) or {}


def _ip_blocked(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return (
        ip.is_loopback
        or ip.is_private
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def check_url(url: str, config: dict) -> None:
    """Raise UrlBlocked unless the URL passes the SSRF policy."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise UrlBlocked(
            f"scheme {parts.scheme or '(none)'!r} refused; only http/https "
            "are allowed (file:, ftp: and friends are never fetched)"
        )
    host = parts.hostname
    if not host:
        raise UrlBlocked("URL has no hostname")
    if _web_cfg(config).get("allow_private_urls", False):
        return
    lowered = host.lower().rstrip(".")
    if lowered in BLOCKED_HOSTNAMES or lowered.endswith(".localhost"):
        raise UrlBlocked(
            f"host {host!r} is blocked (localhost/metadata); set "
            "web.allow_private_urls: true only if you really need internal URLs"
        )
    try:
        addresses = [ipaddress.ip_address(lowered)]
    except ValueError:
        try:
            infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
        except OSError as exc:
            raise UrlBlocked(f"cannot resolve host {host!r}: {exc}") from exc
        addresses = []
        for info in infos:
            try:
                addresses.append(ipaddress.ip_address(info[4][0]))
            except ValueError:
                continue
    for address in addresses:
        if _ip_blocked(address):
            raise UrlBlocked(
                f"host {host!r} resolves to {address} (private/loopback/"
                "link-local/metadata range); blocked by SSRF policy. Set "
                "web.allow_private_urls: true to opt in to internal URLs"
            )


def _make_client(config: dict) -> httpx.Client:
    cfg = _web_cfg(config)
    return httpx.Client(
        transport=_TRANSPORT,
        timeout=float(cfg.get("timeout_seconds", DEFAULT_TIMEOUT)),
        follow_redirects=False,  # redirects are re-validated manually
        headers={
            "User-Agent": str(cfg.get("user_agent", DEFAULT_USER_AGENT)),
            "Accept": "text/html,application/xhtml+xml,application/json,text/*;q=0.9,*/*;q=0.5",
        },
        cookies=None,
    )


@dataclass
class FetchResult:
    url: str
    final_url: str
    status: int
    content_type: str
    body: str
    truncated: bool


def fetch_url(url: str, config: dict) -> FetchResult:
    """GET a URL with SSRF checks on the initial URL and every redirect hop."""
    cfg = _web_cfg(config)
    max_bytes = int(cfg.get("max_bytes", DEFAULT_MAX_BYTES))
    current = url
    with _make_client(config) as client:
        for _ in range(MAX_REDIRECTS + 1):
            check_url(current, config)
            with client.stream("GET", current) as response:
                if response.is_redirect:
                    location = response.headers.get("location", "")
                    if not location:
                        raise ValueError(f"redirect from {current} without Location")
                    current = urljoin(current, location)
                    continue
                chunks: list[bytes] = []
                total = 0
                truncated = False
                for chunk in response.iter_bytes():
                    chunks.append(chunk)
                    total += len(chunk)
                    if total > max_bytes:
                        truncated = True
                        break
                raw = b"".join(chunks)[:max_bytes]
                encoding = response.encoding or "utf-8"
                try:
                    body = raw.decode(encoding, errors="replace")
                except LookupError:
                    body = raw.decode("utf-8", errors="replace")
                return FetchResult(
                    url=url,
                    final_url=current,
                    status=response.status_code,
                    content_type=response.headers.get("content-type", ""),
                    body=body,
                    truncated=truncated,
                )
    raise UrlBlocked(f"too many redirects (> {MAX_REDIRECTS}) fetching {url}")


# --- HTML to text -----------------------------------------------------

_SKIP_TAGS = {"script", "style", "noscript", "template", "svg", "iframe"}
_BLOCK_TAGS = {
    "p", "div", "section", "article", "header", "footer", "li", "ul", "ol",
    "table", "tr", "br", "hr", "h1", "h2", "h3", "h4", "h5", "h6", "pre",
    "blockquote", "main", "nav", "aside", "dd", "dt",
}


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self._in_title = False
        self._skip_depth = 0
        self._parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
        elif tag == "title":
            self._in_title = True
        if tag in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_endtag(self, tag):
        if tag in _SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1
        elif tag == "title":
            self._in_title = False
        if tag in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_data(self, data):
        if self._skip_depth:
            return
        if self._in_title:
            self.title += data
            return
        self._parts.append(data)

    def text(self) -> str:
        lines = []
        for line in "".join(self._parts).splitlines():
            cleaned = " ".join(line.split())
            if cleaned:
                lines.append(cleaned)
        return "\n".join(lines)


def html_to_text(html: str) -> tuple[str, str]:
    """Return (title, readable text) for an HTML document."""
    parser = _TextExtractor()
    try:
        parser.feed(html)
        parser.close()
    except Exception:  # noqa: BLE001 - malformed HTML falls back to raw parts
        pass
    return " ".join(parser.title.split()), parser.text()


# --- search backends --------------------------------------------------


class _DuckResultParser(HTMLParser):
    """Parses DuckDuckGo's HTML endpoint (best-effort; markup may change)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.results: list[dict] = []
        self._current: dict | None = None
        self._in_link = False
        self._in_snippet = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = (attrs.get("class") or "").split()
        if tag == "a" and "result__a" in classes:
            self._current = {"title": "", "url": _decode_ddg_href(attrs.get("href", "")), "snippet": ""}
            self._in_link = True
        elif tag == "a" and "result__snippet" in classes and self._current is not None:
            self._in_snippet = True

    def handle_endtag(self, tag):
        if tag == "a":
            if self._in_link and self._current is not None:
                self.results.append(self._current)
            self._in_link = False
            self._in_snippet = False

    def handle_data(self, data):
        if self._current is None:
            return
        if self._in_link:
            self._current["title"] += data
        elif self._in_snippet:
            # The snippet anchor follows the title anchor; _current is already
            # in results but is the same dict, so this still lands correctly.
            self._current["snippet"] += data


def _decode_ddg_href(href: str) -> str:
    # DDG wraps results as //duckduckgo.com/l/?uddg=<encoded-url>&rut=...
    if "uddg=" in href:
        query = urlsplit(href).query
        target = parse_qs(query).get("uddg", [""])[0]
        if target:
            return unquote(target)
    if href.startswith("//"):
        return "https:" + href
    return href


def _parse_duckduckgo(html: str) -> list[dict]:
    parser = _DuckResultParser()
    try:
        parser.feed(html)
        parser.close()
    except Exception:  # noqa: BLE001
        pass
    seen: set[str] = set()
    out = []
    for result in parser.results:
        url = result["url"].strip()
        if not url or url in seen:
            continue
        seen.add(url)
        out.append(
            {
                "title": " ".join(result["title"].split()),
                "url": url,
                "snippet": " ".join(result["snippet"].split()),
            }
        )
    return out


def _search_duckduckgo(query: str, config: dict) -> list[dict]:
    with _make_client(config) as client:
        response = client.get(f"{DUCKDUCKGO_URL}?q={quote_plus(query)}")
        response.raise_for_status()
        return _parse_duckduckgo(response.text)


def _search_brave(query: str, config: dict, context: ToolContext) -> list[dict]:
    from pulsar_agent.secrets import SecretStore

    env_var = str(
        _web_cfg(config).get("search_results_api_env_var") or "BRAVE_API_KEY"
    )
    key = SecretStore(context.home).get(env_var)
    if not key:
        raise ValueError(
            f"web.search_backend is 'brave' but no key found; put "
            f"{env_var}=<key> in PULSAR_HOME/.env"
        )
    with _make_client(config) as client:
        response = client.get(
            f"{BRAVE_URL}?q={quote_plus(query)}",
            headers={"X-Subscription-Token": key, "Accept": "application/json"},
        )
        response.raise_for_status()
        payload = json.loads(response.text)
    results = []
    for item in ((payload.get("web") or {}).get("results") or []):
        results.append(
            {
                "title": str(item.get("title", "")),
                "url": str(item.get("url", "")),
                "snippet": str(item.get("description", "")),
            }
        )
    return results


# --- tool handlers ----------------------------------------------------


def web_search_handler(args: dict, context: ToolContext) -> str:
    query = str(args.get("query", "")).strip()
    if not query:
        return "ERROR: query is required"
    max_results = min(int(args.get("max_results", 5) or 5), 10)
    config = context.config
    backend = str(_web_cfg(config).get("search_backend", "duckduckgo")).lower()
    context.approvals.check(
        ApprovalRequest(
            kind=KIND_NETWORK,
            description=f"web_search: {query[:120]}",
            detail=f"backend {backend}",
        )
    )
    context.emit("web_search", query[:120])
    try:
        if backend == "brave":
            results = _search_brave(query, config, context)
        else:
            results = _search_duckduckgo(query, config)
    except (httpx.HTTPError, ValueError) as exc:
        return f"ERROR: web search failed: {exc}\n{SEARCH_FAILURE_HINT}"
    if not results:
        return f"No results for {query!r}.\n{SEARCH_FAILURE_HINT}"
    lines = [f"Search results for {query!r} (backend: {backend}):", ""]
    for index, result in enumerate(results[:max_results], start=1):
        lines.append(f"{index}. {result['title'] or '(no title)'}")
        lines.append(f"   {result['url']}")
        if result["snippet"]:
            lines.append(f"   {result['snippet'][:300]}")
    return "\n".join(lines)


_TEXTUAL_TYPES = ("text/", "json", "xml", "javascript", "yaml", "markdown", "csv")


def web_extract_handler(args: dict, context: ToolContext) -> str:
    url = str(args.get("url", "")).strip()
    if not url:
        return "ERROR: url is required"
    config = context.config
    context.approvals.check(
        ApprovalRequest(
            kind=KIND_NETWORK,
            description=f"web_extract: {url[:200]}",
        )
    )
    context.emit("web_extract", url[:200])
    try:
        result = fetch_url(url, config)
    except UrlBlocked:
        raise
    except httpx.HTTPError as exc:
        return (
            f"ERROR: fetch failed for {url}: {exc}. "
            "Check the URL, your network connection, or try again."
        )

    content_type = result.content_type.split(";")[0].strip().lower()
    is_html = "html" in content_type
    is_textual = is_html or any(t in content_type for t in _TEXTUAL_TYPES)
    title, text = ("", "")
    if is_html:
        title, text = html_to_text(result.body)
    elif is_textual:
        text = result.body

    text_limit = int(_web_cfg(config).get("text_limit", DEFAULT_TEXT_LIMIT))
    text_truncated = len(text) > text_limit
    if text_truncated:
        text = text[:text_limit] + "\n[text truncated]"

    header = [
        f"URL: {result.url}",
        f"Status: {result.status}",
        f"Content-Type: {result.content_type or '(none)'}",
    ]
    if result.final_url != result.url:
        header.insert(1, f"Final URL: {result.final_url}")
    if title:
        header.append(f"Title: {title}")
    header.append(
        f"Truncated: {'yes' if result.truncated or text_truncated else 'no'}"
    )
    if not is_textual:
        header.append("[non-text content; body not shown]")
        return "\n".join(header)
    return "\n".join(header) + "\n\n" + text


def _web_enabled(context: ToolContext) -> bool:
    return bool(_web_cfg(context.config).get("enabled", True)) and not context.is_subagent


def build_specs() -> list[ToolSpec]:
    return [
        ToolSpec(
            name="web_search",
            description=(
                "Search the public web (read-only). Good for finding docs, "
                "package references, official project pages, and release "
                "notes. Returns titles, URLs, and snippets."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "max_results": {"type": "integer", "description": "1-10, default 5"},
                },
                "required": ["query"],
            },
            handler=web_search_handler,
            check_fn=_web_enabled,
        ),
        ToolSpec(
            name="web_extract",
            description=(
                "Fetch a public web page (GET only) and return readable text "
                "with title and metadata. Private/internal addresses are "
                "blocked. Use for reading documentation and references."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                },
                "required": ["url"],
            },
            handler=web_extract_handler,
            check_fn=_web_enabled,
        ),
    ]
