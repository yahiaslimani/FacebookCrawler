"""Assemble a finished reel (9:16, 1080x1920, 30 fps, MP4/H.264) from the owner's clips and the reel's lines.

    python publisher/reels.py r01              # build assets/reels_out/r01.mp4 from reels.json + assets/reels/*

Structure (about 15 s): hook card (2 s), one segment per beat (3.5 s: your clip, cropped to vertical, with the Darija
line in the lower third and the logo on top; a branded text card when a beat has no clip yet), closing card (3 s) with
the follow prompt. Every segment fades in and out softly ("a breath, not a pop"). Text sits inside Instagram's safe
area (clear of the top 250 px and the bottom 340 px, where the app draws its own buttons). Clip audio is dropped; an
optional music file (assets/music/*.mp3) is mixed in quietly.

Cards are drawn by cards._page(), so call build() from the thread that owns it (the panel does this for you).
"""

import html
import shutil
import subprocess
import sys
from pathlib import Path

import imageio_ffmpeg

import cards

ROOT = Path(__file__).resolve().parent
REELS_DIR = ROOT / "assets" / "reels"
OUT_DIR = ROOT / "assets" / "reels_out"
MUSIC_DIR = ROOT / "assets" / "music"
FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
W, H, FPS = 1080, 1920, 30
HOOK_S, BEAT_S, CLOSE_S, FADE = 2.0, 3.5, 3.0, 0.25
IG_HANDLE = "@ana.wlidi"
LOGO_CREAM = cards._logo("#FBF6EE", 72)


def _shot(body, out_path, transparent):
    """Render 1080x1920 HTML with the shared Chromium (viewport is switched for this one screenshot)."""
    page = cards._page()
    doc = f"""<!doctype html><html lang="ar" dir="rtl"><head><meta charset="utf-8"><style>{cards._fonts()}
*{{margin:0;box-sizing:border-box}}
html,body{{width:{W}px;height:{H}px;overflow:hidden;background:{'transparent' if transparent else '#FBF6EE'}}}
.h{{font-family:Marhey;font-weight:600;text-align:center;line-height:1.4;width:100%}}
.box{{display:flex;align-items:center;justify-content:center}}
</style></head><body>{body}</body></html>"""
    page.set_viewport_size({"width": W, "height": H})
    try:
        page.set_content(doc)
        page.evaluate("document.fonts.ready")
        page.evaluate(cards.FIT_JS)
        page.evaluate("window.scrollTo(0, 0); document.documentElement.scrollLeft = 0; document.body.scrollLeft = 0")
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(out_path), type="png", omit_background=transparent)
    finally:
        page.set_viewport_size({"width": cards.W, "height": cards.H})
    return Path(out_path)


def overlay_png(text, out_path):
    """Transparent layer for a clip: soft dark fade at the bottom, the line in the lower third, logo mark on top."""
    body = (
        '<div style="position:absolute;left:0;right:0;top:880px;height:1040px;background:linear-gradient(to bottom,rgba(62,42,32,0),rgba(62,42,32,.62))"></div>'
        f'<div style="position:absolute;top:150px;left:0;right:0;display:flex;justify-content:center;opacity:.95">{LOGO_CREAM}</div>'
        '<div class="box" style="position:absolute;left:110px;right:110px;top:1060px;height:420px">'
        f'<div class="h" data-fit="76" style="color:#FBF6EE;text-shadow:0 3px 14px rgba(0,0,0,.55)">{html.escape(text)}</div></div>'
    )
    return _shot(body, out_path, transparent=True)


def card_png(text, out_path, last=False):
    """Full brand screen (opening hook, closing question, or a beat that has no clip yet)."""
    follow = (f'<div style="font-family:Marhey;font-weight:600;font-size:54px;color:#B85C72;margin-top:40px;text-align:center">تابعينا</div>'
              f'<div dir="ltr" style="font-size:46px;color:#7A5240;text-align:center;margin-top:6px">{IG_HANDLE}</div>') if last else ""
    body = (
        '<div style="position:absolute;inset:0;background:linear-gradient(165deg,#F6DCE0,#FBF6EE 70%)"></div>'
        '<div style="position:absolute;inset:0;overflow:hidden">'
        '<div style="position:absolute;left:-260px;top:-260px;width:760px;height:760px;border-radius:50%;background:#F6DCE0;opacity:.8"></div>'
        '<div style="position:absolute;right:-320px;bottom:-300px;width:860px;height:860px;border-radius:50%;background:#EDBFC7;opacity:.55"></div></div>'
        f'<div style="position:absolute;top:300px;left:0;right:0;display:flex;justify-content:center">{cards._logo("#B85C72", 150)}</div>'
        '<div class="box" style="position:absolute;left:110px;right:110px;top:560px;height:620px">'
        f'<div class="h" data-fit="96" style="color:#7E3A4B">{html.escape(text)}</div></div>'
        f'<div style="position:absolute;top:1200px;left:0;right:0">{follow}</div>'
        + ('' if last else '<div style="position:absolute;bottom:420px;left:0;right:0;text-align:center;font-family:Marhey;font-weight:600;font-size:46px;color:#B85C72">أنا و وليدي</div>')
    )
    return _shot(body, out_path, transparent=False)


