# -*- coding: utf-8 -*-
"""重新生成《三大旗舰横评》橄榄手记排版稿。

上次推送的 HTML 因事后多轮破坏性精简导致 section 标签严重失衡(104 开 / 47 闭),
lxml 解析时未闭合的 section 层层嵌套、闭合标签全部堆到文末,公众号渲染格式全乱。
本次从 Markdown 源头重新生成:结构 100% 配平、生成即精简,不再事后裁剪。
"""
import base64
import re
from pathlib import Path

from lxml import html as lxml_html

OUT = 'outputs/2026-09-15-三大旗舰横评_排版_橄榄手记(olive-journal).html'
COVER = 'outputs/runs/2026-09-15-hengping/image-01-2bd52ba40132.jpg'

L = '<span leaf="">{}</span>'
S = '<strong style="color:#23251d;"><span leaf="">{}</span></strong>'
U = '<span style="border-bottom:2px solid #ed7b2f;"><span leaf="">{}</span></span>'


def inline(s):
    """__词__ -> 橙下划线;**词** -> 加粗;其余 -> span leaf。"""
    out = []
    for tok in re.split(r'(\*\*.+?\*\*|__.+?__)', s):
        if not tok:
            continue
        if tok.startswith('**'):
            out.append(S.format(tok[2:-2]))
        elif tok.startswith('__'):
            out.append(U.format(tok[2:-2]))
        else:
            out.append(L.format(tok))
    return ''.join(out)


def para(s):
    return f'<p style="margin:24px 0 0;font-size:14px;line-height:1.9;">{inline(s)}</p>'


def sec_title(num, title):
    return ('<section style="margin-top:24px;display:flex;align-items:baseline;gap:10px;">'
            f'<span style="font-size:22px;font-weight:800;color:#23251d;"><span leaf="">{num}</span></span>'
            f'<span style="font-size:17px;font-weight:800;color:#23251d;"><span leaf="">{title}</span></span>'
            '<span style="flex:1;height:1px;background:#bfc1b7;display:inline-block;font-size:0;"></span>'
            '</section>')


def table(headers, rows, widths):
    cells_h = ''.join(
        f'<span style="width:{w};padding:8px 6px;font-size:12px;font-weight:800;"><span leaf="">{t}</span></span>'
        for w, t in zip(widths, headers))
    hdr = f'<section style="display:flex;background:#eeefe9;">{cells_h}</section>'
    body = ''
    for row in rows:
        cells = ''.join(
            f'<span style="width:{w};padding:8px 6px;font-size:13px;"><span leaf="">{t}</span></span>'
            for w, t in zip(widths, row))
        body += f'<section style="display:flex;">{cells}</section>'
    return ('<section style="margin-top:24px;">'
            '<section style="border-radius:6px;border:1px solid #bfc1b7;background:#fdfdf8;">'
            f'{hdr}{body}</section></section>')


def ulist(items, ordered=False):
    tag = 'ol' if ordered else 'ul'
    ts = 'list-style-type:decimal;' if ordered else 'list-style-type:disc;'
    lis = ''.join(f'<li style="margin-bottom:6px;font-size:15px;{ts}">{inline(i)}</li>' for i in items)
    return f'<{tag} style="margin:16px 0 0;padding-left:22px;">{lis}</{tag}>'


def point(strong_part, rest):
    return ('<section style="margin-top:16px;background:#eeefe9;border-radius:6px;'
            'padding:12px 14px;border:1px solid #bfc1b7;">'
            f'<p style="font-size:14px;color:#4d4f46;margin:0;"><span leaf="">{rest}</span>'
            f'{S.format(strong_part)}</p></section>')


# ---------- 组装 ----------
cover_b64 = base64.b64encode(Path(COVER).read_bytes()).decode()
img = ('<p style="margin:6px 10px 24px;text-align:center;">'
       f'<img src="data:image/jpeg;base64,{cover_b64}" '
       'style="max-width:100%;height:auto;display:block;margin:0 auto;"></p>')

