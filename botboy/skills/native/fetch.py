"""``fetch`` — retrieve a web page as readable text, with SSRF protection.

Threat model: a URL supplied by a user (or by an LLM that read untrusted
content) must not reach loopback, private, link-local (cloud metadata),
multicast or otherwise non-global addresses, and must not be redirected
there. The defences, all enforced per hop:

* scheme allowlist (http, https), port allowlist, no credentials in URLs;
* the host is resolved once and *every* resolved address must be public —
  including IPv4 addresses embedded in IPv6 (mapped, 6to4, Teredo, NAT64);
* the connection is made to the vetted IP itself (Host header and TLS SNI
  carry the name, certificates are verified against the name), so a second
  DNS answer cannot rebind the request to an internal address;
* redirects are followed manually, re-validated, capped, and an https→http
  downgrade is refused;
* GET only, no cookies or auth headers, environment proxies ignored (a
  proxy would resolve the name itself and defeat IP pinning);
* bounded body size, bounded decompression, connect/read timeouts and an
  overall deadline; only textual content types are decoded.

The operator can opt in to private-network targets for a home lab with
``skills.fetch_allow_private_networks``; link-local, multicast, unspecified
and reserved ranges stay blocked even then.
"""
from __future__ import annotations

import asyncio
import http.client
import ipaddress
import json
import re
import socket
import ssl
import time
import zlib
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Callable, Dict, List, Optional, Sequence, Tuple
from urllib.parse import urljoin, urlsplit, urlunsplit

from botboy.skills.native.base import NativeSkillContext, SkillUsageError, failure, parse_options, result, split_command, truncate

ALLOWED_SCHEMES = {"http", "https"}
ALLOWED_PORTS = {80, 443, 8080, 8443}
MAX_REDIRECTS = 5
DEFAULT_MAX_BYTES = 1024 * 1024
HARD_MAX_BYTES = 5 * 1024 * 1024
CONNECT_TIMEOUT = 5.0
TOTAL_DEADLINE = 15.0
DEFAULT_OUTPUT_CHARS = 12000
USER_AGENT = "BotBoy-fetch/1.0 (+local agent; read-only)"
_TEXTUAL_TYPES = ("text/", "application/json", "application/xml", "application/xhtml+xml",
                  "application/rss+xml", "application/atom+xml", "application/ld+json")
_NAT64 = (ipaddress.ip_network("64:ff9b::/96"), ipaddress.ip_network("64:ff9b:1::/48"))

Resolver = Callable[[str, int], Sequence[str]]


class FetchError(ValueError):
    """Blocked by policy or failed to retrieve."""


@dataclass(frozen=True)
class FetchPolicy:
    allow_private_networks: bool = False
    allowed_ports: frozenset = frozenset(ALLOWED_PORTS)
    max_bytes: int = DEFAULT_MAX_BYTES
    deadline_seconds: float = TOTAL_DEADLINE


def _embedded_ipv4(address: ipaddress.IPv6Address) -> List[ipaddress.IPv4Address]:
    embedded: List[ipaddress.IPv4Address] = []
    if address.ipv4_mapped:
        embedded.append(address.ipv4_mapped)
    if address.sixtofour:
        embedded.append(address.sixtofour)
    if address.teredo:
        embedded.extend(address.teredo)
    if any(address in network for network in _NAT64):
        embedded.append(ipaddress.IPv4Address(int(address) & 0xFFFFFFFF))
    return embedded


def address_block_reason(raw: str, policy: FetchPolicy) -> Optional[str]:
    """Return why ``raw`` may not be contacted, or ``None`` if it may."""
    try:
        address = ipaddress.ip_address(raw.split("%", 1)[0])
    except ValueError:
        return f"unparseable address {raw!r}"
    if isinstance(address, ipaddress.IPv6Address):
        for inner in _embedded_ipv4(address):
            reason = address_block_reason(str(inner), policy)
            if reason:
                return f"{reason} (embedded in {address})"
    if address.is_unspecified or address.is_multicast or address.is_link_local:
        return f"{address} is unspecified, multicast or link-local"
    if address.is_reserved and not address.is_private:
        return f"{address} is in a reserved range"
    if address.is_global:
        return None
    if policy.allow_private_networks and (address.is_private or address.is_loopback):
        return None
    return f"{address} is not a public address"


def _default_resolver(host: str, port: int) -> Sequence[str]:
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return sorted({info[4][0] for info in infos})


@dataclass
class Target:
    url: str
    scheme: str
    host: str
    port: int
    path: str
    address: str


