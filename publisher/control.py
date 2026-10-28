"""One control file for everything: publisher/control.xlsx.

    python publisher/control.py --export          # build control.xlsx (refuses to overwrite your edits)
    python publisher/control.py --export --force  # rebuild it from scratch
    python publisher/control.py --upgrade         # add new columns, keep your edits
    python publisher/control.py --import          # read your edits back into posts.json and reels.json

Sheets: Start here, Posts, Reels, Calendar, Specs. The Posts sheet holds the Darija text, the Gemini
rewrite prompt, the Gemini image prompt, the layout and the status. The Reels sheet holds the topic,
duration, beats (on-screen Darija text + Gemini clip prompt), logo and music. Importing updates
posts.json (what the scheduler publishes) and writes reels.json (for the reels pipeline).
"""

import json
import math
import re
import shutil
import sys
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.worksheet.datavalidation import DataValidation

ROOT = Path(__file__).resolve().parent
POSTS, STATE, REELS, XLSX = ROOT / "posts.json", ROOT / "state.json", ROOT / "reels.json", ROOT / "control.xlsx"
GENERATED = ROOT / "assets" / "generated"

# ----------------------------------------------------------------------------- shared prompt pieces
CHAR = {
    "MAMA": "the Algerian mother (early 30s, warm brown eyes, dark wavy hair loosely tied back, soft cream cardigan over a rose-coloured top)",
    "BABY": "her baby (soft dark hair, wrapped in a cream blanket)",
    "TODDLER": "her curly-haired toddler of about 2 (sage-green top)",
    "FATHER": "the Algerian father (mid-30s, short dark hair, short beard, clay-brown shirt)",
}
STYLE = ("Photorealistic candid lifestyle photograph, shot on a full-frame camera with a 35mm lens, shallow depth of field, "
         "natural soft light, realistic skin texture and natural imperfections, documentary style. Real photo look: NOT an "
         "illustration, NOT a painting, NOT a cartoon, NOT 3D or CGI, no airbrushed or plastic skin.")
PALETTE = ("Warm, soft colour grade with cream, blush, sage and clay-brown tones (rose accents in clothes or decor), "
           "no harsh or oversaturated colours.")
COMP = ("4:5 vertical portrait (1080x1350). Fill the ENTIRE frame edge to edge: the scene must run all the way to every edge, "
        "with no border, no margin and no blank or solid-colour band at the top, bottom or sides. Subject in the middle third; "
        "keep the top and bottom of the scene calm and uncluttered (soft, blurred background) but still part of the picture. "
        "No written text, no letters, no logos, no watermarks.")
CLIP_TAIL = ("Soft natural light, calm warm palette of cream #FBF6EE, blush #F6DCE0, rose #B85C72 and clay brown. "
             "Smooth slow camera move. No speech, no on-screen text, no logos, no watermarks.")


def img_prompt(scene):
    return f"{STYLE} {scene.format(**CHAR)} {PALETTE} {COMP}"


def clip_prompt(scene):
    return f"Vertical 9:16 video, 6 seconds. {scene.format(**CHAR)} {CLIP_TAIL}"


REWRITE_POST = (
    "Rewrite the post below for the Page in genuine ALGERIAN Darija, as an Algerian mother would write to a friend. "
    "Keep the same ideas, the same structure (1- 2- 3- for lists) and about the same length. Keep every line that is already "
    "natural Algerian; change only what is not. Max 3 emoji. No Egyptian, Moroccan, Levantine or formal-Arabic words; do not "
    "invent idioms. Give 3 options. Use EXACTLY this template for each option and write nothing else:\n"
    "id: draft-N\ncaption: <the COMPLETE rewritten post text, every line>\nreview: true or false\n"
    "After the 3 options add one last line: RECOMMENDED: draft-N, and why in one short sentence.\n\nPOST:\n{caption}"
)
REWRITE_REEL = (
    "Rewrite the on-screen lines of this reel in genuine ALGERIAN Darija. Keep each line at most 8 words, readable in "
    "3 seconds. Keep the meaning and the order. Give 3 options per line and recommend one.\n\nHOOK: {hook}\n{beats}\nCLOSING: {closing}"
)

