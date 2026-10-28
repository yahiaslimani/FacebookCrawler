"""Scrape every page listed in pages.txt, one after another.

Run: python run_pages.py [max_posts]
Each page is saved to data/<name>.jsonl as items arrive. A run that
stalls (no new item for STALL seconds, the Python 3.14.0 asyncio pipe
bug can freeze it) is killed and restarted, resuming from the file. It
stops everything if the Facebook session has expired, since retrying
cannot fix that. Pass max_posts to cap each page, handy for a test run.
"""

import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

PAGES_FILE = "pages.txt"
DATA_DIR = Path("data")
LOG_DIR = Path("logs")
STALL = 480
MAX_ATTEMPTS = 15
PAUSE_BETWEEN_PAGES = 300


def wait_for_network(limit: int = 7200) -> bool:
    """Block until facebook.com answers, False if it never does."""
    waited = 0
    while True:
        try:
            urllib.request.urlopen("https://www.facebook.com/", timeout=20)
            return True
        except urllib.error.HTTPError:
            return True  # it answered, even if with an error code
        except OSError:
            if waited >= limit:
                return False
            print(f"{time.strftime('%H:%M:%S')} no network, waiting", flush=True)
            time.sleep(60)
            waited += 60


def page_from_url(url: str) -> tuple[str, str]:
    """Return (page_id for the spider, file name) for a facebook url."""
    parts = urlsplit(url.strip())
    path = parts.path.strip("/")
    if path == "profile.php":
        number = parse_qs(parts.query).get("id", [""])[0]
        return f"profile.php?id={number}", f"profile_{number}"
    return path, path


def read_pages() -> list[str]:
    """Urls from pages.txt without blanks and comments."""
    lines = Path(PAGES_FILE).read_text(encoding="utf-8").splitlines()
    return [line.strip() for line in lines if line.strip() and not line.startswith("#")]


def run_page(url: str, max_posts: str) -> bool:
    """Scrape one page with restarts. False means stop everything."""
    page_id, name = page_from_url(url)
    out = DATA_DIR / f"{name}.jsonl"
    for attempt in range(1, MAX_ATTEMPTS + 1):
        if not wait_for_network():
            print("Network is down, stopping. Rerun when it is back.", flush=True)
            return False
        log = LOG_DIR / f"{name}_{attempt}.log"
        cmd = [
            sys.executable, "-m", "scrapy", "crawl", "fb_page",
            "-a", f"page_id={page_id}", "-a", f"out_file={out}",
            "-s", f"LOG_FILE={log}",
        ]
        if max_posts:
            cmd += ["-a", f"max_posts={max_posts}"]
        print(f"{time.strftime('%H:%M:%S')} {name}: attempt {attempt}", flush=True)
        started = time.time()
        with subprocess.Popen(cmd) as proc:
            while proc.poll() is None:
                time.sleep(30)
                last = max(started, out.stat().st_mtime if out.exists() else 0)
                if time.time() - last > STALL:
                    print(f"{name}: stalled, restarting", flush=True)
                    proc.kill()
                    break
        text = log.read_text(encoding="utf-8", errors="replace") if log.exists() else ""
        if "Session expired" in text:
            print("Session expired, run save_auth.py and start again.", flush=True)
            return False
        if "login_wall" in text:
            print("Facebook is throttling (login wall), stopping. Wait a few hours "
                  "and rerun, saved posts are skipped.", flush=True)
            return False
        if "Gave up retrying" in text and "net::ERR" in text:
            # The spider closes cleanly after a network failure, which
            # would otherwise look like a page with no posts.
            print(f"{name}: network error, retrying", flush=True)
            continue
        if "Spider closed (finished)" in text:
            count = sum(1 for _ in out.open(encoding="utf-8")) if out.exists() else 0
            print(f"{name}: finished, {count} posts saved", flush=True)
            return True
    print(f"{name}: gave up after {MAX_ATTEMPTS} attempts", flush=True)
    return True


def main() -> None:
    """Run every listed page, pausing between them."""
    DATA_DIR.mkdir(exist_ok=True)
    LOG_DIR.mkdir(exist_ok=True)
    max_posts = sys.argv[1] if len(sys.argv) > 1 else ""
    pages = read_pages()
    for number, url in enumerate(pages):
        if not run_page(url, max_posts):
            return
        if number < len(pages) - 1:
            print(f"pausing {PAUSE_BETWEEN_PAGES}s before the next page", flush=True)
            time.sleep(PAUSE_BETWEEN_PAGES)


if __name__ == "__main__":
    main()
