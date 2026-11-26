"""Pairing: the one-time code and the link the pairing QR code holds.

The QR code holds a web page link with the pairing fields after ``#``:
``https://…#v=…&h=…&p=…&c=…``, the protocol version, the PC's addresses (comma-separated, best
first), the bridge's phone port and the one-time code. A normal camera app opens the web page,
and browsers never send the part after ``#`` to its server; the phone app's scanner reads the
fields and ignores the page.
"""

import base64
import secrets
from urllib.parse import quote, urlencode

PROTOCOL_VERSION = 2
PAIRING_PAGE = (
    "https://www.kickstarter.com/projects/rekonas/somnolinc-the-smart-headband-that-helps-you-sleep"
)
"""The web page someone lands on who scans the pairing QR code with a normal camera app."""


def new_code() -> str:
    """Return a fresh one-time pairing code.

    Returns
    -------
    str
        10 random bytes as 16 base32 characters.
    """
    return base64.b32encode(secrets.token_bytes(10)).decode()


def pairing_link(page: str, addresses: list[str], port: int, code: str) -> str:
    """Build the link the pairing QR code holds.

    Parameters
    ----------
    page : str
        The web page link before ``#``, normally ``PAIRING_PAGE``.
    addresses : list of str
        The PC's IPv4 addresses, best first.
    port : int
        The bridge's phone port.
    code : str
        One-time code.

    Returns
    -------
    str
        The link.
    """
    fields = {"v": PROTOCOL_VERSION, "h": ",".join(addresses), "p": port, "c": code}
    return f"{page}#{urlencode(fields, safe=',', quote_via=quote)}"
