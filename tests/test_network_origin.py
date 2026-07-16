"""
Unit tests for kyde.network_origin. Pure Python — no DB, no FastAPI.

Covers:
- XFF / RFC 7239 Forwarded / Via parsing
- IP classification (public, rfc1918, cgnat, loopback, link-local, v6 ULA)
- Trusted-hop walker in _pick_origin (incl. spoof rejection)
- User-Agent pattern table
- Upstream host / region extraction
- The public parse_from_request entry point with a synthetic request
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from typing import Optional

import pytest

from kyde import network_origin as no

# ---------------------------------------------------------------------------
# Synthetic Starlette-like Request
# ---------------------------------------------------------------------------


class _Headers(dict):
    """Case-insensitive headers dict, mirroring Starlette's behavior."""

    def __init__(self, data: dict[str, str]):
        super().__init__({k.lower(): v for k, v in data.items()})

    def get(self, key: str, default: str = "") -> str:
        return super().get(key.lower(), default)

    def __contains__(self, key: object) -> bool:
        return super().__contains__(str(key).lower())


@dataclass
class _Client:
    host: str


@dataclass
class _Request:
    headers: _Headers
    client: Optional[_Client]


def _make_request(
    *,
    peer: Optional[str],
    xff: str = "",
    forwarded: str = "",
    via: str = "",
    x_real_ip: str = "",
    user_agent: str = "",
) -> _Request:
    headers = {"User-Agent": user_agent}
    if xff:
        headers["X-Forwarded-For"] = xff
    if forwarded:
        headers["Forwarded"] = forwarded
    if via:
        headers["Via"] = via
    if x_real_ip:
        headers["X-Real-IP"] = x_real_ip
    return _Request(
        headers=_Headers(headers),
        client=_Client(host=peer) if peer else None,
    )


def _cidrs(*strs: str) -> list[ipaddress._BaseNetwork]:
    return [ipaddress.ip_network(s, strict=False) for s in strs]


# ---------------------------------------------------------------------------
# _parse_xff
# ---------------------------------------------------------------------------


class TestParseXFF:
    def test_empty(self):
        assert no._parse_xff("") == []

    def test_single(self):
        assert no._parse_xff("1.2.3.4") == ["1.2.3.4"]

    def test_whitespace_and_order(self):
        assert no._parse_xff("1.2.3.4, 10.0.0.1, 10.0.0.2") == [
            "1.2.3.4",
            "10.0.0.1",
            "10.0.0.2",
        ]

    def test_drops_unknown_literal(self):
        assert no._parse_xff("unknown, 1.2.3.4") == ["1.2.3.4"]

    def test_drops_empty_tokens(self):
        assert no._parse_xff("1.2.3.4,, ,5.6.7.8") == ["1.2.3.4", "5.6.7.8"]

    def test_drops_invalid_tokens(self):
        assert no._parse_xff("garbage, 1.2.3.4, 999.999.999.999") == ["1.2.3.4"]

    def test_strips_v4_port(self):
        assert no._parse_xff("1.2.3.4:12345") == ["1.2.3.4"]

    def test_bare_ipv6(self):
        assert no._parse_xff("2001:db8::1, 10.0.0.1") == ["2001:db8::1", "10.0.0.1"]

    def test_bracketed_ipv6_with_port(self):
        assert no._parse_xff("[2001:db8::1]:4711") == ["2001:db8::1"]

    def test_bracketed_ipv6_no_port(self):
        assert no._parse_xff("[2001:db8::1]") == ["2001:db8::1"]


# ---------------------------------------------------------------------------
# _parse_forwarded (RFC 7239)
# ---------------------------------------------------------------------------


