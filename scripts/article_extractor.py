"""文章与推文高保真解析与原图下载器。

支持：
1. X (Twitter) 推文：提取博主原文案、发布者、时间，以及推文包含的全部高清原图（name=orig）；
2. 微信公众号文章：突破防盗链下载所有正文插图，保留结构化 Markdown；
3. 通用网页链接：提取标题、正文与图片。

所有内容下载并标准化落地到 outputs/runs/{timestamp}-tweet-{id}/ 目录，
完美对接系统的多平台生成（小红书多图轮播图集、今日头条长图文+DOCX）与 Playwright 自动化发布通道。
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT_DIR = Path(__file__).resolve().parents[1]
OUTPUTS_DIR = ROOT_DIR / "outputs"
RUNS_DIR = OUTPUTS_DIR / "runs"

HEADERS_DEFAULT = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

TWEET_URL_PATTERN = re.compile(
    r"(?:https?://)?(?:www\.)?(?:twitter\.com|x\.com)/(?P<user>[a-zA-Z0-9_]+)/status/(?P<id>\d+)",
    re.IGNORECASE,
)
WECHAT_URL_PATTERN = re.compile(r"mp\.weixin\.qq\.com/s", re.IGNORECASE)


def sanitize_filename(name: str) -> str:
    name = re.sub(r'[\\/:*?"<>|\r\n]+', "-", name.strip()).strip().strip(".")
    return name or "未命名"


def download_file(url: str, save_path: Path, referer: Optional[str] = None, timeout: int = 20) -> bool:
    """下载单个文件/图片到本地。"""
    headers = dict(HEADERS_DEFAULT)
    if referer:
        headers["Referer"] = referer

    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            save_path.parent.mkdir(parents=True, exist_ok=True)
            with open(save_path, "wb") as f:
                while chunk := resp.read(65536):
                    f.write(chunk)
        return True
    except Exception as e:
        print(f"[article_extractor] 下载文件失败 {url} -> {save_path}: {e}", file=sys.stderr)
        return False


# ==========================================
# 1. X (Twitter) 推文解析模块
# ==========================================

def extract_tweet_via_api(username: str, tweet_id: str) -> Optional[Dict[str, Any]]:
    """通过公开 API (FixupX / FxTwitter / VxTwitter) 极速提取推文数据。"""
    endpoints = [
        f"https://api.fxtwitter.com/{username}/status/{tweet_id}",
        f"https://api.fxtwitter.com/status/{tweet_id}",
        f"https://api.vxtwitter.com/{username}/status/{tweet_id}",
    ]

    for api_url in endpoints:
        try:
            req = urllib.request.Request(api_url, headers=HEADERS_DEFAULT)
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                
                # FxTwitter 格式
                tweet = data.get("tweet")
                if tweet:
                    author = tweet.get("author", {})
                    author_name = author.get("name") or username
                    screen_name = author.get("screen_name") or username
                    raw_text = tweet.get("text", "").strip()
                    # 剔除末尾由推特自动生成的媒体链接 t.co
                    cleaned_text = re.sub(r"https://t\.co/[a-zA-Z0-9]+$", "", raw_text).strip()

                    # 提取全部高清原图
                    media_list = tweet.get("media", {}).get("photos", []) or []
                    image_urls = []
                    for m in media_list:
                        img_url = m.get("url")
                        if img_url:
                            if "pbs.twimg.com/media" in img_url:
                                parsed = urllib.parse.urlparse(img_url)
                                qs = urllib.parse.parse_qs(parsed.query)
                                qs["name"] = ["orig"]
                                new_query = urllib.parse.urlencode(qs, doseq=True)
                                img_url = urllib.parse.urlunparse(parsed._replace(query=new_query))
                            image_urls.append(img_url)

                    created_at = tweet.get("created_at", "")
                    return {
                        "platform": "x",
                        "tweet_id": tweet_id,
                        "username": screen_name,
                        "author_name": author_name,
                        "text": cleaned_text or raw_text,
                        "image_urls": image_urls,
                        "created_at": created_at,
                        "url": f"https://x.com/{screen_name}/status/{tweet_id}",
                    }
                
                # VxTwitter 格式兜底
                if "text" in data and "mediaURLs" in data:
                    raw_text = data.get("text", "").strip()
                    cleaned_text = re.sub(r"https://t\.co/[a-zA-Z0-9]+$", "", raw_text).strip()
                    author_name = data.get("user_name") or username
                    screen_name = data.get("user_screen_name") or username
                    media_urls = data.get("mediaURLs") or []
                    image_urls = []
                    for img_url in media_urls:
                        if "pbs.twimg.com/media" in img_url:
                            parsed = urllib.parse.urlparse(img_url)
                            qs = urllib.parse.parse_qs(parsed.query)
                            qs["name"] = ["orig"]
                            new_query = urllib.parse.urlencode(qs, doseq=True)
                            img_url = urllib.parse.urlunparse(parsed._replace(query=new_query))
                        image_urls.append(img_url)
                    return {
                        "platform": "x",
                        "tweet_id": tweet_id,
                        "username": screen_name,
                        "author_name": author_name,
                        "text": cleaned_text or raw_text,
                        "image_urls": image_urls,
                        "created_at": data.get("date", ""),
                        "url": f"https://x.com/{screen_name}/status/{tweet_id}",
                    }
        except Exception as e:
            print(f"[article_extractor] API {api_url} 请求失败: {e}", file=sys.stderr)
            continue

    return None


def extract_tweet_via_playwright(url: str) -> Dict[str, Any]:
    """通过 Playwright（连接已有 Chrome CDP 或无头模式）兜底渲染抓取推文。"""
    from playwright.sync_api import sync_playwright

    match = TWEET_URL_PATTERN.search(url)
    username = match.group("user") if match else "twitter_user"
    tweet_id = match.group("id") if match else str(int(time.time()))

    with sync_playwright() as p:
        browser = None
        cdp_used = False
        try:
            browser = p.chromium.connect_over_cdp("http://127.0.0.1:9222")
            context = browser.contexts[0] if browser.contexts else browser.new_context()
            page = context.new_page()
            cdp_used = True
        except Exception:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()

        try:
            page.goto(url, wait_until="domcontentloaded", timeout=25000)
            page.wait_for_selector('article[data-testid="tweet"], [data-testid="tweetText"]', timeout=15000)
            time.sleep(1.5)

            # 提取推文文字
            text_el = page.locator('[data-testid="tweetText"]').first
            text = text_el.inner_text() if text_el.is_visible() else ""

            # 提取作者名
            author_el = page.locator('[data-testid="User-Name"]').first
            author_text = author_el.inner_text() if author_el.is_visible() else username
            author_name = author_text.split("\n")[0] if author_text else username

            # 提取全部推文图片
            img_elements = page.locator('[data-testid="tweetPhoto"] img').all()
            image_urls = []
            for img in img_elements:
                src = img.get_attribute("src")
                if src and "pbs.twimg.com/media" in src:
                    # 替换为最高清参数 name=orig
                    parsed = urllib.parse.urlparse(src)
                    qs = urllib.parse.parse_qs(parsed.query)
                    qs["name"] = ["orig"]
                    new_query = urllib.parse.urlencode(qs, doseq=True)
                    src_orig = urllib.parse.urlunparse(parsed._replace(query=new_query))
                    if src_orig not in image_urls:
                        image_urls.append(src_orig)

            return {
                "platform": "x",
                "tweet_id": tweet_id,
                "username": username,
                "author_name": author_name,
                "text": text,
                "image_urls": image_urls,
                "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "url": url,
            }
        finally:
            try:
                page.close()
            except Exception:
                pass
            if not cdp_used and browser:
                browser.close()


def extract_tweet(url: str) -> Dict[str, Any]:
    """提取 X (Twitter) 推文的文案与图片（API 优先，Playwright 兜底）。"""
    match = TWEET_URL_PATTERN.search(url)
    if not match:
        raise ValueError(f"不是合法的 X (Twitter) 推文链接: {url}")

    username = match.group("user")
    tweet_id = match.group("id")

    # 1. 优先尝试轻量级公开 API
    data = extract_tweet_via_api(username, tweet_id)
    if data and data.get("text"):
        return data

    # 2. 兜底使用 Playwright
    return extract_tweet_via_playwright(url)


# ==========================================
# 2. 微信公众号文章解析模块
# ==========================================

class WechatHTMLParser(HTMLParser):
    """提取微信公众号标题、作者与正文结构，包含 data-src 图片。"""

    def __init__(self):
        super().__init__()
        self.title = ""
        self.author = ""
        self.in_title = False
        self.in_author = False
        self.in_content = False
        self.content_parts = []
        self.image_urls = []
        self.current_tag = ""

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        self.current_tag = tag

        # 标题检测
        if attrs_dict.get("id") == "activity-name" or attrs_dict.get("class") == "rich_media_title":
            self.in_title = True
        elif tag == "meta" and attrs_dict.get("property") == "og:title":
            if not self.title:
                self.title = attrs_dict.get("content", "").strip()

        # 作者检测
        if attrs_dict.get("id") == "js_name" or "rich_media_meta_text" in attrs_dict.get("class", ""):
            self.in_author = True
        elif tag == "meta" and attrs_dict.get("property") == "og:article:author":
            if not self.author:
                self.author = attrs_dict.get("content", "").strip()

        # 正文容器
        if attrs_dict.get("id") == "js_content":
            self.in_content = True

        # 正文内图片提取（微信特有 data-src）
        if self.in_content and tag == "img":
            src = attrs_dict.get("data-src") or attrs_dict.get("src")
            if src and src.startswith("http"):
                self.image_urls.append(src)
                img_idx = len(self.image_urls)
                self.content_parts.append(f"\n\n![文章配图{img_idx}]({src})\n\n")

        # 块级标签换行
        if self.in_content:
            if tag in ("p", "h1", "h2", "h3", "h4", "blockquote"):
                self.content_parts.append("\n\n")
            elif tag == "br":
                self.content_parts.append("\n")

    def handle_endtag(self, tag):
        if self.in_title and tag in ("h1", "h2", "div", "span"):
            self.in_title = False
        if self.in_author and tag in ("a", "span", "div"):
            self.in_author = False
        if self.in_content and tag == "div" and self.current_tag == "js_content":
            self.in_content = False

    def handle_data(self, data):
        text = data.strip()
        if not text:
            return
        if self.in_title and not self.title:
            self.title = text
        elif self.in_author and not self.author:
            self.author = text
        elif self.in_content:
            self.content_parts.append(data)


def extract_wechat_article(url: str) -> Dict[str, Any]:
    """提取微信公众号文章正文与防盗链图片列表。"""
    req = urllib.request.Request(url, headers=HEADERS_DEFAULT)
    with urllib.request.urlopen(req, timeout=15) as resp:
        html = resp.read().decode("utf-8", errors="ignore")

    parser = WechatHTMLParser()
    parser.feed(html)

    body_md = "".join(parser.content_parts).strip()
    return {
        "platform": "wechat",
        "url": url,
        "title": parser.title or "微信文章",
        "author_name": parser.author or "公众号作者",
        "text": body_md,
        "image_urls": parser.image_urls,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


# ==========================================
# 3. 统一入库管道：生成标准 Run 产物
# ==========================================

def import_article_to_run(url: str) -> Dict[str, Any]:
    """主入口：根据传入链接解析文案、下载全部原图并落地为标准 outputs/runs 记录。"""
    url = url.strip()
    if not url:
        raise ValueError("链接不能为空")

    # 1. 识别并提取
    if TWEET_URL_PATTERN.search(url):
        data = extract_tweet(url)
        slug_prefix = f"tweet-{data.get('tweet_id', 'post')}"
        source_name = f"@{data.get('username')}"
        title = f"{source_name} 推文：{data.get('text', '')[:25].strip()}..."
    elif WECHAT_URL_PATTERN.search(url):
        data = extract_wechat_article(url)
        slug_prefix = f"wechat-{sanitize_filename(data.get('title', 'article'))[:15]}"
        source_name = data.get("author_name") or "公众号"
        title = data.get("title", "微信文章")
    else:
        # 通用网页，尝试当成推文或微信提取
        data = extract_wechat_article(url)
        slug_prefix = "web-article"
        source_name = data.get("author_name", "外部网页")
        title = data.get("title", "导入文章")

    # 2. 创建标准 Run 目录 outputs/runs/{timestamp}-{slug}/
    timestamp = datetime.now().strftime("%Y-%m-%d-%H%M%S")
    run_dir_name = f"{timestamp}-{slug_prefix}"
    run_dir = RUNS_DIR / run_dir_name
    run_dir.mkdir(parents=True, exist_ok=True)
    images_dir = run_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)

    # 3. 下载全部图片至本地 images/ 目录
    image_urls = data.get("image_urls") or []
    downloaded_images: List[Dict[str, str]] = []

    is_wechat = data.get("platform") == "wechat"
    referer = "https://mp.weixin.qq.com/" if is_wechat else None

    for idx, img_url in enumerate(image_urls, start=1):
        # 确定后缀
        ext = ".jpg"
        if "format=png" in img_url or "wx_fmt=png" in img_url or img_url.endswith(".png"):
            ext = ".png"
        elif "wx_fmt=gif" in img_url or img_url.endswith(".gif"):
            ext = ".gif"

        img_filename = f"image_{idx}{ext}"
        img_path = images_dir / img_filename
        success = download_file(img_url, img_path, referer=referer)
        if success:
            downloaded_images.append({
                "index": idx,
                "filename": img_filename,
                "abs_path": str(img_path),
                "rel_path": f"images/{img_filename}",
                "original_url": img_url,
            })

    # 4. 组装标准 article.md（正文 + 本地配图引用）
    raw_text = data.get("text", "").strip()
    created_at = data.get("created_at") or datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    md_lines = [
        f"# {title}",
        "",
        f"> **来源平台**：{data.get('platform', '外部').upper()} | **博主/作者**：{source_name} | **发布时间**：{created_at}",
        f"> **原始链接**：[{url}]({url})",
        "",
        "---",
        "",
        "### 📝 博主原文案",
        "",
        raw_text,
        "",
    ]

    if downloaded_images:
        md_lines.extend([
            "---",
            "",
            f"### 🖼️ 原推配图清单（共 {len(downloaded_images)} 张）",
            "",
        ])
        for img_info in downloaded_images:
            md_lines.append(f"![推文原图{img_info['index']}]({img_info['rel_path']})")
            md_lines.append("")

    article_md_content = "\n".join(md_lines)
    article_md_path = run_dir / "article.md"
    article_md_path.write_text(article_md_content, encoding="utf-8")

    # 5. 生成标准 push-result.json（符合 list_runs 识别标准）
    push_result = {
        "title": title,
        "author": source_name,
        "source_url": url,
        "platform_source": data.get("platform", "x"),
        "status": "外部导入",
        "imported": True,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "images_count": len(downloaded_images),
        "image_files": [img["abs_path"] for img in downloaded_images],
        "run_dir": str(run_dir),
    }
    (run_dir / "push-result.json").write_text(
        json.dumps(push_result, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # 6. 初始化 matrix-status.json
    matrix_status = {
        "toutiao": {"status": "待生成"},
        "xhs": {"status": "待生成"},
    }
    (run_dir / "matrix-status.json").write_text(
        json.dumps(matrix_status, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    return {
        "ok": True,
        "title": title,
        "author": source_name,
        "source_url": url,
        "platform": data.get("platform", "x"),
        "run_dir": str(run_dir),
        "run_dir_name": run_dir_name,
        "article_md_path": str(article_md_path),
        "images_count": len(downloaded_images),
        "image_files": [img["abs_path"] for img in downloaded_images],
        "text": raw_text,
    }


def main():
    if len(sys.argv) < 2:
        print("用法: python scripts/article_extractor.py <推文或文章URL>")
        sys.exit(1)

    target_url = sys.argv[1]
    print(f"正在拉取链接: {target_url} ...")
    res = import_article_to_run(target_url)
    print("导入成功！")
    print(f"标题: {res['title']}")
    print(f"作者: {res['author']}")
    print(f"目录: {res['run_dir']}")
    print(f"配图数: {res['images_count']}")
    for img in res['image_files']:
        print(f"  - {img}")


if __name__ == "__main__":
    main()
