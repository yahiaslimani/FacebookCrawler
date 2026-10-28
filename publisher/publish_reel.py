"""Publish a finished reel (assets/reels_out/<id>.mp4) to the Facebook Page and Instagram.

    python publisher/publish_reel.py r01 --test      # SAFE: Facebook as a private DRAFT + Instagram container only; nothing public
    python publisher/publish_reel.py r01             # publish for real (asks you to type the reel id again to confirm)
    python publisher/publish_reel.py r01 --facebook  # only one platform (also --instagram)

Facebook: Reels API in three steps (start, upload the file, finish). Instagram: a REELS media container, the file is
uploaded to Meta's resumable-upload host (no public URL needed), then media_publish. The caption is the reel's chosen
caption plus the hashtags. Needs FB_PAGE_ID, FB_PAGE_TOKEN, IG_USER_ID in .env (same token as the posts).
"""

import json
import os
import sys
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
OUT_DIR = ROOT / "assets" / "reels_out"
GRAPH = "https://graph.facebook.com/v21.0"
HASHTAGS = "#أنا_و_وليدي #أمومة #ماما #الجزائر #نصائح_أمهات #mamanalgerienne"


def call(method, url, retries=4, **kw):
    """HTTP call that survives the flaky connection (retries on network errors, raises on Graph errors)."""
    last = None
    for _ in range(retries):
        try:
            resp = requests.request(method, url, timeout=180, **kw)
            data = resp.json() if resp.content else {}
            if resp.status_code >= 400 or (isinstance(data, dict) and "error" in data):
                raise RuntimeError(f"{url.split('?')[0]}: {data.get('error', data)}")
            return data
        except (requests.ConnectionError, requests.Timeout) as e:
            last = e
            time.sleep(3)
    raise RuntimeError(f"network problem: {last}")


def facebook_reel(video, caption, env, publish):
    """Returns the Facebook video id. publish=False leaves it as a DRAFT visible only to Page admins."""
    token, page = env["FB_PAGE_TOKEN"], env["FB_PAGE_ID"]
    start = call("POST", f"{GRAPH}/{page}/video_reels", data={"upload_phase": "start", "access_token": token})
    vid, upload_url = start["video_id"], start["upload_url"]
    size = video.stat().st_size
    with open(video, "rb") as fh:
        call("POST", upload_url, headers={"Authorization": f"OAuth {token}", "offset": "0", "file_size": str(size)}, data=fh.read())
    call("POST", f"{GRAPH}/{page}/video_reels", data={"upload_phase": "finish", "video_id": vid, "description": caption,
                                                        "video_state": "PUBLISHED" if publish else "DRAFT", "access_token": token})
    for _ in range(40):  # wait until Facebook has processed the video
        st = call("GET", f"{GRAPH}/{vid}", params={"fields": "status", "access_token": token}).get("status", {})
        if st.get("video_status") in ("ready", "published", "draft") or st.get("uploading_phase", {}).get("status") == "complete" and \
                st.get("processing_phase", {}).get("status") in ("complete", None):
            return vid, st
        if st.get("video_status") == "error":
            raise RuntimeError(f"Facebook could not process the reel: {st}")
        time.sleep(4)
    return vid, st


def instagram_reel(video, caption, env, publish):
    """Returns (container id, final media id or None). publish=False stops once Instagram accepted and processed the file."""
    token, ig = env["FB_PAGE_TOKEN"], env["IG_USER_ID"]
    box = call("POST", f"{GRAPH}/{ig}/media", data={"media_type": "REELS", "upload_type": "resumable", "caption": caption,
                                                    "share_to_feed": "true", "access_token": token})
    cid = box["id"]
    size = video.stat().st_size
    with open(video, "rb") as fh:
        call("POST", f"https://rupload.facebook.com/ig-api-upload/v21.0/{cid}",
             headers={"Authorization": f"OAuth {token}", "offset": "0", "file_size": str(size)}, data=fh.read())
    status = {}
    for _ in range(60):
        status = call("GET", f"{GRAPH}/{cid}", params={"fields": "status_code,status", "access_token": token})
        if status.get("status_code") == "FINISHED":
            break
        if status.get("status_code") in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"Instagram could not process the reel: {status}")
        time.sleep(4)
    else:
        raise RuntimeError(f"Instagram did not finish processing in time: {status}")
    if not publish:
        return cid, None
    media = call("POST", f"{GRAPH}/{ig}/media_publish", data={"creation_id": cid, "access_token": token})
    return cid, media["id"]


def reel_caption(reel):
    return f"{reel['caption'].strip()}\n\n{HASHTAGS}"


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    load_dotenv(ROOT.parent / ".env")
    env = {k: os.environ.get(k, "") for k in ("FB_PAGE_ID", "FB_PAGE_TOKEN", "IG_USER_ID")}
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        sys.exit(__doc__)
    rid, test = args[0], "--test" in sys.argv
    reels = json.loads((ROOT / "reels.json").read_text(encoding="utf-8"))
    reel = next((r for r in reels if r["id"] == rid), None)
    video = OUT_DIR / f"{rid}_publish.mp4"  # the version chosen in the panel
    if not video.exists():
        video = OUT_DIR / f"{rid}.mp4"
    if not reel or not video.exists():
        sys.exit(f"{rid}: no finished video at {video}. Build it first (panel: Build reel).")
    if reel.get("review") and rid not in json.loads((ROOT / "state.json").read_text(encoding="utf-8")).get("approved", []) and not test:
        sys.exit(f"{rid} is a medical reel and has not been approved.")
    only_fb, only_ig = "--facebook" in sys.argv, "--instagram" in sys.argv
    if not test:
        if input(f"This publishes {rid} PUBLICLY to Facebook and Instagram. Type {rid} to confirm: ").strip() != rid:
            sys.exit("not confirmed, nothing published")
    caption = reel_caption(reel)
    result = {}
    if not only_ig:
        vid, st = facebook_reel(video, caption, env, publish=not test)
        result["facebook"] = {"video_id": vid, "status": st.get("video_status"), "draft": test}
        print("Facebook:", result["facebook"], flush=True)
    if not only_fb:
        cid, media = instagram_reel(video, caption, env, publish=not test)
        result["instagram"] = {"container": cid, "media": media, "published": media is not None}
        print("Instagram:", result["instagram"], flush=True)
    (OUT_DIR / f"{rid}.publish.json").write_text(json.dumps(result, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