def vet_url(url: str, policy: FetchPolicy, resolver: Resolver = _default_resolver) -> Target:
    if len(url) > 2048:
        raise FetchError("URL longer than 2048 characters")
    if any(ch in url for ch in "\r\n\t\x00 "):
        raise FetchError("URL contains whitespace or control characters")
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    if scheme not in ALLOWED_SCHEMES:
        raise FetchError(f"Scheme {scheme or '(none)'!r} not allowed; use http or https")
    if parts.username or parts.password or "@" in parts.netloc:
        raise FetchError("Credentials in URLs are not allowed")
    host = (parts.hostname or "").rstrip(".").lower()
    if not host:
        raise FetchError("URL has no host")
    if not host.isascii():
        try:
            host = host.encode("idna").decode("ascii")
        except UnicodeError as exc:
            raise FetchError(f"Invalid international host name: {exc}") from exc
    try:
        port = parts.port or (443 if scheme == "https" else 80)
    except ValueError as exc:
        raise FetchError(f"Invalid port: {exc}") from exc
    if port not in policy.allowed_ports:
        raise FetchError(f"Port {port} not allowed; allowed ports: {sorted(policy.allowed_ports)}")
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is None:
        if not re.fullmatch(r"[a-z0-9](?:[a-z0-9\-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9\-]{0,61}[a-z0-9])?)*", host):
            raise FetchError(f"Invalid host name {host!r}")
        try:
            addresses = list(resolver(host, port))
        except (socket.gaierror, OSError, UnicodeError) as exc:
            raise FetchError(f"DNS resolution failed for {host}: {exc}") from exc
    else:
        addresses = [str(literal)]
    if not addresses:
        raise FetchError(f"No addresses found for {host}")
    for candidate in addresses:
        reason = address_block_reason(candidate, policy)
        if reason:
            raise FetchError(f"Blocked {host}: {reason}")
    path = parts.path or "/"
    if parts.query:
        path += "?" + parts.query
    normalized = urlunsplit((scheme, parts.netloc, parts.path or "/", parts.query, ""))
    return Target(url=normalized, scheme=scheme, host=host, port=port, path=path, address=addresses[0])


class _PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(self, target: Target, timeout: float) -> None:
        super().__init__(target.host, target.port, timeout=timeout)
        self._address = target.address

    def connect(self) -> None:
        self.sock = socket.create_connection((self._address, self.port), self.timeout)


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, target: Target, timeout: float) -> None:
        super().__init__(target.host, target.port, timeout=timeout, context=ssl.create_default_context())
        self._address = target.address

    def connect(self) -> None:
        raw = socket.create_connection((self._address, self.port), self.timeout)
        # SNI and certificate verification use the vetted *name*, the TCP
        # connection uses the vetted *address*.
        self.sock = self._context.wrap_socket(raw, server_hostname=self.host)  # type: ignore[attr-defined]


@dataclass
class FetchResponse:
    url: str
    status: int
    reason: str
    content_type: str
    charset: Optional[str]
    body: bytes
    truncated: bool
    redirects: List[str] = field(default_factory=list)
    elapsed_ms: float = 0.0


def _read_body(response: http.client.HTTPResponse, policy: FetchPolicy, deadline: float) -> Tuple[bytes, bool]:
    encoding = (response.getheader("Content-Encoding") or "identity").strip().lower()
    if encoding not in {"identity", "gzip", "deflate", ""}:
        raise FetchError(f"Unsupported content encoding {encoding!r}")
    decompressor = None
    if encoding == "gzip":
        decompressor = zlib.decompressobj(16 + zlib.MAX_WBITS)
    elif encoding == "deflate":
        decompressor = zlib.decompressobj()
    output = bytearray()
    raw_read = 0
    while len(output) <= policy.max_bytes:
        if time.monotonic() > deadline:
            raise FetchError("Overall fetch deadline exceeded")
        # read1 returns after a single receive, so a server dripping bytes
        # cannot hold this loop past the overall deadline while read(n)
        # waits to fill its buffer (the socket timeout only bounds silence).
        chunk = response.read1(64 * 1024)
        if not chunk:
            # read1 reports a premature close as plain EOF; a short body must
            # not pass as a complete document.
            declared = response.getheader("Content-Length")
            if declared and declared.strip().isdigit() and raw_read < int(declared):
                raise FetchError(f"Connection closed after {raw_read} of {int(declared)} declared bytes")
            if decompressor is not None and not decompressor.eof:
                raise FetchError("Compressed body ended before the end of the stream")
            break
        raw_read += len(chunk)
        if decompressor is not None:
            # max_length bounds memory even for a decompression bomb.
            output += decompressor.decompress(chunk, policy.max_bytes + 1 - len(output))
            if raw_read > HARD_MAX_BYTES and len(output) <= policy.max_bytes:
                raise FetchError(f"Compressed body exceeds {HARD_MAX_BYTES} bytes")
        else:
            output += chunk
    truncated = len(output) > policy.max_bytes
    return bytes(output[: policy.max_bytes]), truncated


