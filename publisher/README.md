# Auto-publisher for "أنا وولدي" (Facebook + Instagram)

`posts.json` holds 12 ready posts in Darija, written from the findings in `analysis/content_insights_darija.md`
(numbered lists, first-person moments, reassurance, no contact info, no links, no begging for shares).
`publish.py` renders a text card for each post and publishes it to the Facebook Page and the linked Instagram account
through the official Meta Graph API.

## One-time setup

1. Instagram must be a **Business or Creator** account linked to your Facebook Page.
2. At developers.facebook.com create an app (type Business) and add the Instagram Graph API.
   While the app is in Development mode and you are its admin, no app review is needed for your own page.
3. In Graph API Explorer generate a user token with: `pages_show_list`, `pages_manage_posts`,
   `pages_read_engagement`, `instagram_basic`, `instagram_content_publish`. Exchange it for a long-lived token,
   then call `GET /me/accounts` to get the **Page access token** (this one does not expire when derived
   from a long-lived user token).
4. Page id: in the `/me/accounts` response. Instagram id: `GET /{page_id}?fields=instagram_business_account`.
5. Copy `.env.example` to `.env` (project root, gitignored) and fill `FB_PAGE_ID`, `FB_PAGE_TOKEN`, `IG_USER_ID`.

## Use

```
python publisher/publish.py --dry-run        # preview text + card (publisher/cards/)
python publisher/publish.py --approve p04 p12  # a doctor/midwife has checked the medical posts
python publisher/publish.py                  # publish the next ready post
python publisher/publish.py --status
```

Posts flagged `"review": true` (sleep and urgent-signs posts) are skipped until you `--approve` them.
Published ids are stored in `publisher/state.json`, so a post is never sent twice.

## Schedule

Task `AnaWlidi-Scheduler` runs `publish.py --auto` every 10 minutes and publishes what is due (`schedule.py`).
A post with `publish_at` ("YYYY-MM-DD HH:MM", PC time) goes out then; the other ready posts fill the default
slots (Mon, Wed, Fri, Sun at 20:30) in file order. A day with a pinned or published post gets no default slot.
A post up to 6 hours late still goes out (PC was off); after that it shows as MISSED in the panel.
Set the times on the panel's Schedule tab. The PC must be on. The old fixed task `AnaWlidi-AutoPost` is disabled.

```
schtasks /create /tn "AnaWlidi-Scheduler" /sc minute /mo 10 ^
  /tr "C:\Users\ey_sl\Python\FacebookCrawler\venv\Scripts\pythonw.exe C:\Users\ey_sl\Python\FacebookCrawler\publisher\publish.py --auto"
```

Add new posts to `posts.json` (same fields) whenever the queue runs low.
