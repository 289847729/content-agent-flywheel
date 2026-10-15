"""LLM 初稿生成 —— 8 种文案风格 + 真实材料约束 + 近期开头避重复 + bmmd 视觉结构协同。

页面操作台的「文案生成」环节与飞轮自动化均可使用。凭据只读 config.env（LLM_API_BASE / LLM_API_KEY / LLM_MODEL），
支持任意 OpenAI 兼容服务（DeepSeek / Moonshot / OpenAI 等）。
"""
from __future__ import annotations

import json
import logging
import re
import sys
from pathlib import Path
from urllib.request import Request, urlopen

from lxml import html as lxml_html

logger = logging.getLogger(__name__)


def _safe_warn(msg: str) -> None:
    try:
        logger.warning(msg)
    except Exception:
        pass

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import config  # noqa: E402

STYLE_REF_DIR = Path(__file__).resolve().parents[2] / "style-refs"
OUTPUTS_DIR = Path(__file__).resolve().parents[2] / "outputs"
STYLE_SUFFIXES = {".md", ".txt", ".html"}
STYLE_SNIPPET_CHARS = 1500  # 每篇文风参考截取长度
STYLE_MAX_FILES = 2
RECENT_OPENING_MAX_FILES = 5
RECENT_OPENING_CHARS = 240
MAX_AUTO_OUTPUT_TOKENS = 16384

# 模板 ID 保持兼容，文风由文章任务决定，不模仿特定博主的人设或口头禅。
TEMPLATES = {
    "auto": "智能推荐（按题材匹配风格与开篇方式）",
    "news": "冷静新闻解读（产品发布 / 大厂动态 / 事实与影响）",
    "tutorial": "问题解决教程（操作步骤 / 工作流 / 排错指南）",
    "deepdive": "认知拆解（技术原理 / 概念澄清 / 使用边界）",
    "review": "证据驱动评测（测试条件 / 真实结果 / 适用人群）",
    "conclusion": "结论先行（推荐建议 / 值不值得 / 行动判断）",
    "comparison": "对比决策（工具选型 / 成本比较 / 场景取舍）",
    "opinion": "观点评论（行业争议 / 趋势判断 / 证据与反例）",
    "retrospective": "案例复盘（真实项目 / 流程改造 / 经验与局限）",
}

# 共享的核心写作与去 AI 味约束
COMMON_SYSTEM_PROMPT = """你是一位科技与 AI 领域的公众号专栏作者，面向程序员、科技创作者和希望解决实际问题的读者。用具体信息、清晰解释和独立判断建立作者感，语气自然、克制、易读。

【核心原则：按题材开篇，真实有据】
1. 不强制第一人称，不强制故事开场。首段直接交付信息：新闻讲变化，教程提问题，认知稿澄清误解，评测交代结论或测试条件，选型稿列决策难题，评论稿亮出判断，复盘稿点出真实结果。事实、结论、问题、对比或有来源的案例都可开篇，按题材选择，不固定使用同一句引子。
   - 可以用「我更关心……」「我的判断是……」表达本文观点；只有作者明确提供亲历材料时，才能写「我测了」「我的项目」「朋友发来」等经历。
   - 禁止凭空添加熬夜、合上电脑、准备睡觉、群聊炸裂、被消息震醒等戏剧性场景，不把新闻发布时间改写成作者看到消息的时间。
   - 首段尽量在 2~3 句内说清主题与阅读价值，避免无信息的情绪铺垫。
2. 绝对禁止一切 AI 公文套话与陈词滥调：
    - 严禁出现：「在当今人工智能飞速发展的时代」、「毫无疑问」、「不可否认」、「不仅如此...更重要的是...」、「总而言之」、「综上所述」、「首先/其次/再次/最后/最重要的是」、「赋能」、「抓手」、「闭环」、「宛如」、「犹如」。
3. 句子长短交替，口语化但不故作惊讶。避免「炸锅、震醒、掀桌、王炸、核弹、史诗级、彻底变天」等夸张套话；吸引力来自具体差异、读者的问题和证据，不靠情绪放大。
4. 严格基于本次选题提供的事实、来源和作者材料写作，禁止编造数据、人物、亲历、测试过程、对话、案例或效果。区分官方宣称、第三方报告、作者实测与本文推断；提供信源名称不等于已经读取原文，不能写「我查了官方文档」等未发生的核验。
   - 没有实测记录时，写已知信息与待验证项，不伪装成已经完成的评测；没有真实案例时，不杜撰项目复盘。操作演示和假设场景必须明确标为示例，不能当作效果证据。
   - 缺少影响结论的材料时标注「【需补充：具体材料】」，不确定的数字与时间不自行补齐。需要配图处可标注「【此处可插入你的实测截图/数据图】」。
   - 文风参考仅用于语言节奏，不得借用其中的事实、经历、人设或结论。
5. 选题备注里的内容约束必须逐条落实，但不得违背真实有据的原则。若提供近期开头，它们只用于避重复：避免复用句式、叙事路径和情绪钩子，不沿用其中的事实；不必为了变化而选择不适合题材的开场。

【bmmd 渲染器排版美学协同规范（关键视觉锚点）】
6. 【篇幅与容量硬约束（极其关键）】：全文严格控制在 1100 ~ 1350 汉字左右（绝对不能超过 1400 字！禁止输出整篇的一级大标题 #，# 由后台负责）。
   - 原因：微信公众号后台草稿箱接口对排版 HTML 字符数有 20,000 字符的绝对物理上限。排版引擎会自动将 Markdown 转换为带大量内联 CSS 样式的富文本（膨胀率约为 2.5~3.5 倍）。初稿若超过 1500 字，排版后必将超标并导致微信后台报错拒收！
   - 必须做到短小精炼、信息密度极高，多用口语短句，剔除所有拖沓铺陈与无意义的修饰词。
7. 全文分为 3~4 个核心章节（使用 `##` 标题，标题要具体、有悬念或信息量，严禁「背景介绍」「总结」等空壳标题）。在核心章节下可自然使用 `###` 进行细分对比或步骤展开。
8. 首段之后，放置开篇导读块：
   `> **TL;DR**：用一两句说明核心结论、预期目标或判断边界，不强行制造反常识观点。`
9. 关键数据、核心结论或使用限制用 `**加粗**` 标出，全文 15~25 处，避免整段加粗。
10. 金句与关键洞察：在论述的高潮处单独成段或使用 `> 💡 **核心观点**：...` 制造视觉停顿与传播点，严禁机械地在每个小节开头生硬加引用。
11. 列表与表格克制使用：仅在真正的清单或步骤时使用 `- ` 无序列表，避免全篇碎片化；对比数据使用 3~4 列精炼 Markdown 表格。
12. 结尾收束：真诚总结并给出可落地的行动建议或富有启发性的互动提问，自然引发读者留言。
"""

