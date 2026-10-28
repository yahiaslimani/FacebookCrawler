"""Control panel for the Ana w Wlidi publisher: a small web app that edits posts.json / reels.json directly.

    python publisher/panel/app.py            # then open http://localhost:8765

If PANEL_PIN is set in .env the panel listens on all network interfaces (so a phone on Tailscale or the same
Wi-Fi can open it) and asks for the PIN. Without a PIN it only listens on this PC.
It never publishes by itself: the scheduled task still does that. Approving a held post only lifts the hold.
"""

import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

PUB = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PUB))
from dotenv import load_dotenv  # noqa: E402

load_dotenv(PUB.parent / ".env")

import cards  # noqa: E402
import check_post  # noqa: E402
import compare_texts  # noqa: E402
import control  # noqa: E402
import publish  # noqa: E402
import schedule  # noqa: E402
import reels as reel_builder  # noqa: E402
import stock  # noqa: E402
from flask import Flask, jsonify, make_response, request, send_file, send_from_directory  # noqa: E402
from gemini_ask import GeminiError, ask  # noqa: E402

HERE = Path(__file__).resolve().parent
CACHE = HERE / "cache"
POSTS, STATE, REELS, COMPARE = control.POSTS, control.STATE, control.REELS, compare_texts.COMPARE
GENERATED, REELS_DIR, FONTS = control.GENERATED, control.REELS_DIR, PUB / "assets" / "fonts"
PIN = os.environ.get("PANEL_PIN", "").strip()
COOKIE = hmac.new(b"ana-w-wlidi-panel", PIN.encode(), hashlib.sha256).hexdigest() if PIN else ""
# Playwright's sync API is bound to the thread that started it: ALL card rendering goes through this one worker.
RENDER = ThreadPoolExecutor(max_workers=1, thread_name_prefix="render")
DATA_LOCK = threading.Lock()
JOBS = {}

app = Flask(__name__, static_folder=None)
app.config["MAX_CONTENT_LENGTH"] = 300 * 1024 * 1024


# ----------------------------------------------------------------------------- storage
def read_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def write_json(path, data):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def load_reels():
    reels = read_json(REELS, [])
    have = {r["id"] for r in reels}
    for r in control.REELS_DATA:  # reels defined in code but never imported yet
        if r["id"] not in have:
            reels.append({"id": r["id"], "status": "Draft (hold)", "slot": r["slot"], "topic": r["topic"], "hook": r["hook"],
                          "beats": [{"text": t, "clip_prompt": control.clip_prompt(s), "file": None} for t, s in r["beats"]],
                          "closing": r["closing"], "caption": r["caption"], "final_file": None, "review": r["review"] == "yes"})
    meta = {r["id"]: r for r in control.REELS_DATA}
    for r in reels:  # trust only clip files that really exist (old imports listed names for clips never made)
        for b in r["beats"]:
            if b.get("file") and not (REELS_DIR / b["file"]).exists():
                b["file"] = None
        if r.get("final_file") and not (REELS_DIR / r["final_file"]).exists():
            r["final_file"] = None
        m = meta.get(r["id"], {})
        r.setdefault("dur", m.get("dur", "15 s"))
        r["logo"] = "mark top centre all video; lockup last 2 s"
        r["music"] = "none (test)"
    return reels


# ----------------------------------------------------------------------------- views
def flags_for(text):
    return compare_texts.flags_for(text or "")


RENDER_VERSION = 3  # bump when cards.py changes how a card looks, so cached previews are redrawn


def render_key(p):
    img = ""
    if p.get("image_file") and (GENERATED / p["image_file"]).exists():
        img = f"{p['image_file']}:{(GENERATED / p['image_file']).stat().st_mtime_ns}"
    raw = json.dumps([RENDER_VERSION, p["card"], p["kind"], bool(p.get("framed")), p["text"], img, p.get("photo_id"), p.get("image_query")],
                     ensure_ascii=False)
    return hashlib.sha1(raw.encode()).hexdigest()[:12]


