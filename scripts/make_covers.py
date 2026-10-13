"""生成稿件内嵌封面图（纯 PIL 绘制，无文字），输出 JPEG 与 base64。

公众号草稿推送要求正文至少含一张图片（第一张兼作封面），
各图分别配合石墨极简 / 摸鱼绿 / 橄榄手记主题气质。
"""
from __future__ import annotations

import base64
from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent.parent / "outputs" / "assets"
SIZE = 1024

# 石墨极简配色
GRAY_DARK = "#27272A"
GRAY_MAIN = "#52525B"
GRAY_LINE = "#E4E4E7"
GRAY_LIGHT = "#F4F4F5"
ORANGE = "#F97316"
# 摸鱼绿配色
GREEN = "#059669"
GREEN_MID = "#34D399"
GREEN_LIGHT = "#A7F3D0"
GREEN_BG = "#ECFDF5"
YELLOW = "#FDE68A"
DARK = "#111827"
# 橄榄手记配色
OLIVE_PAPER = "#fdfdf8"
OLIVE_INK = "#1e1f23"
OLIVE_LINE = "#bfc1b7"
OLIVE_LIGHT = "#eeefe9"
OLIVE_ORANGE = "#ed7b2f"
OLIVE_TEXT = "#4d4f46"


def new_canvas(color: str) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    img = Image.new("RGB", (SIZE, SIZE), color)
    return img, ImageDraw.Draw(img)


def draw_graphite_cover() -> Image.Image:
    """纯白底 + 石墨细线几何：大圆对勾、文档纸片、齿轮、光标，一处橙点点睛。"""
    img, d = new_canvas("#FFFFFF")
    cx, cy = 512, 430
    # 主圆（细线圆环）
    d.ellipse([cx - 210, cy - 210, cx + 210, cy + 210], outline=GRAY_MAIN, width=10)
    # 圆内对勾
    d.line([cx - 90, cy + 10, cx - 20, cy + 80, cx + 105, cy - 70], fill=GRAY_DARK, width=34, joint="curve")
    # 左侧文档纸片（两张错落）
    d.rounded_rectangle([150, 560, 420, 800], radius=18, outline=GRAY_MAIN, width=9, fill="#FFFFFF")
    for i, yy in enumerate((610, 660, 710)):
        w = 200 - i * 40
        d.line([185, yy, 185 + w, yy], fill=GRAY_LINE, width=14)
    d.rounded_rectangle([215, 500, 485, 740], radius=18, outline=GRAY_LINE, width=9, fill="#FFFFFF")
    # 右侧齿轮
    gx, gy, gr = 780, 620, 105
    for ang in range(0, 360, 45):
        import math
        rad = math.radians(ang)
        x0 = gx + math.cos(rad) * (gr + 18)
        y0 = gy + math.sin(rad) * (gr + 18)
        d.line([x0, y0, gx + math.cos(rad) * gr, gy + math.sin(rad) * gr], fill=GRAY_MAIN, width=16)
    d.ellipse([gx - gr, gy - gr, gx + gr, gy + gr], outline=GRAY_MAIN, width=10)
    d.ellipse([gx - 34, gy - 34, gx + 34, gy + 34], fill="#FFFFFF", outline=GRAY_MAIN, width=10)
    # 顶部光标
    d.polygon([(300, 130), (300, 285), (345, 245), (392, 300), (412, 278), (365, 222), (408, 212)],
              fill=GRAY_DARK)
    # 连接细线
    d.line([512, 700, 660, 645], fill=GRAY_LINE, width=8)
    # 橙色点睛圆点
    d.ellipse([782, 795, 842, 855], fill=ORANGE)
    # 点缀小圆
    d.ellipse([140, 300, 172, 332], fill=GRAY_LIGHT, outline=GRAY_LINE, width=5)
    d.ellipse([858, 268, 882, 292], fill=GRAY_LIGHT, outline=GRAY_LINE, width=4)
    return img


