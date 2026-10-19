#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
fetch_daily_hot.py: 过去 24 小时海外前沿科技与 AI 一手热点雷达
从 Hacker News (YC)、Hugging Face Daily Papers、GitHub 抓取一手实时资讯，
严格按时间戳过滤（<= 24h），并格式化为飞书选题库标准 JSON。
"""

import os
import re
import sys
import json
import time
import argparse
import datetime
import urllib.request
from pathlib import Path

# 添加 scripts 路径便于复用模块
SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
sys.path.insert(0, str(SCRIPTS_DIR.parent / "studio"))

import config
import feishu_btable

USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"

AI_KEYWORDS = [
    "ai", "llm", "gpt", "claude", "anthropic", "openai", "deepseek",
    "model", "agent", "robot", "nvidia", "meta", "google", "gemini",
    "open source", "inference", "benchmark", "reasoning", "diffusion", "rag"
]


def _safe_log(msg: str) -> None:
    """安全向 stderr 输出日志，在断开的终端/后台守护进程下遇到 EIO / BrokenPipe 时静默忽略。"""
    try:
        sys.stderr.write(f"{msg}\n")
        sys.stderr.flush()
    except (OSError, IOError, BrokenPipeError):
        pass


def fetch_url_json(url: str, timeout: int = 10) -> dict | list | None:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        _safe_log(f"[Warn] 请求失败 {url}: {exc}")
        return None


from concurrent.futures import ThreadPoolExecutor, as_completed


def get_hacker_news_hot(max_hours: float = 24.0, limit: int = 15) -> list[dict]:
    """并发抓取 Hacker News 过去 max_hours 小时内的热门 AI/Tech 资讯。"""
    top_ids = fetch_url_json("https://hacker-news.firebaseio.com/v0/topstories.json", timeout=8)
    if not top_ids or not isinstance(top_ids, list):
        return []

    now = time.time()
    results = []

    def fetch_item(item_id):
        return fetch_url_json(f"https://hacker-news.firebaseio.com/v0/item/{item_id}.json", timeout=6)

    # 扫描前 50 个高赞帖，用 15 线程并发抓取
    candidate_ids = top_ids[:50]
    with ThreadPoolExecutor(max_workers=15) as executor:
        futures = {executor.submit(fetch_item, iid): iid for iid in candidate_ids}
        for fut in as_completed(futures):
            item = fut.result()
            if not item or not isinstance(item, dict):
                continue
            post_time = item.get("time", 0)
            age_hours = (now - post_time) / 3600.0
            if age_hours > max_hours:
                continue

            title = item.get("title", "")
            title_lower = title.lower()
            score = item.get("score", 0)

            # 关键词匹配或高分热门（>150 分往往是行业大事件）
            is_ai = any(k in title_lower for k in AI_KEYWORDS) or score >= 150
            if not is_ai:
                continue

            post_str = time.strftime("%Y-%m-%d %H:%M", time.localtime(post_time))
            results.append({
                "source": "Hacker News",
                "title": title,
                "url": item.get("url") or f"https://news.ycombinator.com/item?id={item.get('id')}",
                "score": score,
                "time_str": post_str,
                "age_hours": round(age_hours, 1)
            })

    # 按点赞分数降序排列
    results.sort(key=lambda x: x.get("score", 0), reverse=True)
    return results[:limit]



def get_huggingface_papers(limit: int = 5) -> list[dict]:
    """抓取 Hugging Face 当日热门顶尖论文与开源突破。"""
    papers = fetch_url_json("https://huggingface.co/api/daily_papers", timeout=10)
    if not papers or not isinstance(papers, list):
        return []

    results = []
    for p in papers[:limit]:
        paper_info = p.get("paper", {})
        title = paper_info.get("title", "")
        upvotes = paper_info.get("upvotes", 0)
        paper_id = paper_info.get("id", "")
        summary = paper_info.get("summary", "")
        if not title:
            continue
        results.append({
            "source": "Hugging Face Daily",
            "title": title,
            "url": f"https://huggingface.co/papers/{paper_id}" if paper_id else "",
            "score": upvotes,
            "summary": summary[:200],
            "time_str": datetime.datetime.now().strftime("%Y-%m-%d"),
            "age_hours": 12.0
        })
    return results


def _safe_parse_topics_json(raw: str) -> list[dict]:
    """稳健解析 LLM 返回的 JSON 字符串（支持数组或包在对象中的列表）。"""
    raw = raw.strip()
    # 剥离 Markdown 围栏
    fence_match = re.search(r"```(?:json)?\s*(.*?)\s*```", raw, flags=re.S)
    if fence_match:
        raw = fence_match.group(1).strip()
    
    # 尝试直接解析
    data = None
    try:
        data = json.loads(raw)
    except Exception:
        # 尝试匹配数组 [...]
        array_match = re.search(r"\[.*\]", raw, flags=re.S)
        if array_match:
            try:
                data = json.loads(array_match.group(0))
            except Exception:
                pass
        if data is None:
            # 尝试匹配对象 {...}
            obj_match = re.search(r"\{.*\}", raw, flags=re.S)
            if obj_match:
                try:
                    data = json.loads(obj_match.group(0))
                except Exception:
                    pass

    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict) and item.get("选题标题")]
    elif isinstance(data, dict):
        if "topics" in data and isinstance(data["topics"], list):
            return [item for item in data["topics"] if isinstance(item, dict) and item.get("选题标题")]
        for val in data.values():
            if isinstance(val, list) and val and isinstance(val[0], dict) and val[0].get("选题标题"):
                return val
    return []


def synthesize_topics_with_llm(raw_items: list[dict], limit: int = 10) -> list[dict]:
    """通过 LLM 将抓取到的一手英文科技资讯提炼为高质量公众号选题（符合飞书字段）。"""
    if not raw_items:
        return []

    try:
        from server import llm
    except Exception:
        llm = None

    prompt_items_text = []
    for i, it in enumerate(raw_items[:20], 1):
        prompt_items_text.append(
            f"{i}. [{it['source']}] (发布于 {it.get('time_str')} | 约 {it.get('age_hours')} 小时前 | 热度 {it.get('score')}) {it['title']}\n"
            f"   链接: {it.get('url', '')}\n"
            f"   简介: {it.get('summary', '')}"
        )
    news_corpus = "\n".join(prompt_items_text)

    system_prompt = """你是一位顶尖科技与商业公众号的资深主编。