def post_view(p, state, compare):
    scene, ai, slot, notes = control.POST_META.get(p["id"], (None, "", "", ""))
    published = p["id"] in state.get("published", {})
    own = bool(p.get("image_file") and (GENERATED / p["image_file"]).exists())
    return {
        **p, "status": control.status_of(p, state), "published_info": state.get("published", {}).get(p["id"]),
        "planned": slot, "notes": notes, "ai_label": ai, "carousel": publish.is_carousel(p), "has_user_image": own,
        "image_prompt": control.img_prompt(scene) if scene else "",
        "rewrite_prompt": "" if published else control.REWRITE_POST.format(caption=p["text"]),
        "flags": flags_for(p["text"]), "gemini": compare.get(p["id"], {}).get("gemini", []),
        "headlines": compare.get(p["id"], {}).get("headlines", []), "claude_card": p.get("claude_card") or p["card"],
        "fb_text": publish.facebook_text(p), "ig_caption": publish.instagram_caption(p), "v": render_key(p),
    }


VARIANTS = {"clips": "{id}.mp4", "slideshow": "{id}_slideshow.mp4", "stock": "{id}_stock.mp4"}


def reel_view(r, compare):
    variants = {}
    for key, pattern in VARIANTS.items():
        f = reel_builder.OUT_DIR / pattern.format(id=r["id"])
        if f.exists():
            variants[key] = f"{f.name}?v={f.stat().st_mtime_ns}"
    pub = reel_builder.OUT_DIR / f"{r['id']}_publish.mp4"
    return {**r, "variants": variants, "chosen": r.get("chosen"), "publish_ready": pub.exists(),
            "has_slideshow": bool(r.get("post")), "has_stock": bool(r.get("stock")),
            "gemini": compare.get(r["id"], {}).get("gemini", []), "flags": flags_for(r.get("caption", "")),
            "rewrite_prompt": control.REWRITE_REEL.format(
                hook=r["hook"], beats="\n".join(f"BEAT {i}: {b['text']}" for i, b in enumerate(r["beats"], 1)), closing=r["closing"])}


# ----------------------------------------------------------------------------- auth
@app.before_request
def guard():
    if not PIN or request.path in ("/", "/api/login") or request.path.startswith("/static/"):
        return None
    if request.cookies.get("panel") != COOKIE:
        return jsonify(auth=False), 401
    return None


FAILS = {}  # ip -> (count, first failure time): 5 wrong PINs lock that address for 10 minutes


@app.post("/api/login")
def login():
    ip, now = request.remote_addr, time.time()
    count, since = FAILS.get(ip, (0, now))
    if count >= 5 and now - since < 600:
        return jsonify(ok=False, error="too many tries, wait 10 minutes"), 429
    if now - since >= 600:
        count = 0
    if PIN and hmac.compare_digest(str((request.get_json(silent=True) or {}).get("pin", "")), PIN):
        FAILS.pop(ip, None)
        resp = make_response(jsonify(ok=True))
        resp.set_cookie("panel", COOKIE, max_age=60 * 60 * 24 * 90, httponly=True, samesite="Lax")
        return resp
    FAILS[ip] = (count + 1, since if count else now)
    return jsonify(ok=False), 403


@app.get("/")
def index():
    return send_from_directory(HERE / "static", "index.html")


@app.get("/static/fonts/<path:name>")
def fonts(name):
    return send_from_directory(FONTS, name)


# ----------------------------------------------------------------------------- API: state
@app.get("/api/state")
def state_api():
    state, compare = read_json(STATE, {}), read_json(COMPARE, {})
    posts = [post_view(p, state, compare) for p in read_json(POSTS, [])]
    entries, missed = schedule.plan(read_json(POSTS, []), state)
    fmt = lambda when: when.strftime("%a %d %b %H:%M")  # noqa: E731
    queue = [{"date": fmt(w), "at": w.strftime("%Y-%m-%dT%H:%M"), "id": p["id"], "pinned": pinned} for w, p, pinned in entries]
    missed = [{"date": fmt(w), "id": p["id"]} for w, p, _ in missed]
    when_of = {q["id"]: q["date"] + (" (pinned)" if q["pinned"] else "") for q in queue} | {m["id"]: "MISSED " + m["date"] for m in missed}
    for p in posts:
        p["scheduled"] = when_of.get(p["id"], "")
    return jsonify(posts=posts, reels=[reel_view(r, compare) for r in load_reels()], queue=queue, missed=missed,
                   proposed=[{"date": d, "id": i, "type": t, "notes": n} for d, i, t, n in control.CALENDAR],
                   specs=[{"topic": a, "value": b} for a, b in control.SPECS], pin=bool(PIN),
                   now=datetime.now().strftime("%a %d %b %H:%M"))


def find_post(posts, pid):
    return next((p for p in posts if p["id"] == pid), None)


