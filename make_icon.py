"""Генерирует icon.ico — стеклянный квадрат с папкой и звёздочкой."""
from PIL import Image, ImageDraw

S = 256
img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(img)

# Фон: вертикальный градиент сине-фиолетовый
top = (90, 108, 255)
bot = (138, 77, 255)
grad = Image.new("RGBA", (S, S))
gd = ImageDraw.Draw(grad)
for y in range(S):
    t = y / (S - 1)
    r = int(top[0] + (bot[0] - top[0]) * t)
    g = int(top[1] + (bot[1] - top[1]) * t)
    b = int(top[2] + (bot[2] - top[2]) * t)
    gd.line([(0, y), (S, y)], fill=(r, g, b, 255))

# Скруглённая маска
mask = Image.new("L", (S, S), 0)
ImageDraw.Draw(mask).rounded_rectangle([8, 8, S - 8, S - 8], radius=56, fill=255)
img.paste(grad, (0, 0), mask)

# Верхний блик (стекло)
hl = Image.new("RGBA", (S, S), (0, 0, 0, 0))
ImageDraw.Draw(hl).rounded_rectangle([8, 8, S - 8, S // 2], radius=56,
                                     fill=(255, 255, 255, 38))
img.alpha_composite(hl)

# Папка
fw = (255, 255, 255, 240)
# вкладка
d.rounded_rectangle([66, 86, 138, 108], radius=8, fill=fw)
# тело
d.rounded_rectangle([66, 100, 190, 178], radius=14, fill=fw)
# прорезь (тень категорий)
d.rounded_rectangle([84, 124, 172, 132], radius=4, fill=(138, 77, 255, 120))
d.rounded_rectangle([84, 142, 150, 150], radius=4, fill=(138, 77, 255, 90))

# Звёздочка ✦
def sparkle(cx, cy, r, fill):
    pts = [(cx, cy - r), (cx + r * 0.28, cy - r * 0.28),
           (cx + r, cy), (cx + r * 0.28, cy + r * 0.28),
           (cx, cy + r), (cx - r * 0.28, cy + r * 0.28),
           (cx - r, cy), (cx - r * 0.28, cy - r * 0.28)]
    d.polygon(pts, fill=fill)

sparkle(176, 92, 26, (255, 255, 255, 255))
sparkle(176, 92, 12, (138, 77, 255, 255))

sizes = [(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)]
img.save("icon.ico", sizes=sizes)
img.save("icon.png")
print("icon.ico создан")
