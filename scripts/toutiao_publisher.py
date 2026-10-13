#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
已停用：今日头条 Playwright/CDP 自动填报与发布脚本。
================================================================================
本项目目前只生成今日头条文案、封面和 DOCX 素材，必须通过官方后台人工审核发布。
旧自动化实现保留在此文件中仅供历史参考，调用入口会主动拒绝执行。

历史工作原理：
1. 本地真实 Chrome 以调试模式启动（--remote-debugging-port=9222）；
2. 脚本通过 CDP 协议连接现有浏览器，复用用户在头条后台（mp.toutiao.com）的登录态；
3. 自动填入已润色标题、注入富文本正文、上传 16:9 封面并点击「保存草稿」；
4. 绝对不自动点击「发表」，由人工做最后把关，零风控封号风险。
"""

import os
import sys
import time
import json
import re
import urllib.request
from pathlib import Path
from typing import Optional, Dict, Any
from datetime import datetime, timedelta

try:
    import markdown
except ImportError:
    markdown = None

from playwright.sync_api import sync_playwright, Page, BrowserContext

DEFAULT_CDP_URL = os.getenv("CHROME_CDP_URL", "http://127.0.0.1:9222")
TOUTIAO_PUBLISH_URL = "https://mp.toutiao.com/profile_v4/graphic/publish"


def check_chrome_cdp_available(cdp_url: str = DEFAULT_CDP_URL) -> bool:
    """检查本地 Chrome 调试端口是否可用。"""
    try:
        url = cdp_url.rstrip("/") + "/json/version"
        req = urllib.request.Request(url, headers={"User-Agent": "ToutiaoPublisher/1.0"})
        with urllib.request.urlopen(req, timeout=2.0) as resp:
            return resp.status == 200
    except Exception:
        return False


def get_chrome_launch_command() -> str:
    """返回当前操作系统的 Chrome 调试端口启动指南（必须带 user-data-dir，否则 Chrome 会安全静默忽略端口）。"""
    if sys.platform == "darwin":
        return 'open -n -a "Google Chrome" --args --remote-debugging-port=9222 --user-data-dir="$HOME/Library/Application Support/Google/Chrome-Automation" --no-first-run'
    elif sys.platform.startswith("win"):
        return '"C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe" --remote-debugging-port=9222 --user-data-dir="%LOCALAPPDATA%\\ChromeAutomation" --no-first-run'
    else:
        return 'google-chrome --remote-debugging-port=9222 --user-data-dir="$HOME/.config/google-chrome-automation" --no-first-run &'


def clean_markdown_for_editor(md_content: str, base_dir: Optional[str] = None) -> str:
    """清理 Markdown 内容：剔除顶部的 H1 标题和首个封面图（发文页有独立封面上传区），保留正文章节中的推文原图。"""
    lines = md_content.splitlines()
    cleaned_lines = []
    skipped_first_h1 = False
    skipped_first_cover = False

    for line in lines:
        stripped = line.strip()
        # 跳过开头的首个一级标题
        if not skipped_first_h1 and (stripped.startswith("# ") or stripped.startswith("## ")):
            skipped_first_h1 = True
            continue
        # 只跳过首个顶部封面图（如 ![今日头条封面图] 或首图）
        if not skipped_first_cover and re.match(r'^!\[.*?\]\(.*?\)', stripped):
            skipped_first_cover = True
            continue
        # 跳过多余的主题配置块或元数据注释
        if stripped.startswith("<!--") and stripped.endswith("-->"):
            continue
        cleaned_lines.append(line)

    return "\n".join(cleaned_lines).strip()


def markdown_to_html(md_text: str, base_dir: Optional[str] = None) -> str:
    """将 Markdown 转换为适合富文本编辑器的轻量 HTML。支持将正文本地图转换为 Base64 内联展示。"""
    import base64

    def _replace_local_img(match):
        alt = match.group(1)
        src = match.group(2).strip()
        if src.startswith("http://") or src.startswith("https://") or src.startswith("data:"):
            return f'<img src="{src}" alt="{alt}" style="max-width:100%;border-radius:6px;margin:12px 0;">'
        
        # 本地路径尝试
        target_path = None
        if os.path.isabs(src) and os.path.isfile(src):
            target_path = Path(src)
        elif base_dir:
            cand = Path(base_dir) / src
            if cand.is_file():
                target_path = cand

        if target_path and target_path.is_file():
            mime = "image/jpeg" if target_path.suffix.lower() in (".jpg", ".jpeg") else "image/png"
            b64 = base64.b64encode(target_path.read_bytes()).decode("utf-8")
            return f'<img src="data:{mime};base64,{b64}" alt="{alt}" style="max-width:100%;border-radius:6px;margin:12px 0;">'
        return f'<img src="{src}" alt="{alt}">'

    # 替换 markdown 图片标记
    md_converted = re.sub(r'!\[(.*?)\]\((.*?)\)', _replace_local_img, md_text)

    if markdown:
        return markdown.markdown(md_converted, extensions=['extra', 'tables'])

    paragraphs = md_converted.split("\n\n")
    html_parts = []
    for p in paragraphs:
        p = p.strip()
        if not p:
            continue
        if p.startswith("<h3>") or p.startswith("<img"):
            html_parts.append(p)
        elif p.startswith("### "):
            html_parts.append(f"<h3>{p[4:]}</h3>")
        elif p.startswith("## "):
            html_parts.append(f"<h2>{p[3:]}</h2>")
        elif p.startswith("# "):
            html_parts.append(f"<h1>{p[2:]}</h1>")
        else:
            html_parts.append(f"<p>{p.replace(chr(10), '<br>')}</p>")
    return "\n".join(html_parts)


def clean_title_for_toutiao(title: str, max_chars: int = 30) -> str:
    """
    今日头条标题合规化：
    1. 限制在 5~30 个字符内；
    2. 严禁在末尾添加省略号（…）或三个点（...），头条算法会判定为格式不规范；
    3. 彻底剥离末尾悬空的冒号、破折号、逗号、句号、省略号等违规标点。
    """
    if not title:
        return ""
    title = title.strip()
    # 移除开头的标签前缀如【爆款】
    title = re.sub(r'^[【\[\(（][^】\]\)）]+[】\]\)）]', '', title).strip() or title

    if len(title) > max_chars:
        title = title[:max_chars]

    # 彻底清理末尾的不合规符号（包括冒号、破折号、省略号、顿号、逗号等）
    title = re.sub(r'[\s:：,，、;；.。…\-\—–——～~·]+$', '', title).strip()
    return title


def truncate_title_for_toutiao(title: str, max_chars: int = 30) -> str:
    """兼容旧接口调用。"""
    return clean_title_for_toutiao(title, max_chars)


def publish_to_toutiao_draft(
    title: str,
    markdown_content: str,
    cover_image_path: Optional[str] = None,
    schedule_time: Optional[str] = None,
    cdp_url: str = DEFAULT_CDP_URL,
    timeout_ms: int = 45000,
) -> Dict[str, Any]:
    """
    通过 Playwright CDP 接管本地真实 Chrome，将文章填入今日头条创作者后台并自动定时发布或保存为草稿。
    """
    raise RuntimeError("今日头条自动发布已停用，请使用生成的素材并通过官方后台人工发布。")
    if not title:
        raise ValueError("文章标题不能为空")
    if not markdown_content:
        raise ValueError("文章正文 Markdown 不能为空")

    # 1. 检查 Chrome 调试端口
    if not check_chrome_cdp_available(cdp_url):
        cmd = get_chrome_launch_command()
        raise ConnectionError(
            f"未检测到运行中的 Chrome 调试端口 ({cdp_url})。\n"
            f"请先打开终端执行以下命令启动带端口的 Chrome 并登录头条：\n\n  {cmd}\n"
        )

    title_clean = truncate_title_for_toutiao(title, 30)
    base_dir = str(Path(cover_image_path).parent) if cover_image_path else None
    cleaned_md = clean_markdown_for_editor(markdown_content, base_dir=base_dir)
    article_html = markdown_to_html(cleaned_md, base_dir=base_dir)

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(cdp_url)
        # 获取或使用默认上下文
        contexts = browser.contexts
        context = contexts[0] if contexts else browser.new_context()

        # 赋予剪贴板读写权限
        try:
            context.grant_permissions(["clipboard-read", "clipboard-write"], origin="https://mp.toutiao.com")
        except Exception:
            pass

        # 查找或新建头条发文页标签
        target_page: Optional[Page] = None
        for page in context.pages:
            if "mp.toutiao.com/profile_v4/graphic/publish" in page.url or "mp.toutiao.com" in page.url:
                target_page = page
                break

        if not target_page:
            target_page = context.new_page()
            target_page.goto(TOUTIAO_PUBLISH_URL, wait_until="domcontentloaded", timeout=timeout_ms)
        else:
            target_page.bring_to_front()
            target_page.goto(TOUTIAO_PUBLISH_URL, wait_until="domcontentloaded", timeout=timeout_ms)

        # 2. 检查登录状态
        current_url = target_page.url
        if "login" in current_url or "auth" in current_url or "sso" in current_url:
            raise PermissionError(
                "检测到当前 Chrome 尚未登录今日头条创作者账号！\n"
                "请先在浏览器中完成今日头条登录后，再次点击推送。"
            )

        # 优雅关闭右侧「头条创作助手」抽屉，绝不能从 DOM 中直接 remove 通用 drawer 容器
        try:
            assistant_close = target_page.locator(".ai-assistant-drawer .close-btn, .ai-assistant-drawer-wrapper .close-btn").first
            if assistant_close.is_visible(timeout=2000):
                assistant_close.click(force=True)
                time.sleep(0.5)
        except Exception:
            pass

        # 兜底：若仍有 ai-assistant 相关浮层，仅隐藏其 display，确保 ByteDesign 通用组件库正常工作
        target_page.evaluate("""() => {
            const aiDrawer = document.querySelector('.ai-assistant-drawer');
            if (aiDrawer) aiDrawer.style.display = 'none';
            const aiWrapper = document.querySelector('.ai-assistant-drawer-wrapper');
            if (aiWrapper) aiWrapper.style.display = 'none';
            const masks = document.querySelectorAll('.byte-drawer-mask');
            masks.forEach(m => {
                if (m.nextElementSibling && m.nextElementSibling.classList.contains('ai-assistant-drawer')) {
                    m.style.display = 'none';
                }
            });
        }""")
        time.sleep(0.5)

        # 标题字数限制：今日头条标题上限为 30 字
        title_for_toutiao = title_clean[:30] if len(title_clean) > 30 else title_clean

        # 等待页面标题输入框渲染就绪
        title_selectors = [
            'textarea[placeholder*="标题"]',
            'input[placeholder*="标题"]',
            '[data-testid="article-title"]',
            '.byte-input__textarea',
            '.article-title textarea',
            '.title-input input',
        ]
        combined_title_sel = ", ".join(title_selectors)
        try:
            target_page.wait_for_selector(combined_title_sel, state="visible", timeout=15000)
        except Exception:
            time.sleep(1.0)

        # 3. 填入文章标题
        title_el = None
        for sel in title_selectors:
            try:
                el = target_page.locator(sel).first
                if el.is_visible(timeout=1500):
                    title_el = el
                    break
            except Exception:
                continue

        if title_el:
            title_el.click(force=True)
            is_mac = sys.platform == "darwin"
            target_page.keyboard.press("Meta+a" if is_mac else "Control+a")
            target_page.keyboard.press("Backspace")
            title_el.fill(title_for_toutiao)
            target_page.keyboard.press("Escape")
        else:
            raise RuntimeError("未在页面中找到标题输入框，请确认发文页面是否已完全加载。")

        time.sleep(0.5)

        # 4. 注入正文富文本
        editor_selectors = [
            '.ProseMirror',
            'div[contenteditable="true"]',
            '.article-content .editor',
            '#rich-editor',
        ]
        editor_el = None
        for sel in editor_selectors:
            try:
                el = target_page.locator(sel).first
                if el.is_visible(timeout=1500):
                    editor_el = el
                    break
            except Exception:
                continue

        if not editor_el:
            raise RuntimeError("未在页面中找到正文编辑器，请检查是否处于图文编辑模式。")

        editor_el.click(force=True)
        time.sleep(0.3)

        # 清空已有正文
        is_mac = sys.platform == "darwin"
        target_page.keyboard.press("Meta+a" if is_mac else "Control+a")
        target_page.keyboard.press("Backspace")
        time.sleep(0.2)

        # 优先使用 insertHTML 注入保留排版的富文本
        injection_success = target_page.evaluate("""
            (html) => {
                const editor = document.querySelector('.ProseMirror') || document.querySelector('div[contenteditable="true"]');
                if (!editor) return false;
                editor.focus();
                const success = document.execCommand('insertHTML', false, html);
                editor.dispatchEvent(new Event('input', { bubbles: true }));
                editor.dispatchEvent(new Event('change', { bubbles: true }));
                return success;
            }
        """, article_html)

        if not injection_success:
            editor_el.fill(cleaned_md)

        time.sleep(1.0)

        # 4.5 确保展开「发文设置」面板
        target_page.evaluate("""() => {
            const footerBack = document.querySelector('.footer-back-content');
            if (footerBack && !document.querySelector('.article-cover-add')) {
                footerBack.click();
            }
        }""")
        time.sleep(0.8)

        # 5. 上传封面图 (16:9 横版单图)
        cover_uploaded = False
        uploaded_cover_url = None
        if cover_image_path and os.path.exists(cover_image_path):
            abs_cover = os.path.abspath(cover_image_path)
            try:
                # 滚动到封面设置区
                target_page.evaluate("window.scrollTo(0, document.body.scrollHeight * 0.45)")
                time.sleep(0.5)

                # 确保选中“单图”选项
                single_radio = target_page.locator('label.byte-radio:has-text("单图")').first
                if single_radio.is_visible(timeout=1500):
                    is_checked = single_radio.locator('.byte-radio-inner.checked').is_visible()
                    if not is_checked:
                        single_radio.click(force=True)
                        time.sleep(0.5)

                # 若已有旧封面图，先删除使其复位为加号按钮
                del_btn = target_page.locator('.article-cover-delete').first
                if del_btn.count() > 0:
                    del_btn.dispatch_event('click')
                    time.sleep(0.8)

                # 定位并点击添加封面加号按钮
                cover_add_btn = target_page.locator('.article-cover-add').first
                if cover_add_btn.is_visible(timeout=2500):
                    cover_add_btn.scroll_into_view_if_needed()
                    time.sleep(0.3)
                    cover_add_btn.click(force=True)
                    time.sleep(1.5)

                    # 找到封面抽屉中专门用于本地上传的 file input
                    file_input = target_page.locator('.mp-ic-img-drawer input[type="file"], input[type="file"]').first
                    file_input.set_input_files(abs_cover)
                    time.sleep(3.0)

                    # 点击抽屉底部的「确定」主按钮
                    confirm_btn = target_page.locator('.mp-ic-img-drawer button:has-text("确定"), button.byte-btn-primary:has-text("确定")').last
                    if confirm_btn.is_visible(timeout=5000):
                        confirm_btn.click(force=True)
                        cover_uploaded = True
                        time.sleep(1.5)

                # 检查并获取上传成功的头条官方 CDN 封面 URL
                cover_img_el = target_page.locator(".article-cover-images-wrap img").first
                if cover_img_el.is_visible(timeout=2000):
                    uploaded_cover_url = cover_img_el.get_attribute("src")

            except Exception as e:
                print(f"[Warning] 封面图上传异常: {e}", file=sys.stderr)

        # 5.1 若封面上传成功，同步将该 16:9 高质感封面大图置入正文开头，提升全文阅读体验
        if uploaded_cover_url:
            try:
                target_page.evaluate("""(coverUrl) => {
                    const editor = document.querySelector('.ProseMirror');
                    if (!editor) return;
                    if (editor.querySelector('img')) return;
                    const p = editor.querySelector('p');
                    const imgHtml = `<p style="text-align: center;"><img src="${coverUrl}" style="max-width: 100%; border-radius: 8px; margin: 12px 0;" /></p>`;
                    if (p) {
                        p.insertAdjacentHTML('beforebegin', imgHtml);
                    } else {
                        editor.insertAdjacentHTML('afterbegin', imgHtml);
                    }
                    editor.dispatchEvent(new Event('input', { bubbles: true }));
                }""", uploaded_cover_url)
                time.sleep(0.5)
            except Exception as e:
                print(f"[Warning] 正文首图插入跳过: {e}", file=sys.stderr)

        # 6. 设置「添加位置」（默认：上海）
        location_selected = False
        try:
            loc_cell = target_page.locator(".position-cell")
            if loc_cell.is_visible(timeout=2000):
                loc_cell.scroll_into_view_if_needed()
                time.sleep(0.3)
                
                # 点击 select 框激活输入
                select_box = loc_cell.locator(".position-select")
                select_box.click(force=True)
                time.sleep(0.4)
                
                # 填入“上海”
                input_el = loc_cell.locator("input").first
                input_el.fill("上海")
                time.sleep(1.0)
                
                # 从下拉候选项中选定“上海”
                option_el = target_page.locator(".byte-select-dropdown-list li, .byte-select-dropdown li, .byte-select-option").filter(has_text="上海").first
                if option_el.is_visible(timeout=2000):
                    option_el.click(force=True)
                    location_selected = True
                    time.sleep(0.5)
        except Exception as e:
            print(f"[Warning] 设置位置跳过或未完成: {e}", file=sys.stderr)

        # 7. 设置「作品声明」（默认：个人观点，仅供参考）
        statement_selected = False
        try:
            statement_cell = target_page.locator('label.byte-checkbox:has-text("个人观点，仅供参考")').first
            if statement_cell.is_visible(timeout=2000):
                statement_cell.scroll_into_view_if_needed()
                time.sleep(0.3)
                is_checked = "byte-checkbox-checked" in (statement_cell.get_attribute("class") or "")
                if not is_checked:
                    statement_cell.click(force=True)
                    time.sleep(0.4)
                statement_selected = True
        except Exception as e:
            print(f"[Warning] 作品声明勾选跳过或未完成: {e}", file=sys.stderr)

        # 8. 提交动作：定时发布 / 即时发布
        is_scheduled = False
        if schedule_time:
            # 校验头条硬规则：定时时间必须在当前时间 2 小时至 7 天后
            dt_match = re.search(r'(\d{4})[-/](\d{1,2})[-/](\d{1,2})\s+(\d{1,2}):(\d{1,2})', schedule_time.strip())
            if not dt_match:
                raise ValueError(f"定时时间格式不正确 ({schedule_time})，标准格式为 YYYY-MM-DD HH:MM")

            t_year, t_month, t_day, t_hour, t_min = (int(dt_match.group(i)) for i in range(1, 6))
            target_dt = datetime(t_year, t_month, t_day, t_hour, t_min)
            now_dt = datetime.now()
            diff_mins = (target_dt - now_dt).total_seconds() / 60.0

            if diff_mins < 120.0:
                min_avail = now_dt + timedelta(minutes=125)
                raise ValueError(
                    f"今日头条官方硬性规定：定时发布时间必须在【当前时间 2小时之后】！\n"
                    f"当前时间为 {now_dt.strftime('%H:%M')}，目标时间 {schedule_time} 距离当前仅 {int(diff_mins)} 分钟（不足 2 小时）。\n"
                    f"今日头条最早可定时时间为 {min_avail.strftime('%Y-%m-%d %H:%M')} 之后，请重新选择时间后再试！"
                )

            try:
                target_day_patterns = [
                    f"{t_month:02d}月{t_day:02d}日",
                    f"{t_month}月{t_day:02d}日",
                    f"{t_month:02d}月{t_day}日",
                    f"{t_month}月{t_day}日",
                ]
                # 头条下拉框中的小时与分钟均为无前导0的纯数字（如 "8" 而非 "08"，"0" 而非 "00"）
                target_hour_num = str(t_hour)
                target_min_num = str(t_min)

                # 清理所有遮罩浮层，防止遮挡按钮点击
                target_page.evaluate("""() => {
                    document.querySelectorAll('.byte-drawer-mask, .ai-assistant-drawer, .ai-assistant-drawer-wrapper').forEach(e => e.remove());
                }""")
                time.sleep(0.3)

                # 滚动到底部并点击「定时发布」按钮唤出时间选择弹窗
                target_page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                time.sleep(0.5)
                sched_btn = target_page.locator('button:has-text("定时发布")').first
                if sched_btn.is_visible(timeout=3000):
                    sched_btn.scroll_into_view_if_needed()
                    sched_btn.click(force=True)
                    time.sleep(1.5)

                    # 等待定时发布弹窗出现
                    modal = target_page.locator('.common-timing-picker').first
                    if modal.is_visible(timeout=4000):
                        # 1. 选择日期（适配前导0与无前导0格式）
                        day_sel = modal.locator('.day-select').first
                        if day_sel.is_visible():
                            day_sel.click(force=True)
                            time.sleep(0.4)
                            day_clicked = target_page.evaluate("""(patterns) => {
                                const opts = Array.from(document.querySelectorAll('.byte-select-option')).filter(o => o.offsetParent !== null);
                                for (const p of patterns) {
                                    const found = opts.find(o => o.innerText.trim() === p || o.innerText.includes(p));
                                    if (found) { found.click(); return true; }
                                }
                                return false;
                            }""", target_day_patterns)
                            if not day_clicked:
                                target_page.locator('.byte-select-option').first.click(force=True)
                            time.sleep(0.4)

                        # 2. 精确选择小时（头条选项为 "0"~"23"，无前导0）
                        hour_sel = modal.locator('.hour-select').first
                        if hour_sel.is_visible():
                            hour_sel.click(force=True)
                            time.sleep(0.4)
                            target_page.evaluate("""(targetHour) => {
                                const opts = Array.from(document.querySelectorAll('.byte-select-option')).filter(o => o.offsetParent !== null);
                                const found = opts.find(o => o.innerText.trim() === targetHour);
                                if (found) { found.click(); return true; }
                                return false;
                            }""", target_hour_num)
                            time.sleep(0.4)

                        # 3. 精确选择分钟（头条选项为 "0"~"59"，无前导0）
                        min_sel = modal.locator('.minute-select').first
                        if min_sel.is_visible():
                            min_sel.click(force=True)
                            time.sleep(0.4)
                            target_page.evaluate("""(targetMin) => {
                                const opts = Array.from(document.querySelectorAll('.byte-select-option')).filter(o => o.offsetParent !== null);
                                const found = opts.find(o => o.innerText.trim() === targetMin);
                                if (found) { found.click(); return true; }
                                return false;
                            }""", target_min_num)
                            time.sleep(0.4)

                        # 点击弹窗内的确认提交按钮
                        confirm_sched = modal.locator('button.byte-btn-primary:has-text("预览并定时发布"), button:has-text("定时发布"), button:has-text("确定")').first
                        if confirm_sched.is_visible(timeout=3000):
                            confirm_sched.click(force=True)
                            time.sleep(2.0)
                            
                            # 点击后头条会弹出手机端全屏预览弹窗，底部工具栏包含「确认发布」或「定时发布」按钮，必须再次点击触发最终发布
                            final_sched_selectors = [
                                '.publish-footer-content button:has-text("确认发布")',
                                '.publish-footer-content button:has-text("定时发布")',
                                'button.publish-btn:has-text("确认发布")',
                                'button.publish-btn:has-text("定时发布")',
                                '.byte-modal button:has-text("确认发布")',
                                '.byte-modal button:has-text("定时发布")',
                            ]
                            for f_sel in final_sched_selectors:
                                try:
                                    f_btn = target_page.locator(f_sel).first
                                    if f_btn.is_visible(timeout=1500):
                                        f_btn.click(force=True)
                                        time.sleep(2.0)
                                        is_scheduled = True
                                        break
                                except Exception:
                                    continue

            except Exception as e:
                print(f"[Warning] 头条定时发布选择异常: {e}", file=sys.stderr)
                raise e

            if not is_scheduled:
                raise RuntimeError("今日头条定时发布流程未成功完成，请检查时间是否有效或浏览器操作是否被阻拦。")

        if not is_scheduled:
            # 即时发布模式：点击「预览并发布」或「立即发布」
            target_page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            time.sleep(0.5)
            publish_selectors = [
                'button.publish-btn-last:has-text("预览并发布")',
                'button.publish-btn-last:has-text("立即发布")',
                'button.byte-btn-primary:has-text("预览并发布")',
                'button.byte-btn-primary:has-text("立即发布")',
                'button:has-text("预览并发布")',
                'button:has-text("立即发布")',
                'button:has-text("发布")',
            ]
            for p_sel in publish_selectors:
                try:
                    b = target_page.locator(p_sel).first
                    if b.is_visible(timeout=1500):
                        b.click(force=True)
                        time.sleep(2.0)
                        break
                except Exception:
                    continue

            # 处理可能弹出的预览弹窗或二次确认浮层
            confirm_publish_selectors = [
                '.publish-footer-content button:has-text("确认发布")',
                '.publish-footer-content button:has-text("立即发布")',
                'button.publish-btn:has-text("确认发布")',
                'button.publish-btn:has-text("立即发布")',
                'button:has-text("确认发布")',
                'button.byte-btn-primary:has-text("确认发布")',
                'div[class*="drawer"] button.byte-btn-primary:has-text("发布")',
                'div[class*="modal"] button.byte-btn-primary:has-text("发布")',
                '.byte-modal-footer button.byte-btn-primary',
            ]
            for cp_sel in confirm_publish_selectors:
                try:
                    cp_btn = target_page.locator(cp_sel).first
                    if cp_btn.is_visible(timeout=2000):
                        cp_btn.click(force=True)
                        time.sleep(1.5)
                        break
                except Exception:
                    continue

        time.sleep(2.0)
        current_page_url = target_page.url

        msg = f"今日头条文章已成功设置为定时发布（定时时间: {schedule_time}）！" if schedule_time else "今日头条文章已成功预览并发布！"

        return {
            "status": "success",
            "message": msg,
            "title": title_clean,
            "schedule_time": schedule_time or "",
            "cover_uploaded": cover_uploaded,
            "location_selected": location_selected,
            "statement_selected": statement_selected,
            "page_url": current_page_url,
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        }


def main():
    """CLI 入口：自动发布已停用，仅保留文件生成链路。"""
    raise SystemExit("今日头条自动发布已停用，请使用 outputs/今日头条/ 中的素材并通过官方后台人工发布。")
    import argparse
    parser = argparse.ArgumentParser(description="今日头条草稿自动填报工具")
    parser.add_argument("--check-chrome", action="store_true", help="检查 Chrome CDP 调试端口状态")
    parser.add_argument("--title", type=str, help="文章标题")
    parser.add_argument("--md", type=str, help="Markdown 正文路径或文本")
    parser.add_argument("--cover", type=str, help="封面图片文件路径")
    parser.add_argument("--schedule-time", type=str, default="", help="定时发布时间（如 2026-10-01 08:00）")
    parser.add_argument("--cdp", type=str, default=DEFAULT_CDP_URL, help="Chrome CDP URL")
    args = parser.parse_args()

    if args.check_chrome:
        ok = check_chrome_cdp_available(args.cdp)
        if ok:
            print(f"✅ Chrome CDP 调试端口正常连接: {args.cdp}")
            sys.exit(0)
        else:
            print(f"❌ 未检测到 Chrome 调试端口: {args.cdp}")
            print(f"启动建议:\n  {get_chrome_launch_command()}")
            sys.exit(1)

    if not args.title or not args.md:
        print("用法: python scripts/toutiao_publisher.py --title <标题> --md <路径或内容> [--cover <封面>] [--schedule-time <时间>]")
        sys.exit(1)

    md_content = args.md
    if os.path.exists(args.md):
        md_content = Path(args.md).read_text(encoding="utf-8")

    res = publish_to_toutiao_draft(
        title=args.title,
        markdown_content=md_content,
        cover_image_path=args.cover,
        schedule_time=args.schedule_time or None,
        cdp_url=args.cdp,
    )
    print(json.dumps(res, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