@app.patch("/api/post/<pid>")
def patch_post(pid):
    body = request.get_json(force=True)
    with DATA_LOCK:
        posts = read_json(POSTS, [])
        p = find_post(posts, pid)
        if not p:
            return jsonify(error="unknown post"), 404
        if p["id"] in read_json(STATE, {}).get("published", {}):
            return jsonify(error="this post is already published"), 409
        if "card" in body and body["card"] != p["card"]:
            p.setdefault("claude_card", p["card"])  # keep the original headline so it can be restored
            p["card_source"] = "Edited"
        for key in ("card", "kind", "framed", "review", "hold"):
            if key in body:
                if body[key] in (False, None) and key in ("framed", "hold"):
                    p.pop(key, None)
                else:
                    p[key] = body[key]
        if "publish_at" in body:  # "YYYY-MM-DDTHH:MM" from the date picker, or empty to go back to the default slots
            when = schedule.parse(str(body["publish_at"] or "").replace("T", " "))
            if body["publish_at"] and not when:
                return jsonify(error="use the date and time picker"), 400
            if when:
                p["publish_at"] = when.strftime(schedule.FORMAT)
            else:
                p.pop("publish_at", None)
        if "text" in body and body["text"] != p["text"]:
            p["text"] = body["text"]
            p["text_source"] = "Edited"
        write_json(POSTS, posts)
        return jsonify(post_view(p, read_json(STATE, {}), read_json(COMPARE, {})))


@app.post("/api/post/<pid>/use")
def use_text(pid):
    """Choose Claude's text or one of Gemini's options as the post text (Claude's original is always kept)."""
    source = (request.get_json(force=True) or {}).get("source", "Claude")
    with DATA_LOCK:
        posts, compare = read_json(POSTS, []), read_json(COMPARE, {})
        p = find_post(posts, pid)
        if not p:
            return jsonify(error="unknown post"), 404
        p.setdefault("claude_text", p["text"])
        if source.startswith("Gemini"):
            options = compare.get(pid, {}).get("gemini", [])
            idx = int(source.split()[-1]) - 1
            if idx >= len(options):
                return jsonify(error="no such option"), 400
            p["text"] = options[idx]
        else:
            p["text"] = p["claude_text"]
        p["text_source"] = source  # the cover headline is NOT touched: it has its own picker
        write_json(POSTS, posts)
        return jsonify(post_view(p, read_json(STATE, {}), compare))


@app.post("/api/post/<pid>/card/use")
def use_headline(pid):
    """Choose the cover headline: Claude's original or one of Gemini's ideas."""
    source = (request.get_json(force=True) or {}).get("source", "Claude")
    with DATA_LOCK:
        posts, compare = read_json(POSTS, []), read_json(COMPARE, {})
        p = find_post(posts, pid)
        if not p:
            return jsonify(error="unknown post"), 404
        p.setdefault("claude_card", p["card"])
        if source.startswith("Gemini"):
            options = compare.get(pid, {}).get("headlines", [])
            idx = int(source.split()[-1]) - 1
            if idx >= len(options):
                return jsonify(error="no such headline"), 400
            p["card"] = options[idx]
        else:
            p["card"] = p["claude_card"]
        p["card_source"] = source
        write_json(POSTS, posts)
        return jsonify(post_view(p, read_json(STATE, {}), compare))


@app.post("/api/post/<pid>/approve")
def approve(pid):
    with DATA_LOCK:
        state = read_json(STATE, {"published": {}, "approved": [], "partial": {}})
        state["approved"] = sorted(set(state.get("approved", [])) | {pid})
        write_json(STATE, state)
    return jsonify(ok=True)


@app.post("/api/post/<pid>/unapprove")
def unapprove(pid):
    with DATA_LOCK:
        state = read_json(STATE, {"published": {}, "approved": [], "partial": {}})
        state["approved"] = [x for x in state.get("approved", []) if x != pid]
        write_json(STATE, state)
    return jsonify(ok=True)


@app.post("/api/post/<pid>/image")
def upload_image(pid):
    f = request.files.get("file")
    suffix = Path(f.filename or "").suffix.lower() if f else ""
    if not f or suffix not in control.IMAGE_SUFFIXES:
        return jsonify(error="send a PNG, JPG or WEBP picture"), 400
    GENERATED.mkdir(parents=True, exist_ok=True)
    for old in GENERATED.glob(f"{pid}.*"):
        old.unlink()
    name = f"{pid}{suffix}"
    f.save(GENERATED / name)
    with DATA_LOCK:
        posts = read_json(POSTS, [])
        p = find_post(posts, pid)
        p["image_file"] = name
        write_json(POSTS, posts)
        return jsonify(post_view(p, read_json(STATE, {}), read_json(COMPARE, {})))