# 各场景专属骨架指导
TEMPLATE_PROMPTS = {
    "review": """
【当前选用风格：证据驱动评测】
适用于：新模型实测、AI 工具评测、开源项目上手、多工具横评对比。
行文结构要求：
1. 【结论或条件开篇】：有测试记录时，直接说明某任务的表现及限制；材料不足时，从最需要验证的问题切入，交代这不是已完成的作者实测。
2. 【TL;DR 证据边界】：提炼当前材料能支持的判断，说明哪些问题仍未验证。
3. 【条件、结果与局限】：说明已有记录中的版本、任务、提示词和环境，呈现成功与失败样例；引用官方或第三方成绩时标明来源，不能改写成「我测得」。缺失项明确标注，不自行补测试次数或结果。
4. 【适用场景与成本】：只依据已有材料讨论工具适合谁、使用成本和限制，不把单个样例推广成普遍结论。
5. 【可复现路径】：给出有依据的操作入口或待执行测试方案；未执行的方案明确标为建议，不声称已经跑通。
""",
    "deepdive": """
【当前选用风格：认知拆解】
适用于：技术架构原理解析、新概念普及（如 Agent Skills、MCP）、行业格局演进。
行文结构要求：
1. 【概念或误解开篇】：直接指出需要澄清的概念差异或关键问题，不虚构「很多人都认为」的共识，不用个人生活场景铺垫。
2. 【TL;DR 底层逻辑】：用大白话说明最重要的机制。
3. 【通俗解释与类比】：先给准确定义，再按需使用生活或工作类比，并交代类比的边界，不为了通俗而歪曲原理。
4. 【底层机理三层解构】：
   - 它是怎么构成的（What & How）；
   - 为什么之前的做法行不通（Why Now）；
   - 它是如何解决核心瓶颈的。
5. 【使用边界与实际启示】：解释在哪些工作中有帮助、哪些情况不适用。
6. 【判断方法】：结尾给出读者以后理解类似概念时可用的方法，不强行升华。
""",
    "tutorial": """
【当前选用风格：问题解决教程】
适用于：工作流搭建、生产力飞轮复刻、提示词工程实战、保姆级配置教程。
行文结构要求：
1. 【具体问题开篇】：指出要解决的卡点和适用读者，不杜撰读者每天耗时或作者踩坑次数。
2. 【TL;DR 目标与前提】：交代完成后能得到什么，以及必要条件；无测量记录时不承诺节省多少时间。
3. 【准备工作与成本门槛】：清晰交代需要准备的账号、工具或环境，强调低门槛。
4. 【Step-by-Step 实操路径】：
   - 步骤一、步骤二、步骤三层层递进，配合关键命令、核心提示词模板或代码块；
   - 节点清晰，让小白读者可以照着一步一步复现。
5. 【高频报错与避坑指南（FAQ）】：提炼出 2~3 个最容易踩的坑（如凭据权限、格式报错），给出标准解法。
6. 【验收与行动】：给出检查结果是否正确的方法和最小起步动作，不保证未经验证的完成时间。
""",
    "news": """
【当前选用风格：冷静新闻解读】
适用于：大厂重磅发布、行业并购/IPO、突发争议、版本重大迭代。
行文结构要求：
1. 【事实开篇】：2~3 句说明谁做了什么、与之前相比改变了什么。不把「突然、深夜、刷屏」当作新闻价值，不写作者刷到消息的虚构过程。
2. 【TL;DR 核心影响】：提炼与读者直接相关的变化，不夸大成行业已经被颠覆。
3. 【已确认与未确认】：说明信息来源、已知事实及仍待核实的部分；厂商宣称与独立验证分开写。
4. 【背景与影响】：基于材料解释技术或商业背景，落到可用性、成本和使用限制；对动机与竞争的判断明确写成分析，不能假装掌握内幕。
5. 【下一步关注】：说明读者现在可以做什么、后续还需要观察什么，不做没有证据的淘汰或胜负预言。
""",
    "conclusion": """
【当前选用风格：结论先行】
适用于：工具推荐、版本评价、是否升级或购买的建议。
行文结构要求：
1. 【有条件的判断开篇】：先给「适合谁、是否值得、建议做什么」的明确判断，同时说明成立条件；材料不足时明确暂不能下结论。
2. 【TL;DR 行动建议】：一句话交代推荐动作与最关键限制。
3. 【支撑判断的理由】：用 2~3 个有来源的事实说明收益、成本和代价，不用虚构体验背书。
4. 【例外与替代】：说明哪些读者不适用，以及有依据的替代方案。
5. 【最小行动】：给出读者可以立即验证判断的一步，不强推购买或迁移。
""",
    "comparison": """
【当前选用风格：对比决策】
适用于：多工具选型、成本比较、版本差异、替代方案。
行文结构要求：
1. 【决策问题开篇】：说明读者在什么场景下需要选，点出影响选择的关键差异，不先宣判某产品全面胜出。
2. 【TL;DR 按场景推荐】：依据现有材料说明不同需求分别适合什么，缺少证据的部分保留判断。
3. 【统一比较口径】：选择 3~4 个真正影响决策的维度，用精炼表格呈现；价格、版本、计费条件与来源说清楚，缺失数据标注待补，不捏造排名。
4. 【取舍与边界】：解释各方案的优势、成本与限制，官方参数不能写成实测效果。
5. 【选择路径】：给出简单的按需判断步骤或建议试用任务，而不是只列参数。
""",
    "opinion": """
【当前选用风格：观点评论】
适用于：行业争议、技术趋势、商业模式与产品方向讨论。
行文结构要求：
1. 【观点开篇】：直接亮出一个具体、可讨论的判断；可用第一人称表达立场，但不编造经历。
2. 【TL;DR 论点与条件】：说清判断成立的前提，不把预测写成已发生事实。
3. 【证据链】：用已有事实推进论证，区分观察、推断和价值判断，不靠情绪词替代分析。
4. 【反例与另一种解释】：认真讨论最强反对意见，以及哪些事实会改变本文判断。
5. 【读者启示】：落到实际选择或值得持续观察的指标，结尾提一个与论点相关的问题。
""",
    "retrospective": """
【当前选用风格：案例复盘】
适用于：有真实材料的项目记录、流程改造、失败分析与经验总结。
行文结构要求：
1. 【结果或关键转折开篇】：从已提供的真实结果或决策切入。案例不是作者亲历时，用来源主体叙述，不改成「我的项目」。
2. 【TL;DR 经验与边界】：说明案例支持什么经验，哪些条件不能照搬。
3. 【目标、约束与过程】：按记录交代原问题、关键选择、实施过程；不补造人物、对话、耗时或决策动机。
4. 【结果与归因】：区分观测到的结果与本文解释，保留失败和不确定性，不把相关性写成因果。
5. 【可复用经验】：给出适用条件、检查清单和下一步。如果没有真实案例材料，明确缺口，只写问题分析或待执行方案，不制造已完成复盘。
""",
}


