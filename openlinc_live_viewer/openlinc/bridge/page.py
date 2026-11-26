"""The pairing page: a local HTML file with the QR code, in Somnolinc style.

The bridge opens it in the browser once and afterwards only rewrites the file when what it shows
changes; reloading the page shows the update. The page is self-contained: the wordmark, the app
icon and the font are embedded, copied unchanged from the phone app's resources (``assets/``). The
font is Bai Jamjuree, whose licence (``assets/OFL.txt``) allows sharing it with the bridge.
"""

import base64
import html
from functools import cache
from importlib.resources import files
from pathlib import Path

import segno

QUIET_ZONE = 4
"""Blank modules around the QR code, as the QR standard asks."""
LOGO_SHARE = 0.22
"""Width of the app icon in the middle, as a share of the code's width. The highest error
correction level restores the covered modules."""
DOT_RADIUS = 0.45
"""Radius of a data dot, in modules."""

DOT_COLOR = "#16162A"
"""The app's screen background: a near-black violet."""
EYE_COLOR = "#6662AC"
"""The wordmark's violet."""
ICON_BACKGROUND = "#D9C8F6"
"""The app icon's lavender background."""

_SCAN = "Scan the QR code from within your Somnolinc App to connect."
_NO_CODE = "A phone is already connected, so there is no code to scan."
"""What the page says under the code, and what it says when the bridge has no code to show."""

_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>OpenLinc</title>
<style>
  @font-face {{ font-family: 'Bai Jamjuree'; font-weight: 400;
               src: url(data:font/ttf;base64,{bai_jamjuree}) format('truetype'); }}
  @font-face {{ font-family: 'Bai Jamjuree'; font-weight: 700;
               src: url(data:font/ttf;base64,{bai_jamjuree_bold}) format('truetype'); }}
  html {{ min-height: 100%; }}
  body {{
    margin: 0; min-height: 100vh; box-sizing: border-box; padding: 2.5rem 1rem;
    display: flex; flex-direction: column; align-items: center; gap: 1.75rem;
    color: #F8F6FF; font-family: 'Bai Jamjuree', system-ui, sans-serif;
    background:
      radial-gradient(60rem 40rem at 10% 0%, rgba(138, 130, 255, 0.22), transparent 70%),
      radial-gradient(50rem 40rem at 100% 60%, rgba(199, 125, 255, 0.14), transparent 70%),
      #05060C;
  }}
  .wordmark svg {{ display: block; height: 1.75rem; width: auto; }}
  .card {{
    display: flex; flex-direction: column; align-items: center; gap: 1.25rem;
    padding: 1.75rem; border-radius: 28px; max-width: 34rem;
    border: 1px solid rgba(255, 255, 255, 0.08);
    background: linear-gradient(rgba(255, 255, 255, 0.04), rgba(255, 255, 255, 0.02));
    box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.06), 0 30px 80px rgba(0, 0, 0, 0.45);
  }}
  svg.qr {{ display: block; width: min(80vw, 60vh); height: auto; border-radius: 20px; }}
  p {{ margin: 0; max-width: 28rem; text-align: center;
       font-size: 1.1rem; line-height: 1.5; color: rgba(248, 246, 255, 0.7); }}