# ----------------------------------------------------------------------------- posts
# id -> (image scene or None, AI label, planned slot now, notes)
POST_META = {
    "p01": (None, "n/a", "Published 19 Oct", "Live with a stock photo. Older text; fixed text is in posts.json."),
    "p02": (None, "n/a", "Published 21 Oct", "Live with a stock photo."),
    "p03": (None, "n/a", "Published 23 Oct", "Live with a stock photo."),
    "p04": ("a hushed night-time nursery seen from the doorway, like a parent peeking in. A baby sleeps peacefully in a simple wooden crib, small in the frame and turned away, face soft and not in focus. A mother's hand with a plain wedding ring rests on the crib rail. A moon-shaped night light throws a warm glow and tiny stars on the ceiling against cool blue light from the window. Small details that tell the story: a muslin cloth over the rail, a glass of water and a baby monitor on the dresser, a folded blanket. Mood: calm, protective, the hush of 'finally asleep'. One soft rose-coloured object (the blanket) as the single colour accent.", "yes: realistic AI image, use the AI info label", "after medical check",
            "Medical: keep the image calm and plain."),
    "p05": ("an intimate 3 a.m. portrait. {MAMA} stands in the doorway of a dark kitchen, lit only by one warm lamp, her baby asleep on her shoulder, her own eyes wide open and tired but tender, looking straight into the camera. Hair in a scrunchie, an oversized cardigan, a steaming mug of tea on the counter with the steam clearly visible. Cool blue pre-dawn light through the window behind her against the warm lamp, soft rim light on the baby's hair, a patterned tile floor. Camera at chest height, close. Mood: exhausted, strong, proud, 'I'm still here'.", "yes: realistic AI image, use the AI info label", "Sun 25 Oct 20:30", ""),
    "p06": ("a funny family-tea moment shot from table height. At a low round table with a brass tea tray, mint tea glasses and a plate of honey cakes, {MAMA} holds her baby with a frozen polite smile and gives the camera a subtle side-eye, while an older relative in a floral headscarf leans in wagging a finger with great confidence and an aunt nods with wide eyes. Warm golden afternoon light through lace curtains, tiny floating dust motes, a patterned rug. Mood: affectionate comedy, instantly relatable to any mother who has been given advice she did not ask for.",
            "yes: realistic AI image, use the AI info label", "Mon 26 Oct 20:30", ""),
    "p07": ("pure joy, caught mid-run. {TODDLER} sprints straight at the camera down a sunny hallway with patterned tiles, arms flung wide, mouth open shouting 'mama', curls bouncing, a slight motion blur on the little feet. At the far end {MAMA} kneels with her arms open, glowing in warm golden backlight with a soft sun flare. Camera at the toddler's eye level, a wide 24mm lens for energy, very shallow depth of field on the child's face. Mood: the single best sound in the world.",
            "yes: realistic AI image, use the AI info label", "Wed 28 Oct 20:30", ""),
    "p08": ("a lively top-down shot of an open maternity bag on a tiled floor, still being packed. Tiny cream and rose-pink baby outfits, soft slippers, a small cushion, a bag of dates, a curled phone charger cable, a pack of wipes and a folder, with a plush bunny peeking out of the bag. {MAMA}'s hand enters the frame from the side placing the last item, a tiny knitted hat, as if mid-action. Warm side light with long soft shadows, one rose-red ribbon as the colour accent. Nothing written on any item. Mood: excited, 'almost time'.",
            "yes: realistic AI image, use the AI info label", "Fri 30 Oct 20:30", "Carousel. Also used as reel r05."),
    "p09": ("chaos, joyfully captured from floor level with a wide 24mm lens. {TODDLER} is mid-laugh, head thrown back, tossing a stuffed toy into the air in the middle of a living room explosion of soft blocks, cushions pulled into a half-built fort and scattered toys on a patterned tile floor. In the background doorway {MAMA} leans on the frame holding a mug and smirking, clearly not cleaning anything. Sun rays through the window, dust in the light, a warm vivid colour grade. Mood: gloriously messy, 'this is what a happy home looks like'.", "yes: realistic AI image, use the AI info label", "Sun 1 Nov 20:30",
            "Gemini's opt3 caption was the best; this queue text is mine."),
    "p10": ("a proud, funny morning in the kitchen. {FATHER} stands at the stove stirring a pot with a dish towel over his shoulder and a sleeping baby in a carrier on his chest, grinning at the camera with an 'I've got this' look. A plate and a glass of tea are already set out on the table; out of focus in the background {MAMA} sleeps peacefully on the sofa under a blanket. Warm morning light through the window with a little steam rising from the pot, a patterned tile backsplash. Camera slightly low, close. Mood: warm, humorous, a small act of love.", "yes: realistic AI image, use the AI info label", "Mon 2 Nov 20:30", "Carousel."),
    "p11": ("an honest, vulnerable moment at eye level. {MAMA} kneels on the living-room floor with one hand over her mouth, as if she just said something she once promised herself she never would, her eyes glossy with a rueful half-smile; opposite her {TODDLER} stares up with big serious eyes, holding a worn toy. Soft side light from a window, a shallow depth of field, creamy blurred background with a few toys. Camera at the child's height, close on both faces. Mood: raw, loving, every mother recognises it.", "yes: realistic AI image, use the AI info label", "Wed 4 Nov 20:30", ""),
    "p12": ("a quiet, alert night-time moment. {MAMA} sits on the edge of a bed in a softly lit room holding her baby asleep against her chest, the baby turned away so only a little hair and a small hand show. Her free hand rests on the baby's back, feeling every breath, and she looks straight into the camera with a focused, calm, protective expression, her face lit from below by the cool blue glow of the phone in her hand, contrasted with a warm bedside lamp behind her. A small thermometer and a glass of water on the nightstand, a rumpled blanket, nothing dramatic or frightening. Camera close at chest height, shallow depth of field. Mood: alert, loving, 'I know my baby, I trust my instinct'.", "yes: realistic AI image, use the AI info label", "after medical check", "Medical: keep the image calm."),
    "p13": ("a warm, glowing close-up about a tender daily ritual. {MAMA}'s hands, with a plain ring and neat short nails, cup a few drops of golden olive oil that catch the light as one drop falls from a small glass bottle, warming the oil between her palms; a tiny baby's foot rests softly in the blurred foreground, no face shown. On a wooden tray beside her: an olive branch with green olives and a folded soft cotton towel. Low golden morning sunlight from the side backlights the oil so it sparkles, a creamy shallow depth of field, tiny swirls of steam from a warm cup in the background. No labels on the bottle, nothing written anywhere. Mood: gentle, careful, loving.", "yes: realistic AI image, use the AI info label", "after medical check", "Medical (oil on baby skin)."),
    "p15": ("a dignified, emotional 1 November portrait at golden hour on a small apartment balcony overlooking Algerian rooftops. {MAMA} kneels beside {TODDLER}, one arm around the child, both looking out toward the horizon; the toddler holds a small Algerian flag (green and white halves with a red star and crescent) up in the wind, the cloth caught mid-flutter. Warm low sun rim-lights their hair and the flag, soft haze over the city, shallow depth of field, camera at the child's height from behind-side. Mood: quiet pride, memory handed from mother to child, respectful and moving rather than loud. No text anywhere.", "yes: realistic AI image, use the AI info label", "Sun 1 Nov 12:00 (pinned: publish_at)", "Revolution Day. Draft on hold: read the Darija and approve it. Flag has no writing."),
    "p14": ("a funny, tender 2 a.m. moment, close and slightly low-angle, like a candid shot by a friend. {MAMA} is slumped on the couch in a dim living "
            "room, hair coming loose, one eye half-closed, a tired but amused crooked smile, holding a cold glass of tea; her baby, perched wide awake on "
            "her lap in a cream sleepsuit, stares straight into the camera with huge bright eyes and a gummy grin, one tiny fist raised like a victory. "
            "Small details that tell the story: a rumpled blanket, a pacifier on the cushion, a baby monitor glowing, a phone face-down, a few toys on the "
            "floor, a wall clock softly out of focus with no readable numbers. Light: a warm table lamp on the two of them against cool blue moonlight "
            "through the window behind, strong warm-and-cool contrast, a soft rim light on the baby's hair, sparkling catchlights in the eyes. "
            "Mood: funny, relatable, loving, the feeling of 'we are both awake'.",
            "yes: realistic AI image, use the AI info label", "draft",
            "New: adapted from Gemini's 2 am draft. Lively version, tested first (story moment, direct eye contact, warm/cool light contrast)."),
}

