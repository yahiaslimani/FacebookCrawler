import json, re, sys, statistics as st
sys.stdout.reconfigure(encoding='utf-8')
rows=json.load(open('analysis/_all.json',encoding='utf-8'))
by={}
for r in rows: by.setdefault(r['src'],[]).append(r)
eng=lambda r:r['reaction_count']+3*r['comment_count']+4*r['share_count']
for rs in by.values():
    rs.sort(key=eng)
    for i,r in enumerate(rs): r['pct']=i/max(1,len(rs)-1)
good=[r for r in rows if r['post_text'] and len(by[r['src']])>=500]
T={
 'حمى/مرض (fever, illness)':r'حمى|سخونة|حرارة|مرض|سعال|كحة|زكام|التهاب|إسهال|اسهال|قيء|حساسية|فيروس|مضاد حيوي|دواء|دوا',
 'نوم الرضيع':r'نوم|يرقد|ينام|رقاد|نعاس|يسهر',
 'رضاعة/تغذية':r'رضاع|ترضع|حليب|الحليب|تغذية|ياكل|أكل|اكل|تنويع|دياڨ|فطام|مرقة|خضر|فواكه',
 'تطور/نمو (milestones)':r'يمشي|يحبي|يتكلم|نمو|تطور|وزن|طول|أسنان|سنة|شهر|يجلس|يضحك|التسنين',
 'تربية/سلوك':r'تربية|عقاب|يعاند|عناد|نصرخ|صراخ|الغضب|غضب|يبكي|بكاء|نفسية|ثقة|حنان|مراقبة|المراهق',
 'حمل/ولادة':r'حامل|حمل|ولادة|الولادة|تلد|طلق|قيصرية|نفاس|الجنين|المخاض|الوضع',
 'أمومة/مشاعر الأم':r'الأم |ماما|مامي|الأمومة|أمومة|تعب|إرهاق|حنين|ضغط|وحدة|اكتئاب|سند',
 'أب/عائلة/حماة':r'الأب|باباه|الزوج|راجلك|حماتك|حماة|العائلة|الأهل|الجدة|جدة|عائلتك',
 'ختان/طبي-إجراءات':r'ختان|الختانة|تلقيح|لقاح|اللقاح|فحص|تحاليل|تحليل|كشف|اشعة|أشعة',
 'سلامة/حوادث':r'اختناق|حرق|سقوط|يطيح|سم|تسمم|حادث|خطر|ابتلاع|غرق|لدغ|مناوبة|استعجالات',
 'قصة/حكاية شخصية':r'قصة|حكاية|تذكر|ذكريات|كي كنت|كنت صغير|مرة |في يوم',
 'دين/دعاء':r'الله|ربي|دعاء|بركة|رمضان|جمعة|اللهم|الحمد',
 'تعليم/لعب':r'لعب|ألعاب|تعلم|تعليم|مدرسة|الروضة|حروف|قراءة|تلوين|نشاط|مونتيسوري|ذكاء',
 'عنوان/إعلان/عيادة':r'العنوان|هاتف|الجامعي|عيادة|مسكن|أوقات العمل|للحجز|رقم الهاتف|www|http',
}
print(f"{'topic':34} {'n':>5} {'avg-pct':>8}   per-page avg-pct (n>=15)")
res=[]
for name,pat in T.items():
    sel=[r for r in good if re.search(pat,r['post_text'])]
    pp=[]
    for s in by:
        x=[r['pct'] for r in sel if r['src']==s]
        if len(x)>=15: pp.append(f"{s[:8]}:{st.mean(x):.2f}")
    res.append((st.mean(r['pct'] for r in sel),name,len(sel),pp))
for a,n,c,pp in sorted(res,reverse=True): print(f"{n:34} {c:5} {a:8.3f}   {' '.join(pp)}")
# format: numbered list x length per page
print('\nnumbered list per page:')
for s in by:
    a=[r['pct'] for r in good if r['src']==s and re.search(r'(^|\n)\s*[\d١-٩][\-\.\)]',r['post_text'])]
    b=[r['pct'] for r in good if r['src']==s and not re.search(r'(^|\n)\s*[\d١-٩][\-\.\)]',r['post_text'])]
    if len(a)>=15: print(s[:20],len(a),f"{st.mean(a):.2f} vs {st.mean(b):.2f}")
print('\nhook openers (first 3 words) among top 5% posts:')
from collections import Counter
c=Counter()
for r in good:
    if r['pct']>=0.95:
        c[' '.join(re.sub(r'[^\w\s]','',r['post_text']).split()[:2])]+=1
print(c.most_common(15))
