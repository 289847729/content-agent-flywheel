"""默认封面生成 —— 复用 scripts/make_covers.py 的绘图函数，输出内嵌 base64。

公众号草稿推送要求正文至少含一张图片（第一张兼作封面），
排版稿没有真实图片素材时用这里生成的主题默认封面兜底。
另外提供小红书 3:4 竖版首图（带大字文案）的生成。
"""
from __future__ import annotations

import base64
import io
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import make_covers  # noqa: E402

# 主题名 → 封面绘图函数（配色与各主题气质对应）
COVER_FUNCTIONS = {
    "moyu-green": make_covers.draw_moyu_cover,
    "graphite-minimal": make_covers.draw_graphite_cover,
    "olive-journal": make_covers.draw_olive_cover,
}

_cache: dict[str, str] = {}


def cover_b64(theme: str) -> str:
    """返回主题默认封面的 JPEG base64（进程内缓存）。"""
    theme = theme or "moyu-green"
    draw = COVER_FUNCTIONS.get(theme) or make_covers.draw_moyu_cover
    if theme not in _cache:
        buffer = io.BytesIO()
        draw().save(buffer, "JPEG", quality=88, optimize=True)
        _cache[theme] = base64.b64encode(buffer.getvalue()).decode("ascii")
    return _cache[theme]


def cover_data_url(theme: str) -> str:
    return f"data:image/jpeg;base64,{cover_b64(theme)}"


# 公众号文章封面（2.35:1，微信推荐比例）
WECHAT_COVER_SIZE = (900, 383)

_CJK_FONT_CANDIDATES = (
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/Supplemental/Songti.ttc",
    "/Library/Fonts/Arial Unicode.ttf",
)

# 内置主题 → 公众号封面配色（bg / fg / accent）
_THEME_COVER_COLORS = {
    "moyu-green": {"bg": "#ECFDF5", "fg": "#111827", "accent": "#059669"},
    "graphite-minimal": {"bg": "#FFFFFF", "fg": "#27272A", "accent": "#F97316"},
    "olive-journal": {"bg": "#fdfdf8", "fg": "#1e1f23", "accent": "#ed7b2f"},
    "moyu-ticket": {"bg": "#fffef8", "fg": "#1a1a1a", "accent": "#059669"},
}


def _find_cjk_font() -> str | None:
    """返回可用的中文字体路径；都没有时返回 None（调用方回退默认字体）。"""
    for candidate in _CJK_FONT_CANDIDATES:
        if Path(candidate).is_file():
            return candidate
    return None


def _load_font(size: int) -> ImageFont.FreeTypeFont:
    path = _find_cjk_font()
    if path:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            pass
    return ImageFont.load_default(size)


def _wrap_cjk(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> list[str]:
    """按像素宽度对中文文本手动换行（逐字累加测量）。"""
    lines: list[str] = []
    current = ""
    for char in text:
        if char == "\n":
            lines.append(current)
            current = ""
            continue
        if draw.textlength(current + char, font=font) <= max_width or not current:
            current += char
        else:
            lines.append(current)
            current = char
    if current:
        lines.append(current)
    return lines


def _fit_banner_title(draw: ImageDraw.ImageDraw, text: str, max_width: int,
                      max_lines: int = 2):
    """公众号横幅封面标题：自动缩字号至不超过 max_lines 行，超出截断加省略号。"""
    fallback = None
    for size in (62, 56, 50, 44, 40, 34):
        font = _load_font(size)
        lines = _wrap_cjk(draw, text, font, max_width)
        if len(lines) <= max_lines:
            return font, lines
        fallback = (font, lines)
    font, lines = fallback
    lines = lines[:max_lines]
    if lines:
        lines[-1] = lines[-1] + "…"
    return font, lines


# ---- 公众号文章封面（2.35:1，带文章标题） ----

def article_cover(title: str, *, bg: str = "#ECFDF5", fg: str = "#111827",
                  accent: str = "#059669", kicker: str = "小崽子AI") -> Image.Image:
    """生成公众号文章封面（900×383，约 2.35:1）：主题配色 + 文章标题大字。

    草稿箱缩略图取正文首图，用它替掉与文章无关的固定主题插画，
    每篇文章的缩略图就能一眼区分。
    """
    width, height = WECHAT_COVER_SIZE
    image = Image.new("RGB", WECHAT_COVER_SIZE, bg)
    draw = ImageDraw.Draw(image)

    # 左侧强调竖条 + 左上角小标签
    draw.rectangle([0, 0, 12, height], fill=accent)
    pad_x = 72
    label_font = _load_font(26)
    draw.text((pad_x, 50), (kicker or "小崽子AI"), font=label_font, fill=accent)
    draw.rounded_rectangle([pad_x, 88, pad_x + 92, 94], radius=3, fill=accent)

    # 标题：自动字号、最多两行、左对齐、垂直略偏下
    title_text = (title or "").strip() or "未命名文章"
    font, lines = _fit_banner_title(draw, title_text, width - pad_x * 2)
    line_height = int(font.size * 1.3)
    y = (height - line_height * len(lines)) // 2 + 26
    for line in lines:
        draw.text((pad_x, y), line, font=font, fill=fg)
        y += line_height
    return image


def article_cover_data_url(title: str, *, bg: str = "#ECFDF5", fg: str = "#111827",
                           accent: str = "#059669", kicker: str = "小崽子AI") -> str:
    """公众号文章封面的 JPEG Data URL（内嵌进排版稿）。"""
    buffer = io.BytesIO()
    article_cover(title, bg=bg, fg=fg, accent=accent, kicker=kicker).save(
        buffer, "JPEG", quality=90, optimize=True
    )
    return "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def article_cover_data_url_for_theme(title: str, theme: str = "moyu-green",
                                     kicker: str = "小崽子AI") -> str:
    """按内置主题配色生成公众号文章封面（内置引擎用）。"""
    colors = _THEME_COVER_COLORS.get(theme) or _THEME_COVER_COLORS["moyu-green"]
    return article_cover_data_url(
        title, bg=colors["bg"], fg=colors["fg"], accent=colors["accent"], kicker=kicker
    )