</style>
</head>
<body>
<div class="wordmark">{wordmark}</div>
<main class="card">
{qr}
<p>{text}</p>
</main>
</body>
</html>
"""


def render(link: str | None) -> str:
    """Render the pairing page.

    Parameters
    ----------
    link : str or None
        The link for the QR code, or None when no code is shown.

    Returns
    -------
    str
        The HTML page.
    """
    return _TEMPLATE.format(
        bai_jamjuree=_asset_base64("baijamjuree_regular.ttf"),
        bai_jamjuree_bold=_asset_base64("baijamjuree_bold.ttf"),
        wordmark=_asset_text("somnolinc-wordmark.svg"),
        qr="" if link is None else qr_svg(link),
        text=html.escape(_NO_CODE if link is None else _SCAN),
    )


def qr_svg(link: str) -> str:
    """Draw a QR code in Somnolinc style: round dots, rounded violet eyes, the app icon inside.

    Parameters
    ----------
    link : str
        The text the code holds.

    Returns
    -------
    str
        An SVG element that scales with its container.
    """
    matrix = segno.make(link, error="h", boost_error=False).matrix
    size = len(matrix)
    logo = int(size * LOGO_SHARE) | 1  # odd, so it centres on a module
    logo_start = (size - logo) // 2
    eyes = [(0, 0), (size - 7, 0), (0, size - 7)]

    def hidden(x: int, y: int) -> bool:
        in_eye = any(ex <= x < ex + 7 and ey <= y < ey + 7 for ex, ey in eyes)
        in_logo = logo_start <= x < logo_start + logo and logo_start <= y < logo_start + logo
        return in_eye or in_logo

    dots = "".join(
        f'<circle cx="{x + 0.5}" cy="{y + 0.5}" r="{DOT_RADIUS}"/>'
        for y, row in enumerate(matrix)
        for x, dark in enumerate(row)
        if dark and not hidden(x, y)
    )
    eye_shapes = "".join(
        f'<rect x="{ex + 0.5}" y="{ey + 0.5}" width="6" height="6" rx="1.8" fill="none" '
        f'stroke="{EYE_COLOR}" stroke-width="1"/>'
        f'<rect x="{ex + 2}" y="{ey + 2}" width="3" height="3" rx="0.9" fill="{EYE_COLOR}"/>'
        for ex, ey in eyes
    )
    icon = (
        f'<rect x="{logo_start}" y="{logo_start}" width="{logo}" height="{logo}" '
        f'rx="{logo * 0.22}" fill="{ICON_BACKGROUND}"/>'
        f'<image x="{logo_start}" y="{logo_start}" width="{logo}" height="{logo}" '
        f'xlink:href="data:image/png;base64,{_asset_base64("app-icon-foreground.png")}"/>'
    )
    full = size + 2 * QUIET_ZONE
    return (
        f'<svg class="qr" xmlns="http://www.w3.org/2000/svg" '
        f'xmlns:xlink="http://www.w3.org/1999/xlink" '
        f'viewBox="{-QUIET_ZONE} {-QUIET_ZONE} {full} {full}" role="img" '
        f'aria-label="Pairing code">'
        f'<rect x="{-QUIET_ZONE}" y="{-QUIET_ZONE}" width="{full}" height="{full}" fill="#FFFFFF"/>'
        f'<g fill="{DOT_COLOR}">{dots}</g>{eye_shapes}{icon}</svg>'
    )


@cache
def _asset_base64(name: str) -> str:
    """Return a bundled asset as base64.

    Parameters
    ----------
    name : str
        File name in ``assets/``.

    Returns
    -------
    str
        The file's bytes, base64-encoded.
    """
    return base64.b64encode((files("openlinc.bridge") / "assets" / name).read_bytes()).decode()


@cache
def _asset_text(name: str) -> str:
    """Return a bundled text asset.

    Parameters
    ----------
    name : str
        File name in ``assets/``.

    Returns
    -------
    str
        The file's text.
    """
    return (files("openlinc.bridge") / "assets" / name).read_text(encoding="utf-8")


class PairingPage:
    """Keeps the pairing page file in step with the bridge.

    Parameters
    ----------
    path : Path
        Where the page is written.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._shown: tuple[str | None] | None = None

    def update(self, link: str | None) -> bool:
        """Rewrite the page if what it shows has changed.

        Parameters
        ----------
        link : str or None
            The link for the QR code, or None when no code is shown.

        Returns
        -------
        bool
            Whether the file was written.
        """
        if self._shown == (link,):
            return False
        self.path.write_text(render(link), encoding="utf-8")
        self._shown = (link,)
        return True
