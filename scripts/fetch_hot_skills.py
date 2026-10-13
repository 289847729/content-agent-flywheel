#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
fetch_hot_skills.py: 高热度、真好用的 AI Skill / Agent 技能 / 效率神器雷达。
数据源：
1. GitHub 实时高增速技能仓库 (topic:mcp-server, agent-skills, claude-code, ai-tools, local-ai)
2. Hacker News "Show HN" 开发者硬核工具口碑帖
3. 真实落地价值过滤 (平替付费/端侧离线/本地隐私/开箱即用)
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
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
sys.path.insert(0, str(SCRIPTS_DIR.parent / "studio"))

import config
import feishu_btable

USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"

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
    except Exception:
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


def get_github_trending_skills(limit_per_query: int = 10) -> list[dict]:
    """从 GitHub API 针对性检索高增速、真有代码的 MCP 与 Agent Skill 仓库。"""
    queries = [
        "topic:mcp-server stars:>80 pushed:>2026-08-01",
        "mcp-server OR agent-skill stars:>80 pushed:>2026-08-01",
        "claude-skill OR claude-code stars:>60 pushed:>2026-08-01",
        "ai-tools local OR offline OR whisper OR scraper stars:>150 pushed:>2026-08-01",
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
            if not full_name or full_name in seen_repos:
                continue
            seen_repos.add(full_name)

            desc = (item.get("description") or "").strip()
            name = item.get("name", "")
            language = item.get("language") or ""
            stars = item.get("stargazers_count", 0)
            topics = item.get("topics") or []
            pushed_at = item.get("pushed_at", "")[:10]

            # 过滤排除纯文本收集列表
            name_lower = name.lower()
            desc_lower = desc.lower()
            if any(k in name_lower or k in desc_lower for k in EXCLUDE_KEYWORDS):
                continue

            # 语言要求：必须具备可执行的代码
            if not language and item.get("size", 0) < 50:
                continue

            # 计算实用价值得分
            value_hits = [k for k in VALUE_KEYWORDS if k in desc_lower or k in topics or k in name_lower]
            candidates.append({
                "source": "GitHub Skill",
                "name": full_name,
                "title": f"{name}: {desc[:100]}",
                "url": item.get("html_url", ""),
                "stars": stars,
                "language": language,
                "updated_at": pushed_at,
                "topics": topics,
                "summary": desc,
                "value_tags": value_hits,
                "score": stars + len(value_hits) * 100,
            })

    # 按综合生产力得分与热度排序
    candidates.sort(key=lambda x: x["score"], reverse=True)
    return candidates


def get_show_hn_tools(limit: int = 10) -> list[dict]:
    """从 Hacker News 获取近期热门的 Show HN 开源效率神器。"""
    url = "https://hn.algolia.com/api/v1/search?query=Show%20HN%20AI%20OR%20Tool%20OR%20Local&tags=show_hn&numericFilters=points>40&hitsPerPage=15"
    data = fetch_url_json(url, timeout=10)
    if not data or not isinstance(data, dict):
        return []

    hits = data.get("hits") or []
    results = []
    for hit in hits:
        title = hit.get("title", "")
        points = hit.get("points", 0)
        num_comments = hit.get("num_comments", 0)
        story_url = hit.get("url") or f"https://news.ycombinator.com/item?id={hit.get('objectID')}"
        created_at = hit.get("created_at", "")[:10]

        # 过滤
        title_lower = title.lower()
        if not any(k in title_lower for k in ["ai", "tool", "local", "mac", "agent", "model", "llm", "cli", "open source"]):
            continue

        results.append({
            "source": "Hacker News (Show HN)",
            "name": title.replace("Show HN: ", "").strip(),
            "title": title,
            "url": story_url,
            "stars": points,
            "language": "Community",
            "updated_at": created_at,
            "topics": ["show-hn", "developer-tool"],
            "summary": f"HN {points} 分热门讨论，{num_comments} 条深度评价",
            "value_tags": ["community-approved"],
            "score": points * 10 + num_comments * 5,
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
    """利用 LLM 将原始高星 Skill 元数据提炼为具有强大受众痛点吸引力的公众号爆款选题。"""
    if not raw_items:
        return []

    try:
        from server import llm
    except Exception:
        llm = None

    corpus_lines = []
    for i, it in enumerate(raw_items[:15], 1):
        corpus_lines.append(
            f"{i}. [{it['source']}] 项目: {it['name']} (Stars/热度: {it.get('stars')} | 语言: {it.get('language')} | 更新: {it.get('updated_at')})\n"
            f"   链接: {it.get('url')}\n"
            f"   功能简介: {it.get('summary')}\n"
            f"   标签: {', '.join(it.get('topics', []))}"
        )
    corpus_text = "\n\n".join(corpus_lines)

    system_prompt = """你是一位精通开源生产力工具、Agent 技能与 AI 效率神作的技术主编。
你的任务是从候选开源 Skill / Agent 插件列表中，挑选出对读者【真正有帮助、开箱即用、能平替付费或翻倍提效】的爆款公众号选题。

【爆款法则与价值标准】：
1. 痛点为王：突出【年省上千元/平替每月几十刀付费软件】、【端侧离线/物理级隐私保密】或【拯救繁琐手工/一行命令自动化】；
2. 拒绝空洞玩具：必须基于真实开源项目与硬核代码，讲清“打工人/程序员如何直接爽用”；
3. 内容方向统一固定为：“实用Skill/效率神器”；
4. 备注字段规范：必须包含【开源项目与热度】、【核心平替/解决痛点】、【上手门槛】与受众定位。

【输出格式要求】：
严格输出标准的 JSON 对象（包含 topics 列表），结构如下：
{
  "topics": [
    {
      "选题标题": "吸引人的爆款中文标题（如：月省30刀！平替大厂付费工具的开源神器...）",
      "关键词": "关键词1, 关键词2, 关键词3",
      "内容方向": "实用Skill/效率神器",
      "流量预估": "高",
      "备注": "【开源地址: https://github.com/... | Stars: 15k】核心痛点：端侧离线/平替...；适合打工人/开发者，开箱即用。"
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
        fallback.append({
            "选题标题": f"斩获 {stars} 星！开源神级 Skill 彻底解放双手：{name.split('/')[-1]} 实测",
            "关键词": f"AI Skill, 开源神器, {it.get('language') or '效率工具'}",
            "内容方向": "实用Skill/效率神器",
            "流量预估": "高" if stars > 500 else "中",
            "备注": f"【开源地址：{url} | Stars: {stars}】简介：{summary[:120]}。端侧提效，开箱即用。"
        })
    return fallback


def fetch_hot_skills_topics(limit: int = 5) -> list[dict]:
    """主入口：扫描 GitHub + Hacker News 中的高热度实用 Skill，并提炼爆款选题。"""
    _safe_warn(">> [1/2] 正在扫描 GitHub 高增速 MCP Server 与 Agent Skills...")
    gh_items = get_github_trending_skills(limit_per_query=10)
    _safe_warn(f"   找到 {len(gh_items)} 个开源实战 Skill 仓库")

    _safe_warn(">> [2/2] 正在扫描 Hacker News 开发者实用工具高分贴...")
    hn_items = get_show_hn_tools(limit=10)
    _safe_warn(f"   找到 {len(hn_items)} 个社区高分工具")

    combined = gh_items + hn_items
    if not combined:
        return []

    # 综合去重与排序
    seen = set()
    unique_items = []
    for it in combined:
        key = it.get("name", "")
        if key not in seen:
            seen.add(key)
            unique_items.append(it)

    unique_items.sort(key=lambda x: x.get("score", 0), reverse=True)

    _safe_warn(">> [3/3] 正在结合受众核心痛点提炼爆款选题...")
    return refine_skills_with_llm(unique_items, limit=limit)


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
