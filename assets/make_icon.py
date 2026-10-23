"""Render the Image Hunt icon set from code (Pillow): red tile, magnifier, skull in the lens.
Run: .venv/bin/python assets/make_icon.py  -> assets/icon.png, apple-touch-icon.png, favicon.png, icon.ico (+ icon.icns on macOS)"""
import os, shutil, subprocess, sys, tempfile
from PIL import Image, ImageDraw, ImageFilter

HERE = os.path.dirname(os.path.abspath(__file__))
S = 1024 * 4  # supersample, downscale at the end


def tile():
    bg = Image.new('RGBA', (S, S), (0, 0, 0, 0))
    grad = Image.new('RGBA', (S, S))
    gd = ImageDraw.Draw(grad)
    for y in range(S):  # hot red top -> blood bottom
        t = y / S
        gd.line([(0, y), (S, y)], fill=(int(255 - 120 * t), int(30 - 30 * t), 0, 255))
    mask = Image.new('L', (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle([S * .08, S * .08, S * .92, S * .92], radius=S * .2, fill=255)
    bg.paste(grad, (0, 0), mask)
    return bg


def art(img):
    d = ImageDraw.Draw(img)
    cx, cy, r, w = S * .44, S * .43, S * .24, S * .055
    ink, bone, hole = (0, 0, 0, 255), (255, 236, 214, 255), (40, 0, 0, 255)
    # handle (drawn first so the ring overlaps it)
    d.line([(cx + r * .72, cy + r * .72), (S * .78, S * .78)], fill=ink, width=int(w * 2.2))
    d.line([(cx + r * .76, cy + r * .76), (S * .76, S * .76)], fill=bone, width=int(w * 1.2))
    d.ellipse([cx - r - w, cy - r - w, cx + r + w, cy + r + w], fill=ink)
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(120, 0, 0, 255))
    # skull: cranium + jaw, eyes, nose, teeth gaps
    k = r * .62
    d.ellipse([cx - k, cy - k * 1.05, cx + k, cy + k * .75], fill=bone)
    d.rounded_rectangle([cx - k * .58, cy + k * .3, cx + k * .58, cy + k * 1.0], radius=k * .15, fill=bone)
    for sx in (-1, 1):
        ex = cx + sx * k * .4
        d.ellipse([ex - k * .26, cy - k * .2, ex + k * .26, cy + k * .3], fill=hole)
    d.polygon([(cx, cy + k * .32), (cx - k * .12, cy + k * .55), (cx + k * .12, cy + k * .55)], fill=hole)
    for i in (-1, 0, 1):
        x = cx + i * k * .22
        d.line([(x, cy + k * .72), (x, cy + k * 1.0)], fill=hole, width=int(k * .06))
    # lens glint
    d.arc([cx - r * .82, cy - r * .82, cx + r * .82, cy + r * .82], 200, 245, fill=(255, 255, 255, 170), width=int(w * .5))
    return img


def main():
    big = art(tile()).resize((1024, 1024), Image.LANCZOS)
    big.save(os.path.join(HERE, 'icon.png'))
    big.resize((180, 180), Image.LANCZOS).save(os.path.join(HERE, 'apple-touch-icon.png'))
    big.resize((64, 64), Image.LANCZOS).save(os.path.join(HERE, 'favicon.png'))
    big.save(os.path.join(HERE, 'icon.ico'), sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    if sys.platform == 'darwin' and shutil.which('iconutil'):
        with tempfile.TemporaryDirectory() as t:
            iset = os.path.join(t, 'icon.iconset'); os.mkdir(iset)
            for n in (16, 32, 128, 256, 512):
                big.resize((n, n), Image.LANCZOS).save(os.path.join(iset, f'icon_{n}x{n}.png'))
                big.resize((n * 2, n * 2), Image.LANCZOS).save(os.path.join(iset, f'icon_{n}x{n}@2x.png'))
            subprocess.run(['iconutil', '-c', 'icns', iset, '-o', os.path.join(HERE, 'icon.icns')], check=True)


if __name__ == '__main__':
    main()