@app.delete("/api/post/<pid>/image")
def delete_image(pid):
    with DATA_LOCK:
        posts = read_json(POSTS, [])
        p = find_post(posts, pid)
        for old in GENERATED.glob(f"{pid}.*"):
            old.unlink()
        p.pop("image_file", None)
        write_json(POSTS, posts)
        return jsonify(post_view(p, read_json(STATE, {}), read_json(COMPARE, {})))


@app.post("/api/queue/move")
def move_queue():
    body = request.get_json(force=True)
    with DATA_LOCK:
        posts, state = read_json(POSTS, []), read_json(STATE, {})
        ready = [i for i, p in enumerate(posts) if control.status_of(p, state) == "Ready (queued)" and not p.get("publish_at")]
        ids = [posts[i]["id"] for i in ready]
        if body["id"] not in ids:
            return jsonify(error="not in the queue"), 400
        k, j = ids.index(body["id"]), ids.index(body["id"]) + int(body["dir"])
        if 0 <= j < len(ready):
            posts[ready[k]], posts[ready[j]] = posts[ready[j]], posts[ready[k]]
            write_json(POSTS, posts)
    return jsonify(ok=True)


@app.post("/api/check")
def check_api():
    return jsonify(flags=flags_for((request.get_json(force=True) or {}).get("text", "")))


# ----------------------------------------------------------------------------- previews
def slide_keys(p):
    keys = ["cover"]
    if publish.is_carousel(p):
        items, outro = cards.parse_list(p["text"])
        keys += [f"i{n}" for n in range(1, len(items) + 1)]
        if len(outro) >= 2:
            keys.append("bonus")
        if outro:
            keys.append("close")
    return keys


def render_slide(p, key, out):
    photo = publish.cover_photo(p)
    if key == "cover":
        return cards.make_card(p["card"], out, p["kind"], photo, False, bool(p.get("framed")))
    items, outro = cards.parse_list(p["text"])
    closing_photo = None if p.get("framed") else photo
    if key.startswith("i"):
        n = int(key[1:])
        return cards.make_item_card(n, len(items), items[n - 1], out, p["kind"])
    if key == "bonus":
        return cards.make_closing_card(outro[-2], out, p["kind"], closing_photo)
    return cards.make_closing_card(outro[-1], out, p["kind"], closing_photo, follow=True)


@app.get("/api/preview/<pid>.jpg")
def preview(pid):
    p = find_post(read_json(POSTS, []), pid)
    if not p:
        return "unknown post", 404
    key = request.args.get("slide", "cover")
    out = CACHE / f"{pid}_{render_key(p)}_{key}.jpg"
    if not out.exists():
        CACHE.mkdir(exist_ok=True)
        RENDER.submit(render_slide, p, key, out).result(timeout=180)
    resp = send_file(out, mimetype="image/jpeg")
    resp.headers["Cache-Control"] = "private, max-age=3600"
    return resp


@app.get("/api/slides/<pid>")
def slides(pid):
    p = find_post(read_json(POSTS, []), pid)
    return jsonify(keys=slide_keys(p) if p else [])


# ----------------------------------------------------------------------------- reels
def save_reel(rid, mutate):
    with DATA_LOCK:
        reels = load_reels()
        r = next((x for x in reels if x["id"] == rid), None)
        if r is None:
            return None
        mutate(r)
        write_json(REELS, [{k: v for k, v in x.items() if k not in ("dur", "logo", "music")} for x in reels])
        return reel_view(r, read_json(COMPARE, {}))


@app.patch("/api/reel/<rid>")
def patch_reel(rid):
    body = request.get_json(force=True)

    def mutate(r):
        for key in ("hook", "closing", "status", "review"):
            if key in body:
                r[key] = body[key]
        if "caption" in body and body["caption"] != r.get("caption"):
            r["caption"], r["caption_source"] = body["caption"], "Edited"
        for i, b in enumerate(body.get("beats", [])):
            if i < len(r["beats"]) and "text" in b:
                r["beats"][i]["text"] = b["text"]

    out = save_reel(rid, mutate)
    return (jsonify(out), 200) if out else (jsonify(error="unknown reel"), 404)


