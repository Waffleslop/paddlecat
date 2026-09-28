"""Build PaddleCAT-app.ico: a crisp "PC" monogram for the small sizes Windows
uses in the taskbar and title bar (16-48 px), where the full PADDLE CAT
wordmark shrinks into mush, plus the designed artwork from 64 px up.

    python icon/make_small_icons.py
"""
import os
from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
WIN = os.path.join(HERE, "windows")
BG, EDGE = (30, 11, 46, 255), (58, 29, 87, 255)
MAG, LIME = (255, 61, 242, 255), (182, 255, 59, 255)
SMALL = [16, 20, 24, 32, 40, 48]
LARGE = [64, 96, 128, 256]


def _font(px):
    f = ImageFont.truetype(r"C:\Windows\Fonts\bahnschrift.ttf", px)
    try:
        f.set_variation_by_name("Bold")
    except Exception:
        pass
    return f


def monogram(s):
    # Drawn at the target size (not downscaled) so FreeType hints each edge.
    im = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((0, 0, s - 1, s - 1), max(2, s // 6), fill=BG,
                        outline=EDGE if s >= 24 else None)
    f = _font(round(s * 0.70))
    wp, wc = d.textlength("P", font=f), d.textlength("C", font=f)
    x = round((s - (wp + wc)) / 2)
    bb = d.textbbox((0, 0), "PC", font=f)
    y = round((s - (bb[3] - bb[1])) / 2 - bb[1])
    d.text((x, y), "P", font=f, fill=MAG)
    d.text((x + wp, y), "C", font=f, fill=LIME)
    return im


def main():
    out_png = os.path.join(WIN, "png-small")
    os.makedirs(out_png, exist_ok=True)
    imgs = []
    for s in SMALL:
        im = monogram(s)
        im.save(os.path.join(out_png, f"PaddleCAT-PC-{s}.png"))
        imgs.append(im)
    for s in LARGE:
        imgs.append(Image.open(os.path.join(WIN, "png", f"PaddleCAT-{s}.png"))
                    .convert("RGBA"))
    big = imgs[-1]
    big.save(os.path.join(WIN, "PaddleCAT-app.ico"), format="ICO",
             sizes=[(i.width, i.height) for i in imgs], append_images=imgs[:-1])
    print("wrote", os.path.join(WIN, "PaddleCAT-app.ico"))


if __name__ == "__main__":
    main()
