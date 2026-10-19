#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
fetch_hot_skills.py: 高热度、真好用的 AI Skill / Agent 技能 / 效率神器雷达。
数据源：
1. GitHub 实时高增速技能仓库 (topic:mcp-server, agent-skills, claude-code, ai-tools, local-ai)
2. Hacker News "Show HN" 开发者硬核工具口碑帖
3. 动态热度雷达（日均涨星速率 Velocity、新星黑马加权）
4. 多层去重闭环（比对飞书选题库已存记录 + 本地历史记录，彻底避免重复扫出已发内容）
"""

from __future__ import annotations

import os
import re
import sys
import json
import time
import argparse
import datetime
import urllib.request
import urllib.parse
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
sys.path.insert(0, str(SCRIPTS_DIR.parent / "studio"))

import config
import feishu_btable

USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
HISTORY_FILE = SCRIPTS_DIR.parent / "outputs" / "scanned_skills_history.json"

# 识别“真有帮助”的核心价值关键词（平替、提效、端侧、自动化）
VALUE_KEYWORDS = [
    "local", "offline", "privacy", "mcp", "agent", "skill", "workflow",
    "automation", "scraper", "terminal", "cli", "whisper", "mac", "browser",
    "replace", "free", "self-hosted", "fast", "tool", "memory"
]

# 排除纯论文、收集列表或与技能无关的噪音
EXCLUDE_KEYWORDS = [
    "awesome-", "collection", "curated list", "survey", "reading-list",
    "interview", "roadmap", "cheatsheet", "papers"
]


def _safe_warn(msg: str) -> None:
    try:
        sys.stderr.write(f"{msg}\n")
        sys.stderr.flush()
    except (OSError, IOError, BrokenPipeError):
        pass


def fetch_url_json(url: str, timeout: int = 10, headers: dict | None = None) -> dict | list | None:
    req_headers = {"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"}
    if headers:
        req_headers.update(headers)
    req = urllib.request.Request(url, headers=req_headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        _safe_warn(f"[Warn] 请求失败 {url}: {exc}")
        return None


def _normalize_repo_key(text: str) -> str:
    """提取规范化的 repo 标识 (例如 owner/repo)。"""
    if not text:
        return ""
    text = text.strip().lower()
    # 匹配形如 github.com/owner/repo
    m = re.search(r"github\.com/([a-zA-Z0-9_\-\.]+/[a-zA-Z0-9_\-\.]+)", text)
    if m:
        return m.group(1).rstrip(".git").rstrip("/").lower()
    # 如果已经是 owner/repo 形式
    if re.fullmatch(r"[a-zA-Z0-9_\-\.]+/[a-zA-Z0-9_\-\.]+", text):
        return text.lower()
    return text.lower()


def load_excluded_repo_keys() -> tuple[set[str], int, int]:
    """
    加载历史已发/已入库的 Repo 指纹：
    1. 从本地 outputs/scanned_skills_history.json
    2. 从飞书多维表格（提取全部记录中的备注、标题、关键词中的 github url 或 repo 标识）
    返回: (excluded_keys, local_count, feishu_count)
    """
    excluded: set[str] = set()
    local_count = 0
    feishu_count = 0

    # 1. 本地历史文件
    if HISTORY_FILE.is_file():
        try:
            data = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                for k in data.keys():
                    norm = _normalize_repo_key(k)
                    if norm:
                        excluded.add(norm)
                        local_count += 1
            elif isinstance(data, list):
                for it in data:
                    k = it.get("key") or it.get("name") or it.get("url") or ""
                    norm = _normalize_repo_key(k)
                    if norm:
                        excluded.add(norm)
                        local_count += 1
        except Exception as exc:
            _safe_warn(f"[Warn] 读取本地历史选题库失败: {exc}")

    # 2. 飞书多维表格历史（如果已配置且可连通）
    try:
        client = feishu_btable.FeishuBitable()
        records = client.list_records(page_size=200)
        for rec in records:
            fields = rec.get("fields", {})
            for f_val in [fields.get("备注", ""), fields.get("选题标题", ""), fields.get("关键词", "")]:
                if not f_val or not isinstance(f_val, str):
                    continue
                for m in re.finditer(r"github\.com/([a-zA-Z0-9_\-\.]+/[a-zA-Z0-9_\-\.]+)", f_val):
                    repo_key = m.group(1).rstrip(".git").rstrip("/").lower()
                    if repo_key:
                        excluded.add(repo_key)
                        feishu_count += 1
    except Exception as exc:
        _safe_warn(f"[Info] 飞书记录查重略过（离线或未配置）: {exc}")

    return excluded, local_count, feishu_count


def save_scanned_skills_to_history(topics: list[dict], raw_items: list[dict] | None = None) -> None:
    """持久化记录已入选的 Skill 到本地历史 JSON。"""
    if not topics and not raw_items:
        return
    history = {}
    if HISTORY_FILE.is_file():
        try:
            history = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
            if not isinstance(history, dict):
                history = {}
        except Exception:
            history = {}

    today_str = datetime.date.today().strftime("%Y-%m-%d")

    # 构建 raw_items 映射便于补充数据
    raw_map = {}
    if raw_items:
        for it in raw_items:
            key = _normalize_repo_key(it.get("url") or it.get("name") or "")
            if key:
                raw_map[key] = it

    for it in topics:
        key = _normalize_repo_key(it.get("url") or it.get("name") or "")
        if not key:
            # 尝试从备注提取 URL
            remark = it.get("备注") or ""
            m = re.search(r"github\.com/([a-zA-Z0-9_\-\.]+/[a-zA-Z0-9_\-\.]+)", remark)
            if m:
                key = m.group(1).rstrip(".git").rstrip("/").lower()
        if not key:
            continue

        raw = raw_map.get(key, {})
        history[key] = {
            "name": raw.get("name") or it.get("name") or key,
            "url": it.get("url") or raw.get("url") or f"https://github.com/{key}",
            "title": it.get("选题标题") or it.get("title") or "",
            "stars": raw.get("stars", it.get("stars", 0)),
            "velocity": raw.get("velocity", it.get("velocity", 0.0)),
            "heat_badge": raw.get("heat_badge", it.get("heat_badge", "")),
            "scanned_at": today_str,
        }

    try:
        HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        HISTORY_FILE.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        _safe_warn(f"[Warn] 保存本地扫描历史失败: {exc}")


def get_github_trending_skills(limit_per_query: int = 10, excluded_keys: set[str] | None = None) -> list[dict]:
    """
    针对性检索高增速、新爆发与真有代码的 MCP 与 Agent Skill 仓库。
    采用动态相对时间窗口，并引入日均涨星速率 Velocity 评估。
    """
    if excluded_keys is None:
        excluded_keys = set()

    today = datetime.date.today()
    pushed_recent = (today - datetime.timedelta(days=14)).strftime("%Y-%m-%d")
    created_recent_30 = (today - datetime.timedelta(days=30)).strftime("%Y-%m-%d")
    created_recent_60 = (today - datetime.timedelta(days=60)).strftime("%Y-%m-%d")

    queries = [
        # 1. 动态新星黑马：近 30~60 天内新建且已迅速起量的神作
        f"topic:mcp-server created:>{created_recent_60} stars:>30",
        f"mcp-server OR agent-skill created:>{created_recent_60} stars:>40",
        f"claude-skill OR claude-code created:>{created_recent_60} stars:>30",
        f"ai-tools local OR offline created:>{created_recent_30} stars:>50",
        # 2. 持续活跃爆发：近两周有提交的高星实战工具
        f"topic:mcp-server stars:>100 pushed:>{pushed_recent}",
        f"agent-skill OR claude-code stars:>100 pushed:>{pushed_recent}",
        f"browser-use OR computer-use OR open-interpreter stars:>150 pushed:>{pushed_recent}",
    ]

    candidates = []
    seen_repos = set()

    headers = {}
    gh_token = os.environ.get("GITHUB_TOKEN") or config.get("GITHUB_TOKEN")
    if gh_token:
        headers["Authorization"] = f"Bearer {gh_token}"

    for q in queries:
        encoded_q = urllib.parse.quote(q)
        api_url = f"https://api.github.com/search/repositories?q={encoded_q}&sort=updated&order=desc&per_page={limit_per_query}"
        data = fetch_url_json(api_url, timeout=12, headers=headers)
        if not data or not isinstance(data, dict):
            continue

        items = data.get("items") or []
        for item in items:
            full_name = item.get("full_name", "")
            if not full_name:
                continue

            repo_key = _normalize_repo_key(full_name)
            # 进程内去重以及跨周期历史去重
            if not repo_key or repo_key in seen_repos or repo_key in excluded_keys:
                continue
            seen_repos.add(repo_key)

            desc = (item.get("description") or "").strip()
            name = item.get("name", "")
            language = item.get("language") or ""
            stars = item.get("stargazers_count", 0)
            topics = item.get("topics") or []
            pushed_at = (item.get("pushed_at") or "")[:10]
            created_at_str = (item.get("created_at") or "")[:10]

            # 过滤排除纯文本收集列表
            name_lower = name.lower()
            desc_lower = desc.lower()
            if any(k in name_lower or k in desc_lower for k in EXCLUDE_KEYWORDS):
                continue

            # 语言要求：必须具备可执行的代码
            if not language and item.get("size", 0) < 50:
                continue

            # 计算诞生天数与日均涨星速率 Velocity
            days_old = 1
            if created_at_str:
                try:
                    c_dt = datetime.datetime.strptime(created_at_str, "%Y-%m-%d").date()
                    days_old = max(1, (today - c_dt).days)
                except Exception:
                    days_old = 1

            velocity = round(stars / days_old, 1)

            # 生成直观的热度量化标签与等级
            if days_old <= 30 and stars >= 80:
                heat_badge = f"⚡ 诞生仅{days_old}天破{stars}★ (日均+{velocity}★)"
                growth_level = "新星黑马"
                novelty_bonus = 200
            elif days_old <= 60 and stars >= 150:
                heat_badge = f"🚀 60天狂飙破{stars}★ (日均+{velocity}★)"
                growth_level = "新星黑马"
                novelty_bonus = 150
            elif velocity >= 15:
                heat_badge = f"🔥 极速狂飙 (日均+{velocity}★ | 总计{stars}★)"
                growth_level = "极高爆发"
                novelty_bonus = 80
            elif stars >= 1000:
                heat_badge = f"🌟 硬核顶流 ({stars}★ | 日均+{velocity}★)"
                growth_level = "高关注度"
                novelty_bonus = 30
            else:
                heat_badge = f"📈 稳健增长 ({stars}★ | 日均+{velocity}★)"
                growth_level = "稳步上升"
                novelty_bonus = 0

            # 计算实用价值得分
            value_hits = [k for k in VALUE_KEYWORDS if k in desc_lower or k in topics or k in name_lower]

            # 综合热度得分：重度考量日均增速与实用价值，避免老僵尸库垄断
            score = round(velocity * 15 + min(stars, 3000) * 0.1 + len(value_hits) * 60 + novelty_bonus, 1)

            candidates.append({
                "source": "GitHub Skill",
                "name": full_name,
                "title": f"{name}: {desc[:100]}",
                "url": item.get("html_url", ""),
                "stars": stars,
                "days_old": days_old,
                "velocity": velocity,
                "heat_badge": heat_badge,
                "growth_level": growth_level,
                "language": language,
                "created_at": created_at_str,
                "updated_at": pushed_at,
                "topics": topics,
                "summary": desc,
                "value_tags": value_hits,
                "score": score,
            })

    # 按综合生产力与增速热度得分排序
    candidates.sort(key=lambda x: x["score"], reverse=True)
    return candidates


def get_show_hn_tools(limit: int = 10, excluded_keys: set[str] | None = None) -> list[dict]:
    """从 Hacker News 获取近期热门的 Show HN 开源效率神器。"""
    if excluded_keys is None:
        excluded_keys = set()

    url = "https://hn.algolia.com/api/v1/search?query=Show%20HN%20AI%20OR%20Tool%20OR%20Local&tags=show_hn&numericFilters=points>40&hitsPerPage=20"
    data = fetch_url_json(url, timeout=10)
    if not data or not isinstance(data, dict):
        return []

    hits = data.get("hits") or []
    results = []
    seen = set()
    for hit in hits:
        title = hit.get("title", "")
        points = hit.get("points", 0)
        num_comments = hit.get("num_comments", 0)
        story_url = hit.get("url") or f"https://news.ycombinator.com/item?id={hit.get('objectID')}"
        created_at = (hit.get("created_at") or "")[:10]

        # 过滤
        title_lower = title.lower()
        if not any(k in title_lower for k in ["ai", "tool", "local", "mac", "agent", "model", "llm", "cli", "open source"]):
            continue

        raw_name = title.replace("Show HN: ", "").replace("Show HN - ", "").strip()
        norm_key = _normalize_repo_key(story_url) or raw_name.lower()
        if norm_key in seen or norm_key in excluded_keys:
            continue
        seen.add(norm_key)

        heat_badge = f"💬 HN热议 {points}分 / {num_comments}条深度讨论"
        results.append({
            "source": "Hacker News (Show HN)",
            "name": raw_name,
            "title": title,
            "url": story_url,
            "stars": points,
            "days_old": 7,
            "velocity": round(points / 7, 1),
            "heat_badge": heat_badge,
            "growth_level": "社区热议",
            "language": "Community",
            "updated_at": created_at,
            "topics": ["show-hn", "developer-tool"],
            "summary": f"HN {points} 分热门讨论，{num_comments} 条深度评价",
            "value_tags": ["community-approved"],
            "score": points * 8 + num_comments * 12 + 100,
        })
    return results


def _safe_parse_topics_json(text: str) -> list[dict]:
    """安全解析 LLM 返回的 JSON 列表。"""
    cleaned = text.strip()
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, re.DOTALL)
    if match:
        cleaned = match.group(1).strip()
    else:
        m2 = re.search(r"(\{.*\})", cleaned, re.DOTALL)
        if m2:
            cleaned = m2.group(1).strip()
    try:
        obj = json.loads(cleaned)
        if isinstance(obj, dict) and "topics" in obj and isinstance(obj["topics"], list):
            return obj["topics"]
        if isinstance(obj, list):
            return obj
    except Exception:
        pass
    return []


def refine_skills_with_llm(raw_items: list[dict], limit: int = 5) -> list[dict]:
    """利用 LLM 将原始高热度 Skill 元数据提炼为具有强大受众痛点吸引力的爆款选题。"""
    if not raw_items:
        return []

    try:
        from server import llm
    except Exception:
        llm = None

    corpus_lines = []
    for i, it in enumerate(raw_items[:15], 1):
        corpus_lines.append(
            f"{i}. [{it['source']}] 项目: {it['name']} ({it.get('heat_badge')})\n"
            f"   链接: {it.get('url')}\n"
            f"   客观热度: 总星数 {it.get('stars')} | 诞生天数: {it.get('days_old')}天 | 日均涨星: +{it.get('velocity')}★/天 | 评级: {it.get('growth_level')}\n"
            f"   功能简介: {it.get('summary')}\n"
            f"   标签: {', '.join(it.get('topics', []))}"
        )
    corpus_text = "\n\n".join(corpus_lines)

    system_prompt = """你是一位精通开源生产力工具、Agent 技能与 AI 效率神作的技术主编。
