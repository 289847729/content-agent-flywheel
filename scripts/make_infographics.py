"""生成两篇稿件的正文信息图（PIL 绘制 + 中文文字），输出 PNG 与 base64。

- Astra 篇（石墨极简）：开放节奏时间线 / 关键数字对比 / 国内替代速查卡
- TARS 篇（摸鱼绿）：工作原理循环 / 安装流程 / OSWorld 参考数字
全部数据取自稿件正文，不包含伪造的实测截图。
"""
from __future__ import annotations

import base64
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parent.parent / "outputs" / "assets" / "infographics"
W = 1200

FONT_TTC = "/System/Library/Fonts/Hiragino Sans GB.ttc"
_W3 = ImageFont.truetype(FONT_TTC, 20, index=0)
_W6 = ImageFont.truetype(FONT_TTC, 20, index=2)


def f3(size: int) -> ImageFont.FreeTypeFont:
    return _W3.font_variant(size=size)


def f6(size: int) -> ImageFont.FreeTypeFont:
    return _W6.font_variant(size=size)


# 石墨极简配色
G_DARK = "#27272A"
G_MAIN = "#52525B"
G_LINE = "#E4E4E7"
G_LIGHT = "#F4F4F5"
G_CARD = "#FAFAFA"
G_SUB = "#A1A1AA"
G_ORANGE = "#F97316"
# 摸鱼绿配色
M_GREEN = "#059669"
M_GREEN_MID = "#34D399"
M_GREEN_LIGHT = "#A7F3D0"
M_GREEN_BG = "#ECFDF5"
M_YELLOW = "#FDE68A"
M_RED = "#DC2626"
M_RED_LIGHT = "#FECACA"
M_DARK = "#111827"
M_GRAY = "#9CA3AF"
M_LINE = "#E5E7EB"
M_CARD = "#F9FAFB"


def canvas(h: int) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    img = Image.new("RGB", (W, h), "#FFFFFF")
    return img, ImageDraw.Draw(img)


def title_bar(d: ImageDraw.ImageDraw, text: str, accent: str) -> int:
    """左上标题 + 点缀圆点，返回正文起始 y。"""
    d.ellipse([70, 66, 84, 80], fill=accent)
    d.text((100, 46), text, font=f6(30), fill="#111827")
    return 124


def tlen(d: ImageDraw.ImageDraw, text: str, font) -> float:
    return d.textlength(text, font=font)


# ---------------------------------------------------------------- Astra 篇

def astra_timeline() -> Image.Image:
    img, d = canvas(460)
    y0 = title_bar(d, "GPT-6 Astra 开放节奏", G_ORANGE)
    axis_y = y0 + 200
    d.line([80, axis_y, 1120, axis_y], fill=G_LINE, width=5)
    nodes = [
        (235, "9 月 3 日", "正式发布", True),
        (510, "首批开放", "Trusted Access 企业客户", True),
        (790, "随后数日", "Plus / Pro / Business / Enterprise", True),
        (1010, "免费版", "不在开放名单", False),
    ]
    for x, top, bottom, active in nodes:
        if active:
            d.ellipse([x - 11, axis_y - 11, x + 11, axis_y + 11], fill=G_DARK)
            tcolor, scolor = G_DARK, G_MAIN
        else:
            d.ellipse([x - 11, axis_y - 11, x + 11, axis_y + 11], outline=G_SUB, width=4, fill="#FFFFFF")
            tcolor, scolor = G_SUB, G_SUB
        tw = tlen(d, top, f6(24))
        d.text((x - tw / 2, axis_y - 78), top, font=f6(24), fill=tcolor)
        bw = tlen(d, bottom, f3(20))
        d.text((x - bw / 2, axis_y + 32), bottom, font=f3(20), fill=scolor)
    return img