P14 = {
    "id": "p14", "kind": "reassure", "card": "الساعة زوج تاع الليل و هو فايق", "review": False, "hold": True,
    "text": ("الساعة زوج تاع الليل، و عينيه مفتوحين ما بانش عليهم النعاس 😅\n\nما درتي والو غالط.\n"
             "ما راكيش أم ناقصة، و ولدك ماشي \"صعيب\".\nهو غير يتعلم كيفاش يوالف هاد الدنيا الكبيرة.\n\n"
             "تنفسي شوية، هاد الليالي يدوزو و يكبر، و نتي تتفكريهم.\nربي يعاونك و يرزقك سويعات راحة 🤍\n\n"
             "شكون مازالت فايقة معانا دروك؟"),
}

# ----------------------------------------------------------------------------- reels
REELS_DATA = [
    {"id": "r01", "topic": "7 sentences every new mother hears (companion to post p01)", "dur": "15 s", "hook": "جمل تسمعها كل أم جديدة",
     "beats": [("ولدك يبكي بزاف، جايع أكيد!", "{MAMA} gently swaying with {BABY} who cries softly, warm living room, morning window light, slow push-in."),
               ("ما تحمليهش بزاف باش ما يتعودش", "{MAMA} looking down lovingly at {BABY} asleep on her chest in an armchair, slow orbit around them."),
               ("أنا في وقتي ما درتش هكا", "close-up of {MAMA}'s hand holding {BABY}'s tiny hand on a woven blanket, slow tilt up to her calm smile.")],
     "closing": "نتي أي جملة سمعتيها بزاف؟",
     "caption": "جمل تسمعها كل أم جديدة 🙈\nو الجواب الهادي؟ \"أنا و ولدي نعرفو بعضانا، يعطيك الصحة\" 🤍\nنتي أي جملة سمعتيها بزاف؟ كتبيها في الكومنتير.",
     "review": "no", "slot": "Mon 2 Nov"},
    {"id": "r02", "topic": "Every child has his own clock (companion to post p02)", "dur": "15 s", "hook": "ما راكيش تأخرتي 🤍",
     "beats": [("كل طفل عندو ساعتو", "{TODDLER} taking first wobbly steps across a sunny tiled room towards the camera, slow tracking shot moving backwards."),
               ("المشي، الهدرة، الأسنان…", "close-up of a baby's open smile showing a first tooth while the mother's finger gently touches the cheek, slow push-in."),
               ("يوصل ملي يكون جاهز", "wide shot of a bright playroom with three small children at different stages (a crawling baby, a standing toddler, a walking child), slow pan.")],
     "closing": "نتي في أي عمر مشى ولدك؟",
     "caption": "ما راكيش تأخرتي 🤍\nالمشي، الهدرة، الأسنان… كل طفل عندو ساعتو.\nنتي في أي عمر مشى ولدك؟",
     "review": "no", "slot": "Fri 6 Nov"},
    {"id": "r03", "topic": "For the mother awake at 3 am (companion to post p05)", "dur": "15 s", "hook": "للأم لي مازالت فايقة",
     "beats": [("راكي تعبانة و هذا صح", "{MAMA} sitting on the edge of a bed at night holding {BABY}, dim bedside lamp, slow push-in, tired but tender."),
               ("و راكي تعطي أكثر مما تظني", "close-up of {MAMA}'s hand stroking {BABY}'s back gently, warm lamp light, slow drift."),
               ("هاد الليالي تدوز", "a window where the first dawn light slowly brightens over rooftops, a mother's silhouette holding a baby, slow upward tilt.")],
     "closing": "ربي يعاونك 🤍",
     "caption": "للأم لي مازالت سهرانة على 3 تاع الصباح 🌙\nراكي تعبانة و هذا صح، و راكي تعطي أكثر مما تظني.\nربي يعاونك 🤍",
     "review": "no", "slot": "Mon 9 Nov"},
    {"id": "r04", "topic": "Messy house, happy child (companion to post p09)", "dur": "15 s", "hook": "دار مقلوبة؟",
     "beats": [("الماعن تقدر تستنى", "a kitchen sink with a few unwashed dishes, the camera slowly pulling back to reveal a toddler laughing in the next room, shallow focus."),
               ("الضحكة ما تستناش", "{TODDLER} laughing and tossing soft toys in the air on a patterned tile floor, slow motion, soft morning light."),
               ("راكي في بلاصتك الصح", "{MAMA} smiling as she sits down on the floor among the toys and picks up {TODDLER}, slow orbit.")],
     "closing": "واش هي الحاجة لي مقلوبة في دارك دروك؟",
     "caption": "دار مقلوبة و ولد فرحان، أحسن من دار نقية و أم تعبانة 🏠🤍\nواش هي الحاجة لي مقلوبة في دارك دروك؟",
     "review": "no", "slot": "Fri 13 Nov"},
    {"id": "r05", "topic": "8 things for the maternity bag (companion to post p08)", "dur": "18 s", "hook": "8 حوايج لساك السبيطار",
     "beats": [("شارجور طويل للتيليفون", "top-down view of hands placing a long phone charger cable into an open maternity bag on a cream surface, soft light."),
               ("مخدة صغيرة ليك", "top-down: hands placing a small cushion and soft slippers into the bag, slow slide."),
               ("قميجة مفتوحة للرضاعة", "top-down: hands folding a soft front-open nightgown in rose tone and placing it in the bag."),
               ("و 5 حوايج أخرى في البوست", "top-down: the bag closing slowly with a gentle zip, hands resting on it, slow pull-back.")],
     "closing": "شاركيها مع صاحبتك لي قريب تولد",
     "caption": "8 حوايج دخليهم في ساك السبيطار و ما يقولهالك حد 👜\nالقايمة كاملة في البوست. شاركيها مع صاحبتك لي قريب تولد 🤍",
     "review": "no", "slot": "Mon 16 Nov"},
    {"id": "r06", "topic": "A word for fathers (companion to post p10)", "dur": "15 s", "hook": "كلمة للأب",
     "beats": [("حضر الماكلة بلا ما تطلب", "{FATHER} setting a plate and a tea glass on a table in a tidy kitchen, morning light, slow dolly."),
               ("خذ البيبي ساعة و خليها ترقد", "{FATHER} gently carrying {BABY} on his shoulder in the living room while {MAMA} rests on the sofa, slow orbit."),
               ("قولها: راكي تديري مليح", "close-up of {MAMA} smiling softly with closed eyes as {FATHER} lays a warm blanket over her, slow push-in.")],
     "closing": "شاركيها لبابا تاع ولدك",
     "caption": "كلمة للأب 👨‍👧 4 حوايج صغار تبدل أسبوع الأم الجديدة. شاركيها لبابا تاع ولدك (بلا كلام) 🤍",
     "review": "no", "slot": "Fri 20 Nov"},
]