class LLMError(RuntimeError):
    """LLM 调用错误，可安全展示给用户（不包含凭据）。"""


class LLMTruncatedError(LLMError):
    """模型已耗尽输出预算，不能将残缺响应交给下游。"""


def _strip_tags(text: str) -> str:
    try:
        return " ".join(lxml_html.fromstring(text).itertext())
    except Exception:
        return re.sub(r"<[^>]+>", "", text)


def style_refs() -> list[dict]:
    """读取 style-refs/ 下的文风参考文件（名称 + 截取片段），最多取 2 篇。"""
    refs = []
    for path in sorted(STYLE_REF_DIR.glob("*")):
        if path.suffix.lower() not in STYLE_SUFFIXES or not path.is_file():
            continue
        text = _strip_tags(path.read_text(encoding="utf-8", errors="ignore"))
        text = re.sub(r"\s+", " ", text).strip()
        if not text:
            continue
        refs.append({"name": path.name, "snippet": text[:STYLE_SNIPPET_CHARS]})
        if len(refs) >= STYLE_MAX_FILES:
            break
    return refs


def _opening_excerpt(markdown: str) -> str:
    """提取首个正文段落，跳过导出稿的元数据、标题、封面和代码。"""
    text = markdown.lstrip("\ufeff \t\r\n")
    text = re.sub(
        r"\A---[ \t]*\r?\n.*?\r?\n(?:---|\.\.\.)[ \t]*(?:\r?\n|$)",
        "", text, count=1, flags=re.S,
    )
    parts = []
    fence_marker = ""
    for raw_line in text.splitlines():
        line = raw_line.strip()
        fence = re.match(r"^(`{3,}|~{3,})", line)
        if fence:
            if parts:
                break
            marker = fence.group(1)
            if not fence_marker:
                fence_marker = marker
            elif marker[0] == fence_marker[0] and len(marker) >= len(fence_marker):
                fence_marker = ""
            continue
        if fence_marker:
            continue
        if not line:
            if parts:
                break
            continue
        if (re.match(r"^(?:#{1,6}\s|!\[|>|<|\|)", line)
                or re.fullmatch(r"[-*_]{3,}", line)):
            if parts:
                break
            continue
        parts.append(line)
        if sum(len(part) for part in parts) >= RECENT_OPENING_CHARS:
            break
    return " ".join(parts)[:RECENT_OPENING_CHARS]


def recent_openings(limit: int = RECENT_OPENING_MAX_FILES) -> list[dict]:
    """读取最近保存的公众号母稿开头，仅作为避重复参考，不扫描衍生平台稿件。"""
    if limit <= 0:
        return []
    candidates = []
    paths = list(OUTPUTS_DIR.glob("????-??-??-*.md"))
    paths.extend((OUTPUTS_DIR / "runs").glob("????-??-??-*/article.md"))
    for path in paths:
        try:
            if path.is_file():
                candidates.append((path.stat().st_mtime, path))
        except OSError:
            continue

    openings = []
    seen = set()
    for _, path in sorted(candidates, key=lambda item: (item[0], str(item[1])), reverse=True):
        try:
            # 只读开头，避免长稿或 Base64 封面占用过多内存和提示词预算。
            with path.open(encoding="utf-8", errors="ignore") as source:
                markdown = source.read(8192)
        except OSError:
            continue
        # 原始推文导入包属于素材，不代表账号自己写的文章开场。
        if "**来源平台**" in markdown and "博主原文案" in markdown:
            continue
        snippet = _opening_excerpt(markdown)
        key = re.sub(r"[\s*_`]", "", snippet)
        if not key or key in seen:
            continue
        seen.add(key)
        openings.append({"name": str(path.relative_to(OUTPUTS_DIR)), "snippet": snippet})
        if len(openings) >= limit:
            break
    return openings


def get_templates() -> list[dict]:
    """返回支持的文案模版列表（用于前端展示与选择）。"""
    return [{"id": k, "name": v} for k, v in TEMPLATES.items()]


def detect_template(topic: dict) -> str:
    """标题与内容方向优先，关键词和备注只作补充，避免附带建议劫持题材。"""
    rules = (
        ("conclusion", ("结论先行", "先说结论", "值不值得", "是否值得", "值得买吗", "要不要换", "要不要买", "购买建议", "推荐清单")),
        ("retrospective", ("复盘", "项目总结", "改造记录", "失败分析")),
        ("tutorial", ("教程", "实操", "手把手", "保姆级", "搭建", "配置指南", "复刻", "sop", "怎么用", "一步步", "操作指南")),
        ("comparison", ("横评", "选型", "怎么选", "如何选", "对比", "比较", "替代方案", "哪个好", "哪款")),
        ("review", ("实测", "评测", "测评", "测试报告", "上手体验", "体验报告")),
        ("opinion", ("观点", "评论", "怎么看", "如何看待", "我的看法", "趋势判断")),
        ("news", ("发布", "突发", "重磅", "ipo", "融资", "收购", "并购", "上线", "官宣", "更新", "降价", "开源", "争议", "罗生门", "改成绩", "刷榜")),
        ("deepdive", ("原理", "概念", "架构", "是什么", "认知", "机制")),
    )
    texts = (
        f"{topic.get('选题标题', '')} {topic.get('内容方向', '')}",
        str(topic.get("关键词", "")),
        str(topic.get("备注", "")),
    )
    for text in texts:
        text = text.lower()
        for template, keywords in rules:
            if any(keyword in text for keyword in keywords):
                return template
        if re.search(r"\bvs\.?\b", text):
            return "comparison"
    return "deepdive"


def resolve_template(topic: dict, template: str = "auto") -> str:
    """显式选择优先；自动或未知 ID 使用题材推荐。"""
    return template if template in TEMPLATE_PROMPTS else detect_template(topic)


