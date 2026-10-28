"""Ask Gemini to rewrite every unpublished post caption (and reel caption), and fill its 3 options in control.xlsx.

    python publisher/compare_texts.py              # all unpublished posts and all reels
    python publisher/compare_texts.py p06 r01      # only these
    python publisher/compare_texts.py --refill     # rewrite the columns from the saved compare.json (no Gemini call)

In control.xlsx each row gets 'Gemini option 1/2/3' next to Claude's caption, and a 'Text choice' dropdown
(Claude / Gemini 1 / 2 / 3). Pick one, save, close Excel, run `python publisher/control.py --import`.
Each option is checked with check_post.py; options with problems are marked with "[!]" at the start so you see it.
Needs the Gemini CLI signed in (see gemini_ask.py) and control.xlsx closed in Excel.
"""

import json
import re
import sys
from pathlib import Path

from openpyxl import load_workbook

sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_post  # noqa: E402
import control  # noqa: E402
from gemini_ask import GeminiError, ask  # noqa: E402

ROOT = Path(__file__).resolve().parent
COMPARE = ROOT / "compare.json"
FIELD = r"(?:id|kind|card|caption|image_prompt|alt_text|review|reel_id|hook|beats|closing)"
CLAUDE = "Caption (Darija, Claude)"


def parse_options(text):
    """Return the caption of each option in Gemini's answer (one block per 'id:' line)."""
    blocks = re.split(r"(?m)^\s*id:\s*\S+.*$", text)[1:] or [text]
    options = []
    for block in blocks:
        m = re.search(rf"(?ms)^\s*caption:\s*(.*?)(?=^\s*{FIELD}:|\Z)", block)
        cap = (m.group(1) if m else "").strip()
        cap = re.sub(r"^```.*?$|```$", "", cap, flags=re.M).strip()
        if cap:
            options.append(cap)
    return options[:3]


def flags_for(text):
    return [f for f in check_post.check(f"id: draft-x\nkind: x\ncard: x\ncaption: {text}\nreview: false")
            if "medical-sounding" not in f and "emoji" not in f and "missing field" not in f]


def marked(option):
    """Prefix an option that has checker problems so it is visible in the sheet."""
    bad = flags_for(option)
    return f"[!] {'; '.join(bad)}\n{option}" if bad else option


def targets(wb, only):
    """(sheet name, id, caption) for every unpublished post and every reel to rewrite."""
    out = []
    for row in control.read_table(wb["Posts"]):
        if not str(row.get("Status", "")).startswith("Published") and (not only or row["ID"] in only):
            out.append(("Posts", row["ID"], row.get(CLAUDE) or ""))
    for row in control.read_table(wb["Reels"]):
        if not only or row["ID"] in only:
            out.append(("Reels", row["ID"], row.get(CLAUDE) or ""))
    return out


def fill(wb, data):
    """Write Gemini's options into the sheets without touching any other cell."""
    for sheet in ("Posts", "Reels"):
        ws = wb[sheet]
        col = {c.value: c.column for c in ws[1]}
        for r in range(2, ws.max_row + 1):
            d = data.get(str(ws.cell(r, 1).value))
            if not d or d["sheet"] != sheet:
                continue
            for i in range(1, 4):
                opt = d["gemini"][i - 1] if i <= len(d["gemini"]) else ""
                ws.cell(r, col[f"Gemini option {i}"]).value = marked(opt) if opt else ""
            choice_col = col["Text choice" if sheet == "Posts" else "Caption choice"]
            if not ws.cell(r, choice_col).value:
                ws.cell(r, choice_col).value = "Claude"


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    only = [a for a in sys.argv[1:] if not a.startswith("--")]
    data = json.loads(COMPARE.read_text(encoding="utf-8")) if COMPARE.exists() else {}
    if not control.XLSX.exists():
        sys.exit("control.xlsx not found: run python publisher/control.py --export first")
    wb = load_workbook(control.XLSX)
    if "--refill" not in sys.argv:
        for sheet, rid, caption in targets(wb, only):
            if not caption:
                continue
            print(f"asking Gemini about {rid} ...", flush=True)
            try:
                out = ask(control.REWRITE_POST.format(caption=caption))
            except GeminiError as e:
                sys.exit(str(e))
            opts = parse_options(out)
            data[rid] = {"sheet": sheet, "claude": caption, "gemini": opts, "raw": out}
            print(f"  {len(opts)} options")
        COMPARE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    fill(wb, data)
    try:
        wb.save(control.XLSX)
    except PermissionError:
        sys.exit("control.xlsx is open in Excel. Close it, then run: python publisher/compare_texts.py --refill")
    print(f"Gemini options written for {len(data)} items. Pick 'Text choice' / 'Caption choice', save, close Excel, then:"
          " python publisher/control.py --import")


if __name__ == "__main__":
    main()
