"""Page spider: scroll the timeline for post urls, then parse each post."""

import base64
import json
import os
import re
from collections import Counter
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from itemloaders.processors import MapCompose
from scrapy import Request
from scrapy.exceptions import CloseSpider
from scrapy.http import Response
from scrapy.selector import Selector
from scrapy_playwright.page import PageMethod

from ..items import FacebookCommentItemLoader, FacebookPostItemLoader
from .fb_base import AUTH_FILE, GOTO_KWARGS, FbBaseSpider

# Post links come in three shapes. Query strings carry tracking noise, so
# rebuild each link from the parts that identify the post.
POST_LINKS_JS = """() => [...document.querySelectorAll('a[href]')].flatMap(a => {
    const u = new URL(a.href);
    const q = u.searchParams;
    if (/\\/posts\\/pfbid/.test(u.pathname)) return [u.origin + u.pathname];
    if (u.pathname.startsWith('/photo') && q.get('fbid')) {
        return [u.origin + '/photo/?fbid=' + q.get('fbid') + (q.get('set') ? '&set=' + q.get('set') : '')];
    }
    if (u.pathname === '/permalink.php' && q.get('story_fbid')) {
        return [u.origin + '/permalink.php?story_fbid=' + q.get('story_fbid') + '&id=' + q.get('id')];
    }
    return [];
})"""
# The first h1 on a profile is often the "Notifications" panel title.
HEADING_JS = """() => [...document.querySelectorAll('h1')]
    .map(h => h.innerText.trim()).find(t => t && t !== 'Notifications')"""
SCROLL_STEP = 3000
SCROLL_PAUSE = 2500
IDLE_LIMIT = 5
MAX_SCROLLS = 500
FEEDBACK_WINDOW = 600
POST_RENDER_WAIT = 7000
WALL_RE = re.compile(
    r"Forgot password|Create new account|See more on Facebook"
    r"|Mot de passe oublié|Créer un nouveau compte|Découvrez plus de contenu",
    re.IGNORECASE,
)
WALL_LIMIT = 5
CAPTION_RE = re.compile(
    r'"message":\{(?:"[a-z_]+":(?:null|\[\]|"[^"]*"|\d+),){0,6}?"text":"((?:[^"\\]|\\.){3,})"'
)
CAPTION_WINDOW = 5000