class TestParseForwarded:
    def test_empty(self):
        assert no._parse_forwarded("") == []

    def test_simple_pair_list(self):
        el = no._parse_forwarded("for=192.0.2.60;proto=http;by=203.0.113.43")
        assert el == [{"for": "192.0.2.60", "proto": "http", "by": "203.0.113.43"}]

    def test_quoted_ipv6_with_port(self):
        el = no._parse_forwarded('for="[2001:db8::1]:4711"')
        assert el == [{"for": "[2001:db8::1]:4711"}]

    def test_multiple_elements(self):
        el = no._parse_forwarded("for=192.0.2.43, for=198.51.100.17")
        assert el == [{"for": "192.0.2.43"}, {"for": "198.51.100.17"}]

    def test_obfuscated_for_kept_as_is(self):
        # _hidden / _obfuscatedid — kept in the parsed dict so the downstream
        # chain walker can decide; it will skip them for origin picking.
        el = no._parse_forwarded("for=_hidden")
        assert el == [{"for": "_hidden"}]

    def test_missing_for(self):
        el = no._parse_forwarded("proto=https;by=203.0.113.43")
        assert el == [{"proto": "https", "by": "203.0.113.43"}]

    def test_case_insensitive_key(self):
        el = no._parse_forwarded("For=1.2.3.4;BY=5.6.7.8")
        assert el == [{"for": "1.2.3.4", "by": "5.6.7.8"}]


# ---------------------------------------------------------------------------
# _parse_via
# ---------------------------------------------------------------------------


class TestParseVia:
    def test_empty(self):
        assert no._parse_via("") == []

    def test_simple(self):
        assert no._parse_via("1.1 vegur") == ["vegur"]

    def test_with_version_and_comment(self):
        assert no._parse_via("HTTP/1.1 proxy.example.com:8080 (squid/3.5)") == [
            "proxy.example.com:8080"
        ]

    def test_multiple(self):
        got = no._parse_via("1.1 edge, 1.1 core.example.com")
        assert got == ["edge", "core.example.com"]


# ---------------------------------------------------------------------------
# _classify
# ---------------------------------------------------------------------------


class TestClassify:
    @pytest.mark.parametrize(
        "ip,expected",
        [
            ("127.0.0.1", "loopback"),
            ("::1", "loopback"),
            ("10.1.2.3", "rfc1918"),
            ("172.16.99.1", "rfc1918"),
            ("192.168.1.1", "rfc1918"),
            ("100.64.0.5", "cgnat"),
            ("169.254.1.5", "link_local"),
            ("fe80::1", "link_local"),
            ("fc00::1", "unique_local_v6"),
            ("fd12:3456:789a::1", "unique_local_v6"),
            ("8.8.8.8", "public"),
            ("2001:db8::1", "public"),
            ("", "unknown"),
            ("not-an-ip", "unknown"),
        ],
    )
    def test_class(self, ip, expected):
        assert no._classify(ip) == expected


# ---------------------------------------------------------------------------
# _subnet_of
# ---------------------------------------------------------------------------


class TestSubnetOf:
    def test_v4(self):
        assert no._subnet_of("10.4.0.17") == "10.4.0.0/24"

    def test_v6(self):
        # /48 rollup
        assert no._subnet_of("2001:db8:abcd:1234::1") == "2001:db8:abcd::/48"

    def test_empty_or_invalid(self):
        assert no._subnet_of("") == ""
        assert no._subnet_of("nope") == ""


# ---------------------------------------------------------------------------
# _pick_origin — the core trust walker
# ---------------------------------------------------------------------------


TRUSTED = _cidrs("127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")


