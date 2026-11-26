"""IPv4 addresses of this PC that a phone on the same network can reach."""

import ipaddress
import re
import socket

import psutil

_VIRTUAL_INTERFACE = re.compile(
    r"^(lo|docker|br-|veth|virbr|vmnet|vboxnet|tailscale|zt|wg|tun|tap|utun|ppp|ipsec)"
    r"|vEthernet|WSL|Hyper-V|VirtualBox|VMware|VPN|Tailscale|ZeroTier|WireGuard",
    re.IGNORECASE,
)
"""Loopback, container, virtual-machine and VPN adapters a phone on the Wi-Fi cannot reach."""

_ROUTE_PROBE = ("192.0.2.1", 9)
"""A documentation address (TEST-NET-1); connecting a UDP socket to it only looks up the route."""


def phone_addresses() -> list[str]:
    """Return the addresses a phone on the same network can use to reach this PC.

    Returns
    -------
    list of str
        IPv4 addresses of active, non-virtual interfaces, the one the system uses for outgoing
        traffic first.
    """
    stats = psutil.net_if_stats()
    interfaces = {
        name: [a.address for a in addresses if a.family == socket.AF_INET]
        for name, addresses in psutil.net_if_addrs().items()
        if name in stats and stats[name].isup
    }
    return rank_addresses(interfaces, _primary_address())


def rank_addresses(interfaces: dict[str, list[str]], primary: str | None) -> list[str]:
    """Order the addresses a phone could use to reach this PC.

    Parameters
    ----------
    interfaces : dict of str to list of str
        IPv4 addresses per active network interface.
    primary : str or None
        The address the system uses for outgoing traffic, if known.

    Returns
    -------
    list of str
        Reachable addresses, the primary one first.
    """
    reachable = [
        address
        for name, addresses in interfaces.items()
        if not _VIRTUAL_INTERFACE.search(name)
        for address in addresses
        if _reachable(address)
    ]
    if primary in reachable:
        reachable.remove(primary)
        reachable.insert(0, primary)
    return list(dict.fromkeys(reachable))


def _reachable(address: str) -> bool:
    """Tell whether another device on the network could reach an address.

    Parameters
    ----------
    address : str
        IPv4 address.

    Returns
    -------
    bool
        False for loopback, self-assigned (169.254.x.x), unspecified and multicast addresses.
    """
    ip = ipaddress.IPv4Address(address)
    return not (ip.is_loopback or ip.is_link_local or ip.is_unspecified or ip.is_multicast)


def _primary_address() -> str | None:
    """Return the address the system would use for outgoing traffic, without sending a packet.

    Returns
    -------
    str or None
        The address, or None without a network route.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        try:
            sock.connect(_ROUTE_PROBE)
        except OSError:
            return None
        return sock.getsockname()[0]
