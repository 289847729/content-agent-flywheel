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

def draw_header(draw, page_num, total_pages=5, tag="科技大事件 · 深度拆解"):
    draw.rounded_rectangle([70, 70, 70 + 290, 118], radius=12, fill="#1E293B")
    draw.text((88, 81), tag, font=get_font(22, bold=True), fill="#FF6700")
    
    draw.text((WIDTH - 150, 81), f"0{page_num} / 0{total_pages}", font=get_font(24, num=True), fill="#64748B")
    draw.line([70, 142, WIDTH - 70, 142], fill="#1E293B", width=2)

def draw_footer(draw):
    draw.line([70, HEIGHT - 120, WIDTH - 70, HEIGHT - 120], fill="#1E293B", width=2)
    draw.text((70, HEIGHT - 88), "小红书精选 · AI生产力工具指南", font=get_font(22), fill="#64748B")
    draw.text((WIDTH - 250, HEIGHT - 88), "关注获取更多干货 >>", font=get_font(22, bold=True), fill="#FF6700")

def create_card_1_cover():
    img = Image.new("RGB", (WIDTH, HEIGHT), color="#0A0E17")
    draw = ImageDraw.Draw(img)
    draw_header(draw, 1, 5, tag="全球开源大模型榜首")
    
    # 突发重磅登顶标签
    draw.rounded_rectangle([70, 210, 310, 268], radius=14, fill="#FF6700")
    draw.text((88, 224), "【突发重磅登顶】", font=get_font(24, bold=True), fill="#FFFFFF")
    
    # 超大核心标题
    draw.text((70, 305), "46 分！", font=get_font(90, bold=True), fill="#FF6700")
    draw.text((70, 415), "全球开源第一", font=get_font(78, bold=True), fill="#FFFFFF")
    draw.text((70, 510), "居然是小米？！", font=get_font(78, bold=True), fill="#38BDF8")
    
    # 核心要点卡片
    card_top = 635
    card_bot = 1135
    draw.rounded_rectangle([70, card_top, WIDTH - 70, card_bot], radius=24, fill="#121826", outline="#1F2A3D", width=2)
    
    draw.text((110, card_top + 45), "颠覆行业认知：小米 MiMo-V2.6-Pro 正式发布", font=get_font(30, bold=True), fill="#F8FAFC")
    
    points = [
        ("TOP 1", "登顶全球开源榜首", "在权威智能指数榜以 46 分超越 GLM-5 与 Kimi 拿下第一"),
        ("FULL", "万亿参数全模态", "1.02 万亿总参数 MoE 架构，看图、听音、处理视频全能"),
        ("OPEN", "破天荒直播训练", "耗时不到 6 天，350 万美元成本 + 75 万条轨迹全透明公开"),
        ("EASY", "普通人装上就能用", "提供 MiMo Desktop 电脑客户端，离线本地跑，不泄密"),
    ]
    
    y = card_top + 115
    for badge, title, desc in points:
        # Badge 标牌
        draw.rounded_rectangle([110, y + 2, 110 + 66, y + 32], radius=6, fill="#FF6700" if badge == "TOP 1" else "#1E293B")
        draw.text((118, y + 5), badge, font=get_font(16, bold=True, num=True), fill="#FFFFFF")
        draw.text((190, y), title, font=get_font(26, bold=True), fill="#FFFFFF")
        draw.text((110, y + 40), desc, font=get_font(22), fill="#94A3B8")
        y += 92
        
    # 底部引导条
    draw.rounded_rectangle([70, 1170, WIDTH - 70, 1265], radius=18, fill="#162032")
    draw.text((110, 1188), ">> 不吹不黑：到底有多强？普通人怎么上手？", font=get_font(26, bold=True), fill="#38BDF8")
    draw.text((110, 1226), "左滑查看完整实操与客观对比 >>", font=get_font(20), fill="#94A3B8")
    
    draw_footer(draw)
    return img