def draw_moyu_cover() -> Image.Image:
    """浅绿底 + 左侧屏幕绿色对勾，右侧圆润小机器人握着鼠标。"""
    img, d = new_canvas(GREEN_BG)
    # 背景点缀
    d.ellipse([90, 120, 150, 180], fill=GREEN_LIGHT)
    d.ellipse([860, 90, 906, 136], fill=GREEN_LIGHT)
    d.ellipse([890, 850, 960, 920], fill=GREEN_LIGHT)
    d.rounded_rectangle([40, 900, 130, 920], radius=10, fill=GREEN_MID)
    # 屏幕（左侧）
    sx, sy, sw, sh = 140, 320, 420, 300
    d.rounded_rectangle([sx, sy, sx + sw, sy + sh], radius=32, fill="#FFFFFF", outline=GREEN, width=12)
    # 屏幕内：标题线 + 对勾
    d.rounded_rectangle([sx + 40, sy + 48, sx + 300, sy + 84], radius=18, fill=GREEN_LIGHT)
    d.rounded_rectangle([sx + 40, sy + 112, sx + 200, sy + 138], radius=12, fill=GREEN_LIGHT)
    ccx, ccy = sx + sw - 90, sy + sh - 90
    d.ellipse([ccx - 60, ccy - 60, ccx + 60, ccy + 60], fill=GREEN)
    d.line([ccx - 34, ccy + 4, ccx - 10, ccy + 28, ccx + 38, ccy - 24], fill="#FFFFFF", width=16, joint="curve")
    # 屏幕底座
    d.rounded_rectangle([sx + 130, sy + sh + 14, sx + sw - 130, sy + sh + 44], radius=15, fill=GREEN_MID)
    # 机器人（右侧）
    bx, by = 640, 480
    # 身体
    d.rounded_rectangle([bx, by, bx + 280, by + 240], radius=44, fill=GREEN, outline=DARK, width=8)
    # 肚皮
    d.rounded_rectangle([bx + 50, by + 60, bx + 210, by + 170], radius=26, fill="#FFFFFF")
    d.line([bx + 130, by + 60, bx + 130, by + 170], fill=GREEN_LIGHT, width=9)
    d.line([bx + 50, by + 115, bx + 210, by + 115], fill=GREEN_LIGHT, width=9)
    # 头
    hx, hy = bx + 30, by - 170
    d.rounded_rectangle([hx, hy, hx + 220, hy + 180], radius=50, fill=GREEN_MID, outline=DARK, width=8)
    # 天线
    d.line([hx + 100, hy, hx + 100, hy - 52], fill=DARK, width=10)
    d.ellipse([hx + 76, hy - 82, hx + 124, hy - 34], fill=YELLOW, outline=DARK, width=8)
    # 眼睛
    d.ellipse([hx + 36, hy + 48, hx + 86, hy + 98], fill="#FFFFFF", outline=DARK, width=7)
    d.ellipse([hx + 114, hy + 48, hx + 164, hy + 98], fill="#FFFFFF", outline=DARK, width=7)
    d.ellipse([hx + 54, hy + 66, hx + 70, hy + 82], fill=DARK)
    d.ellipse([hx + 132, hy + 66, hx + 148, hy + 82], fill=DARK)
    # 微笑
    d.arc([hx + 68, hy + 90, hx + 132, hy + 146], start=20, end=160, fill=DARK, width=8)
    # 手臂到鼠标
    d.line([bx + 10, by + 230, 690, 810], fill=GREEN_MID, width=42)
    d.rounded_rectangle([660, 800, 780, 910], radius=28, fill="#FFFFFF", outline=DARK, width=8)
    d.ellipse([682, 822, 758, 886], fill=YELLOW, outline=DARK, width=7)
    return img


