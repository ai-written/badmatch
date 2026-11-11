from PIL import Image, ImageDraw, ImageFilter
import math, os

S = 4
N = 512
canvas = Image.new('RGBA', (N*S, N*S), (0,0,0,0))

# Soft shadow on a separate layer.
shadow = Image.new('RGBA', canvas.size, (0,0,0,0))
sd = ImageDraw.Draw(shadow)
def sc(v): return int(round(v*S))
sd.ellipse((sc(91),sc(343),sc(238),sc(461)), fill=(73,48,142,42))
shadow = shadow.filter(ImageFilter.GaussianBlur(sc(13)))
canvas.alpha_composite(shadow)

d = ImageDraw.Draw(canvas)
# Feather fan, spreading upward-right from the neck.
colors = [
    (230,221,255,255), (218,204,252,255), (204,185,246,255),
    (190,166,240,255), (177,148,232,255), (163,131,222,255),
    (151,116,212,255)
]
# Fan blades as individually shaped feathers, with dark violet outlines and center ribs.
blades = [
    [(172,361),(118,220),(132,157),(153,136),(181,210),(204,355)],
    [(181,354),(151,191),(170,121),(193,103),(218,184),(212,351)],
    [(190,349),(190,178),(211,112),(238,99),(255,175),(223,350)],
    [(202,348),(230,179),(260,124),(288,123),(292,194),(236,354)],
    [(213,353),(269,202),(309,165),(335,175),(321,238),(250,365)],
    [(220,363),(300,243),(345,217),(365,239),(339,286),(263,380)],
    [(228,376),(330,296),(373,279),(390,305),(350,341),(273,394)],
]
for i, pts in enumerate(blades):
    p = [(sc(x),sc(y)) for x,y in pts]
    d.polygon(p, fill=colors[i])
    d.line(p + [p[0]], fill=(139,105,206,180), width=sc(2), joint='curve')
    # Feather rib runs from cork-side junction to feather tip.
    tip = pts[1]
    d.line((sc(224),sc(371),sc((tip[0]+pts[2][0])//2),sc((tip[1]+pts[2][1])//2)),
           fill=(255,255,255,165), width=sc(3))
# Neck collar connecting the fan.
d.polygon([(sc(162),sc(345)),(sc(221),sc(370)),(sc(246),sc(333)),(sc(190),sc(308))], fill=(130,91,207,255))
d.line((sc(162),sc(345),sc(221),sc(370)), fill=(245,237,255,255), width=sc(5))
d.line((sc(190),sc(308),sc(246),sc(333)), fill=(104,70,178,255), width=sc(3))
# Cork: shaded rounded silhouette with highlight.
d.ellipse((sc(91),sc(342),sc(221),sc(460)), fill=(199,178,246,255), outline=(151,118,219,255), width=sc(3))
d.ellipse((sc(104),sc(349),sc(188),sc(429)), fill=(226,215,255,255))
d.ellipse((sc(119),sc(357),sc(150),sc(386)), fill=(246,241,255,210))
# Smooth supersampled edges.
canvas = canvas.resize((N,N), Image.Resampling.LANCZOS)
out = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'source', 'shuttlecock_clean.png')
canvas.save(out, optimize=True)
print(out)
