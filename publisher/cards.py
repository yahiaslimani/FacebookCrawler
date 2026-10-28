"""Render 1080x1350 (4:5) post cards in the Ana w Wlidi brand (Marhey + Rubik, rose/sage/sand/clay,
logo mark), used as the Instagram image and the Facebook photo.

4:5 is the Instagram feed format. The profile grid center-crops it to 3:4, so all content stays inside
the central SAFE_W pixels. Cards are HTML rendered with a local Chromium (same approach as make_cover.py),
so Arabic shaping and the brand fonts are exact.
"""

import atexit
import base64
import html
import re
from pathlib import Path

from PIL import Image, ImageStat
from playwright.sync_api import sync_playwright

W, H = 1080, 1350
SAFE_W = 860
ROOT = Path(__file__).resolve().parent
FONTS = ROOT / "assets" / "fonts"
PAGE_NAME = "أنا و وليدي"
IG_HANDLE = "@ana.wlidi"

# kind: background, decorative circle, ink (headline), accent, text on accent, pill label
THEMES = {
    "list": ("#FBF6EE", "#F6DCE0", "#7E3A4B", "#B85C72", "#FBF6EE", "للأمهات"),
    "reassure": ("#F6DCE0", "#EDBFC7", "#7E3A4B", "#B85C72", "#FBF6EE", "كلمة من القلب"),
    "personal": ("#EAF2E9", "#D6E6D3", "#3C5E3A", "#6E9A6A", "#FEFBF7", "لحظة صادقة"),
    "family": ("#F4ECE0", "#E7DBCA", "#3E2A20", "#B98A72", "#FEFBF7", "العيلة"),
    "short": ("#FDF3F5", "#F6DCE0", "#9E4A5F", "#CB7A8C", "#FFFFFF", "من القلب"),
    "question": ("#3E2A20", "#5A3B2D", "#FBF6EE", "#DE9CA9", "#3E2A20", "شاركينا"),
}
SAND = "#FBF6EE"
SCRIM = "linear-gradient(to top, rgba(62,42,32,.78) 0%, rgba(62,42,32,.46) 45%, rgba(62,42,32,.18) 100%)"


def _font_face(family, file, weight):
    data = base64.b64encode((FONTS / file).read_bytes()).decode()
    return f"@font-face{{font-family:'{family}';font-weight:{weight};src:url(data:font/woff2;base64,{data}) format('woff2');}}"


_FONT_CSS = None


def _fonts():
    global _FONT_CSS
    if _FONT_CSS is None:
        _FONT_CSS = "".join(
            [
                _font_face("Marhey", "marhey-arabic.woff2", "300 700"),
                _font_face("Marhey", "marhey-latin.woff2", "300 700"),
                _font_face("Rubik", "rubik-arabic.woff2", "300 900"),
            ]
        )
    return _FONT_CSS


def _logo(color, size):
    return (
        f'<svg width="{size}" height="{size}" viewBox="0 0 120 120"><path d="M21 60A40 40 0 0 0 98 64" fill="none" '
        f'stroke="{color}" stroke-width="10" stroke-linecap="round"/><circle cx="44" cy="45" r="25" fill="{color}"/>'
        f'<circle cx="79" cy="61" r="12.5" fill="{color}"/></svg>'
    )


# ---------------------------------------------------------------- browser
_state = {}


def _chromium():
    root = Path.home() / "AppData" / "Local" / "ms-playwright"
    found = sorted(root.glob("chromium-*/chrome-win64/chrome.exe"))
    return str(found[-1]) if found else None


def _page():
    if "page" not in _state:
        pw = sync_playwright().start()
        browser = pw.chromium.launch(executable_path=_chromium())
        _state.update(pw=pw, browser=browser, page=browser.new_page(viewport={"width": W, "height": H}))
        atexit.register(_close)
    return _state["page"]


def _close():
    try:
        _state["browser"].close()
        _state["pw"].stop()
    except Exception:  # noqa: BLE001 - best effort at interpreter exit
        pass
    _state.clear()


FIT_JS = """() => {
  for (const el of document.querySelectorAll('[data-fit]')) {
    let s = parseInt(el.dataset.fit); el.style.fontSize = s + 'px';
    const box = el.parentElement;
    while (s > 44 && (el.offsetHeight > box.clientHeight || el.scrollWidth > box.clientWidth + 1)) {
      s -= 4; el.style.fontSize = s + 'px';
    }
  }
}"""


def _blank_rows(img, from_top, max_frac=0.35, tol_std=7, tol_mean=10):
    """How many rows (as a fraction of the height) at the top/bottom are a flat band of one constant colour."""
    small = img.resize((160, max(40, int(img.height * 160 / img.width))))
    rows = range(small.height) if from_top else range(small.height - 1, -1, -1)
    ref = count = None
    for y in rows:
        st = ImageStat.Stat(small.crop((0, y, small.width, y + 1)))
        mean = st.mean
        if max(st.stddev) > tol_std or (ref is not None and max(abs(a - b) for a, b in zip(mean, ref)) > tol_mean):
            break
        ref = ref or mean
        count = (count or 0) + 1
        if count / small.height > max_frac:
            break
    return (count or 0) / small.height


