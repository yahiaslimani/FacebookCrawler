"""Check text written by Gemini (or anyone) against the brand rules before it goes into posts.json.

    python publisher/check_post.py gemini_output.txt      # a file
    python publisher/check_post.py                        # then paste text, finish with Ctrl+Z + Enter (Windows)

It flags words that are not Algerian Darija, emoji overload, links/phone numbers, and a missing
`review: true` on medical topics. It is a safety net, not a judge: the owner still reads the Darija.
"""

import re
import sys

NOT_ALGERIAN = {
    "شنو": "Moroccan: use واش", "ديال": "Moroccan: use تاع", "دابا": "Moroccan: use ذرك/دروك", "بغيت": "Moroccan",
    "إزاي": "Egyptian: use كيفاش", "ليه": "Egyptian: use علاش", "دلوقتي": "Egyptian: use ذرك", "كده": "Egyptian: use هكا",
    "أوي": "Egyptian: use بزاف", "عايز": "Egyptian", "مش ": "Egyptian: use ماشي/ما...ش", "شنطة": "Egyptian: use ساك",
    "هلق": "Levantine: use ذرك", "شو ": "Levantine: use واش", "كتير": "Levantine: use بزاف", "بدي": "Levantine",
    "هيك": "Levantine: use هكا", "لسا": "Levantine: use مازال", "واجد": "Gulf: use بزاف/جاهز",
    "جيعان": "not Algerian: use جايع", "مبهدلة": "not Algerian: use مقلوبة", "سوف ": "formal Arabic",
    "الهادئ": "formal Arabic: use الهادي", "المنشور": "use البوست", "أنتِ": "use نتي", "ممكن ": "use يقدر",
    "حاليا": "formal Arabic: use ذرك", "الآن": "formal Arabic: use ذرك",
    "أنت": "addresses a mother in the masculine: use نتي", "يجوزو": "use يدوزو (Algerian: دوز)", "تجوز": "use تدوز",
    "هايلة": "Egyptian/Levantine: use نيشان/مليحة بزاف", "هايل": "Egyptian/Levantine: use نيشان",
    "لهيه": "not Algerian, unclear idiom", "يا لميمة": "not Algerian: use يا ماما / يا لالة",
    "تثبتي": "formal Arabic: use تتأكدي",
}
VERB_FORM = [(r"روحي بالوحدة", "unclear phrase")]
MEDICAL = ["حرارة", "سخانة", "حمى", "دواء", "دوا", "جرعة", "مضاد", "زيت", "إكزيما", "ترجيع", "يترجع",
           "اختناق", "استعجال", "تسنين", "مغص", "تطعيم", "لقاح", "طبيب", "الدكتور"]


def check(text):
    problems = []
    for word, why in NOT_ALGERIAN.items():
        w = word.strip()
        # whole word only, allowing a one-letter prefix (و، ف، ب، ل، ك)
        if re.search(rf"(?<![؀-ۿ])[وفبلك]?{re.escape(w)}(?![؀-ۿ])", text):
            problems.append(f"word '{w}': {why}")
    if len(re.findall(r"[\U0001F300-\U0001FAFF☀-➿]", text)) > 3 * max(1, text.count("caption:")):
        problems.append("more than 3 emoji per post")
    if re.search(r"https?://|www\.", text):
        problems.append("contains a link (links hurt engagement)")
    if re.search(r"\b0[567]\d{8}\b|\+213", text):
        problems.append("contains a phone number")
    if re.search(r"(شاركي|تاغي)[^\n]{0,20}(و لا|ولا)\s", text):
        problems.append("begs for shares/tags")
    if any(m in text for m in MEDICAL) and not re.search(r"review:\s*true", text, re.I):
        problems.append("medical-sounding topic but no 'review: true' (a doctor or midwife must check it)")
    if re.search(r"image_prompt:", text) and re.search(r"image_prompt:[^\n]*(text|letters|logo)[^\n]*", text, re.I) \
            and not re.search(r"no (text|letters|logos?)", text, re.I):
        problems.append("image_prompt mentions text/logo without saying 'no text'")
    for rule, why in VERB_FORM:
        if re.search(rule, text):
            problems.append(why)
    try:
        import json
        from pathlib import Path
        existing = {p["id"] for p in json.loads((Path(__file__).resolve().parent / "posts.json").read_text(encoding="utf-8"))}
        for found in re.findall(r"^id:\s*(\S+)", text, re.M):
            if found in existing:
                problems.append(f"id '{found}' is already used by a queued/published post: use draft-1, draft-2...")
    except OSError:
        pass
    if re.search(r"no characters", text, re.I):
        problems.append("image_prompt says 'no characters': say 'no written characters or letters' instead (it may remove people)")
    for field in ("id:", "kind:", "card:", "caption:") if "caption:" in text and "reel_id:" not in text else ():
        if field not in text:
            problems.append(f"missing field {field}")
    return problems


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    text = open(sys.argv[1], encoding="utf-8").read() if len(sys.argv) > 1 else sys.stdin.read()
    issues = check(text)
    if not issues:
        print("OK: no problems found (the owner should still read the Darija).")
    for i in issues:
        print("-", i)
    sys.exit(1 if issues else 0)


if __name__ == "__main__":
    main()