def draw_card(d, x, y, w, h, dark=False):
    fill = OLIVE_INK if dark else OLIVE_LIGHT
    outline = OLIVE_INK if dark else OLIVE_LINE
    d.rounded_rectangle([x, y, x + w, y + h], radius=24, fill=fill, outline=outline, width=8)
    # 头像圆
    cx, cy = x + w // 2, y + 86
    if dark:
        d.ellipse([cx - 44, cy - 44, cx + 44, cy + 44], fill=OLIVE_ORANGE)
        # 深卡头像里画对勾
        d.line([cx - 18, cy, cx - 4, cy + 16, cx + 22, cy - 14], fill=OLIVE_PAPER, width=10, joint="curve")
    else:
        d.ellipse([cx - 44, cy - 44, cx + 44, cy + 44], outline=OLIVE_INK, width=8)
        d.line([cx - 16, cy + 4, cx - 6, cy + 14, cx + 16, cy - 8], fill=OLIVE_INK, width=8, joint="curve")
    # 模拟文字行
    line_color = OLIVE_PAPER if dark else OLIVE_LINE
    for i in range(3):
        lw = 150 - i * 30
        ly = y + 160 + i * 46
        d.rounded_rectangle([cx - lw // 2, ly, cx + lw // 2, ly + 16], radius=8, fill=line_color)
        if dark and i == 0:
            d.rounded_rectangle([cx - lw // 2, ly, cx + 40, ly + 16], radius=8, fill=OLIVE_ORANGE)


def _dashed_hline(d, y, x0, x1, fill, width=10, dash=26, gap=20):
    x = x0
    while x + dash <= x1:
        d.line([x, y, x + dash, y], fill=fill, width=width)
        x += dash + gap
    if x < x1:
        d.line([x, y, x1, y], fill=fill, width=width)


def draw_ticket_cover() -> Image.Image:
    """米黄票据底 + 黑色硬边框硬阴影 + 顶部绿条 + 绿色撕票虚线票根（无文字）。"""
    img, d = new_canvas("#fffef8")
    # 硬阴影（右下偏移 40px）
    d.rectangle([136, 152, 912, 896], fill="#1a1a1a")
    # 票据主体
    d.rectangle([96, 112, 872, 856], fill="#fffef8", outline="#1a1a1a", width=12)
    # 顶部绿条
    d.rectangle([96, 112, 872, 192], fill=GREEN)
    # 绿条左侧标签模拟条（米黄短条）
    d.rounded_rectangle([140, 146, 320, 164], radius=9, fill="#fffef8")
    # 绿条右侧五星（5 个白点）
    for i in range(5):
        cx = 800 - i * 44
        d.ellipse([cx - 9, 146, cx + 9, 164], fill="#fffef8")
    # 主区大标题模拟（深色条）
    d.rounded_rectangle([140, 252, 600, 312], radius=10, fill="#1a1a1a")
    d.rounded_rectangle([140, 340, 460, 392], radius=10, fill="#1a1a1a")
    # 副标题灰条
    d.rounded_rectangle([140, 424, 520, 462], radius=10, fill="#9CA3AF")
    # 绿色虚线分隔
    _dashed_hline(d, 516, 140, 736, GREEN_LIGHT, width=10)
    # 简介块
    d.rounded_rectangle([140, 560, 736, 690], radius=16, fill="#F0FDF4", outline="#A7F3D0", width=8)
    for i in range(3):
        lw = 480 - i * 120
        d.rounded_rectangle([172, 596 + i * 44, 172 + lw, 612 + i * 44], radius=8, fill="#D1FAE5")
    # 三个标签框
    for i in range(3):
        x0 = 140 + i * 148
        d.rounded_rectangle([x0, 726, x0 + 124, 756], radius=10, outline=GREEN, width=6)
        d.rounded_rectangle([x0 + 16, 736, x0 + 108, 746], radius=5, fill=GREEN_LIGHT)
    # 票根区
    d.rectangle([736, 192, 872, 780], fill="#F0FDF4")
    # 票根 NO. 圆 + 绿条
    d.ellipse([776, 236, 832, 292], fill=GREEN)
    d.rounded_rectangle([766, 322, 842, 342], radius=10, fill=GREEN_LIGHT)
    # 竖排三点
    for i in range(3):
        yy = 420 + i * 60
        d.ellipse([796, yy, 812, yy + 16], fill="#A7F3D0")
    # GRADE 框
    d.rounded_rectangle([766, 640, 842, 700], radius=8, outline=GREEN, width=8)
    # 撕票线（横贯）+ 剪刀
    _dashed_hline(d, 780, 96, 872, "#A7F3D0", width=12, dash=34, gap=26)
    d.polygon([(480, 750), (500, 812), (462, 812)], fill="#1a1a1a")
    d.polygon([(520, 750), (538, 812), (500, 812)], fill="#1a1a1a")
    d.ellipse([440, 796, 476, 832], fill="#1a1a1a")
    d.ellipse([524, 796, 560, 832], fill="#1a1a1a")
    # 底部文字模拟条
    d.rounded_rectangle([140, 826, 400, 842], radius=8, fill="#D1D5DB")
    d.rounded_rectangle([700, 826, 828, 842], radius=8, fill="#D1D5DB")
    return img


def draw_olive_cover() -> Image.Image:
    """米白底 + 三张对比卡片（灰/墨黑/灰，中间突出）+ 橙色点缀。"""
    img, d = new_canvas(OLIVE_PAPER)

    # 顶部：墨点 + 两侧橄榄线（呼应头图卡标签条）
    d.ellipse([488, 96, 536, 144], fill=OLIVE_INK)
    d.line([480, 120, 420, 120], fill=OLIVE_LINE, width=6)
    d.line([544, 120, 604, 120], fill=OLIVE_LINE, width=6)

    # 三张卡片横排
    card_w, card_h = 220, 320
    y0 = 260
    draw_card(d, 130, y0, card_w, card_h)                       # 左灰卡
    draw_card(d, 402, y0 - 36, card_w, card_h + 72, dark=True)  # 中墨黑卡（略高，强调）
    draw_card(d, 674, y0, card_w, card_h)                       # 右灰卡

    # 卡间 vs 图形：两个对向箭头
    for ax in (372, 644):
        cy_v = y0 + card_h // 2
        d.line([ax - 18, cy_v + 6, ax + 18, cy_v - 30], fill=OLIVE_LINE, width=10)
        d.line([ax - 18, cy_v + 6, ax + 18, cy_v + 42], fill=OLIVE_LINE, width=10)
    d.ellipse([504, y0 + card_h // 2 - 44, 520, y0 + card_h // 2 - 28], fill=OLIVE_ORANGE)

    # 中卡下方：橙色下划线（呼应主题橙色下划线语言）
    d.rounded_rectangle([470, y0 + card_h + 60, 554, y0 + card_h + 70], radius=5, fill=OLIVE_ORANGE)

    # 底部：三色点状分隔线
    d.ellipse([452, 920, 484, 952], fill=OLIVE_TEXT)
    d.ellipse([500, 920, 532, 952], fill=OLIVE_LINE)
    d.ellipse([548, 920, 580, 952], fill=OLIVE_ORANGE)
    return img


def save_jpeg_and_b64(img: Image.Image, name: str) -> str:
    OUT.mkdir(parents=True, exist_ok=True)
    jpg_path = OUT / f"{name}.jpg"
    img.save(jpg_path, "JPEG", quality=88, optimize=True)
    raw = jpg_path.read_bytes()
    b64 = base64.b64encode(raw).decode("ascii")
    (OUT / f"{name}.b64").write_text(b64, encoding="ascii")
    print(f"{name}: {jpg_path} ({len(raw) // 1024} KiB, b64 {len(b64)} chars)")
    return b64


if __name__ == "__main__":
    save_jpeg_and_b64(draw_graphite_cover(), "gpt6-astra-cover")
    save_jpeg_and_b64(draw_moyu_cover(), "agent-tars-cover")
    save_jpeg_and_b64(draw_olive_cover(), "hengping-3flags-cover")