def create_card_2_rank():
    img = Image.new("RGB", (WIDTH, HEIGHT), color="#0A0E17")
    draw = ImageDraw.Draw(img)
    draw_header(draw, 2, 5, tag="权威榜单 · 实测排位")
    
    draw.text((70, 180), "开源榜首突换人！", font=get_font(48, bold=True), fill="#FFFFFF")
    draw.text((70, 245), "Artificial Analysis 全球大模型综合智能指数最新实测", font=get_font(24), fill="#94A3B8")
    
    ranks = [
        {"badge": "NO.1", "name": "小米 MiMo-V2.6-Pro", "score": "46 分", "tag": "全球开源第一", "color": "#FF6700", "bar": 580, "is_mi": True},
        {"badge": "NO.2", "name": "GLM-5.3", "score": "45 分", "tag": "开源", "color": "#3B82F6", "bar": 555, "is_mi": False},
        {"badge": "NO.3", "name": "Kimi K3", "score": "44 分", "tag": "开源", "color": "#3B82F6", "bar": 530, "is_mi": False},
        {"badge": "前代", "name": "小米上一代 V2.5-Pro", "score": "26 分", "tag": "暴涨 +20分！", "color": "#64748B", "bar": 320, "is_mi": False},
    ]
    
    y = 310
    for r in ranks:
        bg = "#1A2436" if r["is_mi"] else "#121826"
        border = "#FF6700" if r["is_mi"] else "#1E2D42"
        draw.rounded_rectangle([70, y, WIDTH - 70, y + 125], radius=18, fill=bg, outline=border, width=2 if r["is_mi"] else 1)
        
        draw.rounded_rectangle([95, y + 25, 95 + 60, y + 55], radius=6, fill=r["color"])
        draw.text((104, y + 28), r["badge"], font=get_font(18, bold=True, num=True), fill="#FFFFFF")
        
        draw.text((170, y + 25), r["name"], font=get_font(28, bold=True), fill="#FFFFFF")
        draw.text((WIDTH - 195, y + 22), r["score"], font=get_font(32, bold=True, num=True), fill=r["color"])
        
        draw.rounded_rectangle([95, y + 75, 95 + 640, y + 87], radius=6, fill="#0F172A")
        draw.rounded_rectangle([95, y + 75, 95 + r["bar"], y + 87], radius=6, fill=r["color"])
        
        draw.text((755, y + 70), r["tag"], font=get_font(18, bold=True), fill=r["color"])
        y += 145

    # 闭源对比参考框
    draw.rounded_rectangle([70, 910, WIDTH - 70, 1140], radius=20, fill="#141B26", outline="#25354E", width=1)
    draw.text((105, 940), "[客观对比] 离闭源顶尖还差多少？", font=get_font(26, bold=True), fill="#38BDF8")
    draw.text((105, 985), "- GPT-6 Astra（闭源旗舰）：53 分", font=get_font(22), fill="#CBD5E1")
    draw.text((105, 1025), "- Claude Fable 5.1（闭源旗舰）：53 分", font=get_font(22), fill="#CBD5E1")
    draw.text((105, 1065), "- 差距仍有 7 分！但开源一年狂飙 20 分，追赶势头极其凶猛。", font=get_font(22, bold=True), fill="#FB923C")

    draw.text((70, 1180), "结论：不是全球最强AI，但确实是【全球最强开源大模型】", font=get_font(24, bold=True), fill="#FFFFFF")
    draw_footer(draw)
    return img