def slide_png(index, total, text, out_path):
    """Vertical slide for a numbered-list item: story-style progress bar, number badge, the sentence, brand footer."""
    gap = 10
    seg = (860 - gap * (total - 1)) / total
    bar = "".join(f'<div style="width:{seg}px;height:12px;border-radius:6px;background:{"#B85C72" if i < index else "rgba(126,58,75,.18)"}"></div>'
                  for i in range(total))
    body = (
        '<div style="position:absolute;inset:0;background:linear-gradient(165deg,#FBF6EE,#F6DCE0)"></div>'
        '<div style="position:absolute;inset:0;overflow:hidden"><div style="position:absolute;left:-260px;top:-260px;width:760px;height:760px;border-radius:50%;background:#F6DCE0;opacity:.8"></div>'
        '<div style="position:absolute;right:-320px;bottom:-300px;width:860px;height:860px;border-radius:50%;background:#EDBFC7;opacity:.5"></div></div>'
        f'<div style="position:absolute;top:260px;left:110px;width:860px;display:flex;gap:{gap}px;direction:rtl">{bar}</div>'
        '<div style="position:absolute;top:420px;left:0;right:0;display:flex;justify-content:center">'
        '<div style="width:300px;height:300px;border-radius:50%;background:#B85C72;color:#FBF6EE;display:flex;align-items:center;justify-content:center;'
        f'font-family:Marhey;font-weight:700;font-size:190px;line-height:1;padding-top:16px">{index}</div></div>'
        '<div class="box" style="position:absolute;left:110px;right:110px;top:800px;height:560px">'
        f'<div class="h" data-fit="96" style="color:#7E3A4B">{html.escape(text)}</div></div>'
        '<div style="position:absolute;top:1450px;left:0;right:0;display:flex;justify-content:center;align-items:center;gap:18px;'
        f'font-family:Marhey;font-weight:600;font-size:44px;color:#B85C72">{cards._logo("#B85C72", 60)}<span>أنا و وليدي</span></div>'
    )
    return _shot(body, out_path, transparent=False)


def run_ffmpeg(args):
    proc = subprocess.run([FFMPEG, "-y", "-hide_banner", "-loglevel", "error", *args], capture_output=True, text=True)
    if proc.returncode:
        raise RuntimeError("ffmpeg failed: " + proc.stderr.strip()[-600:])