def trim_blank_borders(path, min_frac=0.04):
    """Pictures made with an AI tool sometimes come with a flat blank band at the top and bottom (an "empty space for
    text" taken literally). Return a copy without those bands, or the original when there are none. Cached by file time."""
    path = Path(path)
    cache = ROOT / "cards" / "_trim" / f"{path.stem}_{path.stat().st_mtime_ns}.jpg"
    if cache.exists():
        return cache
    img = Image.open(path).convert("RGB")
    top, bottom = _blank_rows(img, True), _blank_rows(img, False)
    left, right = _blank_rows(img.rotate(-90, expand=True), True), _blank_rows(img.rotate(90, expand=True), True)  # side bands
    if max(top, bottom, left, right) < min_frac:
        return path
    cut = lambda frac, size: int(size * frac) if frac >= min_frac else 0  # noqa: E731 - ignore tiny bands
    box = (cut(left, img.width), cut(top, img.height), img.width - cut(right, img.width), img.height - cut(bottom, img.height))
    cache.parent.mkdir(parents=True, exist_ok=True)
    img.crop(box).save(cache, "JPEG", quality=93)
    return cache


def _prepare_photo(photo):
    """Stock photos are used as they are; the owner's generated pictures get blank borders trimmed."""
    return trim_blank_borders(photo) if Path(photo).parent.name == "generated" else Path(photo)


def _render(body, out_path, bg, deco=None, photo=None):
    """Wrap `body` in the card chrome (background, optional photo + scrim, decoration) and screenshot it."""
    layers = ""
    if photo:
        b64 = base64.b64encode(_prepare_photo(photo).read_bytes()).decode()
        layers = (
            f'<div style="position:absolute;inset:0;background:url(data:image/jpeg;base64,{b64}) center/cover"></div>'
            f'<div style="position:absolute;inset:0;background:{SCRIM}"></div>'
        )
    elif deco:
        layers = (
            '<div style="position:absolute;inset:0;overflow:hidden">'
            f'<div style="position:absolute;left:-230px;top:-230px;width:640px;height:640px;border-radius:50%;background:{deco};opacity:.7"></div>'
            f'<div style="position:absolute;right:-300px;bottom:-260px;width:760px;height:760px;border-radius:50%;background:{deco};opacity:.55"></div>'
            "</div>"
        )
    doc = f"""<!doctype html><html lang="ar" dir="rtl"><head><meta charset="utf-8"><style>
{_fonts()}
*{{margin:0;box-sizing:border-box}}
html{{overflow:hidden}}
body{{width:{W}px;height:{H}px;background:{bg};position:relative;overflow:hidden;font-family:Rubik}}
.col{{position:absolute;inset:0;padding:120px {(W - SAFE_W) // 2}px 110px;display:flex;flex-direction:column;align-items:center}}
.h{{font-family:Marhey;font-weight:600;line-height:1.4;text-align:center;width:100%}}
.fit{{flex:1;min-height:0;display:flex;align-items:center;justify-content:center;width:100%}}
.pill{{font-family:Marhey;font-weight:600;font-size:40px;padding:16px 44px;border-radius:999px}}
.foot{{display:flex;align-items:center;gap:20px;font-family:Marhey;font-weight:600;font-size:46px}}
</style></head><body>{layers}<div class="col">{body}</div></body></html>"""
    page = _page()
    page.set_content(doc)
    page.evaluate("document.fonts.ready")
    page.evaluate(FIT_JS)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(out_path), type="jpeg", quality=92)
    return out_path


def _strip_emoji(text):
    return "".join(ch for ch in text if ord(ch) < 0x1F000).strip()


def _colors(kind, photo):
    bg, deco, ink, accent, on_accent, label = THEMES.get(kind, THEMES["list"])
    if photo:
        ink = SAND
    return bg, deco, ink, accent, on_accent, label


def _footer(ink, accent):
    return f'<div class="foot" style="color:{ink};margin-top:20px">{_logo(accent, 64)}<span>{PAGE_NAME}</span></div>'


def _headline(text, ink, size, bottom=False):
    """Headline that shrinks to fit its box; `bottom` pins it to the lower edge (over a photo's dark band)."""
    align = "flex-end" if bottom else "center"
    return (
        f'<div class="fit" style="align-items:{align}"><div class="h" data-fit="{size}" '
        f'style="color:{ink}">{html.escape(text)}</div></div>'
    )


def parse_list(text):
    """Split a numbered-list post into (items, outro lines). Items are the "1- ..." lines."""
    items, outro = [], []
    for line in text.splitlines():
        m = re.match(r"^\s*(\d+)\s*[-.)]\s*(.+)$", line)
        if m:
            items.append(m.group(2).strip().strip('"'))
        elif items and line.strip():
            outro.append(line.strip())
    return items, outro