def _build_messages(topic: dict, refs: list[dict], template: str = "auto",
                    openings: list[dict] | None = None) -> list[dict]:
    chosen_template = resolve_template(topic, template)
    style_prompt = TEMPLATE_PROMPTS.get(chosen_template, TEMPLATE_PROMPTS["deepdive"])

    system_content = f"{COMMON_SYSTEM_PROMPT}\n\n{style_prompt}"

    user_parts = [
        "请根据下面的选题信息，按题材撰写一篇真实有据、信息清晰的微信公众号图文初稿。\n",
        "【选题信息】",
        f"- 选题标题：{topic.get('选题标题', '')}",
        f"- 关键词：{topic.get('关键词', '')}",
        f"- 内容方向：{topic.get('内容方向', '')}",
        f"- 流量预估：{topic.get('流量预估', '')}",
    ]
    note = str(topic.get("备注", "")).strip()
    user_parts.append(f"\n【作者特别备注（内容约束，须遵守真实有据原则）】\n{note or '无'}")
    user_parts.append(f"\n【文案风格模版】：{TEMPLATES.get(chosen_template, chosen_template)}")

    if refs:
        user_parts.append("\n【文风与语言习惯参考（仅学习语言节奏，不借用事实、亲历或人设）】")
        for ref in refs:
            user_parts.append(f"\n《{ref['name']}》片段：\n{ref['snippet']}")

    if openings:
        user_parts.append(
            "\n【近期开头避重复参考（不是本次事实来源，也不是待模仿的范例）】\n"
            "以下是近期稿件开头。请识别并避开重复句式、叙事路径和情绪钩子，"
            "不要只替换时间或产品名；不能沿用其中的数字、事件、亲历或人物。"
            "在适合本次题材的前提下选择不同的切入角度，不必更换文章风格。"
        )
        for opening in openings:
            user_parts.append(f"\n《{opening['name']}》开头（避重复用）：\n{opening['snippet']}")

    user_parts.append("\n【输出要求】：只输出 Markdown 格式正文，开头不带一级大标题（#），不使用 ```markdown 围栏包裹整篇。")

    return [
        {"role": "system", "content": system_content},
        {"role": "user", "content": "\n".join(user_parts)},
    ]


def chat(messages: list[dict], *, temperature: float = 0.7,
          max_tokens: int = 4096, json_mode: bool = False, max_retries: int = 2) -> str:
    """调用 OpenAI 兼容 /chat/completions，返回 assistant 文本内容。
    json_mode=True 时请求 JSON 输出。长度截断时提高预算并从头重试一次。
    网络重试与截断重试分别计数；残缺响应不会作为成功结果返回。"""
    import time

    api_base = (config.get("LLM_API_BASE") or "http://127.0.0.1:8045/v1").rstrip("/")
    if api_base.startswith("http") and not api_base.endswith("/v1") and "deepseek.com" not in api_base:
        api_base = f"{api_base}/v1"
    api_key = config.require("LLM_API_KEY")
    model = config.get("LLM_MODEL") or "gemini-3.8-flash-high"

    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}

    network_retries = 0
    retried_length = False
    while True:
        try:
            request = Request(
                f"{api_base}/chat/completions",
                data=json.dumps(payload).encode("utf-8"),
                method="POST",
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "Authorization": f"Bearer {api_key}",
                    "Connection": "close",
                },
            )
            with urlopen(request, timeout=180) as response:
                data = json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            err_name = type(exc).__name__
            if network_retries < max_retries:
                network_retries += 1
                _safe_warn(f"[Warn] LLM 调用发生抖动 ({err_name}: {exc})，正在进行第 {network_retries} 次自动重试...")
                time.sleep(1.0)
                continue
            raise LLMError(f"LLM 请求失败（{err_name}）：请检查 LLM_API_BASE 与网络连通性 ({exc})") from exc

        choices = data.get("choices") or []
        choice = choices[0] if choices else {}
        finish = str(choice.get("finish_reason") or "")
        content = str((choice.get("message") or {}).get("content") or "")
        # 即使有正文也必须检查结束状态：推理模型可能把预算消耗在思考中。
        if finish in {"length", "max_tokens"}:
            if not retried_length and payload["max_tokens"] < MAX_AUTO_OUTPUT_TOKENS:
                payload["max_tokens"] = min(payload["max_tokens"] * 2, MAX_AUTO_OUTPUT_TOKENS)
                retried_length = True
                _safe_warn(
                    f"[Warn] LLM 输出截断（finish_reason={finish}），提高 max_tokens 至 "
                    f"{payload['max_tokens']} 并重新生成完整响应。"
                )
                continue
            raise LLMTruncatedError(
                f"模型输出达到长度上限（finish_reason={finish}，max_tokens={payload['max_tokens']}），"
                "未返回不完整文案。请重新生成，或改用思考预算较低的模型。"
            )
        if finish == "content_filter":
            raise LLMError("模型服务中止了输出（finish_reason=content_filter），未返回不完整内容。")
        if not content.strip():
            # 检查是否有 reasoning_content / reasoning 兜底
            msg = choice.get("message") or {}
            reasoning = str(msg.get("reasoning_content") or msg.get("reasoning") or "").strip()
            if reasoning and not json_mode:
                content = reasoning
            elif network_retries < max_retries:
                network_retries += 1
                _safe_warn(f"[Warn] LLM 未返回正文（finish_reason={finish}），正在进行第 {network_retries} 次自动重试...")
                time.sleep(1.0)
                continue
            else:
                error = data.get("error") or {}
                message = error.get("message", "") if isinstance(error, dict) else str(error)
                raise LLMError(f"LLM 未返回正文（finish_reason={finish or '未提供'}）：{message[:200] or '响应中没有可用文本'}")
        return content



