"""One-time manual login that saves a Playwright storage state.

Run: python save_auth.py
A normal Chrome (no automation flags) opens at the Facebook login
page with its own profile folder. Log in with your test account,
complete the two-factor approval or the bot challenge if one appears,
wait until you see your feed, then press Enter here. Playwright then
attaches to that Chrome only to copy the session cookies into
auth.json for the spider to reuse.
"""

import asyncio
import os

from playwright.async_api import async_playwright

URL = "https://www.facebook.com/"
OUTPUT = "auth.json"
PROFILE_DIR = os.path.abspath(".chrome-profile")
DEBUG_PORT = 9222
CHROME_PATHS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
]


def find_chrome() -> str:
    """Return the first installed Chrome executable."""
    for path in CHROME_PATHS:
        if os.path.exists(path):
            return path
    raise SystemExit("Chrome not found, edit CHROME_PATHS in save_auth.py.")


async def main() -> None:
    chrome = await asyncio.create_subprocess_exec(
        find_chrome(),
        f"--remote-debugging-port={DEBUG_PORT}",
        f"--user-data-dir={PROFILE_DIR}",
        "--no-first-run",
        URL,
    )
    try:
        input("Log in and clear 2FA or the bot challenge, then press Enter here... ")
        async with async_playwright() as p:
            browser = await p.chromium.connect_over_cdp(f"http://localhost:{DEBUG_PORT}")
            await browser.contexts[0].storage_state(path=OUTPUT)
        print(f"Saved to {OUTPUT}")
    finally:
        chrome.terminate()
        await asyncio.sleep(1)


if __name__ == "__main__":
    asyncio.run(main())