@app.post("/api/reel/<rid>/use")
def reel_use(rid):
    source = (request.get_json(force=True) or {}).get("source", "Claude")
    options = read_json(COMPARE, {}).get(rid, {}).get("gemini", [])

    def mutate(r):
        r.setdefault("claude_caption", r["caption"])
        r["caption"] = options[int(source.split()[-1]) - 1] if source.startswith("Gemini") else r["claude_caption"]
        r["caption_source"] = source

    out = save_reel(rid, mutate)
    return (jsonify(out), 200) if out else (jsonify(error="unknown reel"), 404)


@app.post("/api/reel/<rid>/clip/<which>")
def reel_clip(rid, which):
    f = request.files.get("file")
    suffix = Path(f.filename or "").suffix.lower() if f else ""
    if not f or suffix not in control.VIDEO_SUFFIXES:
        return jsonify(error="send an MP4, MOV or WEBM video"), 400
    REELS_DIR.mkdir(parents=True, exist_ok=True)
    stem = f"{rid}_final" if which == "final" else f"{rid}_beat{int(which)}"
    for old in REELS_DIR.glob(f"{stem}.*"):
        old.unlink()
    name = f"{stem}{suffix}"
    f.save(REELS_DIR / name)

    def mutate(r):
        if which == "final":
            r["final_file"] = name
        else:
            r["beats"][int(which) - 1]["file"] = name

    return jsonify(save_reel(rid, mutate))


@app.delete("/api/reel/<rid>/clip/<which>")
def reel_clip_delete(rid, which):
    stem = f"{rid}_final" if which == "final" else f"{rid}_beat{int(which)}"
    for old in REELS_DIR.glob(f"{stem}.*"):
        old.unlink()

    def mutate(r):
        if which == "final":
            r["final_file"] = None
        else:
            r["beats"][int(which) - 1]["file"] = None

    return jsonify(save_reel(rid, mutate))


@app.get("/media/out/<path:name>")
def media_out(name):
    return send_from_directory(reel_builder.OUT_DIR, name, conditional=True)


def build_job(job, rid, source):
    """Build one version of a reel: 'clips' (your uploaded clips), 'slideshow' (our designs) or 'stock' (stock footage)."""
    try:
        reel = next(r for r in load_reels() if r["id"] == rid)
        out = reel_builder.OUT_DIR / VARIANTS[source].format(id=rid)
        if source == "slideshow":
            post = find_post(read_json(POSTS, []), reel.get("post", ""))
            items, _ = cards.parse_list((post or {}).get("claude_text") or (post or {}).get("text", ""))
            if not items:
                raise RuntimeError("this reel has no numbered post to turn into a slideshow")
            info = RENDER.submit(reel_builder.build_slideshow, reel, items, out).result(timeout=900)
        elif source == "stock":
            picks = reel.get("stock") or []
            if not picks:
                raise RuntimeError("no stock footage is chosen for this reel yet")
            beats = []
            for pick, beat in zip(picks, reel["beats"]):
                stock.download(pick)
                beats.append(dict(beat, file=f"{pick['id']}.mp4", start=pick.get("start", 0), pos=pick.get("pos", 0.5)))
            info = RENDER.submit(reel_builder.build, dict(reel, beats=beats), out, stock.STOCK_DIR).result(timeout=900)
        else:
            info = RENDER.submit(reel_builder.build, reel).result(timeout=900)  # cards are drawn on the render thread
        JOBS[job] = {"state": "done", "clips_used": info["clips_used"], "beats": info["beats"], "seconds": info["seconds"], "source": source}
    except Exception as e:  # noqa: BLE001 - show any failure in the page
        JOBS[job] = {"state": "error", "message": f"{type(e).__name__}: {e}"}


@app.post("/api/reel/<rid>/build")
def reel_build(rid):
    source = (request.get_json(silent=True) or {}).get("source", "clips")
    if source not in VARIANTS or not any(r["id"] == rid for r in load_reels()):
        return jsonify(error="unknown reel or source"), 404
    job = secrets.token_hex(4)
    JOBS[job] = {"state": "running"}
    threading.Thread(target=build_job, args=(job, rid, source), daemon=True).start()
    return jsonify(job=job)


@app.post("/api/reel/<rid>/choose")
def reel_choose(rid):
    """Pick the version that will be published: it is copied to <id>_publish.mp4, which publish_reel.py uses."""
    source = (request.get_json(force=True) or {}).get("source", "clips")
    src = reel_builder.OUT_DIR / VARIANTS.get(source, "").format(id=rid)
    if not src.is_file():
        return jsonify(error="that version has not been built"), 400
    shutil.copy2(src, reel_builder.OUT_DIR / f"{rid}_publish.mp4")
    out = save_reel(rid, lambda r: r.__setitem__("chosen", source))
    return jsonify(out)


