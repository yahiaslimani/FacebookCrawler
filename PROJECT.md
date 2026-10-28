# FacebookScraper: Project Overview

A Scrapy project that scrapes public Facebook Page posts (text, images, reaction/comment/share counts, comments). Rendering is done by a remote Playwright browser running in Docker, and Scrapy parses the rendered desktop ("Comet") HTML and its embedded Relay JSON.

> Educational use only. Not authorized by Facebook. A scraping account can be suspended if the spider runs too fast.

## Architecture

```
 make auth (save_auth.py)          make pw-server-up
 visible Chromium, manual login    Docker: Playwright run-server :3000
          |                                  ^
          v                                  | ws://localhost:3000/
      auth.json  ----- storage_state ---->  Scrapy + scrapy-playwright
 (gitignored session cookies)                |
                                             v
                      fb_page spider -> items -> JSON feed (fb.json)
```

- **Browser as a service.** Chromium runs in a Docker container (`mcr.microsoft.com/playwright:v1.63.0-noble`). Scrapy connects over `PLAYWRIGHT_CONNECT_URL`, which keeps the Python process light. The client and image versions must match (`Makefile` `PW_VERSION`).
- **Manual login, once.** Automated login trips bot detection. `save_auth.py` opens a visible browser, you log in by hand, and the Playwright storage state is saved to `auth.json`. Every request reuses it via `playwright_context_kwargs.storage_state`.
- **Desktop layout.** Scraping targets the desktop site, not mobile. A mobile version is a TODO.

## Layout

| Path | Purpose |
|---|---|
| [save_auth.py](save_auth.py) | One-time manual login that writes `auth.json` |
| [facebook/settings.py](facebook/settings.py) | Scrapy and Playwright settings (UA, delay, handlers, reactor) |
| [facebook/spiders/fb_base.py](facebook/spiders/fb_base.py) | `FbBaseSpider`: parses `page_id` arg, session check, fan-out per page |
| [facebook/spiders/fb_page.py](facebook/spiders/fb_page.py) | `FbPageSpider` (`fb_page`): timeline to story to post item |
| [facebook/items.py](facebook/items.py) | `FacebookPostItem`, `FacebookCommentItem` and their loaders |
| [facebook/pipelines.py](facebook/pipelines.py) | Placeholder pass-through pipeline (not enabled) |
| [Makefile](Makefile) | `auth`, `pw-server-*`, `install`, `crawl`, `check`, `lint`, `freeze` |
| [requirement.text](requirement.text) | Pinned dependencies (note the `.text` extension) |

## Request flow

1. **`start()`** in `FbBaseSpider` requests `https://www.facebook.com/` with the saved session.
2. **`parse()`** raises `CloseSpider` if `#login_form` is present (session expired). Otherwise it yields one timeline request per `page_id`, waiting 2 s for render.
3. **`parse_page()`** reads the page name (`og:title`) and extracts the actor id and story id with a regex on the UFI JSON (`"id":"…" … "subscription_target_id":"…"`). It builds `story.php?story_fbid=…&id=…` and requests it, waiting 4 s.
4. **`parse_post()`** picks the top-level `div[role=article]` that links the post (falls back to the one with `data-ad-preview="message"`, then the first). It fills the item:
   - `post_text` from `div[data-ad-preview="message"]`, with a short template header dropped by `_join_paragraphs`.
   - `reaction_count`, `comment_count`, `share_count` by regex over the embedded Relay JSON. The share count is keyed by the base64 id `feedback:{post_id}`.
   - Nested articles become comments (`_load_comment`). Reaction counts are keyed by the base64 id `feedback:{post}_{comment}`.

## Output schema

Post item: `page_id`, `page_name`, `page_url`, `post_id`, `post_url`, `post_text`, `image_urls`, `reaction_count`, `comment_count`, `share_count`, `comments[]`.

Comment item: `comment_id`, `comment_text`, `comment_reaction_count`, `author_name`, `author_url`.

## Usage

```
make install
make pw-server-up
make auth                                   # once, or when the session expires
FB_PAGE_ID=ejeab,otherpage FB_OUTPUT=fb.json make crawl
```

`FB_PAGE_ID` takes one page or a comma-separated list. `make lint` runs ruff and pylint on `facebook/`.

## Key settings

- `DOWNLOAD_DELAY = 3`: raise it if the account gets challenged.
- `USER_AGENT`: desktop Chrome 126, forwarded into browser navigations.
- `PLAYWRIGHT_DEFAULT_NAVIGATION_TIMEOUT = 30 s`.
- `TWISTED_REACTOR`: asyncio selector reactor (required by scrapy-playwright).
- `ROBOTSTXT_OBEY = False`.

## Known limitations

- **Only the newest post per page.** One timeline load yields one story url. Older posts and full comment pages need scroll-driven loading (TODO).
- **Fragile selectors and regexes.** Parsing depends on Facebook's current Comet markup and Relay JSON shape. Empty output usually means the timeline render changed.
- **Language follows the account.** Post and comment text language depends on the logged-in account's language setting.
- **`image_urls` and `post_id`.** Both are declared on the item. `post_id` is computed in `parse_post` but not currently added to the loader, and `image_urls` is never populated, so check these before relying on them.
- **Pipeline unused.** `FacebookPipeline` is a stub and not enabled in `ITEM_PIPELINES`.
- **No tests.** Only `scrapy check` (no contracts defined) and lint.
- **Housekeeping.** The `Makefile` declares `FB_EMAIL` and `FB_PASSWORD` that are unused now that login is manual. The README says "Python 3.14" with pinned versions.

## Troubleshooting

| Symptom | Fix |
|---|---|
| Bot challenge or checkpoint | Raise `DOWNLOAD_DELAY`, clear it in the `make auth` window, reuse the session |
| `Session expired, rerun python save_auth.py` | `make auth` |
| Connection refused on `ws://localhost:3000/` | `make pw-server-up`, and check the client and image versions match |
| Interstitial or stub pages | `USER_AGENT` does not match the surface |
| Empty output | Re-snapshot the timeline and check the article selectors |

## TODO

- Mobile layout (fewer requests, smaller bodies).
- Infinite scroll for more posts and comments.