CALENDAR = [
    ("Fri 30 Oct", "p07", "Post (framed card)", "current queue"),
    ("Sun 1 Nov", "p15", "Question card", "REVOLUTION DAY: pinned to 12:00 (publish_at); stays a draft until you approve it"),
    ("Mon 2 Nov", "p08", "List carousel", "current queue"), ("Wed 4 Nov", "p09", "Post (full photo)", "current queue"),
    ("Fri 6 Nov", "p10", "List carousel", "current queue"), ("Sun 8 Nov", "p11", "Question card", "current queue"),
    ("Mon 9 Nov", "p12", "List carousel", "current queue (medical, approved)"), ("Wed 11 Nov", "p13", "List carousel", "current queue (medical, approved)"),
    ("Fri 13 Nov", "p14", "Post", "draft: needs its image first"),
    ("Fri 20 Nov", "?", "Post", "UNIVERSAL CHILDREN'S DAY (20 Nov): plan a post for this date"),
    ("-", "r01-r06", "REELS", "on hold; slots will be set when reels restart"),
]

# Special days to plan around (Algerian first, then the ones mothers care about). Pin a post to a date and time on the panel's Schedule tab (publish_at).
# Fixed dates are certain; Islamic dates move about 11 days earlier each year and depend on the moon sighting, so confirm them a week before.
OCCASIONS = [
    ("2026-11-01", "Revolution Day (1954), national holiday", "fixed"),
    ("2026-11-20", "Universal Children's Day", "fixed"),
    ("2027-01-01", "New Year's Day", "fixed"),
    ("2027-01-12", "Yennayer, Amazigh New Year, national holiday", "fixed"),
    ("2027-02-08", "Start of Ramadan 1448 (about)", "moon: confirm"),
    ("2027-03-08", "International Women's Day", "fixed"),
    ("2027-03-09", "Eid al-Fitr (about)", "moon: confirm"),
    ("2027-05-01", "Labour Day, national holiday", "fixed"),
    ("2027-05-16", "Eid al-Adha (about)", "moon: confirm"),
    ("2027-07-05", "Independence Day, national holiday", "fixed"),
]