hero = ('<section style="background:#fdfdf8;border:1px solid #bfc1b7;border-radius:6px;overflow:hidden;">'
        '<section style="padding:24px 22px 20px;">'
        '<p style="margin:0 0 14px;font-size:10px;font-weight:700;letter-spacing:3px;color:#65675e;">'
        '<span leaf="">模型横评 · 2026.09.15</span></p>'
        '<p style="font-size:24px;font-weight:800;color:#23251d;margin:0 0 10px;line-height:1.25;">'
        '<span leaf="">三大旗舰横评</span><span style="color:#4d4f46;"><span leaf=""> · </span></span>'
        '<span style="border-bottom:3px solid #e5e7e0;"><span leaf="">到底该选谁</span></span></p>'
        '<p style="font-size:13px;color:#65675e;margin:0;line-height:1.7;">'
        '<span leaf="">DeepSeek V4.1 Flash · GPT-5.6 Sol · Claude Opus 5，跑分、价格、场景一次讲清</span></p>'
        '</section>'
        '<section style="background:#1e1f23;padding:11px 20px;">'
        '<p style="font-size:12px;color:rgba(255,255,255,0.92);margin:0;font-weight:600;">'
        '<span leaf="">Agent 编程分数只差 0.2，价格却差 20 多倍</span></p>'
        '</section></section>')