class TestPickOrigin:
    def test_all_public_chain_with_trusted_peer(self):
        # Chain: 203.0.113.5 → 198.51.100.9 → peer 10.0.0.1 (trusted)
        # Walking right-to-left: peer trusted → 198.51.100.9 valid → is 198.51.100.9 trusted? NO.
        # So trusted_chain = [198.51.100.9]. Public → origin = 198.51.100.9.
        ip, cls, subnet = no._pick_origin(
            ["203.0.113.5", "198.51.100.9"], "10.0.0.1", TRUSTED
        )
        assert ip == "198.51.100.9"
        assert cls == "public"
        assert subnet == "198.51.100.0/24"

    def test_all_private_chain(self):
        # Chain: 10.4.0.17 → 10.0.0.5 → peer 10.0.0.6. All trusted.
        # trusted_chain = [10.4.0.17, 10.0.0.5]. No public IP → use leftmost.
        ip, cls, subnet = no._pick_origin(
            ["10.4.0.17", "10.0.0.5"], "10.0.0.6", TRUSTED
        )
        assert ip == "10.4.0.17"
        assert cls == "rfc1918"
        assert subnet == "10.4.0.0/24"

    def test_mixed_chain_public_inside_trusted_zone(self):
        # Chain: 203.0.113.9 → 10.0.0.5 → peer 10.0.0.6. All trusted.
        # First public walking left→right is 203.0.113.9.
        ip, cls, subnet = no._pick_origin(
            ["203.0.113.9", "10.0.0.5"], "10.0.0.6", TRUSTED
        )
        assert ip == "203.0.113.9"
        assert cls == "public"
        assert subnet == "203.0.113.0/24"

    def test_spoofed_first_hop_from_untrusted_peer(self):
        # An attacker on the public internet (peer = 9.9.9.9) sets XFF to
        # "1.2.3.4" hoping we treat that as the origin. Peer is NOT in
        # trusted_cidrs → trusted_chain stays empty → we fall back to peer.
        ip, cls, _ = no._pick_origin(["1.2.3.4"], "9.9.9.9", TRUSTED)
        assert ip == "9.9.9.9"
        assert cls == "public"

    def test_ipv6_chain(self):
        trusted_v6 = _cidrs("fc00::/7", "::1/128")
        ip, cls, subnet = no._pick_origin(
            ["2001:db8::1", "fc00::5"], "fc00::6", trusted_v6
        )
        assert ip == "2001:db8::1"
        assert cls == "public"
        assert subnet == "2001:db8::/48"

    def test_empty_chain_falls_back_to_peer(self):
        ip, cls, _ = no._pick_origin([], "10.0.0.6", TRUSTED)
        assert ip == "10.0.0.6"
        assert cls == "rfc1918"

    def test_no_peer_no_chain(self):
        ip, cls, subnet = no._pick_origin([], None, TRUSTED)
        assert ip is None
        assert cls == "unknown"
        assert subnet == ""


# ---------------------------------------------------------------------------
# _parse_ua
# ---------------------------------------------------------------------------


class TestParseUA:
    @pytest.mark.parametrize(
        "ua,expected_tool",
        [
            ("kyde-gateway/1.2.3", "kyde-gateway"),
            ("kyde-gateway/0.1.0+rc1 (linux)", "kyde-gateway"),
            ("Cursor/0.42.3", "cursor"),
            ("GithubCopilot/1.4.2 (Windows)", "copilot"),
            ("GitHub-Copilot-Chat/1.0.0", "copilot"),
            ("claude-code/0.9.1 node/22", "claude-code"),
            ("Continue/0.8.40", "continue"),
            ("aider/0.50.1", "aider"),
            ("OpenAI/Python 1.12.0", "openai-sdk"),
            ("openai-python/1.12.0", "openai-sdk"),
            ("anthropic-sdk-python/0.34.0", "anthropic-sdk"),
            ("anthropic-typescript/0.20.1", "anthropic-sdk"),
            ("google-generativeai/0.5.4", "google-genai"),
            ("curl/8.4.0", "curl"),
            ("", "unknown"),
            ("Mozilla/5.0 something weird", "unknown"),
        ],
    )
    def test_tool_detection(self, ua, expected_tool):
        tool, _ver, _os = no._parse_ua(ua)
        assert tool == expected_tool

    def test_version_capture(self):
        tool, ver, _ = no._parse_ua("Cursor/0.42.3")
        assert tool == "cursor"
        assert ver == "0.42.3"

    def test_os_from_parenthetical(self):
        _, _, os_str = no._parse_ua("Cursor/0.42.3 (Macintosh; Intel Mac OS X 10_15)")
        assert "Macintosh" in os_str


# ---------------------------------------------------------------------------
# _upstream_host
# ---------------------------------------------------------------------------


class TestUpstreamHost:
    def test_openai(self):
        host, region = no._upstream_host("https://api.openai.com/v1/chat/completions")
        assert host == "api.openai.com"
        assert region == ""

    def test_aws_region(self):
        host, region = no._upstream_host(
            "https://bedrock-runtime.us-east-1.amazonaws.com/model/invoke"
        )
        assert host == "bedrock-runtime.us-east-1.amazonaws.com"
        assert region == "us-east-1"

    def test_azure_region(self):
        host, region = no._upstream_host(
            "https://my-resource.eastus2.api.cognitive.microsoft.com/openai/deployments/gpt4/chat/completions"
        )
        assert host.startswith("my-resource.eastus2")
        assert region == "eastus2"

    def test_empty(self):
        assert no._upstream_host("") == ("", "")