def generate_ai_image(prompt: str, output_path: str | Path, size: str = "1024x1024",
                      *, strict: bool = False) -> bool:
    """生成图片并落盘，支持 Base64 与 URL；strict=True 时失败明确报错。"""
    import base64
    import io

    from PIL import Image

    output = Path(output_path)

    # strict 模式优先使用 agy，且必须验证最终文件确实是可读取的图片。
    # 非 strict 保持原有行为：只走兼容 API，失败返回 False。
    agy_error = "未尝试"
    if strict:
        try:
            if _generate_ai_image_via_agy(prompt, output):
                try:
                    with Image.open(output) as image:
                        image.verify()
                    return True
                except Exception as exc:
                    agy_error = f"生成文件无效（{_error_summary(exc)}）"
                    _safe_warn(f"[Warn] agy 输出文件无法作为图片打开（{type(exc).__name__}）。")
            else:
                agy_error = "agy 未生成有效图片（命令不存在、失败、超时或未找到输出文件）"
        except Exception as exc:
            # 第三方 CLI 适配层不得阻断 API 降级。
            agy_error = _error_summary(exc)
            _safe_warn(f"[Warn] agy 调用失败，回退 API（{type(exc).__name__}）。")

    img_model = config.get("IMAGE_MODEL") or config.get("IMAGE_COVER_MODEL") or "gemini-3.1-flash-image"
    api_base = (config.get("IMAGE_GEN_API_BASE") or config.get("LLM_API_BASE") or "http://127.0.0.1:8045/v1").rstrip("/")
    if api_base.startswith("http") and not api_base.endswith("/v1") and "deepseek.com" not in api_base:
        api_base = f"{api_base}/v1"
    api_key = config.get("IMAGE_GEN_API_KEY") or config.get("LLM_API_KEY")

    if not api_key:
        if strict:
            raise LLMError(
                "自动封面生图失败；agy：%s；API：缺少 IMAGE_GEN_API_KEY 或 LLM_API_KEY。"
                % agy_error
            )
        return False

    payload = {
        "model": img_model,
        "prompt": prompt,
        "n": 1,
        "size": size,
    }
    last_error = None
    for attempt in range(2):
        # 兼容仅接受方图尺寸的服务，重试后仍按横幅构图自动裁切。
        payload["size"] = size if attempt == 0 else "1024x1024"
        request = Request(
            f"{api_base}/images/generations",
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Authorization": f"Bearer {api_key}",
                "Connection": "close",
            },
        )
        try:
            with urlopen(request, timeout=180) as response:
                data = json.loads(response.read().decode("utf-8"))
            items = data.get("data") or []
            if not items:
                raise ValueError("生图接口未返回图片")
            item = items[0]
            if item.get("b64_json"):
                encoded = item["b64_json"]
                if encoded.startswith("data:"):
                    encoded = encoded.split(",", 1)[1]
                img_bytes = base64.b64decode(encoded, validate=True)
            elif item.get("url"):
                image_url = str(item["url"])
                if not image_url.startswith(("https://", "http://")):
                    raise ValueError("生图接口返回了不支持的图片 URL")
                # 下载图片不携带模型服务的 Authorization 凭据。
                with urlopen(image_url, timeout=120) as response:
                    img_bytes = response.read()
            else:
                raise ValueError("生图响应缺少 b64_json 或 url")
            with Image.open(io.BytesIO(img_bytes)) as image:
                image.load()
            p = output
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(img_bytes)
            return True
        except Exception as exc:
            last_error = exc
            _safe_warn(f"[Warn] 生图第 {attempt + 1} 次失败（{type(exc).__name__}）。")
    if strict:
        detail = _error_summary(last_error)
        raise LLMError(
            f"自动封面生图失败；agy: {agy_error}；API: {detail}。"
            "请检查 IMAGE_GEN_API_BASE、IMAGE_MODEL 与生图服务可用性。"
        ) from last_error
    return False


def _error_summary(error: BaseException | None) -> str:
    """返回不含凭据、适合拼进用户错误信息的短异常摘要。"""
    if error is None:
        return "未执行或未知错误"
    code = getattr(error, "code", None)
    prefix = f"HTTP {code}: " if code else f"{type(error).__name__}: "
    message = str(error).replace("\n", " ").strip()
    return (prefix + (message or "无详细信息"))[:240]


def _generate_ai_image_via_agy(prompt: str, output_path: str | Path) -> bool:
    """通过 agy CLI 调用 Antigravity 平台生图能力（独立配额池）。

    agy --print 生成图片后在 stdout 输出绝对路径（反引号包裹），本函数解析路径并复制到 output_path。
    """
    import shutil
    import subprocess

    agy_bin = shutil.which("agy")
    if not agy_bin:
        _safe_warn("[Warn] agy CLI 未找到，跳过 fallback。")
        return False

    # 图片名称用时间戳保证唯一，agy 以此命名生成文件
    import time
    img_name = f"cover_fallback_{int(time.time() * 1000)}"
    agy_prompt = (
        f"请用 generate_image 工具生成一张图片，图片名称（ImageName）用 {img_name}，"
        f"Prompt 如下：\n{prompt}\n\n"
        f"完成后，只输出图片的完整绝对路径，用反引号包裹，不输出其他内容。"
    )
    try:
        result = subprocess.run(
            [agy_bin, "--print", agy_prompt],
            capture_output=True, text=True, timeout=180,
        )
        stdout = result.stdout or ""
        # 解析反引号包裹的绝对路径
        match = re.search(r"`(/[^`\n]+\.(?:jpg|jpeg|png|webp))`", stdout, re.I)
        if not match:
            # 兜底：直接搜路径字符串
            match = re.search(r"(/[\w/.~-]+\.(?:jpg|jpeg|png|webp))", stdout, re.I)
        if not match:
            _safe_warn(f"[Warn] agy fallback：无法从输出中解析图片路径。stdout={stdout[:300]}")
            return False
        src = Path(match.group(1))
        if not src.is_file():
            _safe_warn(f"[Warn] agy fallback：解析到路径 {src} 但文件不存在。")
            return False
        dst = Path(output_path)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        # 适配层自身也做一次校验，避免复制损坏/非图片文件后被误报成功。
        from PIL import Image
        with Image.open(dst) as image:
            image.verify()
        _safe_warn(f"[Info] agy fallback 生图成功：{src} → {dst}")
        return True
    except subprocess.TimeoutExpired:
        _safe_warn("[Warn] agy fallback 超时（180s）。")
        return False
    except Exception as exc:
        _safe_warn(f"[Warn] agy fallback 异常：{exc}")
        return False



