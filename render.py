# -*- coding: utf-8 -*-
"""画一张签条：竖排签诗、签号、干支、卦头故事、朱印。"""
import os
import random

from PIL import Image, ImageDraw, ImageFilter, ImageFont

FONT_DIR = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
F_KAI = os.path.join(FONT_DIR, "STKAITI.TTF")
F_SHU = os.path.join(FONT_DIR, "FZSTK.TTF")
F_SONG = os.path.join(FONT_DIR, "STSONG.TTF")

PAPER = (233, 226, 209)
INK = (46, 40, 34)
INK_SOFT = (96, 87, 76)
INK_FAINT = (146, 134, 116)
BRASS = (138, 108, 64)
VERMILION = (168, 46, 40)

_font_cache = {}


def _font(size: int, kind: str = "kai") -> ImageFont.FreeTypeFont:
    path = {"kai": F_KAI, "shu": F_SHU, "song": F_SONG}.get(kind, F_KAI)
    key = (path, size)
    if key not in _font_cache:
        _font_cache[key] = ImageFont.truetype(path, size)
    return _font_cache[key]


def _paper(w: int, h: int) -> Image.Image:
    """米色纸：低频色斑 + 细颗粒 + 几处霉斑。"""
    img = Image.new("RGB", (w, h), PAPER)
    rng = random.Random(w * 131 + h)
    blot = Image.new("L", (max(2, w // 12), max(2, h // 12)), 0)
    bd = ImageDraw.Draw(blot)
    for _ in range(26):
        x = rng.randint(0, blot.width)
        y = rng.randint(0, blot.height)
        r = rng.randint(6, 22)
        bd.ellipse([x - r, y - r, x + r, y + r], fill=rng.randint(30, 90))
    blot = blot.resize((w, h), Image.BICUBIC).filter(ImageFilter.GaussianBlur(w * 0.02))
    img = Image.composite(Image.new("RGB", (w, h), (226, 217, 198)), img,
                          blot.point(lambda v: v // 3))
    px = img.load()
    for _ in range(w * h // 260):
        x, y = rng.randrange(w), rng.randrange(h)
        r, g, b = px[x, y]
        k = rng.randint(-9, 9)
        px[x, y] = (max(0, min(255, r + k)), max(0, min(255, g + k)), max(0, min(255, b + k)))
    for _ in range(rng.randint(3, 7)):
        cx, cy = rng.randrange(w), rng.randrange(h)
        rr = rng.randint(2, 5)
        d = ImageDraw.Draw(img)
        d.ellipse([cx - rr, cy - rr, cx + rr, cy + rr], fill=(206, 190, 166))
    return img.filter(ImageFilter.GaussianBlur(0.4))


def _center(d, text, font, cx, y, color=INK):
    w = d.textlength(text, font=font)
    d.text((cx - w / 2, y), text, font=font, fill=color)


def render_stick(item: dict, out_path: str, width: int = 760, question: str = "",
                 footer: str = "") -> str:
    """item 来自 data.json。竖排四句签诗，右上角签号。"""
    w = max(560, min(1200, int(width)))
    strip_w = int(w * 0.56)
    strip_h = int(w * 0.98)
    top = int(w * 0.30)
    story_lines = []
    story = item.get("story") or ""
    per = 17
    max_lines = 5
    while story:
        story_lines.append(story[:per])
        story = story[per:]
    if len(story_lines) > max_lines:
        story_lines = story_lines[:max_lines]
        story_lines[-1] = story_lines[-1][: per - 1] + "…"

    h = top + strip_h + int(w * 0.10) + len(story_lines) * int(w * 0.035) + int(w * 0.16)
    img = _paper(w, h).convert("RGBA")
    d = ImageDraw.Draw(img, "RGBA")

    # 标题
    _center(d, "灵 签", _font(int(w * 0.058), "shu"), w / 2, int(w * 0.045), INK)
    _center(d, "六十甲子", _font(int(w * 0.022), "song"), w / 2, int(w * 0.125), INK_FAINT)

    # 签条
    x0 = int(w / 2 - strip_w / 2)
    strip = Image.new("RGBA", (strip_w, strip_h), (0, 0, 0, 0))
    sd = ImageDraw.Draw(strip, "RGBA")
    sd.rectangle([0, 0, strip_w - 1, strip_h - 1], fill=(238, 231, 214, 255))
    sd.rectangle([6, 6, strip_w - 7, strip_h - 7], outline=VERMILION, width=2)
    sd.rectangle([13, 13, strip_w - 14, strip_h - 14], outline=(206, 158, 148), width=1)

    # 右上：签号与干支
    num_txt = f"第 {item['num']} 签"
    _center(sd, num_txt, _font(int(strip_w * 0.062), "song"), strip_w / 2, int(strip_h * 0.05), VERMILION)
    _center(sd, item.get("title", ""), _font(int(strip_w * 0.050), "kai"), strip_w / 2,
            int(strip_h * 0.115), INK)
    _center(sd, item.get("meta", ""), _font(int(strip_w * 0.034), "song"), strip_w / 2,
            int(strip_h * 0.170), INK_SOFT)

    # 竖排签诗：四列，从右往左
    poem = item.get("poem") or []
    f_poem = _font(int(strip_w * 0.082), "kai")
    step = int(f_poem.size * 1.16)
    right = int(strip_w * 0.80)
    col_gap = int(strip_w * 0.155)
    for ci, line in enumerate(poem[:4]):
        cx = right - ci * col_gap
        y = int(strip_h * 0.30)
        for ch in line:
            ch_w = sd.textlength(ch, font=f_poem)
            sd.text((cx - ch_w / 2, y), ch, font=f_poem, fill=INK)
            y += step

    # 朱印
    seal = int(strip_w * 0.12)
    sx, sy = int(strip_w * 0.72), int(strip_h * 0.84)
    sd.rectangle([sx, sy, sx + seal, sy + seal], fill=VERMILION)
    ch = (item.get("title") or "签")[0]
    f_seal = _font(int(seal * 0.62), "kai")
    ch_w = sd.textlength(ch, font=f_seal)
    sd.text((sx + seal / 2 - ch_w / 2, sy + seal * 0.14), ch, font=f_seal, fill=(246, 238, 220))

    img.paste(strip, (x0, top), strip)

    # 卦头故事
    d = ImageDraw.Draw(img, "RGBA")
    y = top + strip_h + int(w * 0.045)
    if story_lines:
        _center(d, "卦头故事", _font(int(w * 0.026), "song"), w / 2, y, INK_FAINT)
        y += int(w * 0.042)
        for line in story_lines:
            _center(d, line, _font(int(w * 0.026), "song"), w / 2, y, INK_SOFT)
            y += int(w * 0.036)

    # 分项：功名 财禄 婚姻 疾病 出行
    items = item.get("items") or {}
    if items:
        _center(d, "，".join(f"{k}{v}" for k, v in items.items()),
                _font(int(w * 0.024), "song"), w / 2, y, INK_SOFT)
        y += int(w * 0.040)

    if question:
        q = "问：" + (question if len(question) <= 24 else question[:24] + "…")
        _center(d, q, _font(int(w * 0.024), "song"), w / 2, h - int(w * 0.070), INK_SOFT)
    if footer:
        _center(d, footer, _font(int(w * 0.020), "song"), w / 2, h - int(w * 0.038), INK_FAINT)

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    rgb = img.convert("RGB")
    rgb.save(out_path, "JPEG", quality=92, optimize=True, subsampling=0)
    return out_path