def article_cover_png_bytes(title: str, *, bg: str = "#ECFDF5", fg: str = "#111827",
                            accent: str = "#059669", kicker: str = "小崽子AI") -> bytes:
    """公众号文章封面的 PNG 字节（供 Word 文档等直接嵌图）。"""
    buffer = io.BytesIO()
    article_cover(title, bg=bg, fg=fg, accent=accent, kicker=kicker).save(
        buffer, "PNG", optimize=True
    )
    return buffer.getvalue()


def article_cover_png_bytes_for_theme(title: str, theme: str = "moyu-green",
                                      kicker: str = "小崽子AI") -> bytes:
    """按内置主题配色生成公众号文章封面（PNG 字节）。"""
    colors = _THEME_COVER_COLORS.get(theme) or _THEME_COVER_COLORS["moyu-green"]
    return article_cover_png_bytes(
        title, bg=colors["bg"], fg=colors["fg"], accent=colors["accent"], kicker=kicker
    )


# ---- 今日头条 16:9 高清横版封面生成 (tuiwen-jrtt-gen) ----

def render_toutiao_cover(config: dict, output_path: str | Path) -> str:
    """渲染今日头条 16:9 (1280×720) 高清吸睛封面图并落盘。"""
    width, height = 1280, 720
    img = Image.new("RGB", (width, height), color="#0A0E17")
    draw = ImageDraw.Draw(img)

    # 1. 顶部标签栏
    top_tag = config.get("top_tag") or "今日头条 · 深度图文"
    draw.rounded_rectangle([70, 50, 70 + 320, 95], radius=10, fill="#1E293B")
    draw.text((90, 60), top_tag, font=_load_font(22), fill="#E22C38")
    draw.text((width - 240, 60), "头条独家精选 >>", font=_load_font(20), fill="#64748B")
    draw.line([70, 115, width - 70, 115], fill="#1E293B", width=2)

    # 2. 视觉焦点区
    badge = config.get("badge") or "【重大突发】"
    draw.rounded_rectangle([70, 145, 300, 195], radius=10, fill="#E22C38")
    draw.text((88, 155), badge, font=_load_font(22), fill="#FFFFFF")

    highlight = (config.get("highlight") or "").strip()
    title = (config.get("title") or "").strip()
    subtitle = (config.get("subtitle") or "").strip()

    y = 220
    if highlight:
        draw.text((70, y), highlight, font=_load_font(68), fill="#FF6700")
        y += 85

    if title:
        font_title = _load_font(52)
        lines = _wrap_cjk(draw, title, font_title, width - 140)[:2]
        for line in lines:
            draw.text((70, y), line, font=font_title, fill="#FFFFFF")
            y += 68

    if subtitle:
        font_sub = _load_font(36)
        lines_sub = _wrap_cjk(draw, subtitle, font_sub, width - 140)[:2]
        for line in lines_sub:
            draw.text((70, y), line, font=font_sub, fill="#38BDF8")
            y += 50

    # 3. 底部行动条
    draw.rounded_rectangle([70, height - 110, width - 70, height - 45], radius=14, fill="#131A26", outline="#1F2A3D", width=1)
    draw.text((100, height - 90), ">> 深度深度长图文拆解，建议收藏研读 >>", font=_load_font(22), fill="#94A3B8")
    draw.text((width - 260, height - 90), "欢迎评论区探讨", font=_load_font(22), fill="#E22C38")

    out_file = Path(output_path).resolve()
    out_file.parent.mkdir(parents=True, exist_ok=True)
    img.save(str(out_file), "JPEG", quality=94, optimize=True)
    return str(out_file)