SPECS = [
    ("Special days", "Plan posts around these; pin a post to a date and time on the Schedule tab (p15 is the example). "
        + "; ".join(f"{d} {n} [{k}]" for d, n, k in OCCASIONS)),
    ("Pattern per week", "2 reels + 1 list carousel + 1 single card or question (Mon reel, Wed carousel/post, Fri reel, Sun post). Posting time 20:30."),
    ("Images: size", "4:5 portrait, 1080x1350 px, PNG or JPG. One image per post (the cover). The carousel slides are built by the tools."),
    ("Images: where to put files", "publisher/assets/generated/<post id>.png, e.g. p05.png. Then fill 'Image file' in this sheet and run: python publisher/control.py --import"),
    ("Images: no text inside", "Never let Gemini write Arabic or any text in the picture. Headline, number badge, logo and footer are added by the tools."),
    ("Images: character sheet", "Paste this at the start of any prompt that has a person so the family looks the same every time: "
        + " | ".join(f"{k}: {v}" for k, v in CHAR.items())),
    ("Images: layouts", "full = the photo fills the card with a warm fade at the bottom (best for photos, the default now). framed = the photo in a rounded frame on cream."),
    ("AI label", "Our images are photorealistic and made with AI, so Meta expects its 'AI info' label on them. Check the published post in the app and add the label if it is missing. Never present these people as real or as a testimonial."),
    ("Reels: format", "9:16, 1080x1920, MP4 (H.264), 30 fps. Target 15 s (12 to 18). Maximum 90 s."),
    ("Reels: timing", "Hook 2 s, then 3 to 4 beats of about 3 s (each beat is a 6 s Gemini clip that we trim), closing line 3 s."),
    ("Reels: Gemini clips", "6 to 8 s, one subject, slow camera move, soft warm light, NO speech, NO text, NO logos. Use Flow 'Veo 3.1 Fast' to save credits (about 20 credits per clip)."),
    ("Reels: character consistency", "In Flow use 'Ingredients to video' with one reference image of the mother, so she looks the same in every clip."),
    ("Reels: file names", "r01_beat1.mp4, r01_beat2.mp4, ... in publisher/assets/reels/"),
    ("Reels: text and logo", "Arabic lines (Marhey, cream with soft shadow) are added by our tools in the lower third. Logo mark (cream) top centre, small, the whole video; on the last 2 s a centred lockup: mark + أنا و وليدي."),
    ("Reels: music", "None while testing. Later: royalty-free, soft, no vocals, about 70 bpm, low volume."),
    ("Reels: status", "The reels pipeline (assemble and publish) is being built and tested. Nothing here publishes a reel yet."),
    ("Darija", "Check every text with: python publisher/check_post.py <file>. The owner still reads the Darija."),
    ("Medical", "Any post about sleep, fever, feeding, skin, oils or emergencies needs 'Medical review = yes' and a doctor or midwife must check it before it is approved."),
    ("Approve a held post", "python publisher/publish.py --approve p04 p12 p13"),
]

# ----------------------------------------------------------------------------- workbook layout
REELS_DIR = ROOT / "assets" / "reels"
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
VIDEO_SUFFIXES = {".mp4", ".mov", ".webm", ".m4v"}

# (header, width, Arabic/right-to-left cell)
POST_COLS = [
    ("ID", 7, False), ("Status", 17, False), ("Planned slot", 17, False), ("Kind", 10, True),
    ("Cover headline (Darija)", 28, True), ("Caption (Darija, Claude)", 52, True),
    ("Gemini option 1", 45, True), ("Gemini option 2", 45, True), ("Gemini option 3", 45, True), ("Text choice", 12, False),
    ("Gemini rewrite prompt", 50, False), ("Gemini image prompt", 60, False),
    ("Image path", 36, False), ("Layout", 9, False), ("AI label", 15, False), ("Medical review", 10, False), ("Notes", 30, False),
]
REEL_COLS = (
    [("ID", 6, False), ("Status", 14, False), ("Planned slot", 12, False), ("Topic", 28, False), ("Duration", 9, False),
     ("Ratio / size", 14, False), ("Hook (on-screen)", 22, True)]
    + [c for i in range(1, 5) for c in ((f"Beat {i} text (on-screen)", 24, True), (f"Beat {i} Gemini clip prompt", 50, False),
                                        (f"Beat {i} clip path", 34, False))]
    + [("Closing line (on-screen)", 26, True), ("Logo", 20, False), ("Music", 12, False),
       ("Caption (Darija, Claude)", 45, True), ("Gemini option 1", 40, True), ("Gemini option 2", 40, True),
       ("Gemini option 3", 40, True), ("Caption choice", 12, False), ("Gemini rewrite prompt", 50, False),
       ("Final reel path (optional)", 36, False), ("Medical review", 9, False), ("Notes", 20, False)]
)
ALIASES = {"Caption (Darija, my text)": "Caption (Darija, Claude)", "Caption (Darija)": "Caption (Darija, Claude)",
           "Image file": "Image path", "Closing line (on-screen)": "Closing line (on-screen)"}