def parse_json_object(raw: str) -> dict:
    """尽力从 LLM 输出里提取 JSON 对象；失败返回空 dict。"""
    raw = raw.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)\s*```", raw, flags=re.S)
    target = fence.group(1).strip() if fence else raw
    try:
        data = json.loads(target, strict=False)
        if isinstance(data, dict):
            return data
    except Exception:
        pass

    match = re.search(r"\{.*\}", target, flags=re.S)
    if match:
        try:
            data = json.loads(match.group(0), strict=False)
            if isinstance(data, dict):
                return data
        except Exception:
            try:
                cleaned = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", match.group(0))
                data = json.loads(cleaned, strict=False)
                if isinstance(data, dict):
                    return data
            except Exception:
                pass
    return {}


def _table_cells(line: str) -> list[str]:
    text = line.strip()
    if text.startswith("|"):
        text = text[1:]
    if re.search(r"(?<!\\)\|$", text):
        text = text[:-1]
    return [cell.strip() for cell in re.split(r"(?<!\\)\|", text)]


def _is_table_separator(line: str) -> bool:
    cells = _table_cells(line)
    return len(cells) >= 2 and all(re.fullmatch(r":?-+:?", cell) for cell in cells)


def _draft_incomplete_reason(markdown: str) -> str:
    """检查明显截断的结构，不以结尾标点判断完整性（清单/代码可正常收尾）。"""
    if not markdown.strip():
        return "正文为空"
    body_lines = []
    fence_marker = ""
    for line in markdown.splitlines():
        stripped = line.strip()
        fence = re.match(r"^(`{3,}|~{3,})", stripped)
        if fence:
            marker = fence.group(1)
            if not fence_marker:
                fence_marker = marker
            elif marker[0] == fence_marker[0] and len(marker) >= len(fence_marker):
                fence_marker = ""
            body_lines.append("")
        elif not fence_marker:
            body_lines.append(stripped)
    if fence_marker:
        return "代码块未闭合，正文可能在代码或提示词中截断"

    index = 0
    while index < len(body_lines):
        line = body_lines[index]
        has_pipe = bool(re.search(r"(?<!\\)\|", line))
        next_is_separator = index + 1 < len(body_lines) and _is_table_separator(body_lines[index + 1])
        if not has_pipe or not (line.startswith("|") or next_is_separator):
            index += 1
            continue
        rows = []
        while index < len(body_lines) and re.search(r"(?<!\\)\|", body_lines[index]):
            rows.append(body_lines[index])
            index += 1
        if len(rows) < 3 or not _is_table_separator(rows[1]):
            return "表格缺少分隔行或数据行，可能停在表头中途"
        columns = len(_table_cells(rows[0]))
        if any(len(_table_cells(row)) != columns for row in rows[1:]):
            return "表格列数不一致，可能在单元格中途截断"

    chapters = sum(bool(re.match(r"^##\s+\S", line)) for line in body_lines)
    if chapters < 3:
        return f"只有 {chapters} 个核心章节，未达到完整稿件要求的 3~4 个章节"
    return ""


def _complete_markdown_draft(messages: list[dict], *, max_tokens: int,
                             temperature: float = 0.7) -> str:
    """生成完整公众号稿件；服务误报 stop 时也检查结构，并最多重写一次。"""
    request_messages = [dict(message) for message in messages]
    for attempt in range(2):
        content = chat(request_messages, max_tokens=max_tokens, temperature=temperature).strip()
        fence = re.fullmatch(r"```(?:markdown|md)?\s*(.*?)\s*```", content, flags=re.S)
        if fence:
            content = fence.group(1).strip()
        reason = _draft_incomplete_reason(content)
        if not reason:
            return content
        if attempt == 0:
            _safe_warn(f"[Warn] 文案结构不完整（{reason}），重新生成完整稿件。")
            max_tokens = max(max_tokens, min(max_tokens * 2, MAX_AUTO_OUTPUT_TOKENS))
            request_messages = [dict(message) for message in request_messages]
            request_messages[-1]["content"] += (
                f"\n\n【完整性修复要求】：上一轮响应存在结构问题：{reason}。"
                "请按原始选题和篇幅要求从头生成完整正文，包含 3~4 个核心章节与完整结尾；"
                "表格需要完整表头、分隔行和数据行，代码块必须闭合。"
                "不要仅续写片段，不要输出修复说明，不新增事实或测试数据。"
            )
            continue
        raise LLMError(f"文案重新生成后仍不完整（{reason}），未返回半篇稿件。请重试或更换模型。")
    raise LLMError("未生成完整文案")


def generate_draft(topic: dict, template: str = "auto") -> str:
    """按选题与风格生成完整初稿，返回 Markdown 文本。"""
    messages = _build_messages(topic, style_refs(), template=template, openings=recent_openings())
    return _complete_markdown_draft(messages, max_tokens=8192)


# ==============================================================================
# 多平台矩阵衍生：小红书 (tuiwen-xhs-gen) & 今日头条 (tuiwen-jrtt-gen)
# 深度打通本地技能：运行时直接读取 .agents/skills/*/SKILL.md 规范与调用内置 scripts
# ==============================================================================

SKILLS_DIR = Path(__file__).resolve().parents[2] / ".agents" / "skills"


def load_skill_spec(skill_name: str) -> str:
    """动态读取本地技能 .agents/skills/{skill_name}/SKILL.md 的核心规范与指令。"""
    skill_file = SKILLS_DIR / skill_name / "SKILL.md"
    if skill_file.is_file():
        try:
            raw = skill_file.read_text(encoding="utf-8")
            if raw.startswith("---"):
                parts = raw.split("---", 2)
                if len(parts) >= 3:
                    raw = parts[2].strip()
            return raw
        except Exception as e:
            _safe_warn(f"[Warn] 读取本地技能规范失败 {skill_name}: {e}")
    return ""


def get_xhs_system_prompt() -> str:
    """构建小红书生成 System Prompt，实时结合本地 tuiwen-xhs-gen 技能规范。"""
    skill_spec = load_skill_spec("tuiwen-xhs-gen")
    skill_part = f"\n【本地技能标准规范（来源于 .agents/skills/tuiwen-xhs-gen/SKILL.md）】：\n{skill_spec}\n" if skill_spec else ""
    return f"""你是一位精通小红书爆款算法与受众心理学的头部科技/效率数码博主。
你的任务是将用户提供的微信公众号深度母稿，严格依据本地技能规范一键转化为适合小红书传播的“爆款单图文案”（对应 tuiwen-xhs-gen 技能标准）。
{skill_part}
【核心交付要求】：
1. 拒绝半成品，开箱即发布：绝不输出备选标题，AI 必须依据小红书 CTR 爆款公式直接敲定唯一最优标题（带情绪表情，长度严格控制在 20 个字以内，不得超过20字）。
2. 提炼 4 个核心维度事实（核心爆点、行业震撼、普通人打工福利、理性客观提醒），供渲染 3:4 竖版置顶封面图。
3. 正文结构一气呵成：黄金前 3 秒 Hook、4 个看点与场景展开（带 Emoji 🖥️/📚/🎧/⚡）、理性冷思考、评论区互动引导与 6 个垂直标签。
4. 【极其重要的字数限制】：小红书平台对正文有 1000 字符的硬性上限。输出的 body_markdown 必须言简意赅、重点突出，总字数严格控制在 600~750 字之间，留足空间给排版与标签，严禁超过 800 字！
5. 【平台合规红线与严禁诱导违规（极高优先级）】：严禁输出任何“评论区扣1/扣关键词”、“发你源码/送资料/给指南/领配置/进群”、“点赞收藏私信发你”等诱导互动话术！小红书 AI 审核会通过 OCR 与语义分析直接拦截违规并限流警告。文末“💬 聊聊看”仅允许提出一个引发读者真实探讨或思考的开放式问题（如：“在你的工作流中你更看重哪一点？”）。若涉及开源项目，直接在文末注明公开自查途径（如：“项目开源地址：GitHub 搜索 [项目名] 即可直达仓库自取”），完全免费公开，严禁设置任何互动门槛或承诺！

【输出格式】：
必须严格输出纯 JSON 对象（不包裹在除 json 以外的多余解释中）：
{{
  "title": "敲定的爆款标题（带表情）",
  "cover_config": {{
    "top_tag": "科技前沿 · 重磅突发",
    "badge": "【突发重磅】",
    "highlight": "46 分登顶！",
    "title": "最强开源大模型",
    "subtitle": "突然是小米做的？！",
    "card_title": "【一图读懂】核心全貌速览",
    "items": [
      {{"tag": "登顶榜首", "color": "#FF6700", "title": "46 分拿下全球第一", "desc": "权威榜单反超友商，全模态通吃。"}},
      {{"tag": "透明厨房", "color": "#38BDF8", "title": "训练全程直播公开", "desc": "不到 6 天训完，75 万条轨迹全公开。"}},
      {{"tag": "打工福利", "color": "#22C55E", "title": "电脑离线本地跑", "desc": "推桌面端不泄密，百万长文速读。"}},
      {{"tag": "理性客观", "color": "#F43F5E", "title": "离闭源旗舰仍有差距", "desc": "不盲目神化，开源狂追让普通人受惠。"}}
    ],
    "footer_tip": ">> 完整深度实测评测与上手方法，正文见 >>"
  }},
  "body_markdown": "正文 Markdown 内容（从黄金前 3 秒 Hook 开始，包含小标题、场景展开、理性思考、评论互动与文末标签。正文开头不要放封面图引用和主标题，由后端自动组装）。"
}}
"""