def _badge(number, accent, on_accent, size):
    return (
        f'<div style="width:{size}px;height:{size}px;border-radius:50%;background:{accent};color:{on_accent};'
        f'display:flex;align-items:center;justify-content:center;font-family:Marhey;font-weight:700;'
        f'font-size:{int(size * 0.62)}px;line-height:1;padding-top:{int(size * 0.06)}px">{number}</div>'
    )


def make_item_card(index, total, text, out_path, kind="list"):
    """One carousel slide for list item number `index` of `total` (story-style progress bar on top)."""
    bg, deco, ink, accent, on_accent, _ = _colors(kind, None)
    gap = 10
    seg = (SAFE_W - gap * (total - 1)) / total
    track = "rgba(126,58,75,.18)" if bg != "#3E2A20" else "rgba(251,246,238,.25)"
    bar = "".join(
        f'<div style="width:{seg}px;height:12px;border-radius:6px;background:{accent if i < index else track}"></div>'
        for i in range(total)
    )
    body = (
        f'<div style="display:flex;gap:{gap}px;margin-bottom:60px">{bar}</div>'
        f"{_badge(index, accent, on_accent, 230)}"
        f'<div style="height:30px"></div>{_headline(_strip_emoji(text), ink, 92)}{_footer(ink, accent)}'
    )
    return _render(body, out_path, bg, deco)


def make_closing_card(text, out_path, kind="list", photo=None, follow=False):
    """Closing carousel slide (bonus line or the final question). `follow` adds a follow prompt."""
    bg, deco, ink, accent, on_accent, _ = _colors(kind, photo)
    extra = ""
    if follow:
        extra = (
            f'<div style="font-family:Marhey;font-weight:600;font-size:52px;color:{accent if not photo else "#F6DCE0"};'
            f'margin-top:10px">تابعينا</div><div style="font-size:44px;color:{ink};opacity:.85;margin-bottom:10px"><span dir="ltr" style="unicode-bidi:isolate">{IG_HANDLE}</span></div>'
        )
    body = f"{_headline(_strip_emoji(text), ink, 104)}{extra}{_footer(ink, accent)}"
    return _render(body, out_path, bg, deco, photo)


def _framed_card(text, out_path, kind, photo, swipe):
    """Cover with the image in a rounded frame on the brand background (suits illustrations and photos
    whose own colors would clash with the brand), label/number badge overlapping the frame's lower edge."""
    bg, deco, ink, accent, on_accent, label = _colors(kind, None)
    data = _prepare_photo(photo).read_bytes()
    mime = "image/png" if data[1:4] == b"PNG" else "image/jpeg"
    b64 = base64.b64encode(data).decode()
    frame = (
        f'<div style="width:{SAFE_W}px;height:640px;border-radius:48px;flex:none;'
        f'background:url(data:{mime};base64,{b64}) center/cover;background-color:#fff;'
        f'box-shadow:0 18px 40px -16px rgba(62,42,32,.35)"></div>'
    )
    words = text.split()
    badge = ""
    if kind == "list" and words and words[0].isdigit():
        badge = f'<div style="margin-top:-100px">{_badge(words[0], accent, on_accent, 200)}</div>'
        text = " ".join(words[1:])
    else:
        badge = f'<div class="pill" style="margin-top:-34px;background:{accent};color:{on_accent}">{label}</div>'
    cue = ""
    if swipe:
        cue = (
            f'<div style="font-family:Marhey;font-weight:600;font-size:38px;color:{on_accent};background:{accent};'
            f'padding:10px 36px;border-radius:999px;margin-top:6px">اسحبي ←</div>'
        )
    body = f"{frame}{badge}{_headline(text, ink, 88)}{cue}{_footer(ink, accent)}"
    return _render(body.replace("padding:120px", "padding:100px"), out_path, bg, deco)


def make_card(text, out_path, kind="list", photo=None, swipe=False, framed=False):
    """Cover card: label pill, number badge for "7 ..." list titles, headline, optional swipe cue."""
    text = _strip_emoji(text)
    if framed and photo:
        return _framed_card(text, out_path, kind, photo, swipe)
    bg, deco, ink, accent, on_accent, label = _colors(kind, photo)
    words = text.split()
    badge = ""
    if kind == "list" and words and words[0].isdigit():
        badge = _badge(words[0], accent, on_accent, 260)
        text = " ".join(words[1:])
    pill = f'<div class="pill" style="background:{accent};color:{on_accent}">{label}</div>'
    cue = ""
    if swipe:
        cue = (
            f'<div style="font-family:Marhey;font-weight:600;font-size:42px;color:{accent if not photo else "#7E3A4B"};'
            f'background:{SAND};padding:14px 40px;border-radius:999px;margin-top:10px">اسحبي ←</div>'
        )
    body = f'{pill}<div style="height:{36 if badge else 0}px"></div>{badge}{_headline(text, ink, 88 if photo else 104, bottom=bool(photo))}{cue}{_footer(ink, accent)}'
    return _render(body, out_path, bg, deco, photo)