def create_card_3_training():
    img = Image.new("RGB", (WIDTH, HEIGHT), color="#0A0E17")
    draw = ImageDraw.Draw(img)
    draw_header(draw, 3, 5, tag="行业震撼 · 透明直播")
    
    draw.text((70, 180), "彻底掀开底裤！", font=get_font(48, bold=True), fill="#FFFFFF")
    draw.text((70, 245), "大模型行业破天荒将训练全流程直播公开", font=get_font(24), fill="#94A3B8")
    
    cards = [
        {"num": "350 万$", "label": "总训练成本全部公开", "sub": "单任务微调不到 1 元人民币", "color": "#FF6700"},
        {"num": "< 6 天", "label": "从开训到登顶发布", "sub": "30 个 RL step 全程公开跑", "color": "#38BDF8"},
        {"num": "75 万条", "label": "完整训练轨迹全开源", "sub": "小团队无需摸黑试错复现", "color": "#4ADE80"},
        {"num": "1.02 万亿", "label": "MoE 架构总参数", "sub": "激活仅 420 亿，跑起来极省", "color": "#F43F5E"},
    ]
    
    coords = [
        (70, 310, 520, 520),
        (560, 310, 1010, 520),
        (70, 550, 520, 760),
        (560, 550, 1010, 760),
    ]
    
    for (x1, y1, x2, y2), c in zip(coords, cards):
        draw.rounded_rectangle([x1, y1, x2, y2], radius=20, fill="#121826", outline="#1F2A3D", width=1)
        draw.text((x1 + 30, y1 + 30), c["num"], font=get_font(42, bold=True, num=True), fill=c["color"])
        draw.text((x1 + 30, y1 + 100), c["label"], font=get_font(22, bold=True), fill="#FFFFFF")
        draw.text((x1 + 30, y1 + 140), c["sub"], font=get_font(18), fill="#94A3B8")

    draw.rounded_rectangle([70, 810, WIDTH - 70, 1160], radius=20, fill="#151D2C", outline="#25354E", width=2)
    draw.text((105, 845), "【为什么说这事比“拿第一”更重要？】", font=get_font(26, bold=True), fill="#FF6700")
    
    points = [
        "1. 过去大模型开源只放成品权重，过程全在黑箱里。",
        "2. 小米把做菜的食材单、火候时长、成本价格全贴墙上。",
        "3. 开源社区与研究团队可以直接照着菜谱练，试错成本大降！",
        "4. 这展现的不是简单炫技，而是对技术与生态真正的底气。"
    ]
    y = 905
    for p in points:
        draw.text((105, y), p, font=get_font(21), fill="#E2E8F0")
        y += 55

    draw_footer(draw)
    return img

def create_card_4_usage():
    img = Image.new("RGB", (WIDTH, HEIGHT), color="#0A0E17")
    draw = ImageDraw.Draw(img)
    draw_header(draw, 4, 5, tag="普通人上手 · 效率翻倍")
    
    draw.text((70, 180), "普通人现在能拿来干嘛？", font=get_font(48, bold=True), fill="#FFFFFF")
    draw.text((70, 245), "打工人立刻能抄作业的 4 大真实场景", font=get_font(24), fill="#94A3B8")
    
    items = [
        {
            "tag": "安全离线",
            "title": "电脑直接跑：MiMo Desktop",
            "desc": "下载桌面客户端直接运行，无需复杂部署。公司保密文档、私密资料本地离线跑，不用传网页，告别泄露风险！"
        },
        {
            "tag": "长文神器",
            "title": "100 万 Token 超大上下文",
            "desc": "整本书、几百页财报与合同直接扔进去。支持超长文本秒级问答与关键提炼，打工人读研报神器。"
        },
        {
            "tag": "全模态通吃",
            "title": "全模态支持：开会秒出纪要",
            "desc": "不只打字！支持听音频、看图片视频。两小时会议录音直接丢进去，自动分角色整理纪要与待办任务。"
        },
        {
            "tag": "极速响应",
            "title": "UltraSpeed 20 倍速生成",
            "desc": "告别逐字慢吞吞吐字，极速出内容与代码。还有低硬件门槛的 Flash 版本，普通笔记本也能轻松带得动。"
        }
    ]
    
    y = 310
    for it in items:
        draw.rounded_rectangle([70, y, WIDTH - 70, y + 180], radius=20, fill="#121826", outline="#1F2A3D", width=1)
        
        draw.rounded_rectangle([105, y + 26, 105 + 130, y + 60], radius=8, fill="#FF6700" if it["tag"] == "安全离线" else "#1E293B")
        draw.text((118, y + 32), it["tag"], font=get_font(18, bold=True), fill="#FFFFFF")
        
        draw.text((255, y + 28), it["title"], font=get_font(26, bold=True), fill="#FFFFFF")
        draw.text((105, y + 80), it["desc"], font=get_font(20), fill="#94A3B8")
        y += 205
        
    draw_footer(draw)
    return img