CHOICES = ["Claude", "Gemini 1", "Gemini 2", "Gemini 3"]
DROPDOWNS = {"Status": ["Published", "Ready (queued)", "Held: medical review", "Draft (hold)", "Ready"],
             "Layout": ["framed", "full"], "Medical review": ["yes", "no"], "Text choice": CHOICES, "Caption choice": CHOICES}

HEAD_FILL = PatternFill("solid", fgColor="B85C72")
CHOICE_FILL = PatternFill("solid", fgColor="FBEEDA")
PATH_FILL = PatternFill("solid", fgColor="EAF2E9")
STATUS_FILL = {"Published": "E7DBCA", "Ready": "D6E6D3", "Held": "FBEEDA", "Draft": "DCE6F5"}
THIN = Side(style="thin", color="E7DBCA")
AR = {"horizontal": "right", "vertical": "top", "wrap_text": True, "readingOrder": 2}
LTR = {"horizontal": "left", "vertical": "top", "wrap_text": True}


def write_sheet(ws, headers, rows, widths, arabic_cols=()):
    """Plain table writer (used by the Calendar and Specs sheets)."""
    ws.append(headers)
    for c in ws[1]:
        c.fill, c.font = HEAD_FILL, Font(bold=True, color="FFFFFF")
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    ws.row_dimensions[1].height = 34
    for r in rows:
        ws.append(r)
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[ws.cell(1, i).column_letter].width = w
    for row in ws.iter_rows(min_row=2):
        lines = 1
        for c in row:
            c.alignment = Alignment(**(AR if c.column - 1 in arabic_cols else LTR))
            c.border = Border(bottom=THIN)
            if c.value:
                w = widths[c.column - 1]
                lines = max(lines, sum(max(1, math.ceil(len(part) / (w * 1.05))) for part in str(c.value).split("\n")))
        ws.row_dimensions[row[0].row].height = min(400, max(30, 15 * lines))
    ws.freeze_panes = "C2"


def write_table(ws, cols, rows):
    """Write dict rows with the given column spec, styles, dropdowns and highlighted input columns."""
    write_sheet(ws, [c[0] for c in cols], [[r.get(c[0], "") for c in cols] for r in rows], [c[1] for c in cols],
                [i for i, c in enumerate(cols) if c[2]])
    for i, (name, _, _) in enumerate(cols, 1):
        letter = ws.cell(1, i).column_letter
        if name in DROPDOWNS:
            dv = DataValidation(type="list", formula1='"' + ",".join(DROPDOWNS[name]) + '"', allow_blank=True)
            ws.add_data_validation(dv)
            dv.add(f"{letter}2:{letter}200")
        if name in ("Text choice", "Caption choice") or name.endswith("path") or name.endswith("(optional)"):
            for r in range(2, ws.max_row + 1):
                ws.cell(r, i).fill = PATH_FILL if "path" in name or "optional" in name else CHOICE_FILL
        if name == "Status":
            for r in range(2, ws.max_row + 1):
                key = str(ws.cell(r, i).value or "").split(" ")[0].split(":")[0]
                ws.cell(r, i).fill = PatternFill("solid", fgColor=STATUS_FILL.get(key, "FFFFFF"))


def read_table(ws):
    """Rows of a sheet as dicts keyed by header (old header names are mapped through ALIASES)."""
    heads = [ALIASES.get(c.value, c.value) for c in ws[1]]
    return [dict(zip(heads, row)) for row in ws.iter_rows(min_row=2, values_only=True) if row[0]]


def status_of(p, state):
    if p["id"] in state.get("published", {}):
        return "Published"
    if p.get("hold"):
        return "Draft (hold)"
    if p["review"] and p["id"] not in state.get("approved", []):
        return "Held: medical review"
    return "Ready (queued)"


def post_rows(posts, state):
    rows = []
    for p in posts:
        scene, ai, slot, notes = POST_META.get(p["id"], (None, "", "", ""))
        published = p["id"] in state.get("published", {})
        rows.append({
            "ID": p["id"], "Status": status_of(p, state), "Planned slot": slot, "Kind": p["kind"],
            "Cover headline (Darija)": p["card"], "Caption (Darija, Claude)": p["text"], "Text choice": "Claude",
            "Gemini rewrite prompt": "(published)" if published else REWRITE_POST.format(caption=p["text"]),
            "Gemini image prompt": img_prompt(scene) if scene else "(published, no new image needed)",
            "Image path": p.get("image_file") or "", "Layout": "framed" if p.get("framed") else "full",
            "AI label": ai, "Medical review": "yes" if p["review"] else "no", "Notes": notes,
        })
    return rows


