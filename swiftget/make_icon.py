"""Generate icon.ico / icon.png (original SwiftGet artwork). Requires Pillow."""
from PIL import Image, ImageDraw

def draw(n=256):
    s = 4  # supersample
    W = n * s
    im = Image.new("RGBA", (W, W), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    r = W // 5
    # gradient tile
    for y in range(W):
        t = y / W
        c = (int(31 + (23 - 31) * t), int(111 + (78 - 111) * t), int(235 + (166 - 235) * t), 255)
        d.line([(0, y), (W, y)], fill=c)
    mask = Image.new("L", (W, W), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, W - 1, W - 1], r, fill=255)
    im.putalpha(mask)
    d = ImageDraw.Draw(im)
    w = W
    # arrow
    shaft = [(w*0.40, w*0.16), (w*0.60, w*0.16), (w*0.60, w*0.46), (w*0.76, w*0.46),
             (w*0.50, w*0.72), (w*0.24, w*0.46), (w*0.40, w*0.46)]
    d.polygon(shaft, fill="white")
    # tray
    d.rounded_rectangle([w*0.20, w*0.78, w*0.80, w*0.86], w*0.04, fill="white")
    # speed streaks
    for i, x in enumerate((0.13, 0.08)):
        y = w * (0.26 + i * 0.12)
        d.rounded_rectangle([w*x, y, w*(x+0.20), y + w*0.045], w*0.02, fill=(255, 255, 255, 170))
    return im.resize((n, n), Image.LANCZOS)

img = draw(256)
img.save("icon.png")
img.save("icon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
print("ok")
