"""Markdown → 公众号样式 HTML 轻量排版引擎（内置主题，带 data-copy-root）。

页面操作台的「文案预览」环节使用。相比 gzh-design-skill 的完整主题库，
这里内置三个精简主题（摸鱼绿 / 石墨极简 / 橄榄手记），
输出自包含样式（全部内联属性，可被 scripts/wechat_api.py 直接推送）。

约束：
- 输出根节点带 data-copy-root，满足 wechat_api.extract_copy_root 要求；
- 正文无真实图片时自动内嵌主题默认封面，保证正文 >= 1 张图（推送硬性要求）；
- Markdown 中引用的本地图片自动读取并转成 base64 Data URL。
"""
from __future__ import annotations

import base64
import re
import sys
from pathlib import Path

import markdown as md_lib
from lxml import html as lxml_html

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import wechat_api  # noqa: E402

SKILL_ROOT = Path(__file__).resolve().parents[2]

# 主题调色板（参照 outputs/ 中三套已有排版稿的气质）
THEMES = {
    "moyu-green": {
        "name": "摸鱼绿",
        "accent": "#059669",
        "accent_light": "#ECFDF5",
        "ink": "#111827",
        "body": "#374151",
        "gray": "#9CA3AF",
        "line": "#E5E7EB",
        "card_border": "rgba(5,150,105,0.15)",
        "label": "PART",
    },
    "graphite-minimal": {
        "name": "石墨极简",
        "accent": "#F97316",
        "accent_light": "#F4F4F5",
        "ink": "#27272A",
        "body": "#52525B",
        "gray": "#A1A1AA",
        "line": "#E4E4E7",
        "card_border": "rgba(39,39,42,0.12)",
        "label": "PART",
    },
    "olive-journal": {
        "name": "橄榄手记",
        "accent": "#ed7b2f",
        "accent_light": "#eeefe9",
        "ink": "#1e1f23",
        "body": "#4d4f46",
        "gray": "#9ca3af",
        "line": "#bfc1b7",
        "card_border": "rgba(30,31,35,0.12)",
        "label": "PART",
    },
}
DEFAULT_THEME = "moyu-green"

IMAGE_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}

INLINE_IMAGE_RE = re.compile(r"<img[^>]*>")
MERMAID_FENCE_RE = re.compile(
    r"^[ \t]*```mermaid[ \t]*\n(.*?)\n[ \t]*```[ \t]*$", re.M | re.S
)


def _theme(theme: str) -> dict:
    return THEMES.get(theme) or THEMES[DEFAULT_THEME]


def _resolve_local_image(src: str) -> str:
    """本地图片路径 → base64 Data URL；找不到文件时原样返回（预览可见裂图，推送会报错）。"""
    if src.startswith(("data:", "http://", "https://")):
        return src
    candidates = []
    path = Path(src).expanduser()
    if path.is_absolute():
        candidates.append(path)
    else:
        candidates.append(SKILL_ROOT / path)
        candidates.append(Path.cwd() / path)
    for candidate in candidates:
        if candidate.is_file():
            mime = IMAGE_MIME.get(candidate.suffix.lower(), "image/png")
            data = base64.b64encode(candidate.read_bytes()).decode("ascii")
            return f"data:{mime};base64,{data}"
    return src


def _inline_images(fragment: str) -> str:
    def replace(match: re.Match) -> str:
        tag = match.group(0)
        src_match = re.search(r'src="([^"]+)"', tag)
        if not src_match:
            return tag
        resolved = _resolve_local_image(src_match.group(1))
        return tag.replace(src_match.group(0), f'src="{resolved}"')

    return INLINE_IMAGE_RE.sub(replace, fragment)


def _extract_mermaid(markdown: str, t: dict) -> tuple[str, list[str]]:
    """把 ```mermaid 围栏替换为占位注释，并渲染成 PNG 图列表。

    复用 bmmd 引擎的 Mermaid 渲染能力（延迟导入避免循环依赖）。
    无 Chrome 或渲染失败时保留原围栏，回退成普通代码块。
    """
    images: list[str] = []

    def replace(match: re.Match) -> str:
        try:
            from . import bmmd_typeset

            image = bmmd_typeset.render_mermaid_image(
                match.group(1), bmmd_typeset.DEFAULT_STYLE, t["body"], "#ffffff"
            )
        except Exception:
            image = ""
        if not image:
            return match.group(0)
        images.append(image)
        return f"\n\n<!--BMMD-MERMAID-{len(images) - 1}-->\n\n"

    return MERMAID_FENCE_RE.sub(replace, markdown), images