body = [
    para('最近一个月，我又在给手头的 AI 工具__换后台模型__了。原因特别朴素：模型名单越来越长，V4、V4.1、5.6、Opus 5，名字一个比一个唬人，__账单也一张比一张难看__。我就想搞明白一件事：这几位「旗舰」，到底哪个值得我把活交给它？'),
    para('不查不知道。2026 年 6 月到 9 月，DeepSeek、OpenAI、Anthropic 三家先后把旗舰更新了一遍。更魔幻的是：在某些核心跑分上，三家的分数__只差 0.2 分__，价格却__差了 20 多倍__。'),
    para('今天就把这三款摆在一起，跑分、价格、场景__一次讲清楚__。'),

    sec_title('01', '先认识一下三位选手'),
    table(
        ['项目', 'DeepSeek V4.1 Flash', 'GPT-5.6 Sol', 'Claude Opus 5'],
        [
            ['发布时间', '9 月 10 日', '6 月 26 日预览，7 月 9 日公开', '7 月 24 日'],
            ['定位', '新架构里最小的模型，取代自家 V4 Pro', 'GPT-5.6 系列最高档', 'Anthropic 主力旗舰'],
            ['上下文', '100 万 token', '105 万 token', '100 万 token'],
            ['开放程度', 'MIT 开源，权重可下载', '闭源', '闭源'],
        ],
        ['20%', '26%', '27%', '27%'],
    ),
    para('三家的__叙事完全不一样__：'),
    ('<p style="margin:16px 0 0;font-size:13px;line-height:1.8;">'
     + S.format('DeepSeek 讲「普惠」：')
     + inline('发完 V4.1 Flash，直接宣布 V4 Pro 从 9 月 14 日中午起全部路由到新模型，理由是「各项指标已全面超越」。__自己把自家旗舰干下线__，行业里头一回。') + '</p>'),
    ('<p style="margin:16px 0 0;font-size:13px;line-height:1.8;">'
     + S.format('OpenAI 讲「最强」：')
     + inline('GPT-5.6 Sol 被称为「迄今最强的模型」，还第一次用 Sol / Terra / Luna 能力层级命名，暗示后面还有更猛的。') + '</p>'),
    ('<p style="margin:16px 0 0;font-size:13px;line-height:1.8;">'
     + S.format('Anthropic 讲「务实」：')
     + inline('官方文档直接说，大多数任务从 Opus 5 起步，实在不行再升级到更贵的 Fable 5.1。') + '</p>'),

    sec_title('02', '跑分：低难度打平，高难度拉开'),
    para('跑分数据来自 DeepSeek 9 月技术报告——这是唯一一份在__同一测试框架__下同时跑了三款模型的数据。OpenAI 和 Anthropic 官方只给「比次优模型高多少」这类相对表述，没法直接拼表。'),
    ('<p style="margin:24px 0 0;font-size:14px;line-height:1.9;">' + S.format('先说打平的部分：') + '</p>'),
    ulist([
        'DeepSWE v1.1（软件工程）：74.2 / 73.0 / 74.0，**差 0.2 分**',
        'Terminal-Bench 2.1（命令行 Agent）：90.6 / 88.8 / 89.1，全部 90 分上下',
        'Automation-Bench（自动化工作流）：54.8 / 45.8 / 50.3，DeepSeek 反而最高',
    ]),
    point('三家的差距在误差范围内。', '也就是说，日常写代码、改 bug、跑命令这类活，'),
    ('<p style="margin:24px 0 0;font-size:14px;line-height:1.9;">' + S.format('再说拉开差距的部分：') + '</p>'),
    ulist([
        'Terminal-Bench 4.0（更难的终端任务）：Claude Opus 5 得 51.8，GPT-5.6 Sol 39.9，DeepSeek 31.2，**Opus 5 领先 20 分**',
        'HLE（人类最难考试）：56.3 / 44.5 / 36.8，Opus 5 领先近 20 分',
        'NL2Repo-Bench（从一句话需求到完整仓库）：75.3 / 56.8 / 65.4，Opus 5 领先 10 分',
        '视觉类（BabyVision、Chartography）：Opus 5 也都领先 5 分左右',
    ]),
    ('<section style="margin-top:16px;background:#eeefe9;border-radius:6px;'
     'padding:12px 14px;border:1px solid #bfc1b7;">'
     '<p style="font-size:14px;color:#4d4f46;margin:0;">'
     + inline('规律很清晰：')
     + S.format('难度越高，闭源旗舰的优势越大')
     + inline('；中等难度的活，已经打平了。') + '</p></section>'),
    ('<section style="margin-top:16px;background:#eeefe9;border-left:3px solid #1e1f23;padding:12px 14px;">'
     '<p style="margin:0;font-size:14px;line-height:1.9;">'
     + inline('一个提醒：以上是 DeepSeek 单方测试，它既是选手也是裁判，分数仅供参考。但「__中等难度打平__」这个结论，和三家的官方口径对得上。')
     + '</p></section>'),

    sec_title('03', '价格：同样的分数，账单差一个数量级'),
    para('这是__最离谱的部分__。按各家官方定价页 2026 年 9 月中旬的数据：'),
    table(
        ['每百万 token', 'DeepSeek V4.1 Flash', 'GPT-5.6 Sol', 'Claude Opus 5'],
        [
            ['输入（未命中缓存）', '高峰 2 元 / 闲时 1 元', '4 美元', '5 美元'],
            ['输入（缓存命中）', '高峰 0.04 元 / 闲时 0.02 元', '0.4 美元', '0.5 美元'],
            ['输出', '高峰 8 元 / 闲时 4 元', '20 美元', '25 美元'],
        ],
        ['20%', '26%', '27%', '27%'],
    ),
    para('按 1 美元约 7.1 元算，DeepSeek 高峰期的输出价只有 Claude Opus 5 的 **4.5%**，闲时只有 **2.3%**。Agent 任务里占比最高的__缓存命中输入__，更是差出约 **90 倍**。'),
    para('翻译成人话：同样让 AI 修一个 bug——'),
    ('<section style="margin-top:16px;display:flex;align-items:stretch;gap:8px;">'
     '<section style="flex:1;text-align:center;padding:10px 8px;background:#1e1f23;border-radius:6px;">'
     '<p style="font-size:13px;font-weight:800;color:#fff;margin:0 0 3px;"><span leaf="">用 Claude Opus 5：</span></p>'
     '<p style="font-size:10px;color:rgba(255,255,255,0.75);margin:0;"><span leaf="">分数最高，钱包出血</span></p>'
     '</section>'
     '<section style="display:flex;align-items:center;color:#bfc1b7;font-size:14px;"><span leaf="">vs</span></section>'
     '<section style="flex:1;text-align:center;padding:10px 8px;background:#fdfdf8;border:1px solid #bfc1b7;border-radius:6px;">'
     '<p style="font-size:13px;font-weight:800;color:#23251d;margin:0 0 3px;"><span leaf="">用 DeepSeek V4.1 Flash：</span></p>'
     '<p style="font-size:10px;color:#65675e;margin:0;">' + inline('分数打平，__花 1/20 的钱__') + '</p>'
     '</section></section>'),
    ('<p style="margin:24px 0 0;font-size:14px;line-height:1.9;">' + S.format('另外两个注意点：') + '</p>'),
    ulist([
        'GPT-5.6 Sol 现在的 4 / 20 美元是**促销价**，官方说至少持续到 11 月 21 日，原价是 5 / 30 美元',
        'DeepSeek 的「闲时」指周一到周五 9:00–12:00、14:00–18:00 之外的全部时间，**含周末**。夜里的批量任务，__直接半价__',
    ]),

    sec_title('04', '普通人到底怎么选'),
    para('开发者看跑分，__普通人看场景__。我的结论：'),
    table(
        ['你的场景', '选谁', '理由'],
        [
            ['日常问答、写文章、翻译', 'DeepSeek V4.1 Flash', '差距感知不到，价格最便宜'],
            ['写代码、改 bug、跑脚本', 'DeepSeek V4.1 Flash', '中等难度打平，价格 1/20'],
            ['复杂项目、从零搭仓库', 'Claude Opus 5', '高难任务领先 10–20 分，多花的钱买到成功率'],
            ['图片理解、图表分析', 'Claude Opus 5', '视觉 Agent 领先 5 分左右'],
            ['追求速度、子智能体编排', 'GPT-5.6 Sol', '独有 750 token/秒加速层和 ultra 模式'],
            ['想私有部署、怕数据外流', 'DeepSeek V4.1 Flash', '唯一 MIT 开源'],
        ],
        ['30%', '28%', '42%'],
    ),
    para('还有一个进阶玩法：**混合路由**。用 DeepSeek 承接大部分常规请求，遇到失败重试或高难任务再升级到 Opus 5，整体成本通常能压到纯闭源方案的__两到三成__。对个人用户来说，就是__「便宜的干活，贵的兜底」__。'),
    ('<p style="margin:24px 0 0;font-size:14px;line-height:1.9;">' + S.format('最后三个提醒：') + '</p>'),
    ulist([
        '三家定价和路由规则变得很快，下单前看一眼官方定价页',
        '跑分都是厂商或利益相关方测的，别当绝对真理，拿自己的真实任务试',
        'GPT-5.6 Sol 的促销价 11 月 21 日到期，__要薅趁早__',
    ], ordered=True),
    ('<section style="margin-top:24px;background:#1e1f23;border-radius:6px;padding:16px 18px;">'
     '<p style="margin:0 0 8px;font-size:11px;letter-spacing:2px;color:rgba(255,255,255,0.55);font-weight:700;">'
     '<span leaf="">SUMMARY · 结案摘要</span></p>'
     '<p style="margin:0;font-size:15px;line-height:1.9;color:rgba(255,255,255,0.92);">'
     '<span leaf="">中等难度的活，三家已经打平，DeepSeek 用约 4% 的价格就能干；高难任务多花的钱，买的是真实的成功率。</span></p>'
     '<p style="margin:8px 0 0;font-size:13px;line-height:1.8;color:rgba(255,255,255,0.76);">'
     '<span leaf="">按场景选型：常规任务 DeepSeek，高难与视觉 Opus 5，求速度选 Sol。下单前看一眼官方定价页。</span></p>'
     '</section>'),
    ('<section style="margin-top:24px;background:#eeefe9;border:1px solid #bfc1b7;border-radius:6px;padding:12px 16px;">'
     '<p style="margin:0;font-size:13px;color:#23251d;font-weight:700;">'
     '<span leaf="">数据来源：DeepSeek 技术报告（2026 年 9 月）、OpenAI 开发者文档、Anthropic 模型文档，采集时间 2026 年 9 月 10–11 日。</span></p>'
     '</section>'),
    para('我是 小崽子AI，热衷于分享 AI 工具实测与干货。'),
    ('<section style="margin-top:24px;background:#fdfdf8;border:1px solid #bfc1b7;border-radius:6px;padding:18px 16px;text-align:center;">'
     '<p style="font-size:13px;font-weight:700;color:#23251d;line-height:1.7;margin:0;">'
     '<span leaf="">如果你觉得今天这篇有收获，欢迎点赞、在看、转发三连，我们下篇见</span></p>'
     '<p style="line-height:1.6;font-size:10px;color:#9ea096;letter-spacing:2px;margin:8px 0 0;">'
     '<span leaf="">THANKS FOR READING</span></p>'
     '</section>'),
]