def _sanitize_xhs_body(text: str) -> str:
    """过滤小红书正文中的违规诱导互动话术（避免触发小红书 AI 审核违规警告）。"""
    if not text:
        return text
    lines = []
    for line in text.splitlines():
        # 如果整行包含诱导关键字
        if re.search(r'(?:扣|私信).*?(?:1|一|源码|地址|指南|资料|清单|发你|送你|安排|速发|领取|抱走)', line, re.I):
            continue
        if re.search(r'(?:想要|获取|领取|送你).*?(?:源码|地址|指引|资料|清单).*?(?:评论|留言|扣|私信)', line, re.I):
            continue
        if re.search(r'想看.*?扣.*?安排', line, re.I):
            continue
        # 行内句子级别清洗
        cleaned_line = re.sub(r'[【\[（(]?(?:想要|如需)?(?:源码|资料|指南).*?(?:评论区|留言|私信)?扣.*?发你.*?[】\]）)]?', '', line, flags=re.I)
        cleaned_line = re.sub(r'评论区扣[“\"【\[]?[^，。\n]+?[”\"】\]]?(?:获取|送你|发你|领取)?[^，。\n]*', '', cleaned_line, flags=re.I)
        cleaned_line = re.sub(r'想省下会员费的宝子，.*?带你跑通', '', cleaned_line, flags=re.I)
        if cleaned_line.strip():
            lines.append(cleaned_line.strip())
    res = '\n'.join(lines)
    return re.sub(r'\n{3,}', '\n\n', res).strip()


def generate_xhs_copy(title: str, markdown: str) -> dict:
    """根据公众号母稿生成小红书笔记文案及封面配置（动态调用本地 tuiwen-xhs-gen 技能标准）。"""
    user_prompt = f"""请根据以下微信公众号母稿，严格按照本地技能要求提炼出小红书爆款文案和置顶封面配置 JSON。

【微信母稿原标题】：{title}

【微信母稿正文】：
{markdown[:8000]}
"""
    messages = [
        {"role": "system", "content": get_xhs_system_prompt()},
        {"role": "user", "content": user_prompt},
    ]
    raw = chat(messages, temperature=0.7, max_tokens=8192, json_mode=True)
    data = parse_json_object(raw)
    if not data or not data.get("title") or not data.get("body_markdown"):
        # 降级防御
        title_res = title if "！" in title else f"{title}！这波确实没想到🔥"
        fallback_body = _sanitize_xhs_body(data.get("body_markdown") or raw)
        return {
            "title": data.get("title") or title_res,
            "cover_config": data.get("cover_config") or {
                "top_tag": "科技热点 · 深度拆解",
                "badge": "【速览】",
                "highlight": "重磅发布",
                "title": title[:14],
                "subtitle": "到底有多强？！",
                "card_title": "【一图读懂】核心看点",
                "items": [
                  {"tag": "核心突破", "color": "#FF6700", "title": "核心能力暴涨", "desc": "指标实现跨越式升级"},
                  {"tag": "颠覆认知", "color": "#38BDF8", "title": "打法与众不同", "desc": "公开透明打破行业黑箱"},
                  {"tag": "打工福利", "color": "#22C55E", "title": "普通人直接上手", "desc": "多场景大幅提升日常效率"},
                  {"tag": "理性客观", "color": "#F43F5E", "title": "保持清醒判断", "desc": "不盲目吹捧，看清实际差距"}
                ],
                "footer_tip": ">> 完整干货拆解，正文见 >>"
            },
            "body_markdown": fallback_body
        }
    # 强制进行合规清洗
    if data.get("body_markdown"):
        data["body_markdown"] = _sanitize_xhs_body(data["body_markdown"])
    return data


def normalize_toutiao_title(raw_title: str, max_chars: int = 30) -> str:
    """今日头条标题严格规范化：去除多余符号，并强制压缩在 30 字符以内，保持语义完整。"""
    t = (raw_title or "").strip()
    t = re.sub(r"^#+\s*", "", t)
    t = re.sub(r"^【(?:今日头条|深度解读|独家拆解|开源爆款|重磅突破)[^】]*】\s*", "", t)
    t = t.strip("《》\"'“”‘’").strip()
    if len(t) <= max_chars:
        return t
    cut = t[:max_chars]
    for punct in "？！?!，,:：…":
        idx = cut.rfind(punct)
        if idx >= 15:
            candidate = cut[:idx + 1].strip()
            if candidate[-1] in "，,:：":
                candidate = candidate[:-1] + "？"
            return candidate
    safe_cut = t[:max_chars - 1].rstrip("，,、:：-— ")
    if not safe_cut.endswith(("？", "！", "!", "?", "。")):
        safe_cut += "？"
    return safe_cut[:max_chars]


