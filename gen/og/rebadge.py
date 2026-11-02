#!/usr/bin/env python3
"""Redraw only the season badge pill on a link-preview image (og-*.png), keeping photos and title.
Usage: rebadge.py IN.png OUT.png "🍂" "HARVEST SPECIAL · DAILY CASE"
Pill geometry and colours were measured from og-thanksgiving.png (1200x630, 40px background grid)."""
import sys
from PIL import Image, ImageDraw, ImageFont

src, out, emoji, text = sys.argv[1:5]
im = Image.open(src).convert("RGB")
X0, X1, Y0, Y1 = 311, 888, 142, 191          # old pill incl. border
GRID = 40
FILL, BORDER, INK = (72, 37, 16), (180, 122, 50), (255, 197, 107)

# 1) erase the old pill: rebuild background + grid by blending the columns just left and right of it
px = im.load()
def ref(x_side, x, y):
    # same grid phase as x, taken from beside the pill
    k = x % GRID
    base = x_side - (x_side % GRID) + k
    return px[base, y]
for y in range(Y0 - 2, Y1 + 3):
    for x in range(X0 - 2, X1 + 3):
        t = (x - X0) / (X1 - X0)
        l, r = ref(260, x, y), ref(920, x, y)
        px[x, y] = tuple(round(a * (1 - t) + b * t) for a, b in zip(l, r))

# 2) draw the new pill, sized to the new text and centred like the old one
font = ImageFont.truetype("/usr/share/fonts/TTF/JetBrainsMonoNerdFont-Bold.ttf", 25)
SPACING = 0.8
d = ImageDraw.Draw(im)
def text_w(s):
    return sum(font.getlength(c) + SPACING for c in s) - SPACING
EMO = 30
gap = 14
tw = EMO + gap + text_w(text)
padx = 26
cx = (X0 + X1) / 2
left, right = round(cx - tw / 2 - padx), round(cx + tw / 2 + padx)
d.rounded_rectangle((left, Y0, right, Y1), radius=7, fill=FILL, outline=BORDER, width=2)

ef = ImageFont.truetype("/usr/share/fonts/noto/NotoColorEmoji.ttf", 109)
tile = Image.new("RGBA", (140, 140), (0, 0, 0, 0))
ImageDraw.Draw(tile).text((0, 0), emoji, font=ef, embedded_color=True)
tile = tile.crop(tile.getbbox()).resize((EMO, EMO), Image.LANCZOS)
x = cx - tw / 2
cy = (Y0 + Y1) / 2
im.paste(tile, (round(x), round(cy - EMO / 2)), tile)
x += EMO + gap
for c in text:
    d.text((x, cy), c, font=font, fill=INK, anchor="lm")
    x += font.getlength(c) + SPACING
im.save(out, optimize=True)
