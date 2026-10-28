"""When each ready post goes out. Shared by the scheduled runner (publish.py --auto) and the control panel.

A post with `publish_at` ("YYYY-MM-DD HH:MM", PC local time) goes out at that time. The other ready posts
fill the default slots (Mon, Wed, Fri, Sun at 20:30) in file order. A day that already has a pinned or a
published post gets no default slot, so a page never posts twice in one day by accident.
"""

from datetime import datetime, timedelta

SLOT_DAYS, SLOT_TIME = (0, 2, 4, 6), (20, 30)
GRACE = timedelta(hours=6)  # a post still goes out up to this long after its time (PC was off); later than that it is missed
FORMAT = "%Y-%m-%d %H:%M"


def parse(text):
    """datetime for a publish_at string, or None when it is empty or not in the expected format."""
    try:
        return datetime.strptime(str(text).strip(), FORMAT)
    except ValueError:
        return None


def is_ready(p, state):
    return (
        p["id"] not in state.get("published", {})
        and not p.get("hold")
        and (not p.get("review") or p["id"] in state.get("approved", []))
    )


def plan(posts, state, now=None):
    """[(when, post, pinned)] for the ready posts, soonest first, plus the pinned posts that were missed.

    Returns (entries, missed). Entries are due now or in the future (within GRACE of the past counts as due)."""
    now = now or datetime.now()
    ready = [p for p in posts if is_ready(p, state)]
    entries, missed, busy = [], [], set()
    for p in ready:
        when = parse(p.get("publish_at", "")) if p.get("publish_at") else None
        if not when:
            continue
        busy.add(when.date())
        (entries if now - when <= GRACE else missed).append((when, p, True))
    for info in state.get("published", {}).values():
        when = parse(info.get("at", ""))
        if when:
            busy.add(when.date())
    loose = [p for p in ready if not parse(p.get("publish_at", ""))]
    day = (now - GRACE).replace(hour=0, minute=0, second=0, microsecond=0)
    while loose:
        slot = day.replace(hour=SLOT_TIME[0], minute=SLOT_TIME[1])
        if day.weekday() in SLOT_DAYS and day.date() not in busy and now - slot <= GRACE:
            entries.append((slot, loose.pop(0), False))
        day += timedelta(days=1)
    entries.sort(key=lambda e: e[0])
    return entries, sorted(missed, key=lambda e: e[0])


def due(posts, state, now=None):
    """Posts whose time has come, oldest first."""
    now = now or datetime.now()
    return [p for when, p, _ in plan(posts, state, now)[0] if when <= now]