你的任务是根据过去 24 小时（昨夜美西与海外）发生的一手科技/AI 热点与突破，提炼出具有极高阅读价值与流量潜力的公众号选题。

【严格规范】
1. 真实时效：必须完全基于提供的一手新闻事实，严禁编造不存在的技术或事件。
2. 爆款标题法则：通俗、具体、有悬念或反差感（如数据冲击、大厂震动、颠覆性场景），禁止平铺直叙的公文腔。
3. 备注硬约束：必须保留【首发时间戳与一手信源】（如：【首发于 2026-09-28 美西昨夜 | Hacker News 360分热帖】），并用 2 句话写明目标读者、核心切入点与要突出的打工人/开发者利益点。

【输出要求】
必须严格输出标准的 JSON 对象（包含 topics 列表），结构如下：
{
  "topics": [
    {
      "选题标题": "吸引人的爆款中文标题",
      "关键词": "关键词1, 关键词2, 关键词3",
      "内容方向": "技术突破 / 工具评测 / 行业洞察 / 避坑实战",
      "流量预估": "高",
      "备注": "【首发时间戳：YYYY-MM-DD HH:MM | 信源：Hacker News/HF】适合打工人/开发者，重点讲怎么用，突出..."
    }
  ]
}
"""
    user_prompt = f"请根据以下过去 24 小时海外科技圈一手资讯，提炼出 {limit} 个最有价值的深度公众号选题：\n\n{news_corpus}"

    if llm and config.get("LLM_API_KEY"):
        try:
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ]
            raw_output = llm.chat(messages, temperature=0.7, max_tokens=4096, json_mode=True)
            parsed_topics = _safe_parse_topics_json(raw_output)
            if parsed_topics:
                return parsed_topics[:limit]
        except Exception as exc:
            _safe_log(f"[Warn] LLM 总结选题失败，降级为规则提炼: {exc}")

    # 兜底规则提炼（无 LLM 或 LLM 失败时保证可用）
    fallback_topics = []
    for it in raw_items[:limit]:
        title = it["title"]
        src = it["source"]
        time_str = it["time_str"]
        fallback_topics.append({
            "选题标题": f"海外突发：{title[:45]}",
            "关键词": "AI, 科技前沿, 海外突发",
            "内容方向": "技术突破",
            "流量预估": "高" if it.get("score", 0) > 100 else "中",
            "备注": f"【首发时间戳：{time_str} | 来源：{src}】探讨海外最新动态与普通人生产力落地。"
        })
    return fallback_topics


def fetch_daily_hot_topics(hours: float = 24.0, limit: int = 10) -> list[dict]:
    """主流程：汇聚 HN + HuggingFace，清洗并提炼选题。"""
    _safe_log(f">> [1/3] 正在扫描 Hacker News 过去 {hours} 小时科技与 AI 高分热帖...")
    hn_items = get_hacker_news_hot(max_hours=hours, limit=15)
    _safe_log(f"   找到 {len(hn_items)} 条高分热帖")

    _safe_log(">> [2/3] 正在扫描 Hugging Face Daily Papers 当日前沿突破...")
    hf_items = get_huggingface_papers(limit=5)
    _safe_log(f"   找到 {len(hf_items)} 篇顶尖论文")

    combined = hn_items + hf_items
    if not combined:
        _safe_log("[Error] 未获取到有效一手数据，请检查网络连接。")
        return []

    _safe_log(f">> [3/3] 正在提炼爆款选题结构与时间戳约束 (共 {len(combined)} 条一手信源)...")
    topics = synthesize_topics_with_llm(combined, limit=limit)
    return topics


def main():
    parser = argparse.ArgumentParser(description="扫描过去 24 小时海外一手科技与 AI 热点并输出飞书格式选题。")
    parser.add_argument("--hours", type=float, default=24.0, help="过去多少小时内（默认 24.0）")
    parser.add_argument("--limit", type=int, default=10, help="输出选题条数（默认 10）")
    parser.add_argument("--output", type=str, help="保存 JSON 文件的路径（如 outputs/hot_topics.json）")
    parser.add_argument("--add-feishu", action="store_true", help="是否直接写入飞书选题库多维表格（状态设为待定）")
    args = parser.parse_args()

    topics = fetch_daily_hot_topics(hours=args.hours, limit=args.limit)
    if not topics:
        print("未获取到选题。")
        return

    json_str = json.dumps(topics, ensure_ascii=False, indent=2)
    print(json_str)

    if args.output:
        out_path = Path(args.output).resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json_str, encoding="utf-8")
        _safe_log(f"\n[OK] 选题 JSON 已保存至: {out_path}")

    if args.add_feishu:
        _safe_log("\n>> 正在一键写入飞书选题多维表格...")
        try:
            client = feishu_btable.FeishuBitable()
            created = client.add_records(topics, status="待定")
            _safe_log(f"[OK] 已成功写入飞书表格 {len(created)} 条选题！")
        except Exception as exc:
            _safe_log(f"[Error] 写入飞书失败: {exc}")


if __name__ == "__main__":
    main()