def reel_rows():
    rows = []
    for r in REELS_DATA:
        row = {"ID": r["id"], "Status": "Draft (hold)", "Planned slot": r["slot"], "Topic": r["topic"], "Duration": r["dur"],
               "Ratio / size": "9:16, 1080x1920, mp4", "Hook (on-screen)": r["hook"],
               "Closing line (on-screen)": r["closing"], "Logo": "mark top centre all video; lockup last 2 s", "Music": "none (test)",
               "Caption (Darija, Claude)": r["caption"], "Caption choice": "Claude", "Medical review": r["review"], "Notes": ""}
        for i, (text, scene) in enumerate(r["beats"], 1):
            row[f"Beat {i} text (on-screen)"], row[f"Beat {i} Gemini clip prompt"] = text, clip_prompt(scene)
        beat_txt = "\n".join(f"BEAT {i}: {b[0]}" for i, b in enumerate(r["beats"], 1))
        row["Gemini rewrite prompt"] = REWRITE_REEL.format(hook=r["hook"], beats=beat_txt, closing=r["closing"])
        rows.append(row)
    return rows


START_HERE = [
    "أنا و وليدي: control file", "",
    "POSTS sheet: one row per post. Edit the Darija caption, headline, layout and status.",
    "  - Text: 'Caption (Darija, Claude)' is my text. Gemini's 3 rewrites go in the 'Gemini option' columns (python publisher/compare_texts.py). "
    "Pick the text you want in 'Text choice' (Claude / Gemini 1 / 2 / 3).",
    "  - Image: copy 'Gemini image prompt' into Gemini, save the picture anywhere, paste its full path into 'Image path'. "
    "On import it is copied into the project, so you can delete the original.",
    "REELS sheet: topic, duration, the on-screen Darija lines, and one Gemini clip prompt per beat. Generate each clip in Flow, "
    "paste its path into 'Beat N clip path' (or paste a finished video into 'Final reel path'). Same Claude/Gemini choice for the caption.",
    "CALENDAR sheet: the proposed schedule with reels. The CURRENT schedule keeps running until the reels pipeline is tested.",
    "SPECS sheet: sizes, durations, logo, music, character sheet, AI label and medical rules.",
    "", "WHEN YOU FINISH: save, CLOSE Excel (Windows locks an open file), then tell me or run: python publisher/control.py --import",
    "Status values: Published, Ready (queued), Held: medical review, Draft (hold). Held and Draft rows are never posted by the scheduler.",
    "Medical review = yes: a doctor or midwife must check it, then: python publisher/publish.py --approve <id>",
]


def export(force=False):
    if XLSX.exists() and not force:
        sys.exit(f"{XLSX.name} already exists. Use --upgrade to add new columns keeping your edits, --import to apply them, "
                 "or --export --force to rebuild it from scratch (your edits would be lost).")
    posts = json.loads(POSTS.read_text(encoding="utf-8"))
    if not any(p["id"] == "p14" for p in posts):
        posts.append(P14)
        POSTS.write_text(json.dumps(posts, ensure_ascii=False, indent=2), encoding="utf-8")
    state = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}
    wb = Workbook()
    ws = wb.active
    ws.title = "Start here"
    for line in START_HERE:
        ws.append([line])
    ws.column_dimensions["A"].width = 160
    ws["A1"].font = Font(bold=True, size=16, color="7E3A4B")
    for r in ws.iter_rows(min_row=2):
        r[0].alignment = Alignment(wrap_text=True, vertical="top")
    write_table(wb.create_sheet("Posts"), POST_COLS, post_rows(posts, state))
    write_table(wb.create_sheet("Reels"), REEL_COLS, reel_rows())
    write_sheet(wb.create_sheet("Calendar"), ["Date (20:30)", "ID", "Type", "Notes"], [list(c) for c in CALENDAR], [16, 8, 22, 60])
    ws = wb.create_sheet("Specs")
    write_sheet(ws, ["Topic", "Value"], [list(s) for s in SPECS], [30, 140])
    ws.freeze_panes = "A2"
    wb.save(XLSX)
    print(f"wrote {XLSX} ({len(posts)} posts, {len(REELS_DATA)} reels)")


def upgrade():
    """Add the new columns to an existing control.xlsx and keep every value you already edited."""
    wb = load_workbook(XLSX)
    for name, cols in (("Posts", POST_COLS), ("Reels", REEL_COLS)):
        ws = wb[name]
        rows = read_table(ws)
        for r in rows:  # old suggestions like "p04.png" were not real paths; blank them unless the file exists
            img = str(r.get("Image path") or "")
            if re.fullmatch(r"p\d+\.png", img) and not (GENERATED / img).exists():
                r["Image path"] = ""
            choice = "Text choice" if name == "Posts" else "Caption choice"
            r[choice] = r.get(choice) or "Claude"
        idx = wb.sheetnames.index(name)
        wb.remove(ws)
        write_table(wb.create_sheet(name, idx), cols, rows)
    if "Compare" in wb.sheetnames:
        wb.remove(wb["Compare"])
    start = wb["Start here"]
    start.delete_rows(1, start.max_row)
    for line in START_HERE:
        start.append([line])
    start["A1"].font = Font(bold=True, size=16, color="7E3A4B")
    wb.save(XLSX)
    print("control.xlsx upgraded: new columns added, your edits kept.")