def astra_numbers() -> Image.Image:
    img, d = canvas(660)
    y0 = title_bar(d, "几个关键数字", G_ORANGE)

    # 左：Terminal-Bench
    d.text((90, y0 + 6), "Terminal-Bench 4.0", font=f6(22), fill=G_MAIN)
    bars = [("GPT-5.6", 37.3, G_LINE, G_SUB), ("Astra", 57.9, G_DARK, G_DARK)]
    by = y0 + 58
    for name, val, bar, label in bars:
        d.text((90, by + 2), name, font=f6(20), fill=G_MAIN)
        bw = 300 * val / 60
        d.rounded_rectangle([210, by, 210 + bw, by + 26], radius=13, fill=bar)
        d.text((222 + bw, by + 1), f"{val}%", font=f6(20), fill=label)
        by += 52

    # 右：API 价格
    rx = 640
    d.text((rx, y0 + 6), "API 价格（美元 / 百万 token）", font=f6(22), fill=G_MAIN)
    rows = [("输入", 4, 10), ("输出", 20, 50)]
    by = y0 + 58
    for name, prev, astra in rows:
        d.text((rx, by + 2), name, font=f6(20), fill=G_MAIN)
        pw = 150 * prev / 50
        aw = 150 * astra / 50
        d.rounded_rectangle([rx + 90, by, rx + 90 + pw, by + 26], radius=13, fill=G_LINE)
        d.text((rx + 98 + pw, by + 1), f"上代 {prev}", font=f3(18), fill=G_SUB)
        d.rounded_rectangle([rx + 90, by + 40, rx + 90 + aw, by + 66], radius=13, fill=G_DARK)
        d.text((rx + 98 + aw, by + 41), f"Astra {astra}", font=f6(20), fill=G_DARK)
        by += 100
    d.text((rx, by + 4), "整体比上一代贵 2.5 倍", font=f6(22), fill=G_ORANGE)

    # 下部：ARC-AGI-3
    cy = y0 + 306
    d.line([90, cy, 1110, cy], fill=G_LINE, width=3)
    d.text((90, cy + 22), "ARC-AGI-3 跑分", font=f6(22), fill=G_MAIN)
    base = cy + 130
    # 官方柱
    d.rounded_rectangle([170, base - 150, 260, base], radius=10, fill=G_DARK)
    d.text((150, base - 196), "官方自报", font=f6(22), fill=G_DARK)
    d.text((178, base - 150 + 8), "99.9%", font=f6(22), fill="#FFFFFF")
    # 第三方虚线示意柱
    for yy in range(base - 55, base, 12):
        d.line([430, yy, 520, yy], fill=G_SUB, width=5)
    d.text((428, base - 108), "第三方复测", font=f6(22), fill=G_SUB)
    d.text((430, base + 18), "统一框架下远低于官方口径", font=f3(20), fill=G_SUB)
    d.text((600, base - 96), "看跑分要带余量", font=f6(26), fill=G_DARK)
    d.text((600, base - 48), "别把厂商叙事当事实", font=f3(22), fill=G_MAIN)
    return img


def astra_alt() -> Image.Image:
    img, d = canvas(560)
    y0 = title_bar(d, "不翻墙，现在能用的", G_ORANGE)
    cards = [
        ("01", "DeepSeek", "专家模式旗舰模型免费", "中文场景推理第一梯队"),
        ("02", "Kimi", "长文档阅读看家", "几十万字资料丢进去提问"),
        ("03", "豆包 · 通义", "字节 / 阿里全家桶", "办公 · 画图 · 文档处理"),
        ("04", "WPS AI · ima", "离办公更近", "贴合你的日常工作流"),
    ]
    cw, ch, gap = 520, 168, 24
    for i, (num, name, line1, line2) in enumerate(cards):
        col, row = i % 2, i // 2
        x = 80 + col * (cw + gap)
        y = y0 + row * (ch + gap)
        d.rounded_rectangle([x, y, x + cw, y + ch], radius=16, fill=G_CARD, outline=G_LINE, width=2)
        d.text((x + 24, y + 18), num, font=f3(16), fill=G_SUB)
        d.ellipse([x + 24, y + 52, x + 36, y + 64], fill=G_ORANGE)
        d.text((x + 52, y + 40), name, font=f6(26), fill=G_DARK)
        d.text((x + 24, y + 88), line1, font=f3(20), fill=G_MAIN)
        d.text((x + 24, y + 122), line2, font=f3(18), fill=G_SUB)
    return img