def create_card_5_summary():
    img = Image.new("RGB", (WIDTH, HEIGHT), color="#0A0E17")
    draw = ImageDraw.Draw(img)
    draw_header(draw, 5, 5, tag="总结 · 终极观点")
    
    draw.text((70, 180), "神仙打架，普通人赢麻！", font=get_font(48, bold=True), fill="#FFFFFF")
    draw.text((70, 245), "大模型变局背后，真正的大趋势是什么？", font=get_font(24), fill="#94A3B8")
    
    draw.rounded_rectangle([70, 310, WIDTH - 70, 710], radius=22, fill="#121826", outline="#FF6700", width=2)
    draw.text((105, 345), ">> 三句大实话：", font=get_font(28, bold=True), fill="#FF6700")
    
    points = [
        ("1. 不要无脑吹“超越一切”", "离顶级闭源旗舰（GPT-6）仍有 7 分差距，专业极难任务闭源依然最稳。"),
        ("2. 开源追分速度超乎想象", "一年狂飙 20 分，说明开源正在用极低成本复现顶级推理与全模态。"),
        ("3. 最终收益的一定是普通人", "闭源越强，开源越追。桌面免费、离线跑、API降价，AI 正在从奢侈品变成自来水。")
    ]
    
    y = 410
    for title, desc in points:
        draw.text((105, y), title, font=get_font(24, bold=True), fill="#FFFFFF")
        draw.text((105, y + 36), desc, font=get_font(20), fill="#94A3B8")
        y += 90

    # 互动卡片
    draw.rounded_rectangle([70, 740, WIDTH - 70, 1080], radius=22, fill="#162032", outline="#25354E", width=1)
    draw.text((105, 780), ">> 评论区互动时间", font=get_font(26, bold=True), fill="#38BDF8")
    draw.text((105, 835), "你平时工作中最常用哪款 AI 工具？", font=get_font(22, bold=True), fill="#FFFFFF")
    draw.text((105, 875), "• DeepSeek / ChatGPT / Claude / Kimi / 豆包？", font=get_font(20), fill="#94A3B8")
    draw.text((105, 930), "觉得小米这波开源登顶，能让你换掉手头的工具吗？", font=get_font(22, bold=True), fill="#FFFFFF")
    draw.text((105, 970), "面对闭源商业版与开源自建，你更倾向哪种？", font=get_font(21, bold=True), fill="#FB923C")

    draw.text((70, 1140), "★ 欢迎在评论区分享你的实测体验！", font=get_font(26, bold=True), fill="#F43F5E")
    draw_footer(draw)
    return img

def main():
    os.makedirs("outputs/images", exist_ok=True)
    cards = [
        ("outputs/images/mimo_xhs_1_cover.jpg", create_card_1_cover()),
        ("outputs/images/mimo_xhs_2_rank.jpg", create_card_2_rank()),
        ("outputs/images/mimo_xhs_3_training.jpg", create_card_3_training()),
        ("outputs/images/mimo_xhs_4_usage.jpg", create_card_4_usage()),
        ("outputs/images/mimo_xhs_5_summary.jpg", create_card_5_summary()),
    ]
    
    for path, img in cards:
        img.save(path, quality=92)
        print(f"Generated {path}, size={os.path.getsize(path)} bytes")

if __name__ == "__main__":
    main()