class FbPageSpider(FbBaseSpider):
    """Scrape one Facebook page timeline into post items."""

    name = 'fb_page'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # max_posts=0 means every post the timeline will give us.
        self.max_posts = int(kwargs.get("max_posts", 0))
        self.max_scrolls = int(kwargs.get("max_scrolls", MAX_SCROLLS))
        # out_file=fb_all.jsonl saves every item as it is scraped, and
        # skips posts an earlier run already saved there.
        self.out_file = kwargs.get("out_file")
        self.done = self._load_done(self.out_file)
        self.walls = 0
        self.seen_posts: set = set()

    @staticmethod
    def _load_done(path: str | None) -> set:
        if not path or not os.path.exists(path):
            return set()
        with open(path, encoding="utf-8") as handle:
            return {json.loads(line)["post_url"] for line in handle if line.strip()}

    async def parse_page(self, response: Response, **_kwargs: Any) -> Any:
        """Scroll the timeline, collect post permalinks and follow each."""
        page = response.meta["playwright_page"]
        page_id = response.meta.get("page_id")
        page_name = response.css('meta[property="og:title"]::attr(content)').get()
        if not page_name:
            title = response.xpath("//title/text()").get()
            page_name = re.sub(r"^\(\d+\)\s*", "", title or "")
        if page_name in ("", "Facebook"):
            # New-style pages title themselves just "Facebook", use the heading.
            heading = await page.evaluate(HEADING_JS)
            page_name = (heading or "").strip()
        found = 0
        try:
            async for post_url in self._scroll_post_urls(page, page_id):
                found += 1
                yield Request(
                    url=post_url,
                    callback=self.parse_post,
                    meta={
                        "page_id": page_id,
                        "page_name": page_name,
                        "page_url": response.url,
                        "post_url": post_url,
                        "playwright": True,
                        "playwright_context_kwargs": {"storage_state": AUTH_FILE},
                        "playwright_page_goto_kwargs": GOTO_KWARGS,
                        "playwright_page_methods": [
                            PageMethod("wait_for_timeout", POST_RENDER_WAIT),
                        ],
                    },
                )
        finally:
            await page.close()
        self.logger.info("%s: %d posts found", page_id, found)

    async def _scroll_post_urls(self, page: Any, page_id: str) -> Any:
        # Facebook unloads posts that scroll out of view, so links have
        # to be gathered at every step. New ones are yielded right away
        # so posts get scraped while the timeline is still scrolling.
        seen: set = set()
        idle = 0
        for step in range(self.max_scrolls):
            if step % 10 == 0:
                self.logger.info("scroll %d: %d posts so far", step, len(seen))
            new = 0
            for href in await page.evaluate(POST_LINKS_JS):
                if href in seen or not self._own_post(href, page_id):
                    continue
                seen.add(href)
                new += 1
                if href not in self.done:
                    yield href
                if 0 < self.max_posts <= len(seen):
                    return
            idle = 0 if new else idle + 1
            if idle >= IDLE_LIMIT:
                return
            await page.mouse.wheel(0, SCROLL_STEP)
            await page.wait_for_timeout(SCROLL_PAUSE)

    @staticmethod
    def _own_post(href: str, page_id: str) -> bool:
        # Shared posts from other pages sit on the timeline too. Numeric
        # ids (also profile.php?id=...) cannot be matched against the
        # permalink slug, accept all.
        if not page_id or page_id.isdigit() or page_id.startswith("profile.php"):
            return True
        return f"/{page_id.lower()}/posts/" in href.lower()

    def parse_post(self, response: Response, **_kwargs: Any) -> Any:
        """Extract the post item with counts and comments."""
        # open_in_browser(response)
        if self._login_wall(response):
            # Facebook throttles heavy sessions with a logged-out view that
            # hides the text. Saving it would store empty posts, skip it
            # so a later resume fetches it again, and stop if it persists.
            self.walls += 1
            self.logger.warning("login wall on %s (%d in a row)", response.url, self.walls)
            if self.walls >= WALL_LIMIT:
                raise CloseSpider("login_wall: Facebook is throttling, retry later")
            return
        self.walls = 0
        post_id = self._post_id(response.text)
        if post_id in self.seen_posts:
            return  # another photo of a post already scraped
        self.seen_posts.add(post_id)
        root = self._post_root(response)
        # Photo and video posts open in a different layout without the
        # dialog, take comments from the whole page and the caption from JSON.
        post_sel = root if root is not None else response.selector
        text = self._post_text(root) if root is not None else ""
        text = text or self._json_caption(response.text, post_id)
        if not text and not post_sel.css('div[role="article"]'):
            self.logger.warning("no post content on %s", response.url)
            return

        loader = FacebookPostItemLoader(selector=post_sel)
        loader.add_value("page_id", response.meta.get("page_id"))

        loader.add_value("page_name", self._page_name(response))
        loader.add_value("page_url", response.meta.get("page_url"))
        loader.add_value("post_id", post_id)
        loader.add_value("post_url", response.meta.get("post_url"))
        loader.add_value("post_text", text)
        loader.add_value("reaction_count", self._feedback_count(
            response.text, post_id, r'"reaction_count":\{"count":(\d+)\}',
        ))
        loader.add_value("comment_count", self._feedback_count(
            response.text, post_id, r'"comments":\{"total_count":(\d+)\}',
        ))
        loader.add_value("share_count", self._feedback_count(
            response.text, post_id, r'"share_count":\{"count":(\d+)\}',
        ))
        loader.add_value("image_urls", self._image_urls(post_sel))

        seen = set()
        for comment in post_sel.css('div[role="article"]'):
            comment_loader = self._load_comment(comment, response, post_id)
            if comment_loader is None:
                continue
            item = comment_loader.load_item()
            if item.get("comment_id") in seen:
                continue
            seen.add(item.get("comment_id"))
            loader.add_value("comments", item)

        item = loader.load_item()
        if item.get("comment_count") and not item.get("comments"):
            self.logger.warning("comments missing on %s, render too slow?", response.url)
        yield item

    @staticmethod
    def _post_root(response: Response) -> Selector | None:
        # The story opens as a dialog holding the message and the
        # comments. Dialogs nest, the first one is the outermost. The
        # feed behind it has its own message blocks, so never search
        # the whole document first.
        message = 'div[data-ad-preview="message"]'
        dialogs = response.css('div[role="dialog"]')
        for dialog in dialogs:
            if dialog.css(message):
                return dialog
        # Posts without the message marker (styled text, photo or video
        # posts) still open in a dialog holding the comments.
        for dialog in dialogs:
            if dialog.css('div[role="article"]'):
                return dialog
        return next(iter(response.css(message)), None)

    @staticmethod
    def _post_text(root: Selector) -> str:
        marked = root.css('div[data-ad-preview="message"] div[dir="auto"] ::text').getall()
        if marked:
            return FbPageSpider._join_paragraphs(marked)
        # No marker, the text is a plain block outside the comments.
        blocks = root.xpath(
            './/div[@dir="auto"][not(ancestor::*[@role="article"])]'
            '[not(ancestor::div[@dir="auto"])]'
        )
        return FbPageSpider._join_paragraphs(blocks[0].css("::text").getall() if blocks else [])

    @staticmethod
    def _image_urls(root: Selector) -> list:
        # Comments carry avatars and stickers, keep post media only.
        urls = []
        for img in root.css('img[src*="scontent"]'):
            if img.xpath('./ancestor::*[@role="article"]').get():
                continue
            src = img.attrib["src"]
            if src not in urls:
                urls.append(src)
        return urls

    def _load_comment(self, comment: Selector, response: Response, post_id: str) -> Any:
        href = comment.css('a[href*="comment_id"]::attr(href)').get()
        if not href:
            return None
        comment_id = self._comment_id(href)
        comment_loader = FacebookCommentItemLoader(selector=comment)
        comment_loader.add_value("comment_id", comment_id)
        comment_loader.add_css("comment_text", 'div[dir="auto"] ::text')
        name = comment.css('a[href*="comment_id"] ::text').get()
        comment_loader.add_value("author_name", (name or "").strip())
        comment_loader.add_value(
            "author_url",
            href,
            MapCompose(response.urljoin, self._profile_url),
        )
        comment_loader.add_value(
            "comment_reaction_count",
            self._comment_reactions(response.text, post_id, comment_id),
        )
        return comment_loader

    @staticmethod
    def _profile_url(url: str) -> str:
        # Drop tracking and comment params, keep the id of profile.php urls.
        parts = urlsplit(url)
        query = ""
        if parts.path.endswith("profile.php"):
            query = "?id=" + dict(parse_qsl(parts.query)).get("id", "")
        return f"{parts.scheme}://{parts.netloc}{parts.path}{query}"

    @staticmethod
    def _join_paragraphs(values: list) -> str:
        # First block is sometimes a template header (emoji plus
        # names), the rest are the message paragraphs.
        texts = [v.strip() for v in values if v and v.strip()]
        if len(texts) > 1 and len(texts[0]) < 40:
            texts = texts[1:]
        return "\n".join(texts)

    @staticmethod
    def _comment_id(href: str) -> str:
        raw = dict(parse_qsl(urlsplit(href).query)).get("comment_id", "")
        if "_" in raw:
            return raw.split("_")[-1]
        try:
            decoded = base64.b64decode(raw + "==").decode()
            return decoded.split("_")[-1]
        except (ValueError, UnicodeDecodeError):
            return raw

    @staticmethod
    def _page_name(response: Response) -> str:
        title = response.xpath("//title/text()").get() or ""
        tail = title.split(" - ")[-1].replace(" | Facebook", "")
        title_name = re.sub(r"^\(\d+\)\s*", "", tail).strip()
        # An unloaded timeline reports the bare site title as the name.
        names = [response.meta.get("page_name"), title_name, response.meta.get("page_id")]
        return next((n for n in names if n and n != "Facebook"), "")

    @staticmethod
    def _json_caption(text: str, post_id: str) -> str:
        # The caption is also in the page JSON as "message":{..."text":"..."}.
        # Other stories on the page have their own, far from this post's id,
        # so take the one nearest to it.
        if not post_id:
            return ""
        key = base64.b64encode(f"feedback:{post_id}".encode()).decode()
        anchors = [m.start() for m in re.finditer(re.escape(key), text)]
        anchors += [m.start() for m in re.finditer(f'"post_id":"{post_id}"', text)]
        best, best_dist = "", CAPTION_WINDOW
        for match in CAPTION_RE.finditer(text):
            dist = min((abs(match.start() - a) for a in anchors), default=best_dist)
            if dist < best_dist:
                best_dist = dist
                try:
                    best = json.loads(f'"{match.group(1)}"')
                except ValueError:
                    best = match.group(1)
        return best.strip()

    @staticmethod
    def _login_wall(response: Response) -> bool:
        # Only the dialog text, the page scripts carry these phrases always.
        text = " ".join(response.xpath('//div[@role="dialog"]//text()').getall())
        return bool(WALL_RE.search(text))

    @staticmethod
    def _post_id(text: str) -> str:
        # Comment feedback ids are base64 of feedback:{post}_{comment},
        # so the dominant prefix is the post. The page JSON also holds
        # ids of other stories, which makes the first match unreliable.
        prefixes: Counter = Counter()
        for key in set(re.findall(r'"id":"(ZmVlZGJhY2s6[A-Za-z0-9+/=]+)"', text)):
            try:
                raw = base64.b64decode(key + "==").decode()
            except ValueError:
                continue
            if "_" in raw:
                prefixes[raw.split(":")[1].split("_")[0]] += 1
        if prefixes:
            return prefixes.most_common(1)[0][0]
        ids = Counter(re.findall(r'"post_id":"(\d+)"', text))
        return ids.most_common(1)[0][0] if ids else ""

    @staticmethod
    def _feedback_count(text: str, post_id: str, pattern: str) -> int:
        # The post's own counts sit right next to its feedback id. Other
        # stories on the page carry the same fields thousands of
        # characters away, so take the match nearest to the key.
        if not post_id:
            return 0
        key = base64.b64encode(f"feedback:{post_id}".encode()).decode()
        anchors = [m.start() for m in re.finditer(re.escape(key), text)]
        best, best_dist = 0, FEEDBACK_WINDOW
        for match in re.finditer(pattern, text):
            dist = min((abs(match.start() - a) for a in anchors), default=best_dist)
            if dist < best_dist:
                best, best_dist = int(match.group(1)), dist
        return best

    @staticmethod
    def _comment_reactions(text: str, post_id: str, comment_id: str) -> int:
        # Relay ids comments as base64 of feedback:{post}_{comment}.
        if post_id and comment_id:
            key = base64.b64encode(f"feedback:{post_id}_{comment_id}".encode()).decode()
            match = re.search(
                r'"reaction_count":(\d+),"id":"' + re.escape(key) + '"', text
            )
            if match:
                return int(match.group(1))
        return 0
