#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
已停用：小红书 Playwright/CDP 自动填报与发布脚本。
================================================================================
本项目目前只生成小红书文案、封面和本地素材，必须通过官方后台人工审核发布。
旧自动化实现保留在此文件中仅供历史参考，调用入口会主动拒绝执行。

历史工作原理：
1. 本地真实 Chrome 以调试模式启动（--remote-debugging-port=9222）；
2. 脚本通过 CDP 协议连接现有浏览器，复用用户在小红书创作者服务平台（creator.xiaohongshu.com）的登录态；
3. 切换至「上传图文」，自动上传 3:4 超清置顶封面图；
4. 填写合规网感标题（<=20字）；
5. 随机应用排版风格注入正文，并自动补齐至少 5 个高热垂直话题标签；
6. 自动勾选「原创声明」并确认协议弹窗；
7. 地点搜索下拉列表中每次「随机选择一个真实打卡地标」；
8. 调试期默认执行「暂存离开（保存草稿）」，稳定后可一键切换为自动发布。
"""

import os
import sys
import time
import json
import re
import random
import urllib.request
from pathlib import Path
from typing import Optional, Dict, Any, List

from playwright.sync_api import sync_playwright, Page, BrowserContext

DEFAULT_CDP_URL = os.getenv("CHROME_CDP_URL", "http://127.0.0.1:9222")
XHS_PUBLISH_URL = "https://creator.xiaohongshu.com/publish/publish?from=menu&target=article"

# 备选热门垂直话题库（当文案自带话题少于 5 个时自动按相关度补充）
DEFAULT_HOT_TOPICS = [
    "#AI改变生活",
    "#人工智能",
    "#大模型",
    "#科技前沿",
    "#搞钱路子",
    "#效率工具",
    "#干货分享",
    "#程序员日常",
    "#数码科技",
    "#打工人摸鱼",
    "#今日科技大事件",
]

# 备选搜索城市（用于添加地点随机打卡）
RANDOM_CITIES = ["上海", "北京", "深圳", "杭州", "广州", "成都", "南京", "武汉"]


def check_chrome_cdp_available(cdp_url: str = DEFAULT_CDP_URL) -> bool:
    """检查本地 Chrome 调试端口是否可用。"""
    try:
        url = cdp_url.rstrip("/") + "/json/version"
        req = urllib.request.Request(url, headers={"User-Agent": "XhsPublisher/1.0"})
        with urllib.request.urlopen(req, timeout=2.0) as resp:
            return resp.status == 200
    except Exception:
        return False


def get_chrome_launch_command() -> str:
    """返回当前操作系统的 Chrome 调试端口启动指南。"""
    if sys.platform == "darwin":
        return 'open -n -a "Google Chrome" --args --remote-debugging-port=9222 --user-data-dir="$HOME/Library/Application Support/Google/Chrome-Automation" --no-first-run'
    elif sys.platform.startswith("win"):
        return '"C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe" --remote-debugging-port=9222 --user-data-dir="%LOCALAPPDATA%\\ChromeAutomation" --no-first-run'
    else:
        return 'google-chrome --remote-debugging-port=9222 --user-data-dir="$HOME/.config/google-chrome-automation" --no-first-run &'


def clean_title_for_xhs(title: str, max_chars: int = 20) -> str:
    """
    小红书标题合规化：
    1. 严格限制在 20 个字符以内（小红书平台强制字符上限）；
    2. 剔除外层包裹符号如【爆款】；
    3. 截断时优先在完整标点意群处切断，避免半词半句；
    4. 剔除末尾悬空的冒号、破折号、省略号、逗号等不美观符号；
    5. 保持小红书网感与反差感。
    """
    if not title:
        return ""
    title = title.strip()
    # 移除开头的标签前缀
    title = re.sub(r'^[【\[\(（][^】\]\)）]+[】\]\)）]', '', title).strip() or title
    if len(title) <= max_chars:
        return title

    # 如果超过 max_chars，尝试在标点处智能切断（优先在第 12~20 字之间的标点处断句）
    truncated = title[:max_chars]
    punc_matches = list(re.finditer(r'[！!？?，,。\s]', truncated))
    if punc_matches and punc_matches[-1].start() >= 10:
        truncated = truncated[:punc_matches[-1].end()]

    # 清理末尾悬空的非法标点
    truncated = re.sub(r'[\s:：,，、;；.。…\-\—–——～~·]+$', '', truncated).strip()
    return truncated


def format_xhs_body_and_topics(raw_content: str, min_topics: int = 5, max_total_chars: int = 950) -> Dict[str, Any]:
    """
    清洗并规范小红书正文描述：
    1. 剔除开头的 H1 标题与本地/Base64 图片标记；
    2. 随机选用一种排版样式（如增加空行呼吸感、重点符号强调、清单分段等）；
    3. 提取已有 #话题，若少于 5 个则智能补齐，确保话题数 >= 5；
    4. 严格字数硬上限控制：小红书正文上限为 1000 字符，默认限制在 950 字以内，防止超出平台阈值。
    """
    lines = raw_content.splitlines()
    cleaned_lines: List[str] = []
    skipped_first_h1 = False

    for line in lines:
        stripped = line.strip()
        # 跳过开头的 H1 标题
        if not skipped_first_h1 and (stripped.startswith("# ") or stripped.startswith("## ")):
            skipped_first_h1 = True
            continue
        # 跳过图片标记（如 Base64 封面图）
        if re.match(r'^!\[.*?\]\(.*?\)', stripped):
            continue
        # 跳过 HTML 注释
        if stripped.startswith("<!--") and stripped.endswith("-->"):
            continue
        cleaned_lines.append(line)

    body_text = "\n".join(cleaned_lines).strip()

    # 提取已有的所有 #话题
    existing_topics = re.findall(r'#([^\s#]+)', body_text)
    unique_topics = []
    seen = set()
    for t in existing_topics:
        t_clean = t.strip()
        if t_clean and t_clean not in seen:
            unique_topics.append(t_clean)
            seen.add(t_clean)

    # 如果话题少于 min_topics，从热门库中随机补充
    supplement_topics = []
    candidate_pool = [t.lstrip("#") for t in DEFAULT_HOT_TOPICS if t.lstrip("#") not in seen]
    random.shuffle(candidate_pool)

    while len(unique_topics) + len(supplement_topics) < min_topics and candidate_pool:
        supplement_topics.append(candidate_pool.pop(0))

    all_topics = unique_topics + supplement_topics

    # 从正文中剥离结尾已存在的散乱标签行，统一在篇末以优雅格式呈现
    body_text = re.sub(r'(?:#[^\s#]+\s*)+$', '', body_text).strip()

    # 4 种随机排版策略增强文案视觉体验
    style_idx = random.randint(1, 4)
    style_names = {
        1: "Emoji呼吸流（轻量留白，视觉灵动）",
        2: "结构清单流（重点突出，条理清晰）",
        3: "干货爆点流（短句反差，高密集度）",
        4: "沉浸对话流（互动设问，拉近距离）",
    }

    if style_idx == 1:
        # 增加段落间距留白
        body_text = re.sub(r'\n{3,}', '\n\n', body_text)
    elif style_idx == 2:
        # 对数字序号增强标识
        body_text = re.sub(r'^(\d+)[\.、]', r'🔹 第\1点：', body_text, flags=re.MULTILINE)
    elif style_idx == 3:
        # 重点段落前置高能提醒
        if not body_text.startswith("💡"):
            body_text = "💡 先说结论与核心看点：\n\n" + body_text

    # 拼接最终话题标签
    topics_str = " ".join([f"#{t}" for t in all_topics])
    divider = "\n\n---\n"
    
    # 严格字数安全上限截断（小红书硬上限 1000 字符，保留 50 字余量）
    current_total = len(body_text) + len(divider) + len(topics_str)
    if current_total > max_total_chars:
        allowed_body_len = max_total_chars - len(divider) - len(topics_str) - 10
        if allowed_body_len > 100:
            sub_body = body_text[:allowed_body_len]
            # 优先在最后的段落分隔符处切断
            last_para = sub_body.rfind("\n\n")
            if last_para > int(allowed_body_len * 0.65):
                body_text = sub_body[:last_para].strip() + "\n\n...(篇幅受限，完整干货详见主页)"
            else:
                last_punc = max(sub_body.rfind("。"), sub_body.rfind("！"), sub_body.rfind("？"))
                if last_punc > int(allowed_body_len * 0.65):
                    body_text = sub_body[:last_punc + 1].strip() + "\n\n...(篇幅受限，完整干货详见主页)"
                else:
                    body_text = sub_body.rstrip() + "..."

    final_content = f"{body_text}{divider}{topics_str}"

    return {
        "content": final_content,
        "topics": all_topics,
        "style_idx": style_idx,
        "style_name": style_names.get(style_idx, "标准排版"),
    }


def publish_to_xhs(
    title: str,
    markdown_content: str,
    cover_image_path: Optional[str] = None,
    image_paths: Optional[List[str]] = None,  # 支持多图列表（封面+推文全部原图）
    schedule_time: Optional[str] = None,
    action: str = "publish",  # "publish" 自动定时/即时发布 | "draft" 暂存草稿
    city: str = "上海",
    cdp_url: str = DEFAULT_CDP_URL,
    timeout_ms: int = 45000,
) -> Dict[str, Any]:
    """
    通过 Playwright CDP 接管本地真实 Chrome，将笔记填入小红书创作者后台并自动定时发布或保存为草稿。
    支持上传多图图集：封面图排第一张，推文原图依次排在后续作为轮播图集。
    """
    raise RuntimeError("小红书自动发布已停用，请使用生成的素材并通过官方后台人工发布。")
    if not title:
        raise ValueError("笔记标题不能为空")
    if not markdown_content:
        raise ValueError("笔记正文不能为空")

    # 汇总待上传的图片列表（封面置顶，推文原图依次追加）
    upload_files: List[str] = []
    if cover_image_path and os.path.exists(cover_image_path):
        upload_files.append(os.path.abspath(cover_image_path))

    if image_paths:
        for p in image_paths:
            abs_p = os.path.abspath(p)
            if os.path.exists(abs_p) and abs_p not in upload_files:
                upload_files.append(abs_p)

    if not upload_files:
        raise ValueError(f"小红书发布必须提供封面或图片，未找到有效图片文件: {cover_image_path}")

    # 小红书支持最多 18 张图片
    upload_files = upload_files[:18]

    # 1. 检查 Chrome CDP
    if not check_chrome_cdp_available(cdp_url):
        cmd = get_chrome_launch_command()
        raise ConnectionError(
            f"未检测到运行中的 Chrome 调试端口 ({cdp_url})。\n"
            f"请先打开终端执行以下命令启动带端口的 Chrome 并登录小红书：\n\n  {cmd}\n"
        )

    title_clean = clean_title_for_xhs(title, 20)
    body_info = format_xhs_body_and_topics(markdown_content, min_topics=5, max_total_chars=950)
    final_content = body_info["content"]

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(cdp_url)
        contexts = browser.contexts
        context = contexts[0] if contexts else browser.new_context()

        # 查找或新建小红书发文页
        target_page: Optional[Page] = None
        for page in context.pages:
            if "creator.xiaohongshu.com/publish" in page.url or "creator.xiaohongshu.com" in page.url:
                target_page = page
                break

        clean_publish_url = "https://creator.xiaohongshu.com/publish/publish?source=official"
        if not target_page:
            target_page = context.new_page()
            target_page.goto(clean_publish_url, wait_until="domcontentloaded", timeout=timeout_ms)
        else:
            # 无论原先停在什么页面（如上次未完成的旧草稿），强制重新导航到干净的发文入口，彻底重置旧表单与旧配图
            target_page.bring_to_front()
            target_page.goto(clean_publish_url, wait_until="domcontentloaded", timeout=timeout_ms)

        time.sleep(1.5)

        # 2. 检查登录状态
        if "login" in target_page.url:
            raise PermissionError(
                "检测到当前 Chrome 尚未登录小红书创作者服务平台！\n"
                "请先在浏览器中完成小红书登录后，再次点击推送。"
            )

        # 3. 确保切换到「上传图文」Tab
        target_page.wait_for_selector(".creator-tab, span.title", timeout=15000)
        target_page.evaluate("""() => {
            const tabs = Array.from(document.querySelectorAll(".creator-tab, span.title, .tab"));
            const tuwenTab = tabs.find(t => t.innerText && t.innerText.includes("上传图文"));
            if (tuwenTab) {
                tuwenTab.click();
            }
        }""")
        time.sleep(1.0)

        # 4. 上传当前文章对应的 3:4 超清封面图片及推文原图图集
        file_input = target_page.locator('input[type="file"]').first
        file_input.set_input_files(upload_files)
        time.sleep(3.5)

        # 检查并自动关闭任何可能阻挡后续操作的引导弹窗（如“我知道了”）
        try:
            guide_clicked = target_page.evaluate("""() => {
                const btns = Array.from(document.querySelectorAll('button, div, span, .feature-guide__btn'));
                const guideBtn = btns.find(b => (b.innerText || '').trim() === '我知道了');
                if (guideBtn) {
                    guideBtn.click();
                    return true;
                }
                return false;
            }""")
            if guide_clicked:
                time.sleep(0.5)
        except Exception:
            pass

        # 等待进入图文编辑表单
        target_page.wait_for_selector('input.d-text[placeholder*="填写标题"], .tiptap.ProseMirror', state="visible", timeout=20000)
        time.sleep(0.5)

        # 5. 先填入正文描述（小红书智能标题算法基于正文内容作为语义输入）
        desc_editor = target_page.locator('.tiptap.ProseMirror, div[contenteditable="true"]').first
        if desc_editor.is_visible():
            desc_editor.click()
            is_mac = sys.platform == "darwin"
            target_page.keyboard.press("Meta+a" if is_mac else "Control+a")
            target_page.keyboard.press("Backspace")
            time.sleep(0.2)
            desc_editor.fill(final_content)
            time.sleep(0.8)

        # 6. 填入标题：优先尝试使用小红书官方【智能标题】推荐，若不可用则使用标点友好合规标题（<=20字）
        smart_title_selected = False
        final_used_title = title_clean
        try:
            rec_btn = target_page.locator('.recommand-title-btn').first
            if rec_btn.is_visible(timeout=2500):
                rec_btn.click()
                time.sleep(1.5)
                # 检查弹窗并点击第 1 个推荐标题
                clicked_title = target_page.evaluate("""() => {
                    const pop = document.querySelector('.creator-title-recommend-popover');
                    if (!pop) return null;
                    const firstItem = pop.querySelector('.item');
                    if (firstItem && firstItem.innerText && firstItem.innerText.trim().length > 0) {
                        const txt = firstItem.innerText.trim();
                        firstItem.click();
                        return txt;
                    }
                    return null;
                }""")
                if clicked_title:
                    smart_title_selected = True
                    final_used_title = clicked_title
                    time.sleep(0.5)
        except Exception as e:
            print(f"[Info] 智能标题获取跳过或降级: {e}", file=sys.stderr)

        # 如果智能标题未成功应用，则主动填充 title_clean（标点友好截断）
        if not smart_title_selected:
            title_el = target_page.locator('input.d-text[placeholder*="填写标题"]').first
            if title_el.is_visible():
                title_el.click()
                is_mac = sys.platform == "darwin"
                target_page.keyboard.press("Meta+a" if is_mac else "Control+a")
                target_page.keyboard.press("Backspace")
                title_el.fill(title_clean)
                final_used_title = title_clean
                time.sleep(0.3)

        # 7. 勾选「原创声明」（带协议勾选与弹窗确认处理）
        original_declared = False
        try:
            card_el = target_page.locator(".custom-switch-card:has-text('原创声明')").first
            if card_el.is_visible(timeout=3000):
                card_el.scroll_into_view_if_needed()
                time.sleep(0.3)

                is_checked = target_page.evaluate("""() => {
                    const sim = document.querySelector(".custom-switch-card .d-switch-simulator");
                    return sim ? sim.classList.contains("checked") : false;
                }""")

                if not is_checked:
                    target_page.evaluate("""() => {
                        const card = document.querySelector(".custom-switch-card");
                        const inp = card ? card.querySelector("input[type=checkbox]") : null;
                        if (inp) inp.click();
                        else {
                            const switchBox = card ? card.querySelector(".d-switch") : null;
                            if (switchBox) switchBox.click();
                        }
                    }""")
                    time.sleep(1.0)

                    # 检查是否弹出了《原创声明须知》弹窗
                    has_modal = target_page.evaluate("""() => {
                        const modal = document.querySelector(".d-modal");
                        if (!modal) return false;
                        const chkBox = modal.querySelector(".d-checkbox");
                        if (chkBox) chkBox.click();
                        return true;
                    }""")

                    if has_modal:
                        time.sleep(0.8)
                        target_page.evaluate("""() => {
                            const modal = document.querySelector(".d-modal");
                            const btn = modal ? modal.querySelector("button") : null;
                            if (btn && !btn.disabled) {
                                btn.click();
                            }
                        }""")
                        time.sleep(1.0)

                original_declared = target_page.evaluate("""() => {
                    const sim = document.querySelector(".custom-switch-card .d-switch-simulator");
                    return sim ? sim.classList.contains("checked") : false;
                }""")
        except Exception as e:
            print(f"[Warning] 原创声明开启异常: {e}", file=sys.stderr)

        # 8. 添加地点：下拉框随机选择一个地点
        selected_location = ""
        try:
            search_city = city or random.choice(RANDOM_CITIES)
            target_page.evaluate("""(cityName) => {
                const wrap = document.querySelector(".address-card-wrapper");
                if (!wrap) return false;
                const select = wrap.querySelector(".d-select");
                if (select) select.click();
                const inp = wrap.querySelector("input");
                if (inp) {
                    inp.focus();
                    inp.value = cityName;
                    inp.dispatchEvent(new Event("input", { bubbles: true }));
                    return true;
                }
                return false;
            }""", search_city)
            
            time.sleep(1.5)

            selected_location = target_page.evaluate("""() => {
                const dropdowns = Array.from(document.querySelectorAll(".d-dropdown, .d-popover, .custom-dropdown-44"));
                const locDropdown = dropdowns.find(d => {
                    const t = d.innerText || "";
                    return t.includes("市") || t.includes("区") || t.includes("路") || t.includes("大厦") || t.includes("站");
                });
                
                if (!locDropdown) return "";
                const items = Array.from(locDropdown.querySelectorAll(".d-select-option, [class*=item], div, span")).filter(el => {
                    return el.children.length === 0 && el.innerText && el.innerText.trim().length > 1;
                });
                
                if (items.length === 0) return "";
                const picked = items[Math.floor(Math.random() * items.length)];
                const locText = picked.innerText.trim();
                picked.click();
                return locText;
            }""")
            time.sleep(0.5)
        except Exception as e:
            print(f"[Warning] 添加地点跳过或异常: {e}", file=sys.stderr)

        # 8.5 定时发布设置（若提供了 schedule_time）
        scheduled = False
        if schedule_time:
            try:
                # 解析 schedule_time 中的 年、月、日、时、分
                dt_match = re.search(r'(\d{4})[-/](\d{1,2})[-/](\d{1,2})\s+(\d{1,2}):(\d{1,2})', schedule_time.strip())
                if dt_match:
                    t_year = int(dt_match.group(1))
                    t_month = int(dt_match.group(2))
                    t_day = int(dt_match.group(3))
                    t_hour = f"{int(dt_match.group(4)):02d}"
                    t_min = f"{int(dt_match.group(5)):02d}"

                    # 1. 滚动到定时发布卡片
                    post_time_el = target_page.locator('.post-time-wrapper').first
                    post_time_el.scroll_into_view_if_needed()
                    time.sleep(0.4)

                    # 2. 检查开关状态，若未开则点击开启
                    is_sw_checked = target_page.evaluate("""() => {
                        const wrap = document.querySelector('.post-time-wrapper');
                        if (!wrap) return false;
                        const sim = wrap.querySelector('.d-switch-simulator');
                        return sim ? sim.classList.contains('checked') : false;
                    }""")

                    if not is_sw_checked:
                        target_page.evaluate("""() => {
                            const wrap = document.querySelector('.post-time-wrapper');
                            if (!wrap) return;
                            const inp = wrap.querySelector('input[type=checkbox]');
                            if (inp) inp.click();
                            else {
                                const sw = wrap.querySelector('.d-switch');
                                if (sw) sw.click();
                            }
                        }""")
                        time.sleep(0.8)

                    # 3. 点击打开日期时间选择弹窗
                    date_picker = target_page.locator('.post-time-wrapper .date-picker-container').first
                    if date_picker.is_visible(timeout=3000):
                        date_picker.click()
                        time.sleep(1.0)

                        # 4. 在弹出的 popover 中步进导航年份/月份，并选择对应日期与时间
                        target_page.evaluate("""({ targetYear, targetMonth, targetDay, targetHour, targetMin }) => {
                            const pop = document.querySelector('.post-time-date-picker-popover-class');
                            if (!pop) return { error: 'popover not found' };

                            const header = pop.querySelector('.d-datepicker-header');
                            if (!header) return { error: 'header not found' };
                            const clickables = Array.from(header.querySelectorAll('.d-clickable'));
                            // 0: prevYear, 1: prevMonth, 2: yearTxt, 3: monthTxt, 4: nextMonth, 5: nextYear

                            // 调整月份和年份
                            for (let step = 0; step < 24; step++) {
                                const hText = header.innerText || '';
                                const mMatch = hText.match(/(\d+)年\s*(\d+)月/);
                                if (!mMatch) break;
                                const curY = parseInt(mMatch[1]);
                                const curM = parseInt(mMatch[2]);
                                if (curY === targetYear && curM === targetMonth) {
                                    break;
                                }
                                if (curY < targetYear || (curY === targetYear && curM < targetMonth)) {
                                    if (clickables[4]) clickables[4].click();
                                } else {
                                    if (clickables[1]) clickables[1].click();
                                }
                            }

                            // 选择日期：在当前月份未禁用的单元格中匹配
                            const cells = Array.from(pop.querySelectorAll('.d-datepicker-cell:not(.disabled)'));
                            const dayCell = cells.find(c => parseInt((c.innerText || '').trim()) === targetDay);
                            if (dayCell) {
                                dayCell.click();
                            }

                            // 选择小时与分钟
                            const timebars = Array.from(pop.querySelectorAll('.d-timepicker-timebar'));
                            if (timebars.length >= 2) {
                                const hourItems = Array.from(timebars[0].querySelectorAll('*')).filter(
                                    el => el.children.length === 0 && (el.innerText || '').trim() === targetHour
                                );
                                if (hourItems.length > 0) hourItems[0].click();

                                const minItems = Array.from(timebars[1].querySelectorAll('*')).filter(
                                    el => el.children.length === 0 && (el.innerText || '').trim() === targetMin
                                );
                                if (minItems.length > 0) minItems[0].click();
                            }
                            return { success: true };
                        }""", {
                            'targetYear': t_year,
                            'targetMonth': t_month,
                            'targetDay': t_day,
                            'targetHour': t_hour,
                            'targetMin': t_min,
                        })

                        time.sleep(0.5)
                        target_page.keyboard.press("Escape")
                        time.sleep(0.5)

                        # 验证输入框是否已显示设定的时间
                        val_now = target_page.evaluate("""() => {
                            const inp = document.querySelector('.post-time-wrapper input.d-text');
                            return inp ? inp.value : '';
                        }""")
                        if val_now:
                            scheduled = True
            except Exception as e:
                print(f"[Warning] 小红书定时发布设置异常: {e}", file=sys.stderr)

        # 9. 滚动到底部并提交
        target_page.evaluate("""() => {
            window.scrollTo(0, document.body.scrollHeight);
            const pubPage = document.querySelector('.publish-page');
            if (pubPage) pubPage.scrollTop = pubPage.scrollHeight;
        }""")
        time.sleep(1.0)

        action_result_msg = ""
        is_publish_mode = (action == "publish") or bool(schedule_time)

        if is_publish_mode:
            # 执行发布（若已设置时间，小红书底层会自动作为“定时发布”提交）
            submitted = target_page.evaluate("""() => {
                const btnComp = document.querySelector('xhs-publish-btn');
                if (!btnComp) return false;
                if (typeof btnComp._onPublish === 'function') {
                    btnComp._onPublish();
                    return true;
                }
                if (btnComp._sr) {
                    const redBtn = btnComp._sr.querySelector('button.ce-btn.bg-red');
                    if (redBtn && !redBtn.disabled) {
                        redBtn.click();
                        return true;
                    }
                }
                return false;
            }""")
            if schedule_time:
                action_result_msg = f"小红书笔记已成功设置为定时发布（定时时间: {schedule_time}，标题: {final_used_title}）！"
            else:
                action_result_msg = f"小红书笔记已自动提交发布（标题: {final_used_title}）！"
            time.sleep(3.0)
        else:
            # 暂存离开（保存草稿）
            saved = target_page.evaluate("""() => {
                const btnComp = document.querySelector('xhs-publish-btn');
                if (!btnComp) return false;
                if (typeof btnComp._onSave === 'function') {
                    btnComp._onSave();
                    return true;
                }
                if (btnComp._sr) {
                    const whiteBtn = btnComp._sr.querySelector('button.ce-btn.white');
                    if (whiteBtn && !whiteBtn.disabled) {
                        whiteBtn.click();
                        return true;
                    }
                }
                return false;
            }""")
            action_result_msg = f"小红书笔记已成功暂存为草稿（标题: {final_used_title}）！"
            time.sleep(2.0)

        current_url = target_page.url

        return {
            "status": "success",
            "action": "schedule" if schedule_time else action,
            "schedule_time": schedule_time or "",
            "message": action_result_msg,
            "title": final_used_title,
            "style_name": body_info["style_name"],
            "topics_count": len(body_info["topics"]),
            "topics": body_info["topics"],
            "original_declared": original_declared,
            "location": selected_location or city,
            "page_url": current_url,
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        }


def main():
    """CLI 入口：自动发布已停用，仅保留文件生成链路。"""
    raise SystemExit("小红书自动发布已停用，请使用 outputs/小红书/ 中的素材并通过官方后台人工发布。")
    import argparse
    parser = argparse.ArgumentParser(description="小红书自动填报工具")
    parser.add_argument("--check-chrome", action="store_true", help="检查 Chrome CDP 状态")
    parser.add_argument("--title", type=str, help="笔记标题")
    parser.add_argument("--md", type=str, help="Markdown 正文路径或内容")
    parser.add_argument("--cover", type=str, help="3:4 封面图片文件路径")
    parser.add_argument("--schedule-time", type=str, default="", help="定时发布时间（如 2026-10-01 08:00）")
    parser.add_argument("--action", type=str, default="publish", choices=["draft", "publish"], help="动作模式: draft(暂存草稿) 或 publish(直接发布/定时发布)")
    parser.add_argument("--city", type=str, default="上海", help="地点搜索词")
    parser.add_argument("--cdp", type=str, default=DEFAULT_CDP_URL, help="Chrome CDP URL")
    args = parser.parse_args()

    if args.check_chrome:
        ok = check_chrome_cdp_available(args.cdp)
        print(f"Chrome CDP 状态: {'可用' if ok else '不可用'} ({args.cdp})")
        sys.exit(0 if ok else 1)

    if not args.title or not args.md:
        print("用法: python scripts/xhs_publisher.py --title <标题> --md <路径或内容> [--cover <封面>] [--action draft/publish]")
        sys.exit(1)

    md_content = args.md
    if os.path.exists(args.md):
        md_content = Path(args.md).read_text(encoding="utf-8")

    res = publish_to_xhs(
        title=args.title,
        markdown_content=md_content,
        cover_image_path=args.cover,
        schedule_time=args.schedule_time or None,
        action=args.action,
        city=args.city,
        cdp_url=args.cdp,
    )
    print(json.dumps(res, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