html = ('<section data-copy-root="" style="max-width:677px;margin:0 auto;padding:8px;background:#fdfdf8;color:#4d4f46;">'
        + img + hero + ''.join(body) + '</section>')

# ---------- 结构验证:栈配对 ----------
opens = len(re.findall(r'<section(?:\s|>)', html))
closes = len(re.findall(r'</section>', html))
assert opens == closes, f'section 数量不等: {opens} 开 vs {closes} 闭'
depth = 0
for tok in re.findall(r'</?section(?:\s[^>]*)?>', html):
    if tok.startswith('</'):
        depth -= 1
        assert depth >= 0, '闭合多于开标签'
    else:
        depth += 1
assert depth == 0, '栈未归零'
print(f'[结构] section {opens} 开 / {closes} 闭, 栈配对 100% 配平')

# ---------- 长度模拟:lxml 解析后替换 base64 为 URL ----------
doc = lxml_html.fromstring(html)
root = doc.xpath('//*[@data-copy-root]')[0]
for i, im in enumerate(root.xpath('.//img')):
    im.set('src', 'https://mmbiz.qpic.cn/mmbiz_jpg/example' + str(i))
content = lxml_html.tostring(root, encoding='unicode', method='html')
pure = re.sub(r'data:image/[^"]+"', 'X', html)
print(f'[长度] 纯结构 {len(pure)} 字符, lxml content {len(content)} 字符 (限 20000)')

Path(OUT).write_text(html, encoding='utf-8')
print(f'[写入] {OUT}')
