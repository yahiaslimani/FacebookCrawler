"""Remove duplicate posts from data/*.jsonl, keeping the fullest copy.

Run: python clean_data.py
Facebook's pfbid post links change between scrolls, so a resumed run can
save the same post twice under different urls. Posts are matched on
post_id, and the copy with text and the most comments wins. A backup of
every file is kept in data/_backup before it is rewritten.
"""

import json
import shutil
import time
from pathlib import Path

DATA_DIR = Path("data")
BACKUP_DIR = DATA_DIR / "_backup"


def fullness(post: dict) -> tuple:
    """Sort key, bigger means a more complete copy of the post."""
    return (bool(post.get("post_text")), len(post.get("comments", [])), len(post.get("post_text", "")))


def clean(path: Path) -> tuple[int, int]:
    """Rewrite one file without duplicates, return (before, after)."""
    posts = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    best: dict = {}
    for post in posts:
        key = post.get("post_id") or post["post_url"]
        if key not in best or fullness(post) > fullness(best[key]):
            best[key] = post
    if len(best) != len(posts):
        BACKUP_DIR.mkdir(exist_ok=True)
        shutil.copy(path, BACKUP_DIR / f"{path.stem}.{time.strftime('%Y%m%d-%H%M%S')}.jsonl")
        with path.open("w", encoding="utf-8") as handle:
            for post in best.values():
                handle.write(json.dumps(post, ensure_ascii=False) + "\n")
    return len(posts), len(best)


def main() -> None:
    """Clean every data file."""
    for path in sorted(DATA_DIR.glob("*.jsonl")):
        before, after = clean(path)
        print(f"{path.name}: {before} -> {after} posts")


if __name__ == "__main__":
    main()