你的任务是从候选开源 Skill / Agent 插件列表中，挑选出对读者【真正有帮助、开箱即用、能平替付费或翻倍提效】的爆款公众号选题。

【爆款法则与价值标准】：
1. 痛点为王：突出【年省上千元/平替每月几十刀付费软件】、【端侧离线/物理级隐私保密】或【拯救繁琐手工/一行命令自动化】；
2. 呈现客观热度：充分利用给出的客观热度数据（如上线仅X天破千星、日均+40★、HN热议等）打造可信且引人瞩目的爆款标题；
3. 内容方向统一固定为：“实用Skill/效率神器”；
4. 流量预估字段：严格依据客观热度评级填入具体的量化评级（如“新星黑马”、“极高爆发”、“高关注度”），切勿泛泛而谈；
5. 备注字段规范：必须结构化包含【开源地址: https://... | 热度: ...】核心痛点：...；适合打工人/开发者，开箱即用。

【输出格式要求】：
严格输出标准的 JSON 对象（包含 topics 列表），结构如下：
{
  "topics": [
    {
      "选题标题": "吸引人的爆款中文标题（如：诞生仅15天破千星！开源神级工具彻底解放双手... 或 月省30刀！平替大厂付费工具...）",
      "关键词": "关键词1, 关键词2, 关键词3",
      "内容方向": "实用Skill/效率神器",
      "流量预估": "新星黑马",
      "备注": "【开源地址: https://github.com/... | 热度: ⚡ 诞生仅15天破1200★ (日均+80★)】核心痛点：端侧离线/平替...；适合打工人/开发者，开箱即用。"
    }
  ]
}
"""

    user_prompt = f"请从以下最新高热度实用 Skill 中，精选提炼出 {limit} 个最能引发打工人和开发者疯狂收藏转发的深度选题：\n\n{corpus_text}"

    if llm and config.get("LLM_API_KEY"):
        try:
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ]
            raw_output = llm.chat(messages, temperature=0.7, max_tokens=4096, json_mode=True)
            parsed = _safe_parse_topics_json(raw_output)
            if parsed:
                return parsed[:limit]
        except Exception as exc:
            _safe_warn(f"[Warn] LLM 提炼 Skill 选题异常，回退为规则提炼: {exc}")

    # 规则兜底降级提炼
    fallback = []
    for it in raw_items[:limit]:
        name = it["name"]
        stars = it.get("stars", 0)
        url = it.get("url", "")
        summary = it.get("summary", "")
        heat_badge = it.get("heat_badge", f"Stars: {stars}")
        growth_level = it.get("growth_level", "高关注度")

        fallback.append({
            "选题标题": f"{heat_badge} 开源神级 Skill 彻底解放双手：{name.split('/')[-1]} 实测",
            "关键词": f"AI Skill, 开源神器, {it.get('language') or '效率工具'}",
            "内容方向": "实用Skill/效率神器",
            "流量预估": growth_level,
            "备注": f"【开源地址：{url} | 热度：{heat_badge}】简介：{summary[:120]}。端侧提效，开箱即用。"
        })
    return fallback


def fetch_hot_skills_topics(limit: int = 5) -> list[dict]:
    """主入口：历史去重 -> 动态热度扫描 (GitHub + HN) -> 综合打分 -> 提炼爆款选题 -> 自动持久化本地历史。"""
    # 1. 加载历史排除名单（飞书选题库 + 本地已发记录）
    _safe_warn(">> [1/4] 正在加载飞书多维表格与本地历史选题库，执行去重校准...")
    excluded_keys, local_cnt, feishu_cnt = load_excluded_repo_keys()
    _safe_warn(f"   已加载历史排除指纹 {len(excluded_keys)} 个 (本地历史: {local_cnt}, 飞书表格: {feishu_cnt})")

    # 2. 针对性抓取 GitHub 高增速新星与活跃实战工具
    _safe_warn(">> [2/4] 正在根据动态时间窗口与日均涨星速率扫描 GitHub Agent / MCP Skills...")
    gh_items = get_github_trending_skills(limit_per_query=10, excluded_keys=excluded_keys)
    _safe_warn(f"   找到 {len(gh_items)} 个未重复的高潜力开源 Skill 仓库")

    # 3. 扫描 Hacker News Show HN 开发者口碑神器
    _safe_warn(">> [3/4] 正在扫描 Hacker News 开发者实用工具深度讨论帖...")
    hn_items = get_show_hn_tools(limit=10, excluded_keys=excluded_keys)
    _safe_warn(f"   找到 {len(hn_items)} 个未重复的社区口碑工具")

    combined = gh_items + hn_items
    if not combined:
        _safe_warn("[Warn] 未检索到新的未处理 Skill 候选。")
        return []

    # 4. 综合去重与按增速打分排序
    seen = set()
    unique_items = []
    for it in combined:
        key = _normalize_repo_key(it.get("url") or it.get("name") or "")
        if key not in seen:
            seen.add(key)
            unique_items.append(it)

    unique_items.sort(key=lambda x: x.get("score", 0), reverse=True)

    _safe_warn(f">> [4/4] 正在结合受众核心痛点与客观热度指标提炼前 {limit} 个爆款选题...")
    topics = refine_skills_with_llm(unique_items, limit=limit)

    # 5. 将本轮选中的项目持久化保存至本地扫描历史，防止后续再次重复
    if topics:
        save_scanned_skills_to_history(topics, raw_items=unique_items)

    return topics


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="扫描高热度、真好用的实用 AI Skill 并提炼选题")
    parser.add_argument("--limit", type=int, default=5, help="返回的选题数量")
    parser.add_argument("--add-feishu", action="store_true", help="是否直接写入飞书选题库")
    args = parser.parse_args()

    results = fetch_hot_skills_topics(limit=args.limit)
    print(json.dumps(results, ensure_ascii=False, indent=2))

    if args.add_feishu and results:
        client = feishu_btable.FeishuBitable()
        recs = client.add_records(results, status="待定")
        print(f"已成功写入飞书选题库 {len(recs)} 条记录！")
