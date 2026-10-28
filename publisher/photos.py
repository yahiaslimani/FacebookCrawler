"""Fetch a free stock photo for a post's background, from Pixabay or Pexels.

Pixabay (https://pixabay.com/api/docs/) and Pexels (https://www.pexels.com/api/) both allow commercial
use without attribution, but do not imply that the people shown endorse the page. Queries for
sensitive posts should avoid identifiable faces. Pexels has paused new keys, so Pixabay is the default.
"""

import hashlib
import os
from pathlib import Path

import requests

CACHE = Path(__file__).resolve().parent / "photos"


def _pixabay(query, key):
    resp = requests.get(
        "https://pixabay.com/api/",
        params={
            "key": key,
            "q": query[:100],
            "image_type": "photo",
            "orientation": "vertical",
            "min_height": 1000,
            "safesearch": "true",
            "per_page": 30,
        },
        timeout=30,
    )
    resp.raise_for_status()
    return [
        {"id": str(h["id"]), "url": h["largeImageURL"], "by": h["user"], "page": h["pageURL"]}
        for h in resp.json().get("hits", [])
    ]


def _pixabay_by_id(photo_id, key):
    resp = requests.get("https://pixabay.com/api/", params={"key": key, "id": photo_id}, timeout=30)
    resp.raise_for_status()
    return resp.json()["hits"][0]["largeImageURL"]


def _pexels(query, key):
    resp = requests.get(
        "https://api.pexels.com/v1/search",
        headers={"Authorization": key},
        params={"query": query, "orientation": "portrait", "size": "large", "per_page": 30},
        timeout=30,
    )
    resp.raise_for_status()
    return [
        {"id": str(p["id"]), "url": p["src"]["large2x"], "by": p["photographer"], "page": p["url"]}
        for p in resp.json().get("photos", [])
    ]


def fetch_photo(query, seed, pinned=None):
    """Return (path, credit) for a portrait photo matching `query`, or (None, None) when no key is
    configured or anything fails. The pick is deterministic per `seed` (the post id)."""
    try:
        if pinned:  # a hand-picked Pixabay photo id (cached after the first download)
            path = CACHE / f"pixabay_{pinned}.jpg"
            if not path.exists():
                url = _pixabay_by_id(pinned, os.environ["PIXABAY_API_KEY"])
                CACHE.mkdir(exist_ok=True)
                path.write_bytes(requests.get(url, timeout=60).content)
            return path, {"source": "pixabay", "id": str(pinned)}
        if os.environ.get("PIXABAY_API_KEY"):
            source, hits = "pixabay", _pixabay(query, os.environ["PIXABAY_API_KEY"])
        elif os.environ.get("PEXELS_API_KEY"):
            source, hits = "pexels", _pexels(query, os.environ["PEXELS_API_KEY"])
        else:
            return None, None
        if not hits:
            return None, None
        hit = hits[int(hashlib.md5(seed.encode()).hexdigest(), 16) % len(hits)]
        CACHE.mkdir(exist_ok=True)
        path = CACHE / f"{source}_{hit['id']}.jpg"
        if not path.exists():
            img = requests.get(hit["url"], timeout=60)
            img.raise_for_status()
            path.write_bytes(img.content)
        return path, {"source": source, "id": hit["id"], "by": hit["by"], "url": hit["page"]}
    except (requests.RequestException, KeyError, ValueError):
        return None, None
