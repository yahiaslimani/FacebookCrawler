"""Stock footage for reels from Pixabay (free for commercial use, no attribution required). Real footage only:
clips tagged as AI-generated are skipped, so no "AI info" label is needed for them.

    python publisher/stock.py search "mother rocking baby"       # list candidates
    python publisher/stock.py sheet beat1 "mother rocking baby" "crying baby"   # thumbnails contact sheet in scratch

Downloads go to publisher/assets/stock/<id>.mp4. The largest file under ~45 MB is taken, so a vertical crop of a
landscape 4K clip still has enough pixels for a 1080x1920 reel.
"""

import io
import json
import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
STOCK_DIR = ROOT / "assets" / "stock"
load_dotenv(ROOT.parent / ".env")
BAD_TAGS = ("ai generated", "ai-generated", "midjourney", "artificial intelligence", "stable diffusion", "generative")


def search(query, limit=40, min_seconds=4):
    """Real-footage candidates for a query, best first (portrait clips first, then high resolution)."""
    resp = requests.get("https://pixabay.com/api/videos/", timeout=30,
                        params={"key": os.environ["PIXABAY_API_KEY"], "q": query, "per_page": min(limit, 50), "safesearch": "true"})
    out = []
    for h in resp.json().get("hits", []):
        if any(t in h.get("tags", "").lower() for t in BAD_TAGS) or h["duration"] < min_seconds:
            continue
        files = [(q, v) for q, v in h["videos"].items() if v.get("url") and int(v["size"]) < 45e6]
        if not files:
            continue
        q, v = max(files, key=lambda kv: int(kv[1]["width"]) * int(kv[1]["height"]))
        out.append({"id": h["id"], "by": h["user"], "page": h["pageURL"], "duration": h["duration"], "tags": h["tags"],
                    "url": v["url"], "w": int(v["width"]), "h": int(v["height"]), "size": int(v["size"]), "thumb": v["thumbnail"]})
    out.sort(key=lambda c: (c["h"] <= c["w"], -c["w"] * c["h"]))  # portrait first
    return out


def download(clip):
    STOCK_DIR.mkdir(parents=True, exist_ok=True)
    path = STOCK_DIR / f"{clip['id']}.mp4"
    if not path.exists():
        data = requests.get(clip["url"], timeout=300).content
        path.write_bytes(data)
    return path


def credit(clip):
    return {"id": clip["id"], "by": clip["by"], "url": clip["page"], "size": f"{clip['w']}x{clip['h']}"}


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    if sys.argv[1] == "search":
        for c in search(" ".join(sys.argv[2:]))[:10]:
            print(c["id"], f"{c['w']}x{c['h']}", f"{c['duration']}s", c["tags"][:60])