@app.get("/media/reels/<path:name>")
def media_reels(name):
    return send_from_directory(REELS_DIR, name, conditional=True)


# ----------------------------------------------------------------------------- Gemini
def gemini_job(job, rid, sheet, caption):
    try:
        out = ask(control.REWRITE_POST.format(caption=caption))
        opts = compare_texts.parse_options(out)
        if not opts:
            raise GeminiError("Gemini answered but no options could be read from the answer.")
        with DATA_LOCK:
            data = read_json(COMPARE, {})
            data.setdefault(rid, {}).update({"sheet": sheet, "claude": caption, "gemini": opts, "raw": out})  # keeps headline ideas
            write_json(COMPARE, data)
        JOBS[job] = {"state": "done", "n": len(opts)}
    except GeminiError as e:
        JOBS[job] = {"state": "error", "message": str(e)}
    except Exception as e:  # noqa: BLE001 - report anything to the page instead of dying silently
        JOBS[job] = {"state": "error", "message": f"{type(e).__name__}: {e}"}


REWRITE_HEADLINE = (
    "Suggest 3 cover headlines for the post below, in genuine ALGERIAN Darija. Each is at most 8 words, no emoji, short and "
    "catchy enough to make a mother stop scrolling. If the post is a numbered list, start the headline with the number as a "
    "digit (for example: 7 جمل ...). They must fit the post and must not promise anything it does not say. Use EXACTLY this "
    "template and write nothing else:\nheadline: <text>\nheadline: <text>\nheadline: <text>\n"
    "Then one last line: RECOMMENDED: 1, 2 or 3, and why in one short sentence.\n\nPOST:\n{text}"
)


def parse_headlines(text):
    found = []
    for line in text.splitlines():
        m = re.match(r"^\s*headline:\s*(.+?)\s*$", line, re.I)
        if m:
            h = "".join(ch for ch in m.group(1) if ord(ch) < 0x1F000).strip(" \"'*`")
            if h:
                found.append(h)
    return found[:3]


def headline_job(job, pid, text):
    try:
        out = ask(REWRITE_HEADLINE.format(text=text))
        heads = parse_headlines(out)
        if not heads:
            raise GeminiError("Gemini answered but no headlines could be read from the answer.")
        with DATA_LOCK:
            data = read_json(COMPARE, {})
            data.setdefault(pid, {})["headlines"] = heads
            write_json(COMPARE, data)
        JOBS[job] = {"state": "done", "n": len(heads)}
    except GeminiError as e:
        JOBS[job] = {"state": "error", "message": str(e)}
    except Exception as e:  # noqa: BLE001
        JOBS[job] = {"state": "error", "message": f"{type(e).__name__}: {e}"}


@app.post("/api/gemini/<rid>")
def gemini_start(rid):
    if (request.get_json(silent=True) or {}).get("what") == "headlines" and not rid.startswith("r"):
        item = find_post(read_json(POSTS, []), rid)
        if not item:
            return jsonify(error="unknown item"), 404
        job = secrets.token_hex(4)
        JOBS[job] = {"state": "running"}
        threading.Thread(target=headline_job, args=(job, rid, item["text"]), daemon=True).start()  # ideas for the text as it is now
        return jsonify(job=job)
    if rid.startswith("r"):
        item = next((r for r in load_reels() if r["id"] == rid), None)
        caption, sheet = (item or {}).get("claude_caption") or (item or {}).get("caption", ""), "Reels"
    else:
        item = find_post(read_json(POSTS, []), rid)
        caption, sheet = (item or {}).get("claude_text") or (item or {}).get("text", ""), "Posts"
    if not item:
        return jsonify(error="unknown item"), 404
    job = secrets.token_hex(4)
    JOBS[job] = {"state": "running"}
    threading.Thread(target=gemini_job, args=(job, rid, sheet, caption), daemon=True).start()
    return jsonify(job=job)


@app.get("/api/job/<job>")
def job_status(job):
    return jsonify(JOBS.get(job, {"state": "error", "message": "unknown job"}))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    port = int(os.environ.get("PANEL_PORT", "8765"))
    host = "0.0.0.0" if PIN else "127.0.0.1"
    print(f"Panel: http://localhost:{port}" + ("   (PIN required, also reachable from other devices)" if PIN else
                                               "   (local only; set PANEL_PIN in .env to allow your phone)"))
    app.run(host=host, port=port, threaded=True)