def _inline_styles(fragment: str, t: dict) -> str:
    """对 Markdown 生成的富文本行内元素套主题样式（跳过 pre 代码块）。"""
    fragment = re.sub(
        r"<strong>(.*?)</strong>",
        rf'<strong style="font-weight:700;color:{t["accent"]}">\1</strong>',
        fragment,
        flags=re.S,
    )
    fragment = re.sub(
        r"<em>(.*?)</em>",
        r'<em style="font-style:italic">\1</em>',
        fragment,
        flags=re.S,
    )
    fragment = re.sub(
        r"<code>(.*?)</code>",
        r'<code style="background:#F3F4F6;padding:2px 6px;border-radius:4px;font-size:13px;color:#111827">\1</code>',
        fragment,
        flags=re.S,
    )
    fragment = re.sub(
        r'<a href="([^"]+)">(.*?)</a>',
        rf'<a href="\1" style="color:{t["accent"]};text-decoration:none;border-bottom:1px solid {t["accent"]}">\2</a>',
        fragment,
        flags=re.S,
    )
    return fragment


def _block_html(node, t: dict, part_counter: list[int]) -> str:
    tag = node.tag
    if tag == "p":
        children = lxml_html.tostring(node, encoding="unicode", method="html")
        children = re.sub(r"^<p>(.*)</p>$", r"\1", children, flags=re.S)
        images = node.xpath(".//img")
        if images and not node.text_content().strip():
            # 纯图片段落 → 居中图注块
            return (
                f'<p style="margin:6px 0 20px;text-align:center">'
                f'<img src="{images[0].get("src")}" '
                f'style="max-width:100%;height:auto;display:block;margin:0 auto;border-radius:8px"></p>'
            )
        inner = _inline_styles(children, t)
        return f'<p style="font-size:15px;color:{t["body"]};margin:0 0 14px;text-align:justify">{inner}</p>'
    if tag in ("h1", "h2", "h3"):
        text = node.text_content().strip()
        if not text:
            return ""
        if tag == "h2":
            part_counter[0] += 1
            number = f"{part_counter[0]:02d}"
            return (
                f'<section style="margin:26px 0 14px">'
                f'<p style="font-size:10px;color:{t["accent"]};letter-spacing:2px;margin:0 0 6px">'
                f'{t["label"]} {number}</p>'
                f'<p style="font-size:19px;font-weight:900;color:{t["ink"]};margin:0;line-height:1.4">{text}</p>'
                f'<section style="height:4px;width:44px;background:{t["accent"]};border-radius:2px;margin-top:8px"></section>'
                f"</section>"
            )
        if tag == "h1":
            return (
                f'<p style="font-size:20px;font-weight:900;color:{t["ink"]};margin:24px 0 12px;line-height:1.4">'
                f"{text}</p>"
            )
        return (
            f'<p style="font-size:16px;font-weight:700;color:{t["accent"]};margin:18px 0 10px">{text}</p>'
        )
    if tag in ("ul", "ol"):
        # 微信会重置 list-style，改用符号 + 悬挂缩进保证圆点可见
        items = []
        for idx, li in enumerate(node.xpath("./li"), start=1):
            children = lxml_html.tostring(li, encoding="unicode", method="html")
            children = re.sub(r"^<li>(.*)</li>$", r"\1", children, flags=re.S)
            marker = "•" if tag == "ul" else f"{idx}."
            items.append(
                f'<li style="margin:7px 0;padding-left:20px;text-indent:-20px;'
                f'font-size:15px;color:{t["body"]};line-height:1.8">'
                f'<span style="color:{t["accent"]};font-weight:700">{marker}</span> '
                f'{_inline_styles(_inline_images(children), t)}</li>'
            )
        if not items:
            return ""
        return (
            '<ul style="margin:0 0 16px;padding:0;list-style:none">'
            + "".join(items)
            + "</ul>"
        )
    if tag == "table":
        head_cells = node.xpath("./thead//th | ./thead//td")
        body_rows = node.xpath("./tbody/tr") or node.xpath("./tr")
        if not head_cells and not body_rows:
            return ""

        def cell_inner(cell) -> str:
            frag = lxml_html.tostring(cell, encoding="unicode", method="html")
            frag = re.sub(r"^<(?:td|th)[^>]*>(.*)</(?:td|th)>$", r"\1", frag, flags=re.S)
            return _inline_styles(frag.strip(), t)

        th_style = (
            f'padding:9px 10px;font-size:13px;font-weight:700;color:#ffffff;'
            f'background:{t["accent"]};border:1px solid {t["accent"]};text-align:left'
        )
        td_style = (
            f'padding:9px 10px;font-size:13px;color:{t["body"]};'
            f'border:1px solid {t["line"]};text-align:left'
        )
        parts = [
            '<table style="width:100%;border-collapse:collapse;margin:0 0 18px;font-size:13px">'
        ]
        if head_cells:
            parts.append(
                "<tr>" + "".join(
                    f'<th style="{th_style}">{cell_inner(cell)}</th>' for cell in head_cells
                ) + "</tr>"
            )
        for row in body_rows:
            cells = row.xpath("./td | ./th")
            if not cells:
                continue
            parts.append(
                "<tr>" + "".join(
                    f'<td style="{td_style}">{cell_inner(cell)}</td>' for cell in cells
                ) + "</tr>"
            )
        parts.append("</table>")
        return "".join(parts)
    if tag == "blockquote":
        text = node.text_content().strip()
        if not text:
            return ""
        return (
            f'<section style="background:{t["accent_light"]};border-left:4px solid {t["accent"]};'
            f'border-radius:0 12px 12px 0;padding:14px 16px;margin:0 0 16px">'
            f'<p style="font-size:14px;color:{t["ink"]};margin:0;line-height:1.8">{text}</p>'
            f"</section>"
        )
    if tag == "pre":
        code = node.text_content().rstrip("\n")
        if not code:
            return ""
        return (
            f'<pre style="background:#F8FAFC;border:1px solid {t["line"]};border-radius:12px;'
            f"padding:14px 16px;font-size:13px;line-height:1.6;overflow-x:auto;margin:0 0 16px>"
            f'<code style="color:{t["ink"]}">{code}</code></pre>'
        )
    if tag == "hr":
        return (
            f'<p style="text-align:center;color:{t["gray"]};margin:8px 0 20px;'
            f'letter-spacing:4px">· · ·</p>'
        )
    return ""


