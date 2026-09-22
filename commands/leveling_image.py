import io
import os

from PIL import Image, ImageDraw, ImageFont

_FONT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "fonts")
_FONT_REGULAR = os.path.join(_FONT_DIR, "DejaVuSans.ttf")
_FONT_BOLD = os.path.join(_FONT_DIR, "DejaVuSans-Bold.ttf")

_BG = (35, 39, 42)
_HEADER_BG = (43, 47, 51)
_ROW_BG = (54, 58, 63)
_NAME = (235, 237, 240)
_MUTED = (150, 154, 160)
_ACCENT = (88, 101, 242)
_GOLD = (255, 202, 40)
_SILVER = (203, 209, 217)
_BRONZE = (205, 127, 50)
_BAR_BG = (30, 33, 36)
_MEDAL_TEXT = (30, 33, 36)

_font_cache = {}


def _font(size, bold=False):
    key = (size, bold)
    cached = _font_cache.get(key)
    if cached is not None:
        return cached
    path = _FONT_BOLD if bold else _FONT_REGULAR
    try:
        font = ImageFont.truetype(path, size)
    except Exception:
        font = ImageFont.load_default()
    _font_cache[key] = font
    return font


def _fit_name(draw, name, font, max_width):
    name = str(name)
    if draw.textlength(name, font=font) <= max_width:
        return name
    while name and draw.textlength(name + "…", font=font) > max_width:
        name = name[:-1]
    return name + "…"


def _round_rect_pill(draw, x0, y0, x1, y1, radius, fill, outline=None):
    draw.rounded_rectangle([x0, y0, x1, y1], radius=radius, fill=fill, outline=outline)


def _avatar_image(img):
    size = 84
    im = img.convert("RGB").resize((size, size), Image.LANCZOS)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size, size), fill=255)
    return im, mask


def render_leaderboard(entries, guild_name):
    width = 920
    top = 230
    row_h = 96
    pad_bottom = 40
    left_pad = 64
    right_edge = width - 64

    height = top + row_h * len(entries) + pad_bottom
    img = Image.new("RGB", (width, height), _BG)
    draw = ImageDraw.Draw(img)

    _round_rect_pill(draw, 24, 24, width - 24, height - 24, 24, _BG, outline=(54, 58, 63))

    _round_rect_pill(draw, 24, 24, width - 24, 198, 24, _HEADER_BG)
    draw.text((64, 54), "Server Leveling Leaderboard", font=_font(40, True), fill=_NAME)
    draw.text((66, 116), guild_name, font=_font(28, True), fill=_ACCENT)
    draw.text((66, 158), "Sorted by total XP earned", font=_font(20), fill=_MUTED)

    for i, (name_part, av_img, level, into, need, xp) in enumerate(entries):
        y = top + i * row_h
        _round_rect_pill(draw, 40, y, width - 40, y + row_h - 12, 16, _ROW_BG)

        medal = None
        num_color = _MUTED
        if i == 0:
            medal = _GOLD
        elif i == 1:
            medal = _SILVER
        elif i == 2:
            medal = _BRONZE

        # rank badge
        bbox = (40 + 24, y + 30, 40 + 24 + 36, y + 30 + 36)
        if medal:
            draw.ellipse(bbox, fill=medal)
            draw.text((bbox[0] + 8, bbox[1] + 4), str(i + 1), font=_font(20, True), fill=_MEDAL_TEXT)
        else:
            draw.rounded_rectangle(bbox, radius=18, fill=(40, 44, 48))
            draw.text((bbox[0] + 9, bbox[1] + 4), str(i + 1), font=_font(20, True), fill=num_color)

        # avatar
        if av_img is not None:
            av, _ = _avatar_image(av_img)
            img.paste(av, (40 + 24 + 52, y + 10), rgba_mask_84())

        name_x = 40 + 24 + 52 + 96
        name_font = _font(30, True)
        name = _fit_name(draw, name_part, name_font, right_edge - name_x - 260)
        draw.text((name_x, y + 14), name, font=name_font, fill=_NAME)

        # level + progress
        level_tag = f"LEVEL {level}"
        tag_font = _font(24, True)
        tag_w = draw.textlength(level_tag, font=tag_font)
        tag_x = right_edge - tag_w
        draw.text((tag_x, y + 16), level_tag, font=tag_font, fill=_ACCENT)

        # xp text
        xp_font = _font(19)
        xp_str = f"{xp:,} XP" if xp >= 0 else "0 XP"
        xp_w = draw.textlength(xp_str, font=xp_font)
        draw.text((right_edge - xp_w, y + 54), xp_str, font=xp_font, fill=_MUTED)

        # progress bar
        bar_x0 = name_x
        bar_x1 = right_edge - 260
        bar_w = max(40, bar_x1 - bar_x0)
        bar_top = y + 60
        bar_h = 14
        _round_rect_pill(draw, bar_x0, bar_top, bar_x0 + bar_w, bar_top + bar_h, 7, _BAR_BG)

        pct = (into / need) if need > 0 and into >= 0 else 0.0
        pct = max(0.0, min(1.0, pct))
        fill_w = int(bar_w * pct)
        if fill_w > 0:
            _round_rect_pill(draw, bar_x0, bar_top, bar_x0 + fill_w, bar_top + bar_h, 7, _ACCENT)

        next_str = f"{into:,}/{need:,} XP to level {level + 1}" if need > 0 else f"Level {level + 1}"
        next_font = _font(17)
        draw.text((bar_x0, bar_top + bar_h + 6), next_str, font=next_font, fill=_MUTED)

    buf = io.BytesIO()
    img.save(buf, "PNG")
    buf.seek(0)
    return buf


_avatar_masks = {}


def rgba_mask_84():
    size = 84
    mask = _avatar_masks.get("84")
    if mask is None:
        mask = Image.new("L", (size, size), 0)
        ImageDraw.Draw(mask).ellipse((0, 0, size, size), fill=255)
        _avatar_masks["84"] = mask
    return mask