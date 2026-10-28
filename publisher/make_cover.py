"""Render the Facebook cover (brand: Ana w Wlidi design system) so it survives every Facebook layout.

The image is 820x360 at 2x (1640x720). Facebook shows it three ways:
  - desktop: 820x312, top/bottom 24px trimmed
  - older phone layout: 640x360, left/right 90px trimmed
  - current phone layout: the same 640x360 view, but the status bar and buttons cover the top ~60px
    and the round profile picture (about 272px across, centered) covers the lower middle from y~212 down.
So all text sits in the band above the profile picture (y 62..190 at 1x), centered, and only soft
decoration lives in the bottom corners.

    python publisher/make_cover.py   # writes publisher/assets/cover_fb.png + cover_preview.png
"""

import base64
from pathlib import Path

from playwright.sync_api import sync_playwright
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent
FONTS = ROOT / "assets" / "fonts"
OUT = ROOT / "assets"
W, H = 1640, 720

TITLE = "لكل يمّا جزائرية،<br>من الحمل لحتى 3 سنين"
SUBTITLE = "نصائح، قصص حقيقية، و دعم بحنان"


def font_face(family, file, weight):
    data = base64.b64encode((FONTS / file).read_bytes()).decode()
    return (
        f"@font-face{{font-family:'{family}';font-weight:{weight};"
        f"src:url(data:font/woff2;base64,{data}) format('woff2');}}"
    )


def motif(color, size, style):
    """The logo's arc + two circles, used as quiet decoration."""
    return (
        f'<svg style="position:absolute;{style}" width="{size}" height="{size}" viewBox="0 0 120 120">'
        f'<path d="M21 60A40 40 0 0 0 98 64" fill="none" stroke="{color}" stroke-width="10" stroke-linecap="round"/>'
        f'<circle cx="44" cy="45" r="25" fill="{color}"/><circle cx="79" cy="61" r="12.5" fill="{color}"/></svg>'
    )


HTML = f"""<!doctype html><html lang="ar" dir="rtl"><head><meta charset="utf-8"><style>
{font_face("Marhey", "marhey-arabic.woff2", "300 700")}
{font_face("Marhey", "marhey-latin.woff2", "300 700")}
{font_face("Rubik", "rubik-arabic.woff2", "300 900")}
:root{{--rose-100:#F6DCE0;--rose-200:#EDBFC7;--rose-700:#7E3A4B;--sand-100:#FBF6EE;--clay-600:#7A5240}}
*{{margin:0;box-sizing:border-box}}
html{{overflow:hidden}}
body{{width:{W}px;height:{H}px;background:var(--sand-100);position:relative;overflow:hidden}}
.text{{position:absolute;left:270px;width:1100px;top:136px;text-align:center}}
h1{{font-family:Marhey;font-weight:600;font-size:68px;line-height:1.3;color:var(--rose-700)}}
p{{font-family:Rubik;font-weight:400;font-size:30px;color:var(--clay-600);margin-top:12px}}
</style></head><body>
{motif("#F6DCE0", 640, "left:-150px;bottom:-250px")}
{motif("#EDBFC7", 520, "right:-120px;bottom:-190px;opacity:.8")}
<div class="text"><h1>{TITLE}</h1><p>{SUBTITLE}</p></div>
</body></html>"""


def _chromium():
    """Any locally installed Playwright Chromium (the pinned playwright build may not be installed)."""
    root = Path.home() / "AppData" / "Local" / "ms-playwright"
    found = sorted(root.glob("chromium-*/chrome-win64/chrome.exe"))
    return str(found[-1]) if found else None


def _phone_mockup(cover):
    """640x360 phone view with the status-bar zone and the round profile picture drawn over it."""
    view = cover.crop((180, 0, W - 180, H)).resize((640, 360)).convert("RGBA")
    over = Image.new("RGBA", view.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(over)
    d.rectangle((0, 0, 640, 60), fill=(30, 30, 30, 120))  # status bar + back/share/search buttons
    cx, cy, r = 320, 348, 136
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(184, 92, 114, 235), outline=(40, 40, 44, 255), width=8)
    return Image.alpha_composite(view, over).convert("RGB")


def main():
    OUT.mkdir(exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=_chromium())
        page = browser.new_page(viewport={"width": W, "height": H})
        page.set_content(HTML)
        page.evaluate("document.fonts.ready")
        page.wait_for_timeout(500)
        page.screenshot(path=str(OUT / "cover_fb.png"))
        browser.close()

    cover = Image.open(OUT / "cover_fb.png").convert("RGB")
    desktop = cover.crop((0, 48, W, H - 48)).resize((820, 312))
    sheet = Image.new("RGB", (820 + 640 + 60, 360 + 40), (255, 255, 255))
    sheet.paste(desktop, (20, 20))
    sheet.paste(_phone_mockup(cover), (820 + 40, 20))
    sheet.save(OUT / "cover_preview.png")
    print("wrote", OUT / "cover_fb.png", cover.size)


if __name__ == "__main__":
    main()