# ---------------------------------------------------------------- TARS 篇

def tars_workflow() -> Image.Image:
    img, d = canvas(560)
    y0 = title_bar(d, "它是怎么干活的", M_GREEN)
    steps = [("1", "看屏幕", "截屏理解当前界面"), ("2", "想步骤", "视觉模型推理下一步"), ("3", "动鼠标", "模拟键鼠执行操作")]
    cw, ch, gap = 280, 170, 110
    x0 = 80
    cy = y0 + 70
    for i, (num, name, sub) in enumerate(steps):
        x = x0 + i * (cw + gap)
        fill = M_GREEN_BG if i % 2 == 0 else "#FFFFFF"
        d.rounded_rectangle([x, cy, x + cw, cy + ch], radius=18, fill=fill, outline=M_GREEN, width=3)
        d.ellipse([x + 22, cy + 20, x + 60, cy + 58], fill=M_GREEN)
        d.text((x + 33, cy + 26), num, font=f6(24), fill="#FFFFFF")
        d.text((x + 22, cy + 76), name, font=f6(30), fill=M_DARK)
        d.text((x + 22, cy + 120), sub, font=f3(18), fill=M_GRAY)
        if i < 2:
            d.line([x + cw + 10, cy + ch / 2, x + cw + gap - 10, cy + ch / 2], fill=M_GREEN_MID, width=8)
            d.polygon([(x + cw + gap - 4, cy + ch / 2), (x + cw + gap - 34, cy + ch / 2 - 14), (x + cw + gap - 34, cy + ch / 2 + 14)], fill=M_GREEN_MID)
    # 回环箭头
    loop_y = cy + ch + 46
    d.arc([x0 + 140, loop_y - 40, x0 + 260, loop_y + 40], start=200, end=340, fill=M_GREEN_MID, width=8)
    d.polygon([(x0 + 60, loop_y + 40), (x0 + 90, loop_y + 52), (x0 + 92, loop_y + 26)], fill=M_GREEN_MID)
    d.text((x0 + 300, loop_y - 14), "循环往复，直到任务完成", font=f3(20), fill=M_GRAY)
    # 底部对比
    by = loop_y + 70
    d.rounded_rectangle([80, by, 560, by + 58], radius=12, fill=M_CARD, outline=M_LINE, width=2)
    d.text((106, by + 13), "传统 RPA：写死流程", font=f6(22), fill=M_GRAY)
    d.rounded_rectangle([580, by, 1120, by + 58], radius=12, fill=M_GREEN_BG, outline=M_GREEN, width=2)
    d.text((606, by + 13), "Agent TARS：像人一样边看边做", font=f6(22), fill=M_GREEN)
    return img