def typeset(markdown: str, *, title: str, author: str, theme: str = DEFAULT_THEME,
            hook: str = "", subtitle: str = "", cover_data_url: str | None = None) -> str:
    """Markdown → 完整公众号排版 HTML 文档（含封面卡与内嵌图）。

    cover_data_url 缺省时按主题生成“带文章标题”的专属封面（2.35:1）。
    """
    t = _theme(theme)
    title = title.strip() or "未命名文章"
    author = author.strip() or "小崽子AI"
    if not cover_data_url:
        from . import covers

        cover_data_url = covers.article_cover_data_url_for_theme(title, theme, author)

    markdown, mermaid_images = _extract_mermaid(markdown, t)
    rendered = md_lib.markdown(markdown, extensions=["fenced_code", "tables"])
    for index, image in enumerate(mermaid_images):
        rendered = rendered.replace(f"<!--BMMD-MERMAID-{index}-->", image)
    rendered = _inline_images(rendered)
    root = lxml_html.fromstring(f"<div>{rendered}</div>")
    part_counter = [0]
    body_blocks = [
        _block_html(child, t, part_counter)
        for child in root
    ]
    body_html = "".join(block for block in body_blocks if block)

    # 封面卡：删除线钩子句 + 主标题 + 副标题 + 封面图（右）
    hook_html = (
        f'<p style="font-size:15px;color:{t["gray"]};margin:0 0 6px;text-decoration:line-through;'
        f'letter-spacing:0.5px">{hook}</p>'
        if hook.strip()
        else ""
    )
    subtitle_html = (
        f'<p style="font-size:13px;color:{t["gray"]};margin:0;line-height:1.7">{subtitle}</p>'
        if subtitle.strip()
        else ""
    )
    cover_card = (
        f'<section style="margin:0 0 28px;background:#fff;border:1.5px solid {t["card_border"]};'
        f"border-radius:20px;overflow:hidden;box-shadow:0 4px 20px rgba(0,0,0,0.06)\">"
        f'<img src="{cover_data_url}" style="width:100%;height:auto;display:block">'
        f'<section style="padding:22px 24px 24px">'
        f"{hook_html}"
        f'<p style="font-size:22px;font-weight:900;color:{t["ink"]};margin:0;line-height:1.3">{title}</p>'
        f"{subtitle_html}"
        f"</section></section>"
    )

    # 尾注：作者签名
    signature = (
        f'<section style="margin:28px 0 4px;padding-top:16px;border-top:1px solid {t["line"]}">'
        f'<p style="font-size:13px;color:{t["gray"]};margin:0;text-align:right">— {author} · '
        f"{t['name']}排版</p></section>"
    )

    content = (
        f'<section data-copy-root="" style="max-width:677px;margin:0 auto;background:#ffffff;'
        f'color:{t["body"]};line-height:1.75;letter-spacing:0.5px;padding:10px 0 20px">'
        f"{cover_card}"
        f"{body_html}"
        f"{signature}"
        f"</section>"
    )
    return (
        "<!DOCTYPE html>\n"
        '<html lang="zh-CN"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        "<title>公众号排版预览</title></head>"
        '<body style="margin:0;background:#f5f5f5;padding:12px">'
        f"{content}</body></html>"
    )


def content_length(html: str) -> int:
    """返回排版稿正文节点内容字符数（与 wechat_api 推送限制口径一致）。

    wechat_api 在校验长度前会把内嵌图片替换为短 URL，
    所以这里同样把 Data URL 折成短占位再统计，避免封面图误报超限。
    """
    try:
        root = wechat_api.extract_copy_root(html)
        content = lxml_html.tostring(root, encoding="unicode", method="html")
    except Exception:
        content = html
    content = re.sub(r"data:[^\"')\s]+", "data:img", content)
    return len(content)
