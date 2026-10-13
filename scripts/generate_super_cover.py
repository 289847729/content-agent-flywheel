import os
import base64
from PIL import Image, ImageDraw, ImageFont

WIDTH = 1080
HEIGHT = 1440

FONT_MAIN = '/System/Library/Fonts/Hiragino Sans GB.ttc'
FONT_BOLD = '/System/Library/Fonts/STHeiti Medium.ttc'
FONT_NUM = '/Library/Fonts/Arial Unicode.ttf'

def get_font(size, bold=False, num=False):
    path = FONT_BOLD if bold else (FONT_NUM if num else FONT_MAIN)
    return ImageFont.truetype(path, size)

img = Image.new('RGB', (WIDTH, HEIGHT), color='#0B0F17')
draw = ImageDraw.Draw(img)

# 1. 顶部小红书标签栏
draw.rounded_rectangle([60, 60, 60 + 290, 110], radius=12, fill='#1E293B')
draw.text((80, 72), '科技前沿 · 重磅突发', font=get_font(22, bold=True), fill='#FF6700')
draw.text((WIDTH - 280, 72), '小红书独家速览 >>', font=get_font(22, bold=True), fill='#64748B')
draw.line([60, 130, WIDTH - 60, 130], fill='#1E293B', width=2)

# 2. 核心大字报冲击区 (吸睛)
draw.rounded_rectangle([60, 165, 340, 220], radius=12, fill='#FF6700')
draw.text((80, 178), '【全球开源榜首换人】', font=get_font(22, bold=True), fill='#FFFFFF')

draw.text((60, 245), '46 分登顶！', font=get_font(86, bold=True), fill='#FF6700')
draw.text((60, 350), '最强开源大模型', font=get_font(74, bold=True), fill='#FFFFFF')
draw.text((60, 440), '突然是小米做的？！', font=get_font(74, bold=True), fill='#38BDF8')

# 3. 核心概括导览大卡片
card_top = 550
card_bot = 1240
draw.rounded_rectangle([60, card_top, WIDTH - 60, card_bot], radius=24, fill='#131A26', outline='#222E42', width=2)

draw.text((100, card_top + 38), '【一图读懂】小米 MiMo-V2.6 核心全貌', font=get_font(28, bold=True), fill='#F8FAFC')
draw.line([100, card_top + 85, WIDTH - 100, card_top + 85], fill='#1E293B', width=2)

summary_items = [
    {
        'tag': '登顶榜首',
        'tag_color': '#FF6700',
        'title': '46 分拿下全球开源第一',
        'desc': '权威榜单反超 GLM 与 Kimi；万亿参数 MoE 架构，文本/音频/视频全模态吃透。'
    },
    {
        'tag': '透明厨房',
        'tag_color': '#38BDF8',
        'title': '训练全程直播，打破行业黑箱',
        'desc': '不到 6 天训完，总成本约 350 万美元，75 万条轨迹全公开，底气直接拉满。'
    },
    {
        'tag': '打工福利',
        'tag_color': '#22C55E',
        'title': '电脑直接离线跑，长录音秒出纪要',
        'desc': '推桌面客户端（敏感资料不泄密），100 万字长文速读，开会音频秒整待办。'
    },
    {
        'tag': '理性客观',
        'tag_color': '#F43F5E',
        'title': '离闭源旗舰（53分）尚有差距',
        'desc': '不盲目神化！但开源狂飙让普通人也能用上更便宜、甚至免费的顶尖 AI。'
    }
]

y = card_top + 105
for item in summary_items:
    draw.rounded_rectangle([100, y + 4, 100 + 130, y + 38], radius=8, fill=item['tag_color'])
    draw.text((115, y + 9), item['tag'], font=get_font(18, bold=True), fill='#FFFFFF')
    draw.text((250, y + 4), item['title'], font=get_font(25, bold=True), fill='#FFFFFF')
    draw.text((100, y + 48), item['desc'], font=get_font(20), fill='#94A3B8')
    y += 115

# 4. 底部行动呼吁条
draw.rounded_rectangle([60, 1265, WIDTH - 60, 1355], radius=16, fill='#1A2332', outline='#2D3B4E', width=1)
draw.text((100, 1285), '>> 完整深度实测评测与上手方法，正文见 >>', font=get_font(24, bold=True), fill='#38BDF8')
draw.text((WIDTH - 220, 1285), '建议点赞收藏', font=get_font(22, bold=True), fill='#FF6700')

cover_path = 'outputs/images/mimo_xhs_super_cover.jpg'
img.save(cover_path, quality=94)
print('Regenerated clean super cover image successfully!')