# ----------------------------------------------------------------------------- import
def find_file(value):
    """Locate a file from a path typed or pasted in the sheet (quotes allowed, absolute or relative)."""
    v = str(value or "").strip().strip('"').strip("'")
    if not v:
        return None
    for c in (Path(v), ROOT / v, ROOT.parent / v, GENERATED / v, REELS_DIR / v):
        try:
            if c.is_file():
                return c
        except OSError:
            continue
    return None


def adopt(src, folder, stem):
    """Copy a file into the project (so publishing never depends on a Downloads folder); return the new name."""
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / f"{stem}{src.suffix.lower()}"
    if src.resolve() != dest.resolve():
        shutil.copy2(src, dest)
    return dest.name


def chosen_text(row, choice_col, claude_col="Caption (Darija, Claude)"):
    choice = str(row.get(choice_col) or "Claude")
    if choice.startswith("Gemini"):
        picked = row.get(f"Gemini option {choice.split()[-1]}")
        if picked:
            return str(picked).strip(), choice
    return str(row.get(claude_col) or "").strip(), "Claude"


def import_back():
    if POSTS.exists() and POSTS.stat().st_mtime > XLSX.stat().st_mtime and "--force" not in sys.argv:
        sys.exit("posts.json was changed (probably in the control panel) after control.xlsx was last saved. Importing now would "
                 "overwrite those newer edits. The panel is the main control now; use --force only if you really mean it.")
    wb = load_workbook(XLSX)
    posts = json.loads(POSTS.read_text(encoding="utf-8"))
    by_id = {p["id"]: p for p in posts}
    changed, missing = [], []
    for row in read_table(wb["Posts"]):
        pid, status = row["ID"], str(row.get("Status") or "")
        p = by_id.get(pid)
        if p is None:
            p = by_id[pid] = {"id": pid, "kind": "reassure", "review": False}
            posts.append(p)
        if status.startswith("Published"):
            continue
        text, source = chosen_text(row, "Text choice")
        new = {"text": text or p.get("text", ""), "card": row.get("Cover headline (Darija)") or p.get("card", ""),
               "kind": row.get("Kind") or p["kind"], "review": str(row.get("Medical review")).lower() == "yes"}
        if row.get("Layout") == "framed":
            new["framed"] = True
        else:
            p.pop("framed", None)
        if status.startswith("Draft"):
            new["hold"] = True
        else:
            p.pop("hold", None)
        raw = row.get("Image path")
        if raw:
            src = find_file(raw)
            if src and src.suffix.lower() in IMAGE_SUFFIXES:
                new["image_file"] = adopt(src, GENERATED, pid)
            elif str(raw) != p.get("image_file"):
                missing.append(f"{pid}: '{raw}'")
        if any(p.get(k) != v for k, v in new.items()):
            changed.append(pid + (f" (text from {source})" if source != "Claude" and new["text"] != p.get("text") else ""))
        p.update(new)
    POSTS.write_text(json.dumps(posts, ensure_ascii=False, indent=2), encoding="utf-8")

    reels = []
    for row in read_table(wb["Reels"]):
        rid = row["ID"]
        beats = []
        for i in range(1, 5):
            text = row.get(f"Beat {i} text (on-screen)")
            if not text:
                continue
            raw, name = row.get(f"Beat {i} clip path"), None
            if raw:
                src = find_file(raw)
                if src and src.suffix.lower() in VIDEO_SUFFIXES:
                    name = adopt(src, REELS_DIR, f"{rid}_beat{i}")
                else:
                    missing.append(f"{rid} beat {i}: '{raw}'")
            beats.append({"text": text, "clip_prompt": row.get(f"Beat {i} Gemini clip prompt"), "file": name})
        final, raw = None, row.get("Final reel path (optional)")
        if raw:
            src = find_file(raw)
            if src and src.suffix.lower() in VIDEO_SUFFIXES:
                final = adopt(src, REELS_DIR, f"{rid}_final")
            else:
                missing.append(f"{rid} final: '{raw}'")
        caption, source = chosen_text(row, "Caption choice")
        reels.append({"id": rid, "status": row.get("Status"), "slot": row.get("Planned slot"), "topic": row.get("Topic"),
                      "hook": row.get("Hook (on-screen)"), "beats": beats, "closing": row.get("Closing line (on-screen)"),
                      "caption": caption, "caption_source": source, "final_file": final,
                      "review": str(row.get("Medical review")).lower() == "yes"})
    REELS.write_text(json.dumps(reels, ensure_ascii=False, indent=2), encoding="utf-8")
    clips = sum(1 for r in reels for b in r["beats"] if b["file"]) + sum(1 for r in reels if r["final_file"])
    print(f"imported: posts changed {changed or 'none'}; {len(reels)} reels ({clips} video files found)")
    if missing:
        print("  file not found or wrong type (check the path):", "; ".join(missing))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    if "--export" in sys.argv:
        export("--force" in sys.argv)
    elif "--upgrade" in sys.argv:
        upgrade()
    elif "--import" in sys.argv:
        import_back()
    else:
        print(__doc__)
