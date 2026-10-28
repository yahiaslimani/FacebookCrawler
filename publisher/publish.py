"""Publish the next queued post to the Facebook Page and the linked Instagram account.

Usage (from the project root):
    python publisher/publish.py --dry-run          # render the card, print what would be posted
    python publisher/publish.py                    # publish the next ready post (run it on a schedule)
    python publisher/publish.py --approve p04 p12  # clear posts flagged "review" (medical content)
    python publisher/publish.py --status

Needs .env: FB_PAGE_ID, FB_PAGE_TOKEN, IG_USER_ID  (see publisher/README.md).
"""

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import requests
from dotenv import load_dotenv

import schedule
from cards import make_card, make_closing_card, make_item_card, parse_list
from photos import fetch_photo

ROOT = Path(__file__).resolve().parent
POSTS = ROOT / "posts.json"
STATE = ROOT / "state.json"
GRAPH = "https://graph.facebook.com/v21.0"
HASHTAGS = "#أنا_و_وليدي #أمومة #ماما #الجزائر #نصائح_أمهات #mamanalgerienne"


def load_json(path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def save_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def graph(method, path, token, params=None, **kw):
    params = {**(params or {}), "access_token": token}
    resp = requests.request(method, f"{GRAPH}/{path}", params=params, timeout=120, **kw)
    data = resp.json()
    if resp.status_code >= 400 or "error" in data:
        raise RuntimeError(f"Graph API {path}: {data.get('error', data)}")
    return data


def wait_ready(media_id, token):
    for _ in range(30):
        status = graph("GET", media_id, token, params={"fields": "status_code"}).get("status_code")
        if status == "FINISHED":
            return
        if status == "ERROR":
            raise RuntimeError("Instagram could not process the media")
        time.sleep(3)
    raise RuntimeError("Instagram media was not ready in time")


def post_instagram(ig_id, token, image_urls, caption):
    """One image_url publishes a single photo; several publish a swipeable carousel."""
    if len(image_urls) == 1:
        media = graph("POST", f"{ig_id}/media", token, data={"image_url": image_urls[0], "caption": caption})
    else:
        children = []
        for url in image_urls:
            child = graph("POST", f"{ig_id}/media", token, data={"image_url": url, "is_carousel_item": "true"})
            wait_ready(child["id"], token)
            children.append(child["id"])
        media = graph(
            "POST",
            f"{ig_id}/media",
            token,
            data={"media_type": "CAROUSEL", "children": ",".join(children), "caption": caption},
        )
    wait_ready(media["id"], token)
    return graph("POST", f"{ig_id}/media_publish", token, data={"creation_id": media["id"]})


def hosted_url(page_id, token, image):
    """Upload an image to the Page as an unpublished photo (not shown on the timeline) and return
    its public CDN url, which Instagram needs. Returns (photo_id, url)."""
    with open(image, "rb") as fh:
        photo = graph("POST", f"{page_id}/photos", token, data={"published": "false"}, files={"source": fh})
    url = graph("GET", photo["id"], token, params={"fields": "images"})["images"][0]["source"]
    return photo["id"], url


def cover_photo(post, partial=None):
    """Stock photo path for the post's cover, or None (plain gradient). Needs an `image_query`
    (or a pinned `photo_id`) and a PIXABAY_API_KEY / PEXELS_API_KEY in .env."""
    if post.get("image_file"):  # an image made by the owner (e.g. with Gemini), kept in publisher/assets/generated/
        local = ROOT / "assets" / "generated" / post["image_file"]
        if local.exists():
            return local
    if not (post.get("image_query") or post.get("photo_id")):
        return None
    photo, credit = fetch_photo(post.get("image_query", ""), post["id"], pinned=post.get("photo_id"))
    if partial is not None and credit:
        partial["photo_credit"] = credit
    return photo


def render_cover(post, partial=None, swipe=False):
    name = f"{post['id']}_ig.jpg" if swipe else f"{post['id']}.jpg"
    return make_card(
        post["card"], ROOT / "cards" / name, post["kind"], cover_photo(post, partial), swipe, post.get("framed", False)
    )


def is_carousel(post):
    items, _ = parse_list(post["text"])
    return post["kind"] == "list" and 2 <= len(items) <= 8


def carousel_images(post, env):
    """Instagram carousel for a numbered list, in the order a creator would build it:
    cover with a swipe cue, one slide per item (progress bar on top), an optional bonus slide
    (the post's second-to-last paragraph), and a closing question slide with a follow prompt.
    The cover and closing slides share the photo, the item slides stay on the plain gradient.
    Returns (urls, temporary photo ids)."""
    items, outro = parse_list(post["text"])
    folder = ROOT / "cards" / post["id"]
    # closing slides reuse the photo, except framed posts: their image only suits the framed cover
    photo = None if post.get("framed") else cover_photo(post)
    slides = [render_cover(post, swipe=True)]
    slides += [make_item_card(i, len(items), t, folder / f"{i:02d}.jpg", post["kind"]) for i, t in enumerate(items, 1)]
    if len(outro) >= 2:
        slides.append(make_closing_card(outro[-2], folder / "98.jpg", post["kind"], photo))
    if outro:
        slides.append(make_closing_card(outro[-1], folder / "99.jpg", post["kind"], photo, follow=True))
    urls, temp = [], []
    for slide in slides:
        photo_id, url = hosted_url(env["FB_PAGE_ID"], env["FB_PAGE_TOKEN"], slide)
        urls.append(url)
        temp.append(photo_id)
    return urls, temp


def facebook_text(post):
    """Facebook caption: the post text with keycap-emoji numerals (1-9) on list lines."""
    return re.sub(r"(?m)^(\d)\s*-\s*", lambda m: m.group(1) + "\ufe0f\u20e3 ", post["text"])


def instagram_caption(post):
    """Carousel captions are short (the content is in the slides): hook, swipe cue, question."""
    if not is_carousel(post):
        return f"{post['text']}\n\n{HASHTAGS}"
    lines = [ln for ln in post["text"].splitlines() if ln.strip()]
    return f"{lines[0]}\n\nاسحبي ← للقايمة كاملة 👉\n\n{lines[-1]}\n\n{HASHTAGS}"


def publish_one(post, env, partial, save_partial):
    """Publish to Facebook then Instagram. `partial` holds the Facebook result if a previous run
    got that far, so a failed Instagram step is retried without posting to Facebook twice."""
    ig_caption = instagram_caption(post)
    token = env["FB_PAGE_TOKEN"]

    if "photo_id" not in partial:
        image = render_cover(post, partial)
        with open(image, "rb") as fh:
            photo = graph(
                "POST", f"{env['FB_PAGE_ID']}/photos", token, data={"message": facebook_text(post)}, files={"source": fh}
            )
        partial.update(photo_id=photo["id"], facebook=photo.get("post_id", photo["id"]))
        save_partial(partial)
    # The uploaded photo has a public CDN url, which Instagram needs.
    if is_carousel(post):
        urls, temp_photos = carousel_images(post, env)
    else:
        cover_url = graph("GET", partial["photo_id"], token, params={"fields": "images"})["images"][0]["source"]
        urls, temp_photos = [cover_url], []
    ig = post_instagram(env["IG_USER_ID"], token, urls, ig_caption)
    for photo_id in temp_photos:  # the slides only needed to be hosted until Instagram fetched them
        try:
            graph("DELETE", photo_id, token)
        except RuntimeError:
            pass
    return {"facebook": partial["facebook"], "instagram": ig["id"], "slides": len(urls)}


def main():
    if sys.stdout:  # pythonw (the scheduled task) has no console
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--approve", nargs="*", default=[])
    ap.add_argument("--count", type=int, default=1, help="posts to publish in this run")
    ap.add_argument("--auto", action="store_true", help="publish what is due now (used by the scheduled task)")
    ap.add_argument("--only", help="publish this post id now instead of the next in line")
    args = ap.parse_args()

    posts = load_json(POSTS, [])
    state = load_json(STATE, {"published": {}, "approved": []})

    if args.approve:
        state["approved"] = sorted(set(state["approved"]) | set(args.approve))
        save_json(STATE, state)
        print("approved:", ", ".join(args.approve))
        return

    def ready(p):
        return (
            p["id"] not in state["published"]
            and not p.get("hold")
            and (not p["review"] or p["id"] in state["approved"])
        )

    if args.status:
        for p in posts:
            tag = "PUBLISHED" if p["id"] in state["published"] else ("ready" if ready(p) else ("on hold" if p.get("hold") else "needs --approve"))
            print(f"{p['id']}  {tag:16} {p['card']}")
        return

    if args.auto:  # the scheduled task: whatever is due by its publish_at / default slot, see schedule.py
        now = datetime.now()
        for when, p, _ in schedule.plan(posts, state, now)[1]:
            print(f"MISSED {p['id']} (was due {when:%Y-%m-%d %H:%M}); set a new time in the panel")
        queue = schedule.due(posts, state, now)
    else:
        queue = [p for p in posts if ready(p) and (not args.only or p["id"] == args.only)][: args.count]
    if not queue:
        print("Nothing ready to publish.")
        return

    load_dotenv(ROOT.parent / ".env")
    env = {k: os.environ.get(k, "") for k in ("FB_PAGE_ID", "FB_PAGE_TOKEN", "IG_USER_ID")}
    for post in queue:
        if args.dry_run:
            card = render_cover(post)
            print(f"--- {post['id']} (card: {card})\n{post['text']}\n\n{HASHTAGS}\n")
            continue
        missing = [k for k, v in env.items() if not v]
        if missing:
            sys.exit(f"Missing in .env: {', '.join(missing)}")
        partial = state.setdefault("partial", {}).setdefault(post["id"], {})

        def save_partial(_):
            save_json(STATE, state)

        ids = publish_one(post, env, partial, save_partial)
        state["published"][post["id"]] = {"at": time.strftime("%Y-%m-%d %H:%M"), **ids}
        state["partial"].pop(post["id"], None)
        save_json(STATE, state)
        print(f"published {post['id']}: {ids}")


if __name__ == "__main__":
    try:
        main()
    except Exception:  # noqa: BLE001 - scheduled runs have no console, so keep the error in a log file
        import traceback

        with open(ROOT / "publish.log", "a", encoding="utf-8") as log:
            log.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M')} FAILED\n{traceback.format_exc()}")
        raise
