#!/usr/bin/env python3
"""Лист сравнения кадров: sheet.py <out.png> <crop x0,y0,x1,y1|full> <scale> <cols> <png> [<png> ...] — подпись = имя файла."""
import sys
from pathlib import Path
from PIL import Image, ImageDraw
out, crop, scale, cols = sys.argv[1], sys.argv[2], float(sys.argv[3]), int(sys.argv[4])
files = sys.argv[5:]
ims = []
for f in files:
    im = Image.open(f).convert('RGB')
    if crop != 'full':
        x0, y0, x1, y1 = map(int, crop.split(','))
        im = im.crop((x0, y0, x1, y1))
    im = im.resize((int(im.width * scale), int(im.height * scale)), Image.LANCZOS)
    d = ImageDraw.Draw(im)
    lab = Path(f).parent.name + '/' + Path(f).stem
    d.rectangle((0, 0, 8 + 7 * len(lab), 18), fill=(0, 0, 0))
    d.text((4, 3), lab, fill=(255, 255, 0))
    ims.append(im)
w, h = ims[0].size
rows = (len(ims) + cols - 1) // cols
W = Image.new('RGB', (w * cols + 4 * (cols - 1), h * rows + 4 * (rows - 1)), (60, 60, 60))
for i, im in enumerate(ims):
    W.paste(im, ((i % cols) * (w + 4), (i // cols) * (h + 4)))
W.save(out)
print(out, W.size)
