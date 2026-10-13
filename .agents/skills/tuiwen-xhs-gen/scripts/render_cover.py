#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
tuiwen-xhs-gen: 小红书置顶概括封面图自动化渲染引擎
基于 Pillow 渲染 1080x1440 (3:4) 竖屏高清专业设计卡片，支持输出本地图片与 Base64 字符串。
"""

import os
import sys
import json
import argparse
import base64
from PIL import Image, ImageDraw, ImageFont

WIDTH = 1080
HEIGHT = 1440

# 系统字体备选优先级
FONT_CANDIDATES_MAIN = [
    '/System/Library/Fonts/Hiragino Sans GB.ttc',
    '/System/Library/Fonts/PingFang.ttc',
    '/System/Library/Fonts/STHeiti Light.ttc',
    '/System/Library/Fonts/Supplemental/Songti.ttc'
]

FONT_CANDIDATES_BOLD = [
    '/System/Library/Fonts/STHeiti Medium.ttc',
    '/System/Library/Fonts/Hiragino Sans GB.ttc',
    '/System/Library/Fonts/PingFang.ttc'
]

FONT_CANDIDATES_NUM = [
    '/Library/Fonts/Arial Unicode.ttf',
    '/System/Library/Fonts/Supplemental/Arial.ttf'
]

def find_font(candidates):
    for path in candidates:
        if os.path.exists(path):
            return path
    return None

FONT_MAIN_PATH = find_font(FONT_CANDIDATES_MAIN)
FONT_BOLD_PATH = find_font(FONT_CANDIDATES_BOLD) or FONT_MAIN_PATH
FONT_NUM_PATH = find_font(FONT_CANDIDATES_NUM) or FONT_BOLD_PATH

def get_font(size, bold=False, num=False):
    path = FONT_BOLD_PATH if bold else (FONT_NUM_PATH if num else FONT_MAIN_PATH)
    if path:
        return ImageFont.truetype(path, size)
    return ImageFont.load_default()

def render_cover(config: dict, output_image_path: str) -> str:
    """渲染封面并返回 base64 数据 url"""
    img = Image.new('RGB', (WIDTH, HEIGHT), color='#0B0F17')
    draw = ImageDraw.Draw(img)

    # 1. 顶部标签栏
    top_tag = config.get('top_tag', '科技前沿 · 重磅突发')
    draw.rounded_rectangle([60, 60, 60 + 300, 110], radius=12, fill='#1E293B')
    draw.text((80, 72), top_tag, font=get_font(22, bold=True), fill='#FF6700')
    draw.text((WIDTH - 280, 72), '小红书独家速览 >>', font=get_font(22, bold=True), fill='#64748B')
    draw.line([60, 130, WIDTH - 60, 130], fill='#1E293B', width=2)

    # 2. 核心大字报冲击区
    badge = config.get('badge', '【突发重磅】')
    draw.rounded_rectangle([60, 165, 340, 220], radius=12, fill='#FF6700')
    draw.text((80, 178), badge, font=get_font(22, bold=True), fill='#FFFFFF')

    highlight = config.get('highlight', '')
    title = config.get('title', '')
    subtitle = config.get('subtitle', '')

    y_text = 245
    if highlight:
        draw.text((60, y_text), highlight, font=get_font(86, bold=True), fill='#FF6700')
        y_text += 105
    if title:
        draw.text((60, y_text), title, font=get_font(74, bold=True), fill='#FFFFFF')
        y_text += 95
    if subtitle:
        draw.text((60, y_text), subtitle, font=get_font(74, bold=True), fill='#38BDF8')

    # 3. 核心概括导览大卡片
    card_top = 550
    card_bot = 1240
    draw.rounded_rectangle([60, card_top, WIDTH - 60, card_bot], radius=24, fill='#131A26', outline='#222E42', width=2)

    card_title = config.get('card_title', '【一图读懂】核心全貌速览')
    draw.text((100, card_top + 38), card_title, font=get_font(28, bold=True), fill='#F8FAFC')
    draw.line([100, card_top + 85, WIDTH - 100, card_top + 85], fill='#1E293B', width=2)

    items = config.get('items', [])
    default_colors = ['#FF6700', '#38BDF8', '#22C55E', '#F43F5E']

    y_item = card_top + 105
    for i, item in enumerate(items[:4]):
        tag = item.get('tag', f'要点 {i+1}')
        color = item.get('color', default_colors[i % len(default_colors)])
        it_title = item.get('title', '')
        it_desc = item.get('desc', '')

        # 标签胶囊
        draw.rounded_rectangle([100, y_item + 4, 100 + 130, y_item + 38], radius=8, fill=color)
        draw.text((115, y_item + 9), tag, font=get_font(18, bold=True), fill='#FFFFFF')
        
        # 标题
        draw.text((250, y_item + 4), it_title, font=get_font(25, bold=True), fill='#FFFFFF')
        
        # 详细描述 (两行截断适配)
        draw.text((100, y_item + 48), it_desc, font=get_font(20), fill='#94A3B8')
        y_item += 115

    # 4. 底部行动呼吁条
    draw.rounded_rectangle([60, 1265, WIDTH - 60, 1355], radius=16, fill='#1A2332', outline='#2D3B4E', width=1)
    footer_text = config.get('footer_tip', '>> 完整深度实测评测与上手方法，正文见 >>')
    draw.text((100, 1285), footer_text, font=get_font(24, bold=True), fill='#38BDF8')
    draw.text((WIDTH - 220, 1285), '建议点赞收藏', font=get_font(22, bold=True), fill='#FF6700')

    # 保存图片
    os.makedirs(os.path.dirname(os.path.abspath(output_image_path)), exist_ok=True)
    img.save(output_image_path, quality=94)

    # 转 base64
    with open(output_image_path, 'rb') as f:
        b64_str = base64.b64encode(f.read()).decode('utf-8')
    
    return f'data:image/jpeg;base64,{b64_str}'

def main():
    parser = argparse.ArgumentParser(description="生成小红书高质感置顶概括封面图")
    parser.add_argument("--config", help="JSON 配置文件路径")
    parser.add_argument("--json-str", help="JSON 配置字符串")
    parser.add_argument("--output", default="outputs/小红书/images/xhs_cover.jpg", help="输出图片路径")
    parser.add_argument("--print-b64", action="store_true", help="是否在终端输出 Base64")
    
    args = parser.parse_args()

    cfg = {}
    if args.config and os.path.exists(args.config):
        with open(args.config, 'r', encoding='utf-8') as f:
            cfg = json.load(f)
    elif args.json_str:
        cfg = json.loads(args.json_str)
    else:
        # 默认测试样例
        cfg = {
            "top_tag": "科技前沿 · 重磅突发",
            "badge": "【全球开源榜首换人】",
            "highlight": "46 分登顶！",
            "title": "最强开源大模型",
            "subtitle": "突然是小米做的？！",
            "card_title": "【一图读懂】核心全貌速览",
            "items": [
                {"tag": "登顶榜首", "color": "#FF6700", "title": "46 分拿下全球开源第一", "desc": "权威榜单反超友商，万亿参数 MoE 架构，全模态通吃。"},
                {"tag": "透明厨房", "color": "#38BDF8", "title": "训练全程直播公开", "desc": "不到 6 天训完，总成本约 350 万美元，75 万条轨迹全公开。"},
                {"tag": "打工福利", "color": "#22C55E", "title": "电脑直接离线跑，长录音秒出纪要", "desc": "推桌面客户端，本地隐私安全，100 万字长文速读。"},
                {"tag": "理性客观", "color": "#F43F5E", "title": "离闭源旗舰（53分）尚有差距", "desc": "不盲目神化，开源一路狂追让普通人享受免费普惠 AI。"}
            ]
        }

    b64 = render_cover(cfg, args.output)
    print(f"Cover generated at: {args.output}")
    if args.print_b64:
        print(b64)

if __name__ == '__main__':
    main()
