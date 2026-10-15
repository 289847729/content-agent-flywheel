#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
fetch_aihot.py: aihot.news 热门 AI 榜单采集与智能提炼
从 aihot.news 官方 API 抓取当前热度最高的 Top N 科技事件，
获取其多源交叉核验的来龙去脉，并转换为飞书/系统选题库的标准格式。
"""

import sys
import json
import re
import urllib.request
import datetime
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

SCRIPTS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPTS_DIR.parent
sys.path.insert(0, str(SCRIPTS_DIR))
sys.path.insert(0, str(PROJECT_ROOT))

USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"


def _fetch_url_json(url: str, timeout: int = 10) -> dict | None:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        print(f"[Warn] 获取 URL JSON 失败 {url}: {exc}", file=sys.stderr)
        return None


def _fetch_url_text(url: str, timeout: int = 10) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8")
    except Exception as exc:
        print(f"[Warn] 获取 URL 文本失败 {url}: {exc}", file=sys.stderr)
        return ""


def _extract_story_id(story_url: str) -> str:
    if not story_url:
        return ""
    # 形如 https://aihot.virxact.com/story/b9d1e480-fac1-479a-9209-5241ad001528
    parts = story_url.rstrip("/").split("/")
    return parts[-1] if parts else ""


def _extract_summary(story_text: str) -> str:
    if not story_text:
        return ""
    match = re.search(r"事件综述[：:](.*?)(?=\n\n|\n报道时间线|\n相关事件|\Z)", story_text, re.S)
    if match:
        return match.group(1).strip()
    return ""


def _refine_title_and_tags(rank: int, raw_title: str, summary: str) -> tuple[str, str, str]:
    """尝试使用本地 LLM 提炼更具爆款潜质的标题和标签；失败时优雅降级。"""
    default_title = raw_title.strip()
    # 默认分类判定
    if any(k in default_title.lower() for k in ["发布", "上线", "模型", "gemini", "gpt", "claude", "grok", "deepseek"]):
        category = "大模型动态"
    elif any(k in default_title.lower() for k in ["开源", "自研", "git", "代码", "agent"]):
        category = "技术突破"
    else:
        category = "行业洞察"

    # 默认关键词
    words = re.findall(r"[\u4e00-\u9fa5]{2,}|[a-zA-Z0-9\.\_\-\+]{3,}", default_title)
    keywords = ", ".join(list(dict.fromkeys(words))[:5]) or "AI热点, 行业前沿"

    try:
        from studio.server import llm
        prompt = f"""你是一位擅长科技新媒体选题的资深总监。请根据以下热点事件，提炼一个符合今日头条与微信公众号传播的爆款选题标题（20~28字之间，绝不超过30字，具有悬念或反差感），并提取4个核心关键词。

【热点原标题】：{raw_title}
【事件综述】：{summary[:800]}

请输出纯 JSON：
{{
  "title": "20~28字爆款标题",
  "keywords": "关键词1, 关键词2, 关键词3, 关键词4",
  "category": "{category}"
}}
"""
        messages = [
            {"role": "system", "content": "你是一位专业的科技新媒体主编。"},
            {"role": "user", "content": prompt}
        ]
        raw_resp = llm.chat(messages, max_tokens=300, temperature=0.7, json_mode=True)
        data = llm.parse_json_object(raw_resp)
        if data and data.get("title"):
            cand_title = llm.normalize_toutiao_title(data["title"], max_chars=30)
            cand_kw = data.get("keywords") or keywords
            cand_cat = data.get("category") or category
            return cand_title, cand_kw, cand_cat
    except Exception as exc:
        print(f"[Warn] LLM 提炼标题降级: {exc}", file=sys.stderr)

    return default_title, keywords, category


def fetch_aihot_top_topics(limit: int = 3) -> list[dict]:
    """抓取 aihot.news 热门榜单前 N 条事件，并构造成标准选题字典列表。"""
    hot_data = _fetch_url_json("https://aihot.news/api/v1/hot-topics", timeout=12)
    if not hot_data or not isinstance(hot_data.get("items"), list):
        raise ValueError("未能从 aihot.news 获取到热点榜单数据，请检查网络或站点状态")

    raw_items = hot_data["items"][:limit]
    if not raw_items:
        return []

    today_str = datetime.date.today().isoformat()

    def process_item(item, rank):
        story_url = item.get("links", {}).get("story", "")
        story_id = _extract_story_id(story_url)
        story_text = ""
        if story_id:
            story_text = _fetch_url_text(f"https://aihot.news/api/v1/agent/stories/{story_id}", timeout=10)

        raw_title = item.get("title", "").strip()
        summary = _extract_summary(story_text) or raw_title
        original_url = item.get("links", {}).get("original", "")
        source_name = item.get("source", {}).get("name", "多方信源")
        participants = item.get("participantCount", 0)
        sources_count = item.get("sourceCount", 0)

        # 智能润色标题与标签
        title, keywords, category = _refine_title_and_tags(rank, raw_title, summary)

        remark_lines = [
            f"【AIHOT热点榜 Top {rank} | 信源数: {sources_count} | 讨论数: {participants}】",
            f"原始链接: {original_url}",
            f"主信源: {source_name}",
            f"核心事实: {summary[:500]}"
        ]
        remark = "\n".join(remark_lines)

        return {
            "_rank": rank,
            "选题标题": title,
            "关键词": keywords,
            "内容方向": category,
            "流量预估": "高",
            "状态": "待定",
            "入库日期": today_str,
            "备注": remark
        }

    topics = []
    with ThreadPoolExecutor(max_workers=min(len(raw_items), 5)) as pool:
        futures = [pool.submit(process_item, it, idx + 1) for idx, it in enumerate(raw_items)]
        for f in futures:
            try:
                topics.append(f.result())
            except Exception as e:
                print(f"[Warn] 处理热点事件异常: {e}", file=sys.stderr)

    # 保持原榜单排名顺序
    topics.sort(key=lambda t: t.get("_rank", 0))
    for t in topics:
        t.pop("_rank", None)
    return topics


if __name__ == "__main__":
    count = 3
    if len(sys.argv) > 1:
        count = int(sys.argv[1])
    print(f"正在抓取 aihot.news 热点榜 Top {count}...")
    res = fetch_aihot_top_topics(limit=count)
    print(json.dumps(res, ensure_ascii=False, indent=2))
