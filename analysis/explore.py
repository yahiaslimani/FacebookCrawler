import json, glob, re, sys, statistics as st
from collections import Counter
sys.stdout.reconfigure(encoding='utf-8')
rows=[]
for f in glob.glob('data/*.jsonl'):
    for l in open(f,encoding='utf-8'):
        if l.strip():
            r=json.loads(l); r['src']=f[5:-6]; rows.append(r)
def eng(r): return r['reaction_count']+3*r['comment_count']+4*r['share_count']
by={}
for r in rows: by.setdefault(r['src'],[]).append(r)
# per-page rank percentile so big pages don't dominate
for s,rs in by.items():
    rs.sort(key=eng)
    for i,r in enumerate(rs): r['pct']=i/max(1,len(rs)-1)
for s,rs in by.items():
    print(s[:30], len(rs), 'median react', st.median(r['reaction_count'] for r in rs), 'median comments', st.median(r['comment_count'] for r in rs))
good=[r for r in rows if r['post_text'] and len(by[r['src']])>=500]
print(len(good),'posts used (pages>=500 posts, with text)')
def feat(name,fn):
    a=[r['pct'] for r in good if fn(r)]; b=[r['pct'] for r in good if not fn(r)]
    print(f"{name:28} n={len(a):5} avg-pct {st.mean(a):.3f} | rest {st.mean(b):.3f}")
feat('has image', lambda r: bool(r.get('image_urls')))
feat('question mark', lambda r: '?' in r['post_text'] or '؟' in r['post_text'])
feat('numbered list', lambda r: bool(re.search(r'(^|\n)\s*[\d١-٩][\-\.\)]',r['post_text'])))
feat('short <150', lambda r: len(r['post_text'])<150)
feat('medium 150-500', lambda r: 150<=len(r['post_text'])<=500)
feat('long >500', lambda r: len(r['post_text'])>500)
feat('hashtag', lambda r: '#' in r['post_text'])
feat('has phone/contact', lambda r: bool(re.search(r'0[567]\d{8}|whatsapp|واتساب|اتصل|للحجز',r['post_text'],re.I)))
feat('tag a friend/share ask', lambda r: bool(re.search(r'شارك|انشر|تاغ|منشن|شاركي|ارسل|ابعث',r['post_text'])))
feat('story-ish (3rd person)', lambda r: bool(re.search(r'قصة|حكاية|مرة وحدة|كانت|في يوم',r['post_text'])))
# words
stop=set('من في على الى إلى عن ما لا أن ان هذا هذه الي التي الذي و مع كل او أو لو إذا اذا كان كانت هو هي يا قد لم لن ثم بعد قبل عند أي أو'.split())
def words(t): return [w for w in re.findall(r'[؀-ۿa-zA-Zéèàç]{3,}',t) if w not in stop]
hi=Counter(); lo=Counter()
for r in good:
    ws=set(words(r['post_text']))
    (hi if r['pct']>=0.8 else lo if r['pct']<=0.4 else Counter()).update(ws)
nh=sum(1 for r in good if r['pct']>=0.8); nl=sum(1 for r in good if r['pct']<=0.4)
sc=[]
for w,c in hi.items():
    if c>=25:
        sc.append(((c/nh)/((lo[w]+1)/nl),w,c,lo[w]))
sc.sort(reverse=True)
print('--- words over-represented in top 20% posts')
for s,w,c,l in sc[:45]: print(f"{w} x{s:.1f} ({c} top vs {l} bottom)")
sc2=[]
for w,c in lo.items():
    if c>=25:
        sc2.append((((c/nl)/((hi[w]+1)/nh)),w,c,hi[w]))
sc2.sort(reverse=True)
print('--- words over-represented in bottom 40%')
for s,w,c,h in sc2[:25]: print(f"{w} x{s:.1f} ({c} bottom vs {h} top)")
json.dump(rows,open('analysis/_all.json','w',encoding='utf-8'),ensure_ascii=False)