def segment_from_clip(clip, overlay, seconds, out, start=0.0, pos=0.5):
    """`start` skips into the clip; `pos` (0..1) chooses which vertical strip of a widescreen clip is kept."""
    fade = f"fade=t=in:st=0:d={FADE},fade=t=out:st={seconds - FADE}:d={FADE}"
    graph = (
        f"[0:v]tpad=stop_mode=clone:stop_duration={seconds},trim=duration={seconds},setpts=PTS-STARTPTS,"
        f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H}:(iw-ow)*{pos}:(ih-oh)/2,fps={FPS},format=yuv420p[v];"
        f"[v][1:v]overlay=0:0:format=auto,{fade}[o]"
    )
    run_ffmpeg(["-ss", str(start), "-i", str(clip), "-loop", "1", "-t", str(seconds), "-i", str(overlay), "-filter_complex", graph,
                "-map", "[o]", "-t", str(seconds), "-r", str(FPS), "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                "-pix_fmt", "yuv420p", "-an", str(out)])


def segment_from_card(card, seconds, out):
    fade = f"fade=t=in:st=0:d={FADE},fade=t=out:st={seconds - FADE}:d={FADE}"
    run_ffmpeg(["-loop", "1", "-t", str(seconds), "-i", str(card), "-vf", f"fps={FPS},format=yuv420p,{fade}", "-t", str(seconds),
                "-r", str(FPS), "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-an", str(out)])


def pick_music():
    files = sorted(MUSIC_DIR.glob("*.mp3")) + sorted(MUSIC_DIR.glob("*.m4a")) if MUSIC_DIR.exists() else []
    return files[0] if files else None


def build(reel, out_path=None, clip_dir=None, work=None, music=None):
    """Build the reel and return the path of the finished MP4. `reel` is a reels.json entry."""
    clip_dir = Path(clip_dir or REELS_DIR)
    out_path = Path(out_path or OUT_DIR / f"{reel['id']}.mp4")
    work = Path(work or (OUT_DIR / f"_work_{reel['id']}"))
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True, exist_ok=True)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    segs, used = [], 0
    card_png(reel["hook"], work / "hook.png")
    segment_from_card(work / "hook.png", HOOK_S, work / "s0.mp4"); segs.append(work / "s0.mp4")
    for i, beat in enumerate(reel["beats"], 1):
        seg = work / f"s{i}.mp4"
        clip = clip_dir / beat["file"] if beat.get("file") else None
        if clip and clip.exists():
            overlay_png(beat["text"], work / f"o{i}.png")
            segment_from_clip(clip, work / f"o{i}.png", BEAT_S, seg, beat.get("start", 0.0), beat.get("pos", 0.5))
            used += 1
        else:
            card_png(beat["text"], work / f"c{i}.png")
            segment_from_card(work / f"c{i}.png", BEAT_S, seg)
        segs.append(seg)
    card_png(reel["closing"], work / "close.png", last=True)
    last = work / f"s{len(reel['beats']) + 1}.mp4"
    segment_from_card(work / "close.png", CLOSE_S, last); segs.append(last)
    return _finish(segs, work, out_path, HOOK_S + BEAT_S * len(reel["beats"]) + CLOSE_S, music, len(reel["beats"]), used)


def _finish(segs, work, out_path, total, music, beats, used=0):
    """Join the segments, add (quiet) music or a silent track, write the final MP4 and clean up."""
    (work / "list.txt").write_text("".join(f"file '{s.as_posix()}'\n" for s in segs), encoding="utf-8")
    silent = work / "silent.mp4"
    run_ffmpeg(["-f", "concat", "-safe", "0", "-i", str(work / "list.txt"), "-c", "copy", str(silent)])
    music = Path(music) if music else pick_music()
    if music and music.exists():
        run_ffmpeg(["-i", str(silent), "-stream_loop", "-1", "-i", str(music), "-filter_complex",
                    f"[1:a]volume=0.18,afade=t=in:d=1,afade=t=out:st={total - 1.5}:d=1.5[a]", "-map", "0:v", "-map", "[a]",
                    "-c:v", "copy", "-c:a", "aac", "-b:a", "128k", "-shortest", "-movflags", "+faststart", str(out_path)])
    else:  # a silent AAC track keeps Instagram and Facebook happy with the file
        run_ffmpeg(["-i", str(silent), "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-map", "0:v", "-map", "1:a",
                    "-c:v", "copy", "-c:a", "aac", "-b:a", "96k", "-shortest", "-movflags", "+faststart", str(out_path)])
    shutil.rmtree(work, ignore_errors=True)
    return {"path": out_path, "seconds": total, "clips_used": used, "beats": beats}


def build_slideshow(reel, items, out_path=None, work=None, per_slide=2.8):
    """Option A: the reel made only from our own designs. `items` are the numbered sentences of the companion post."""
    out_path = Path(out_path or OUT_DIR / f"{reel['id']}_slideshow.mp4")
    work = Path(work or (OUT_DIR / f"_work_{reel['id']}_ss"))
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True, exist_ok=True)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    segs = []
    card_png(reel["hook"], work / "hook.png")
    segment_from_card(work / "hook.png", HOOK_S, work / "s0.mp4"); segs.append(work / "s0.mp4")
    for i, text in enumerate(items, 1):
        slide_png(i, len(items), text, work / f"i{i}.png")
        segment_from_card(work / f"i{i}.png", per_slide, work / f"s{i}.mp4"); segs.append(work / f"s{i}.mp4")
    card_png(reel["closing"], work / "close.png", last=True)
    last = work / f"s{len(items) + 1}.mp4"
    segment_from_card(work / "close.png", CLOSE_S, last); segs.append(last)
    return _finish(segs, work, out_path, HOOK_S + per_slide * len(items) + CLOSE_S, None, len(items))


if __name__ == "__main__":
    import json

    sys.stdout.reconfigure(encoding="utf-8")
    rid = sys.argv[1]
    reel = next(r for r in json.loads((ROOT / "reels.json").read_text(encoding="utf-8")) if r["id"] == rid)
    info = build(reel)
    print(f"built {info['path']} ({info['seconds']:.1f} s, {info['clips_used']}/{info['beats']} beats from your clips)")
