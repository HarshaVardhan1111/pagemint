"""SSRF protection: only public http(s) destinations are allowed by default."""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from typing import Awaitable, Callable
from urllib.parse import urlsplit

from pagemint.errors import BlockedError, FetchError, InvalidURLError

Resolver = Callable[[str, int], Awaitable[list[str]]]

_CGNAT = ipaddress.ip_network("100.64.0.0/10")
_BLOCKED_NAMES = {"localhost", "localhost.localdomain", "ip6-localhost", "ip6-loopback"}
# Names that are only meaningful inside a private network.
_INTERNAL_SUFFIXES = (".internal", ".local", ".lan", ".corp", ".home.arpa", ".intranet", ".private", ".localdomain")

__all__ = ["BlockedError", "FetchError", "is_blocked_ip", "validate_url", "resolve_and_check", "system_resolver"]


def is_blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """True for any address that is not a normal public unicast address."""
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            return is_blocked_ip(ip.ipv4_mapped)
        if ip.sixtofour is not None and is_blocked_ip(ip.sixtofour):
            return True
    if (
        ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_multicast
        or ip.is_reserved or ip.is_unspecified or not ip.is_global
    ):
        return True
    return isinstance(ip, ipaddress.IPv4Address) and ip in _CGNAT


def validate_url(url: str) -> tuple[str, str, int]:
    """Check scheme and host. Returns ``(scheme, host, port)``."""
    try:
        parts = urlsplit(url)
        scheme = parts.scheme.lower()
        host = (parts.hostname or "").lower()
        port = parts.port
    except ValueError as e:
        raise InvalidURLError(f"Invalid URL '{url}': {e}.") from e
    if scheme not in ("http", "https"):
        raise InvalidURLError(f"Invalid URL '{url}'. Only http:// and https:// URLs are supported.")
    if not host:
        raise InvalidURLError(f"Invalid URL '{url}': no host name.")
    return scheme, host, port or (443 if scheme == "https" else 80)


async def system_resolver(host: str, port: int) -> list[str]:
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return list(dict.fromkeys(info[4][0] for info in infos))


async def resolve_and_check(
    host: str, port: int, allow_private: bool, resolver: Resolver | None = None, via_proxy: bool = False
) -> list[str]:
    """Resolve ``host`` and return its addresses, refusing private/internal ones.

    Every address must be public: a name that resolves to both a public and a
    private address is refused, since the connection could use either.

    ``via_proxy``: the request goes through a configured HTTP proxy, which does
    its own DNS. If the name cannot be resolved locally, the proxy is trusted to
    resolve it and ``[]`` is returned (IP literals and localhost are still checked).
    """
    resolver = resolver or system_resolver
    host = host.strip("[]").rstrip(".").lower()
    if not allow_private and (host in _BLOCKED_NAMES or host.endswith(".localhost")):
        raise BlockedError(
            f"Blocked '{host}': PageMint does not fetch localhost or private network addresses "
            "(set PAGEMINT_ALLOW_PRIVATE=1 to allow this on purpose)."
        )
    try:
        addrs = [str(ipaddress.ip_address(host))]
    except ValueError:
        try:
            addrs = await resolver(host, port)
        except (OSError, UnicodeError) as e:
            if via_proxy:
                if not allow_private and ("." not in host or host.endswith(_INTERNAL_SUFFIXES)):
                    raise BlockedError(
                        f"Blocked '{host}': it looks like an internal network name and cannot be checked "
                        "(set PAGEMINT_ALLOW_PRIVATE=1 to allow this on purpose)."
                    ) from e
                return []
            raise FetchError(f"Could not resolve host '{host}': {e}.") from e
    if not addrs:
        raise FetchError(f"Host '{host}' has no addresses.")
    if not allow_private:
        for a in addrs:
            ip = ipaddress.ip_address(a.split("%", 1)[0])
            if is_blocked_ip(ip):
                raise BlockedError(
                    f"Blocked '{host}' because it points to a private or internal address ({ip}). "
                    "PageMint only fetches public websites (set PAGEMINT_ALLOW_PRIVATE=1 to allow this on purpose)."
                )
    return addrs
