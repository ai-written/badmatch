from PIL import Image, ImageDraw, ImageFilter
import os

# 仓库根目录（脚本放在 design/ 下，资源在 design/source/，产物供 client/ 使用）
root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
src = Image.open(os.path.join(root, 'design', 'source', 'badminton_source.jpg')).convert('RGB')
w, h = src.size

# Rebuild the list area with a smooth, sampled cool-white page background.
base = src.copy()
pix = src.load()
# Estimate a subtle vertical background gradient from unobstructed outer margins.
rows = []
for y in range(480, 2825):
    samples = [pix[x, y] for x in (8, 18, 1372, 1382)]
    rows.append(tuple(sum(c[i] for c in samples) // len(samples) for i in range(3)))
# Smooth neighboring row estimates to avoid card edge contamination.
smoothed = []
for i in range(len(rows)):
    band = rows[max(0, i-35):min(len(rows), i+36)]
    smoothed.append(tuple(sum(c[k] for c in band) // len(band) for k in range(3)))
fill = Image.new('RGB', (w, 2345))
d = ImageDraw.Draw(fill)
for yy, color in enumerate(smoothed):
    d.line((0, yy, w, yy), fill=color)
# Gentle horizontal luminance blend, matching the bright center of the original page.
fp = fill.load()
for yy in range(fill.height):
    for xx in range(w):
        r, g, b = fp[xx, yy]
        t = max(0, 1 - abs(xx - w*0.5)/(w*0.5)) * 0.025
        fp[xx, yy] = (min(255, int(r + (255-r)*t)), min(255, int(g + (255-g)*t)), min(255, int(b + (255-b)*t)))
base.paste(fill, (0, 480))
# Softly blend horizontal boundaries into the retained top scene and bottom navigation.
base.save(os.path.join(root, 'design', 'source', 'badminton_no_list.jpg'), quality=94, subsampling=0)

# Four standalone visual crops with no event text: three colored shuttle tiles and the hero shuttle.
crops = {
    'shuttle_purple.jpg': (53, 1832, 316, 2097),
    'shuttle_orange.jpg': (53, 2140, 316, 2405),
    'shuttle_mint.jpg': (53, 2435, 316, 2703),
    'shuttle_hero.jpg': (760, 165, 1105, 490),
}
for name, box in crops.items():
    crop = src.crop(box)
    crop.save(os.path.join(root, 'design', 'source', name), quality=95, subsampling=0)
print('created:', ', '.join(['badminton_no_list.jpg', *crops.keys()]))
