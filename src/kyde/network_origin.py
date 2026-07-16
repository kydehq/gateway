"""
Network-origin enrichment.

Parses a proxied HTTP request into a `NetworkOrigin` record: the full
forwarded chain (XFF + RFC 7239 `Forwarded` + `Via`), a classified
representative origin IP, the subnet it belongs to, a parsed User-Agent
(tool / version / OS), and the upstream host we forwarded to.

Trust model: XFF entries are only trustworthy to the extent each hop was
added by a proxy we know about. We walk the chain right-to-left starting
from the TCP peer; an entry at index i is trusted iff the entry at i+1
(the one that added it) is in `trusted_cidrs`. Everything before the
first untrusted reporter is client-supplied and discarded.

`trusted_cidrs` is sourced from the runtime-settings layer under
`TRUSTED_PROXY_CIDRS` and cached for a handful of seconds — operators
change it from the Settings page without a redeploy.
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass, field
from threading import Lock
from typing import Iterable, Optional
from urllib.parse import urlparse

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class NetworkOrigin:
    remote_addr: Optional[str] = None
    forwarded_chain: list[dict] = field(default_factory=list)
    forwarded_for_raw: str = ""
    forwarded_raw: str = ""
    via_raw: str = ""
    origin_ip: Optional[str] = None
    origin_class: str = "unknown"
    origin_subnet: str = ""
    ua_tool: str = ""
    ua_version: str = ""
    ua_os: str = ""
    upstream_host: str = ""
    upstream_region: str = ""


# ---------------------------------------------------------------------------
# Header parsing
# ---------------------------------------------------------------------------


_BRACKET_V6 = re.compile(r"^\[([0-9a-fA-F:]+)\](?::\d+)?$")
_V4_PORT = re.compile(r"^(\d{1,3}(?:\.\d{1,3}){3}):\d+$")


def _strip_port(token: str) -> str:
    """Return a plain IP from an XFF token, handling both bracketed v6 and v4:port."""
    token = token.strip()
    if not token:
        return ""
    m = _BRACKET_V6.match(token)
    if m:
        return m.group(1)
    m = _V4_PORT.match(token)
    if m:
        return m.group(1)
    return token


def _is_valid_ip(token: str) -> bool:
    if not token:
        return False
    try:
        ipaddress.ip_address(token)
        return True
    except ValueError:
        return False


def _parse_xff(value: str) -> list[str]:
    """Parse X-Forwarded-For into an ordered list of IPs (far-end first).

    Drops empty entries, `unknown`, port suffixes, and anything that isn't a
    syntactically valid IP. Preserves order — the leftmost entry is the
    claimed original client.
    """
    if not value:
        return []
    out: list[str] = []
    for raw in value.split(","):
        ip = _strip_port(raw)
        if not ip or ip.lower() == "unknown":
            continue
        if _is_valid_ip(ip):
            out.append(ip)
    return out


# Matches one forwarded-element: a semicolon-separated list of key=value
# pairs, where value may be a quoted string, a token, or bracketed v6.
_FWD_PAIR = re.compile(
    r"""
    \s*                                         # leading space
    (?P<key>[A-Za-z][A-Za-z0-9_-]*)             # key
    \s*=\s*
    (?P<value>
        "(?:[^"\\]|\\.)*"                       # quoted string
      | \[[^\]]+\](?::\d+)?                     # bracketed v6 (with optional port)
      | [^;,\s]+                                # bare token
    )
    \s*
    """,
    re.VERBOSE,
)


def _parse_forwarded(value: str) -> list[dict]:
    """Parse an RFC 7239 Forwarded header into a list of `{for, by, proto}` dicts.

    Returns one dict per forwarded-element in left-to-right order. Obfuscated
    identifiers (`_hidden`) and missing `for=` produce empty-string fields
    rather than being dropped, so downstream can still see that a hop was
    present in the chain.
    """
    if not value:
        return []
    elements: list[dict] = []
    # Elements are comma-separated, but commas can appear inside quoted
    # strings — cheap split works because RFC 7239 doesn't allow bare commas
    # in tokens and quoted commas are rare in practice. If we ever see one
    # we'll get a spurious extra element; not worth a full parser.
    for element in value.split(","):
        pairs: dict[str, str] = {}
        for match in _FWD_PAIR.finditer(element):
            key = match.group("key").lower()
            raw_val = match.group("value")
            if raw_val.startswith('"') and raw_val.endswith('"'):
                raw_val = raw_val[1:-1]
            pairs[key] = raw_val
        if pairs:
            elements.append(pairs)
    return elements


# Match the "received-by" token in each Via element. Per RFC 7230 §5.7.1,
# an element is: <received-protocol> <received-by> [ <comment> ]. We keep
# the received-by (a host[:port] or pseudonym).
_VIA_ELEMENT = re.compile(
    r"""
    ^\s*
    (?:HTTP/)?[\d.]+                             # protocol
    \s+
    (?P<by>[^\s()]+)                             # received-by
    (?:\s+\([^)]*\))?                            # optional comment
    \s*$
    """,
    re.VERBOSE,
)


def _parse_via(value: str) -> list[str]:
    """Return the received-by tokens from a Via header in order."""
    if not value:
        return []
    out: list[str] = []
    for element in value.split(","):
        m = _VIA_ELEMENT.match(element)
        if m:
            out.append(m.group("by"))
    return out


# ---------------------------------------------------------------------------
# IP classification + subnet derivation
# ---------------------------------------------------------------------------


_CGNAT = ipaddress.ip_network("100.64.0.0/10")
_V4_LINK_LOCAL = ipaddress.ip_network("169.254.0.0/16")
_V6_LINK_LOCAL = ipaddress.ip_network("fe80::/10")
_V6_ULA = ipaddress.ip_network("fc00::/7")
# RFC1918 ranges checked explicitly: ipaddress.is_private also flags
# documentation ranges (192.0.2.0/24, 198.51.100.0/24, 203.0.113.0/24)
# and several reserved blocks — none of those are "corporate private".
_RFC1918 = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
)


def _classify(ip_str: str) -> str:
    """Return the origin-class label for an IP address string."""
    if not ip_str:
        return "unknown"
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return "unknown"
    if ip.is_loopback:
        return "loopback"
    if ip.version == 4 and ip in _V4_LINK_LOCAL:
        return "link_local"
    if ip.version == 6 and ip in _V6_LINK_LOCAL:
        return "link_local"
    if ip.version == 4 and ip in _CGNAT:
        return "cgnat"
    if ip.version == 6 and ip in _V6_ULA:
        return "unique_local_v6"
    if ip.version == 4 and any(ip in n for n in _RFC1918):
        return "rfc1918"
    return "public"


def _subnet_of(ip_str: str) -> str:
    """Return the /24 (v4) or /48 (v6) CIDR that `ip_str` lies in, as a string."""
    if not ip_str:
        return ""
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return ""
    prefix = 24 if ip.version == 4 else 48
    net = ipaddress.ip_network(f"{ip_str}/{prefix}", strict=False)
    return str(net)


def _in_any(ip_str: str, nets: Iterable[ipaddress._BaseNetwork]) -> bool:
    """True if `ip_str` lies in any of the given networks."""
    if not ip_str:
        return False
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    for n in nets:
        # Skip mismatched families to avoid TypeError.
        if ip.version != n.version:
            continue
        if ip in n:
            return True
    return False


# ---------------------------------------------------------------------------
# Origin picking
# ---------------------------------------------------------------------------


def _pick_origin(
    chain: list[str],
    peer_ip: Optional[str],
    trusted_cidrs: list[ipaddress._BaseNetwork],
) -> tuple[Optional[str], str, str]:
    """Pick a representative origin IP + class + subnet from a chain.

    Trust walks right-to-left. An XFF entry at position i is trusted iff
    the reporter (entry at i+1, or the TCP peer if i is last) is in
    `trusted_cidrs`. Scan stops at the first untrusted reporter; everything
    before that is dropped.

    From the trusted chain, pick the first public IP scanning far-end →
    us. If none are public, use the leftmost (outermost) private IP. If the
    trusted chain is empty, fall back to the TCP peer.
    """
    trusted_chain: list[str] = []
    reporter = peer_ip
    for ip in reversed(chain):
        if reporter and _in_any(reporter, trusted_cidrs):
            trusted_chain.insert(0, ip)
            reporter = ip
        else:
            break

    if not trusted_chain:
        if peer_ip:
            return peer_ip, _classify(peer_ip), _subnet_of(peer_ip)
        return None, "unknown", ""

    for ip in trusted_chain:
        if _classify(ip) == "public":
            return ip, "public", _subnet_of(ip)

    leftmost = trusted_chain[0]
    return leftmost, _classify(leftmost), _subnet_of(leftmost)


# ---------------------------------------------------------------------------
# User-Agent parsing
# ---------------------------------------------------------------------------


# Ordered: first match wins. Each pattern produces (tool, version, os).
# `os` is only filled when the UA puts it in an easy-to-grab spot.
#
# `kyde-gateway` sits at the top — when the gateway's own internal HTTP
# clients identify themselves with this UA, topology classifies them
# positively as the gateway. Without this positive marker the topology
# layer falls back to "Unknown Client" rather than guessing.
_UA_PATTERNS: list[tuple[str, re.Pattern]] = [
    (
        "kyde-gateway",
        re.compile(r"kyde[-_]gateway/(?P<version>[\w.+-]+)", re.IGNORECASE),
    ),
    ("cursor", re.compile(r"Cursor/(?P<version>[\w.+-]+)", re.IGNORECASE)),
    (
        "copilot",
        re.compile(
            r"(?:GitHub)?[-_]?Copilot(?:[-_][\w]+)?/(?P<version>[\w.+-]+)",
            re.IGNORECASE,
        ),
    ),
    ("claude-code", re.compile(r"claude[-_]code/(?P<version>[\w.+-]+)", re.IGNORECASE)),
    ("continue", re.compile(r"Continue/(?P<version>[\w.+-]+)", re.IGNORECASE)),
    ("aider", re.compile(r"aider/(?P<version>[\w.+-]+)", re.IGNORECASE)),
    (
        "openai-sdk",
        re.compile(
            # Matches both "OpenAI/Python 1.12.0" (SDK default UA with space
            # separator) and "openai-python/1.12.0" (alternate form with
            # slash). Same for the JS/Node variants.
            r"(?:OpenAI/(?:Python|JS)|openai-(?:python|node))[/ ](?P<version>[\w.+-]+)",
            re.IGNORECASE,
        ),
    ),
    (
        "anthropic-sdk",
        re.compile(
            r"anthropic[-_](?:sdk[-_])?(?:python|typescript|js|node)/(?P<version>[\w.+-]+)",
            re.IGNORECASE,
        ),
    ),
    (
        "google-genai",
        re.compile(r"google[-_]generativeai/(?P<version>[\w.+-]+)", re.IGNORECASE),
    ),
    ("curl", re.compile(r"curl/(?P<version>[\w.+-]+)", re.IGNORECASE)),
]


_UA_OS = re.compile(
    r"\(([^)]*?)\)"  # first parenthetical group in a UA usually carries OS info
)


def _parse_ua(ua: str) -> tuple[str, str, str]:
    """Parse a User-Agent into (tool, version, os).

    Returns ("unknown", "", "") if nothing matches — callers should keep the
    raw UA on the ledger row for forensic lookup.
    """
    if not ua:
        return "unknown", "", ""
    os_str = ""
    m_os = _UA_OS.search(ua)
    if m_os:
        os_str = m_os.group(1).strip().split(";")[0].strip()
    for tool, pattern in _UA_PATTERNS:
        m = pattern.search(ua)
        if m:
            return tool, m.group("version"), os_str
    return "unknown", "", os_str


# ---------------------------------------------------------------------------
# Upstream host + region
# ---------------------------------------------------------------------------


_AWS_REGION = re.compile(r"\b([a-z]{2,}-[a-z]+-\d+)\b")
_AZURE_REGION = re.compile(
    r"\b("
    r"east(?:us|asia|europe)\d?|"
    r"west(?:us|europe|centralus)\d?|"
    r"central(?:us|india)\d?|"
    r"north(?:europe|centralus)\d?|"
    r"south(?:centralus|africanorth|eastasia)\d?"
    r")\b",
    re.IGNORECASE,
)


def _upstream_host(url: str) -> tuple[str, str]:
    """Return `(host, region)` from a forwarded-to URL. Region is best-effort."""
    if not url:
        return "", ""
    try:
        parsed = urlparse(url)
    except Exception:
        return "", ""
    host = (parsed.hostname or "").lower()
    if not host:
        return "", ""
    m = _AWS_REGION.search(host)
    if m:
        return host, m.group(1)
    m = _AZURE_REGION.search(host)
    if m:
        return host, m.group(1).lower()
    return host, ""


# ---------------------------------------------------------------------------
# Trusted-CIDR config (read from runtime settings)
# ---------------------------------------------------------------------------


_DEFAULT_TRUSTED = (
    "127.0.0.0/8,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,::1/128,fc00::/7"
)

# Parsed-form cache keyed on the raw string, so we don't re-parse each
# request. The settings layer already caches the string read; this just
# avoids re-running `ip_network` on every call.
_parsed_cache: dict[str, list[ipaddress._BaseNetwork]] = {}
_parsed_lock = Lock()


def parse_cidr_list(raw: str) -> list[ipaddress._BaseNetwork]:
    """Parse a comma-separated CIDR list. Raises ValueError on any invalid token."""
    out: list[ipaddress._BaseNetwork] = []
    for token in (raw or "").split(","):
        token = token.strip()
        if not token:
            continue
        out.append(ipaddress.ip_network(token, strict=False))
    return out


def get_trusted_cidrs() -> list[ipaddress._BaseNetwork]:
    """Return the effective trusted-proxy CIDR list.

    Pulls the raw string from `settings.get("TRUSTED_PROXY_CIDRS")` and
    memoizes its parsed form. On a malformed stored value we fall back to
    the compile-time default rather than failing capture — the proxy path
    must not crash because of a bad settings row.
    """
    # Import lazily to avoid an import cycle with settings → ledger.
    from . import settings as _settings

    try:
        raw = str(_settings.get("TRUSTED_PROXY_CIDRS") or "")
    except KeyError:
        raw = _DEFAULT_TRUSTED
    key = raw or _DEFAULT_TRUSTED
    with _parsed_lock:
        cached = _parsed_cache.get(key)
        if cached is not None:
            return cached
    try:
        parsed = parse_cidr_list(key)
    except ValueError:
        parsed = parse_cidr_list(_DEFAULT_TRUSTED)
    with _parsed_lock:
        _parsed_cache[key] = parsed
    return parsed


def is_enabled() -> bool:
    """True if the network-origin capture pipeline should run on this request."""
    from . import settings as _settings

    try:
        return bool(_settings.get("NETWORK_ORIGIN_ENABLED"))
    except KeyError:
        return True


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def _headers_get(request, name: str) -> str:
    """Case-insensitive header read that works for Starlette Request and dicts."""
    headers = getattr(request, "headers", None)
    if headers is None:
        return ""
    try:
        return headers.get(name, "")
    except AttributeError:
        # Fallback for plain dicts without get()
        return headers.get(name) if name in headers else ""


def _peer_ip(request) -> Optional[str]:
    client = getattr(request, "client", None)
    if client is None:
        return None
    host = getattr(client, "host", None)
    if not host:
        return None
    return host


def parse_from_request(
    request,
    upstream_url: str,
    trusted_cidrs: Optional[list[ipaddress._BaseNetwork]] = None,
) -> NetworkOrigin:
    """Build a NetworkOrigin from a FastAPI/Starlette-like request object.

    `request` must expose `.headers` (dict-like, case-insensitive) and
    `.client.host`. `upstream_url` is the URL we forwarded to — stored so
    the topology view can layer-group by provider host, not just name.
    `trusted_cidrs` overrides the settings-backed default (used in tests).
    """
    if trusted_cidrs is None:
        trusted_cidrs = get_trusted_cidrs()

    xff_raw = _headers_get(request, "X-Forwarded-For")
    forwarded_raw = _headers_get(request, "Forwarded")
    via_raw = _headers_get(request, "Via")
    x_real_ip = _headers_get(request, "X-Real-IP")

    xff_ips = _parse_xff(xff_raw)
    fwd_elements = _parse_forwarded(forwarded_raw)

    # RFC 7239 `Forwarded` takes precedence for the far-end identity when
    # present; extract its for= IPs and merge any that aren't already in XFF.
    forwarded_for_ips: list[str] = []
    for el in fwd_elements:
        raw_for = el.get("for", "")
        if not raw_for or raw_for.startswith("_") or raw_for.lower() == "unknown":
            continue
        ip = _strip_port(raw_for)
        if _is_valid_ip(ip):
            forwarded_for_ips.append(ip)

    # The "chain" we reason about for trust is the XFF list. If `Forwarded`
    # carries IPs not in XFF (rare — most stacks populate both identically),
    # prepend them to the far-end side so they get the same trust treatment.
    chain_ips: list[str] = []
    for ip in forwarded_for_ips:
        if ip not in xff_ips:
            chain_ips.append(ip)
    chain_ips.extend(xff_ips)

    # X-Real-IP is only useful if nothing else populated the chain (some
    # nginx configs set it but not XFF).
    if not chain_ips and _is_valid_ip(x_real_ip):
        chain_ips.append(x_real_ip)

    peer = _peer_ip(request)
    origin_ip, origin_class, origin_subnet = _pick_origin(
        chain_ips, peer, trusted_cidrs
    )

    # Build the display chain (client → us order) with per-hop source info.
    chain_display: list[dict] = []
    xff_set = set(xff_ips)
    fwd_set = {ip for ip in forwarded_for_ips}
    for ip in chain_ips:
        if ip in fwd_set and ip not in xff_set:
            source = "forwarded"
        else:
            source = "xff"
        role = "client" if chain_ips.index(ip) == 0 else "proxy"
        chain_display.append({"ip": ip, "role": role, "source": source})
    if peer:
        chain_display.append({"ip": peer, "role": "gateway", "source": "peer"})

    ua_tool, ua_version, ua_os = _parse_ua(_headers_get(request, "User-Agent"))
    upstream_host, upstream_region = _upstream_host(upstream_url)

    return NetworkOrigin(
        remote_addr=peer,
        forwarded_chain=chain_display,
        forwarded_for_raw=xff_raw[:500],
        forwarded_raw=forwarded_raw[:500],
        via_raw=via_raw[:500],
        origin_ip=origin_ip,
        origin_class=origin_class,
        origin_subnet=origin_subnet,
        ua_tool=ua_tool,
        ua_version=ua_version[:100],
        ua_os=ua_os[:100],
        upstream_host=upstream_host[:253],
        upstream_region=upstream_region[:50],
    )
