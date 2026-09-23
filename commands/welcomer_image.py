import io

from PIL import Image, ImageDraw

from .leveling_image import _avatar_circle, _fit_name, _font, _round_rect_pill

_BG = (35, 39, 42)
_HEADER_BG = (43, 47, 51)
_ROW_BG = (54, 58, 63)
_NAME = (235, 237, 240)
_MUTED = (150, 154, 160)
_BADGE_BG = (30, 33, 36)


def render_welcomer_card(display_name, guild_name, av_img, member_count, header, accent, show_count):
    width = 880
    height = 470
    img = Image.new("RGB", (width, height), _BG)
    draw = ImageDraw.Draw(img)

    _round_rect_pill(draw, 24, 24, width - 24, height - 24, 24, _ROW_BG)
    _round_rect_pill(draw, 24, 24, width - 24, 150, 24, _HEADER_BG)

    draw.text((64, 40), header, font=_font(36, True), fill=accent)
    draw.text((64, 98), str(guild_name), font=_font(20), fill=_MUTED)

    avatar_size = 150
    if av_img is not None:
        av, mask = _avatar_circle(av_img, avatar_size)
        img.paste(av, (64, 180), mask)
    else:
        draw.ellipse((64, 180, 64 + avatar_size, 180 + avatar_size), outline=accent, width=6)

    name_font = _font(40, True)
    name = _fit_name(draw, display_name, name_font, 460)
    draw.text((280, 224), name, font=name_font, fill=_NAME)

    if show_count and member_count:
        count_str = f"MEMBER #{member_count:,}"
        badge_font = _font(24, True)
        badge_w = draw.textlength(count_str, font=badge_font)
        badge_x, badge_y, badge_h = 280, 316, 46
        _round_rect_pill(draw, badge_x, badge_y, badge_x + badge_w + 36, badge_y + badge_h, 12, _BADGE_BG)
        draw.text((badge_x + 18, badge_y + 9), count_str, font=badge_font, fill=_NAME)

    buf = io.BytesIO()
    img.save(buf, "PNG")
    buf.seek(0)
    return buf