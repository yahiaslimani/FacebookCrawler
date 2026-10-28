"""Performance report for published posts (Facebook views/reactions/comments/shares, Instagram reach/likes/
comments/saves/shares) plus follower counts.

    python publisher/report.py

Needs the insights permissions on the Page token (read_insights, pages_read_user_content,
instagram_manage_insights). A metric that Facebook rejects shows as "-" instead of stopping the report.
"""

import json
import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
GRAPH = "https://graph.facebook.com/v21.0"


def get(path, token, **params):
    resp = requests.get(f"{GRAPH}/{path}", params={"access_token": token, **params}, timeout=60)
    return resp.json()


def insight(path, token, metric):
    """Value of one lifetime insight metric, or None when unavailable."""
    data = get(f"{path}/insights", token, metric=metric).get("data")
    if not data:
        return None
    return data[0]["values"][0]["value"]


def ig_metrics(media_id, token):
    out = {}
    for metric in ("reach", "likes", "comments", "saved", "shares"):
        out[metric] = insight(media_id, token, metric)
    return out


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    load_dotenv(ROOT.parent / ".env")
    token, page, ig = os.environ["FB_PAGE_TOKEN"], os.environ["FB_PAGE_ID"], os.environ["IG_USER_ID"]
    state = json.loads((ROOT / "state.json").read_text(encoding="utf-8"))["published"]
    posts = {p["id"]: p for p in json.loads((ROOT / "posts.json").read_text(encoding="utf-8"))}

    fb_page = get(page, token, fields="followers_count")
    ig_page = get(ig, token, fields="followers_count,media_count")
    print(f"Followers: Facebook {fb_page.get('followers_count', '-')} | Instagram {ig_page.get('followers_count', '-')}\n")

    cols = ("post", "kind", "published", "FB views", "FB react", "FB comm", "FB share", "IG reach", "IG likes", "IG comm", "IG saved", "IG share")
    rows = []
    for pid, info in state.items():
        fb = get(
            info["facebook"], token, fields="reactions.summary(true).limit(0),comments.summary(true).limit(0),shares"
        )
        views = insight(info["facebook"], token, "post_media_view")
        m = ig_metrics(info["instagram"], token)

        def v(x):
            return "-" if x is None else str(x)

        rows.append(
            (
                pid,
                posts.get(pid, {}).get("kind", "?"),
                info["at"],
                v(views),
                v(fb.get("reactions", {}).get("summary", {}).get("total_count")),
                v(fb.get("comments", {}).get("summary", {}).get("total_count")),
                v(fb.get("shares", {}).get("count", 0)),
                v(m["reach"]),
                v(m["likes"]),
                v(m["comments"]),
                v(m["saved"]),
                v(m["shares"]),
            )
        )
    widths = [max(len(c), *(len(r[i]) for r in rows)) if rows else len(c) for i, c in enumerate(cols)]
    print("  ".join(c.ljust(w) for c, w in zip(cols, widths)))
    for r in rows:
        print("  ".join(x.ljust(w) for x, w in zip(r, widths)))


if __name__ == "__main__":
    main()