def get_jrtt_system_prompt() -> str:
    """构建今日头条生成 System Prompt，实时结合本地 tuiwen-jrtt-gen 技能规范。"""
    skill_spec = load_skill_spec("tuiwen-jrtt-gen")
    skill_part = f"\n【本地技能标准规范（来源于 .agents/skills/tuiwen-jrtt-gen/SKILL.md）】：\n{skill_spec}\n" if skill_spec else ""
    return f"""你是一位擅长今日头条信息流推荐机制的资深科技/财经深度专栏作者。
你的任务是将用户提供的微信公众号深度母稿，严格依据本地技能规范一键转化为适合今日头条（Toutiao APP）生态传播的“深度爆款长图文”（对应 tuiwen-jrtt-gen 技能标准）。
{skill_part}
【核心交付要求】：
1. 【头条爆款标题字数铁律（绝对红线）】：
   - 标题总字符数必须在 20~28 字符之间，绝对不得超过 30 个字符（汉字、英文字母、数字和标点符号均计入 1 字符）！
   - 标题公式：[核心事件/颠覆认知] + [知名主体/反差动作] + [引发求证的悬念疑问]
   - 示范标杆（严格 ≤ 28 字符）：
     * 开源平替掀桌OpenAI！告别每月20刀，真能年省上千？（27字符）
     * 单线程终结！手机调度AI军团，9万星神器革了谁的命？（26字符）
     * 开源AI向剪映会员开刀！单行命令搞定剪辑，真能平替？（26字符）
   - 严禁出现过长复合句，严禁包含“【今日头条】”等任何废话渠道说明。直接敲定唯一最优主标题，不给多选。
2. 专业客观拒绝盲目吹捧，严格基于母稿真实数据，指出客观差距。
3. 正文脉络结构规范：导语痛点引入 → 一、核心看点榜单成绩（包含 Markdown 三线表格） → 二、颠覆认知底牌细节 → 三、普通人日常落地场景 → 四、理性客观差距点明 → 互动探讨热议问题。
4. 封面图配置（用于生成 16:9 横版吸睛封面）：提炼主标题、副标题、高光标签。

【输出格式】：
必须严格输出纯 JSON 对象：
{{
  "title": "敲定的头条爆款标题（严格控制在20~28字，绝不超过30字）",
  "cover_config": {{
    "top_tag": "今日头条 · 深度图文",
    "badge": "【重磅突破】",
    "highlight": "登顶世界第一",
    "title": "核心大标题",
    "subtitle": "颠覆认知背后的真相到底是什么？"
  }},
  "body_markdown": "完整的头条深度长图文 Markdown（从 ### 导语 开始，包含四个小节、表格、加粗数据以及文末 ### 互动探讨。正文开头不要放 # 标题或封面图占位符，由后端自动组装）。"
}}
"""


def generate_toutiao_copy(title: str, markdown: str) -> dict:
    """根据公众号母稿生成今日头条图文文案及封面配置（动态调用本地 tuiwen-jrtt-gen 技能标准）。"""
    user_prompt = f"""请根据以下微信公众号母稿，严格按照本地技能要求提炼出今日头条深度爆款长图文与 16:9 封面配置 JSON。
注意：标题必须严格控制在 30 字符以内（推荐 22~28 字符），绝不能超标！

【微信母稿原标题】：{title}

【微信母稿正文】：
{markdown[:8000]}
"""
    messages = [
        {"role": "system", "content": get_jrtt_system_prompt()},
        {"role": "user", "content": user_prompt},
    ]
    raw = chat(messages, temperature=0.7, max_tokens=8192, json_mode=True)
    data = parse_json_object(raw)
    if not data or not data.get("title") or not data.get("body_markdown"):
        fallback_body = (data.get("body_markdown") if data else "") or ""
        if not fallback_body:
            bm_match = re.search(r'"body_markdown"\s*:\s*"(.*?)(?:"\s*,\s*"|"\s*\}\s*$)', raw, flags=re.S)
            if bm_match:
                try:
                    fallback_body = bm_match.group(1).encode("utf-8").decode("unicode_escape", errors="ignore")
                except Exception:
                    fallback_body = bm_match.group(1)
            else:
                fallback_body = raw
        fallback_body = re.sub(r"^```(?:json)?\s*", "", fallback_body.strip())
        fallback_body = re.sub(r"\s*```$", "", fallback_body)
        raw_title_cand = (data.get("title") if data else "") or title
        safe_title = normalize_toutiao_title(raw_title_cand, max_chars=30)
        return {
            "title": safe_title,
            "cover_config": (data.get("cover_config") if data else {}) or {
                "top_tag": "今日头条 · 独家拆解",
                "badge": "【深度解读】",
                "highlight": "颠覆认知",
                "title": safe_title[:14],
                "subtitle": "背后到底藏着什么大招？"
            },
            "body_markdown": fallback_body
        }

    # 严格规范化标题，确保无论大模型输出多少字，强制保证 ≤ 30 字符
    data["title"] = normalize_toutiao_title(data.get("title") or title, max_chars=30)
    return data



COMPRESS_SYSTEM_PROMPT = """你是一位顶尖科技与商业公众号的资深总编。
你的任务是将作者提供的偏长微信公众号初稿，精简为一篇字数在 1100~1300 汉字以内、紧凑且真实有据的图文。

【硬性背景与目标】：
微信草稿箱接口对富文本 HTML 有 20,000 字符的严格物理上限。当前原文排版后严重超标。
你必须将正文 Markdown 压缩到 1100~1300 字（绝对不能超过 1350 字！）。

【核心精炼原则】：
1. 保持原汁原味与核心干货：保留所有核心事实、硬核数据、对比表格与金句，删去冗长的前戏铺垫、套话、重复论述和过细的边角料。
2. 保持视觉结构：依然保持 3~4 个核心小节（##），保留 TL;DR 导读块、核心加粗和文末总结互动。
3. 语言清晰克制：去掉夸张套话，保留原文开篇方式与判断边界；不把压缩改成戏剧性重写，不新增作者亲历、实测、数字或新闻时间。
4. 输出格式：只输出精炼后的 Markdown 正文，不输出大标题（#），不使用 ```markdown 围栏包裹整篇。
"""


def compress_draft(title: str, markdown: str) -> str:
    """将超长文案智能精简至微信安全容量（1100~1300字）。"""
    user_prompt = f"""文章标题：《{title}》

【当前偏长初稿（需精简至 1100~1300 字）】：
{markdown}
"""
    messages = [
        {"role": "system", "content": COMPRESS_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]
    return _complete_markdown_draft(messages, temperature=0.5, max_tokens=4096)