def fetch_url(url: str, policy: FetchPolicy, resolver: Resolver = _default_resolver) -> FetchResponse:
    """Blocking fetch with per-hop validation; run it off the event loop."""
    started = time.monotonic()
    deadline = started + policy.deadline_seconds
    redirects: List[str] = []
    current = url
    previous_scheme: Optional[str] = None
    for _ in range(MAX_REDIRECTS + 1):
        target = vet_url(current, policy, resolver)
        if previous_scheme == "https" and target.scheme == "http":
            raise FetchError("Redirect from https to http refused")
        remaining = max(0.5, deadline - time.monotonic())
        timeout = min(CONNECT_TIMEOUT, remaining)
        connection_cls = _PinnedHTTPSConnection if target.scheme == "https" else _PinnedHTTPConnection
        connection = connection_cls(target, timeout)
        try:
            connection.request("GET", target.path, headers={
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,text/plain,application/json;q=0.9,*/*;q=0.1",
                "Accept-Encoding": "gzip, deflate",
                "Connection": "close",
            })
            response = connection.getresponse()
            if response.status in {301, 302, 303, 307, 308}:
                location = response.getheader("Location")
                if not location:
                    raise FetchError(f"Redirect {response.status} without Location header")
                redirects.append(target.url)
                previous_scheme = target.scheme
                current = urljoin(target.url, location.strip())
                continue
            content_type_header = response.getheader("Content-Type") or ""
            content_type = content_type_header.split(";", 1)[0].strip().lower()
            charset_match = re.search(r"charset=([\w\-:.]+)", content_type_header, re.IGNORECASE)
            if content_type and not content_type.startswith(_TEXTUAL_TYPES):
                raise FetchError(f"Refusing non-text content type {content_type!r}")
            body, truncated = _read_body(response, policy, deadline)
            return FetchResponse(
                url=target.url, status=response.status, reason=response.reason or "",
                content_type=content_type or "text/plain",
                charset=charset_match.group(1) if charset_match else None,
                body=body, truncated=truncated, redirects=redirects,
                elapsed_ms=round((time.monotonic() - started) * 1000, 1),
            )
        except (OSError, http.client.HTTPException, ssl.SSLError) as exc:
            raise FetchError(f"Request to {target.host} failed: {exc.__class__.__name__}: {exc}") from exc
        finally:
            connection.close()
    raise FetchError(f"More than {MAX_REDIRECTS} redirects")


class _ReadableHTML(HTMLParser):
    _SKIP = {"script", "style", "noscript", "svg", "template", "iframe", "canvas", "head"}
    _BLOCK = {"p", "div", "section", "article", "header", "footer", "main", "aside", "nav", "li", "ul", "ol",
              "table", "tr", "br", "hr", "h1", "h2", "h3", "h4", "h5", "h6", "pre", "blockquote", "dd", "dt",
              "figcaption", "form"}

    def __init__(self, base_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.parts: List[str] = []
        self.title = ""
        self.links: List[Dict[str, str]] = []
        self._skip_depth = 0
        self._in_title = False
        self._current_link: Optional[Dict[str, str]] = None
        self.meta_charset: Optional[str] = None

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        attributes = {name: value or "" for name, value in attrs}
        if tag == "meta":
            charset = attributes.get("charset") or ""
            content = attributes.get("content", "")
            if not charset and "charset=" in content.lower():
                charset = content.lower().split("charset=", 1)[1].split(";")[0]
            if charset:
                self.meta_charset = charset.strip()
        if tag == "title":
            self._in_title = True
        if tag in self._SKIP:
            self._skip_depth += 1
            return
        if tag in self._BLOCK:
            self.parts.append("\n")
        if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self.parts.append("#" * int(tag[1]) + " ")
        if tag == "li":
            self.parts.append("- ")
        if tag == "a" and attributes.get("href") and self._skip_depth == 0:
            href = urljoin(self.base_url, attributes["href"])
            if urlsplit(href).scheme in ALLOWED_SCHEMES:
                self._current_link = {"href": href, "text": ""}

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        if tag in self._SKIP:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if tag in self._BLOCK:
            self.parts.append("\n")
        if tag == "a" and self._current_link is not None:
            self._current_link["text"] = " ".join(self._current_link["text"].split())[:120]
            if len(self.links) < 200:
                self.links.append(self._current_link)
            self._current_link = None

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
        if self._skip_depth:
            return
        self.parts.append(data)
        if self._current_link is not None:
            self._current_link["text"] += data

    def text(self) -> str:
        joined = "".join(self.parts)
        lines = [" ".join(line.split()) for line in joined.splitlines()]
        compact: List[str] = []
        for line in lines:
            if line or (compact and compact[-1]):
                compact.append(line)
        return "\n".join(compact).strip()


def _decode(body: bytes, charset: Optional[str]) -> str:
    for candidate in (charset, "utf-8"):
        if not candidate:
            continue
        try:
            return body.decode(candidate)
        except (LookupError, UnicodeDecodeError):
            continue
    return body.decode("utf-8", errors="replace")


def readable(response: FetchResponse) -> Tuple[str, str, List[Dict[str, str]]]:
    """Return (title, text, links) for a fetched response."""
    if response.content_type in {"text/html", "application/xhtml+xml"}:
        sniff = _ReadableHTML(response.url)
        sniff.feed(response.body[:4096].decode("ascii", errors="ignore"))
        parser = _ReadableHTML(response.url)
        parser.feed(_decode(response.body, response.charset or sniff.meta_charset))
        parser.close()
        return " ".join(parser.title.split()), parser.text(), parser.links
    text = _decode(response.body, response.charset)
    if response.content_type in {"application/json", "application/ld+json"}:
        try:
            text = json.dumps(json.loads(text), indent=2, ensure_ascii=False)
        except json.JSONDecodeError:
            pass
    return "", text, []


def policy_for(ctx: NativeSkillContext, max_bytes: int) -> FetchPolicy:
    return FetchPolicy(allow_private_networks=bool(ctx.fetch_allow_private_networks), max_bytes=max_bytes)


_USAGE = (
    "Usage: fetch <http(s)://url> [--max-chars N] [--max-bytes N] [--links] [--raw]\n"
    "Retrieves the page as readable text. Private, loopback and link-local targets are blocked."
)


async def run(command: str, ctx: NativeSkillContext, *, resolver: Resolver = _default_resolver) -> dict:
    try:
        _, tokens = split_command(command)
        opts = parse_options(tokens, valued=("max-chars", "max-bytes"), flags=("links", "raw"))
        if len(opts.positionals) != 1 or opts.positionals[0].lower() in {"help", "--help"}:
            return failure("fetch", _USAGE)
        url = opts.positionals[0]
        max_chars = opts.get_int("max-chars", DEFAULT_OUTPUT_CHARS, minimum=200, maximum=100000)
        max_bytes = opts.get_int("max-bytes", DEFAULT_MAX_BYTES, minimum=1024, maximum=HARD_MAX_BYTES)
        policy = policy_for(ctx, max_bytes)
        response = await asyncio.to_thread(fetch_url, url, policy, resolver)
    except (SkillUsageError, FetchError) as exc:
        return failure("fetch", f"fetch: {exc}")
    if opts.flag("raw"):
        title, text, links = "", _decode(response.body, response.charset), []
    else:
        title, text, links = readable(response)
    body, output_truncated = truncate(text, max_chars)
    header = [f"{response.status} {response.reason} {response.url}",
              f"type={response.content_type} bytes={len(response.body)}"
              + (" (truncated)" if response.truncated else "") + f" time={response.elapsed_ms}ms"]
    if response.redirects:
        header.append("redirects: " + " -> ".join(response.redirects))
    if title:
        header.append(f"title: {title}")
    sections = ["\n".join(header), body]
    if opts.flag("links") and links:
        sections.append("links:\n" + "\n".join(f"- {link['text'] or '(no text)'} <{link['href']}>" for link in links[:50]))
    return result(
        "fetch", "\n\n".join(section for section in sections if section),
        success=200 <= response.status < 300,
        url=response.url, status=response.status, content_type=response.content_type,
        bytes=len(response.body), truncated=response.truncated or output_truncated,
        redirects=response.redirects, title=title, text=body, links=links[:50],
        elapsed_ms=response.elapsed_ms,
    )