# ---------------------------------------------------------------------------
# parse_cidr_list + get_trusted_cidrs
# ---------------------------------------------------------------------------


class TestParseCIDRList:
    def test_good(self):
        out = no.parse_cidr_list("10.0.0.0/8, 192.168.0.0/16")
        assert len(out) == 2

    def test_blank_tokens_ignored(self):
        out = no.parse_cidr_list("10.0.0.0/8, , ,192.168.0.0/16")
        assert len(out) == 2

    def test_invalid_raises(self):
        with pytest.raises(ValueError):
            no.parse_cidr_list("10.0.0.0/8, not-a-cidr")


# ---------------------------------------------------------------------------
# parse_from_request — full pipeline
# ---------------------------------------------------------------------------


class TestParseFromRequest:
    def test_corporate_proxy_chain(self):
        """Typical enterprise: client → corp LB → us. Origin is the user's IP."""
        req = _make_request(
            peer="10.0.0.6",
            xff="203.0.113.5, 10.0.0.5",
            user_agent="Cursor/0.42.3 (Macintosh; Intel Mac OS X 10_15)",
        )
        origin = no.parse_from_request(
            req,
            upstream_url="https://api.openai.com/v1/chat/completions",
            trusted_cidrs=TRUSTED,
        )
        assert origin.remote_addr == "10.0.0.6"
        assert origin.origin_ip == "203.0.113.5"
        assert origin.origin_class == "public"
        assert origin.origin_subnet == "203.0.113.0/24"
        assert origin.ua_tool == "cursor"
        assert origin.ua_version == "0.42.3"
        assert origin.upstream_host == "api.openai.com"
        # Display chain includes every hop plus the peer.
        ips = [h["ip"] for h in origin.forwarded_chain]
        assert ips == ["203.0.113.5", "10.0.0.5", "10.0.0.6"]
        assert origin.forwarded_chain[0]["role"] == "client"
        assert origin.forwarded_chain[-1]["source"] == "peer"

    def test_no_proxy_direct_client(self):
        """Client talks to us directly — peer IS the origin."""
        req = _make_request(peer="8.8.8.8", user_agent="curl/8.4.0")
        origin = no.parse_from_request(
            req,
            upstream_url="https://api.anthropic.com/v1/messages",
            trusted_cidrs=TRUSTED,
        )
        # peer is 8.8.8.8 — not in trusted_cidrs, but the XFF chain is empty
        # so there's nothing to validate; fallback uses the peer directly.
        assert origin.origin_ip == "8.8.8.8"
        assert origin.origin_class == "public"
        assert origin.ua_tool == "curl"

    def test_spoofed_xff_from_public_peer_is_rejected(self):
        req = _make_request(peer="9.9.9.9", xff="1.2.3.4", user_agent="curl/8.4.0")
        origin = no.parse_from_request(
            req,
            upstream_url="https://api.openai.com/",
            trusted_cidrs=TRUSTED,
        )
        assert origin.origin_ip == "9.9.9.9"  # peer wins — XFF dropped

    def test_x_real_ip_only(self):
        """Some nginx configs set only X-Real-IP. Treat it as a one-element chain."""
        req = _make_request(peer="10.0.0.6", x_real_ip="203.0.113.42")
        origin = no.parse_from_request(
            req,
            upstream_url="https://api.openai.com/",
            trusted_cidrs=TRUSTED,
        )
        assert origin.origin_ip == "203.0.113.42"
        assert origin.origin_class == "public"

    def test_forwarded_header_takes_precedence_over_xff(self):
        """When RFC 7239 Forwarded carries an IP not in XFF, it joins the chain."""
        req = _make_request(
            peer="10.0.0.6",
            xff="10.0.0.5",
            forwarded='for="198.51.100.7"',
            user_agent="OpenAI/Python 1.12.0",
        )
        origin = no.parse_from_request(
            req,
            upstream_url="https://api.openai.com/v1/chat/completions",
            trusted_cidrs=TRUSTED,
        )
        assert origin.origin_ip == "198.51.100.7"
        assert origin.ua_tool == "openai-sdk"