def tars_install() -> Image.Image:
    img, d = canvas(730)
    y0 = title_bar(d, "五分钟装好，但别跳过这一步", M_GREEN)
    steps = [
        ("一条命令安装", "brew install --cask ui-tars", "code"),
        ("或从 Releases 下载安装包", "把应用拖进 Applications 即可", "plain"),
        ("开两个权限（缺一不可）", "辅助功能：注入鼠标键盘操作\n屏幕录制：截屏“看”你的界面", "warn"),
        ("接上“大脑”模型服务", "火山引擎 API Key（有免费额度）\n或 Hugging Face 部署 7B 模型", "plain"),
        ("跑第一个任务", "打字下指令，看它边截图边操作", "plain"),
    ]
    sy = y0 + 10
    for i, (head, body, kind) in enumerate(steps):
        y = sy + i * 98
        d.ellipse([80, y + 4, 120, y + 44], fill=M_GREEN)
        d.text((92, y + 9), str(i + 1), font=f6(22), fill="#FFFFFF")
        if kind == "code":
            d.text((150, y), head, font=f6(24), fill=M_DARK)
            d.rounded_rectangle([150, y + 38, 950, y + 84], radius=10, fill="#111827")
            d.text((176, y + 52), body, font=f3(22), fill="#FFFFFF")
        elif kind == "warn":
            d.text((150, y), head, font=f6(24), fill=M_RED)
            for j, line in enumerate(body.split("\n")):
                d.text((150, y + 40 + j * 34), line, font=f3(21), fill="#374151")
        else:
            d.text((150, y), head, font=f6(24), fill=M_DARK)
            for j, line in enumerate(body.split("\n")):
                d.text((150, y + 40 + j * 34), line, font=f3(21), fill="#374151")
    # 底部提示
    by = sy + 5 * 98 + 14
    d.rounded_rectangle([80, by, 1120, by + 62], radius=12, fill=M_YELLOW)
    d.text((106, by + 14), "多屏用户注意：先拔副屏再跑，当前版本只支持单显示器", font=f6(22), fill="#78350F")
    return img


def tars_osworld() -> Image.Image:
    img, d = canvas(500)
    y0 = title_bar(d, "它靠不靠谱：两个参考数字", M_GREEN)
    # 左：OSWorld
    d.text((90, y0 + 10), "OSWorld 电脑操作基准", font=f6(22), fill=M_GRAY)
    d.text((100, y0 + 60), "27.5", font=f6(72), fill="#D1D5DB")
    d.text((108, y0 + 168), "开源 7B 版本", font=f3(18), fill=M_GRAY)
    ax = 270
    d.line([ax, y0 + 130, ax + 88, y0 + 130], fill=M_GREEN_MID, width=10)
    d.polygon([(ax + 94, y0 + 130), (ax + 60, y0 + 112), (ax + 60, y0 + 148)], fill=M_GREEN_MID)
    d.text((380, y0 + 60), "42.5", font=f6(72), fill=M_GREEN)
    d.text((388, y0 + 168), "UI-TARS-1.5", font=f3(18), fill=M_GRAY)
    d.text((90, y0 + 220), "提升主要来自“怎么想”，不是模型变大", font=f3(20), fill="#374151")
    # 右：横评
    rx = 620
    d.line([rx, y0, rx, y0 + 280], fill=M_LINE, width=3)
    d.text((rx + 30, y0 + 10), "博主横评 8 款桌面 AI 工具", font=f6(22), fill=M_GRAY)
    d.text((rx + 30, y0 + 56), "每款至少 20 个任务", font=f3(20), fill=M_GRAY)
    d.text((rx + 30, y0 + 104), "整体能干活，但到不了", font=f6(24), fill=M_DARK)
    d.text((rx + 30, y0 + 148), "“放心托管”的程度", font=f6(24), fill=M_DARK)
    d.rounded_rectangle([rx + 30, y0 + 200, rx + 520, y0 + 252], radius=12, fill=M_GREEN_BG)
    d.text((rx + 56, y0 + 216), "更适合：盯着它干活", font=f6(22), fill=M_GREEN)
    return img


def save_png_and_b64(img: Image.Image, name: str) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    png_path = OUT / f"{name}.png"
    img.save(png_path, "PNG", optimize=True)
    raw = png_path.read_bytes()
    b64 = base64.b64encode(raw).decode("ascii")
    (OUT / f"{name}.b64").write_text(b64, encoding="ascii")
    print(f"{name}: {png_path} ({len(raw) // 1024} KiB, b64 {len(b64)} chars)")


if __name__ == "__main__":
    save_png_and_b64(astra_timeline(), "astra-timeline")
    save_png_and_b64(astra_numbers(), "astra-numbers")
    save_png_and_b64(astra_alt(), "astra-alt")
    save_png_and_b64(tars_workflow(), "tars-workflow")
    save_png_and_b64(tars_install(), "tars-install")
    save_png_and_b64(tars_osworld(), "tars-osworld")
