"""bmmd 排版引擎封装 —— Markdown 经 bmmd CLI 渲染为公众号 HTML，再包封面卡。

bmmd（github.com/miantiao-me/bm.md）提供 16 款精调排版样式与 14 款代码高亮主题，
输出全内联样式的 HTML 片段，可直接粘贴公众号编辑器。
本模块负责：调用 CLI 渲染、把本地图片转 base64、包一层带封面卡的
data-copy-root 容器（满足 scripts/wechat_api.py 的推送要求）。
"""
from __future__ import annotations

import base64
import hashlib
import logging
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

from . import covers
from . import typeset as typeset_mod

SKILL_ROOT = Path(__file__).resolve().parents[2]

# 样式 ID → 展示名（与 bmmd --markdown-style 可选值一致）
STYLES = {
    "kami": "Kami · 现代书卷（默认）",
    "bauhaus": "Bauhaus · 包豪斯杂志",
    "blueprint": "Blueprint · 工程极简",
    "botanical": "Botanical · 植物清新",
    "newsprint": "Newsprint · 新闻纸",
    "retro": "Retro · 复古海报",
    "sketch": "Sketch · 手稿素描",
    "terminal": "Terminal · 终端暗色",
    "forest-review": "Forest Review · 森林评论",
    "navy-vellum": "Navy Vellum · 藏蓝牛皮纸",
    "rose-nocturne": "Rose Nocturne · 玫瑰夜曲",
    "solar-catalog": "Solar Catalog · 暖色目录",
    "triad-paper": "Triad Paper · 三色纸",
    "field-tablet": "Field Tablet · 田野平板",
    "public-square": "Public Square · 人文报章",
    "pixel-orbit": "Pixel Orbit · 像素暗色",
}

CODE_THEMES = {
    "": "默认（随样式）",
    "catppuccin-latte": "Catppuccin Latte",
    "catppuccin-frappe": "Catppuccin Frappe",
    "catppuccin-macchiato": "Catppuccin Macchiato",
    "catppuccin-mocha": "Catppuccin Mocha",
    "kimbie-light": "Kimbie Light",
    "kimbie-dark": "Kimbie Dark",
    "panda-syntax-light": "Panda Syntax Light",
    "panda-syntax-dark": "Panda Syntax Dark",
    "paraiso-light": "Paraiso Light",
    "paraiso-dark": "Paraiso Dark",
    "rose-pine": "Rose Pine",
    "rose-pine-dawn": "Rose Pine Dawn",
    "tokyo-night-light": "Tokyo Night Light",
    "tokyo-night-dark": "Tokyo Night Dark",
}

DEFAULT_STYLE = "kami"

# 微信编辑器会对内联 SVG 做二次处理（改写属性、影响回读校验），
# 因此 bmmd 渲染出的 Mermaid 图统一用 Chrome 无头模式转成 PNG 内嵌。
_CHROME_CANDIDATES = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "google-chrome",
    "chromium",
    "chromium-browser",
    "chrome",
)
_MERMAID_FIGURE_RE = re.compile(
    r'<figure[^>]*data-bm-rich="mermaid"[^>]*>.*?</figure>', re.S
)
_SVG_RE = re.compile(r"<svg\b.*?</svg>", re.S)
_VIEWBOX_RE = re.compile(
    r'viewBox="\s*[\d.]+[ ,]+[\d.]+[ ,]+([\d.]+)[ ,]+([\d.]+)\s*"'
)
_png_cache: dict[str, str] = {}
_png_bytes_cache: dict[str, bytes] = {}
_svg_cache: dict[str, str] = {}


def _find_chrome() -> str | None:
    """定位可用的 Chrome/Chromium 可执行文件（用于 SVG→PNG 光栅化）。"""
    for candidate in _CHROME_CANDIDATES:
        path = Path(candidate)
        if path.is_file():
            return str(path)
        found = shutil.which(candidate)
        if found:
            return found
    return None


def _trim_png(path: Path, bg: tuple[int, int, int] = (255, 255, 255),
              threshold: int = 12, pad: int = 10) -> None:
    """裁掉截图四周的空白区（按与背景色的显著差异定位内容边界）。"""
    try:
        from PIL import Image, ImageChops
    except ImportError:
        return
    image = Image.open(path).convert("RGB")
    diff = ImageChops.difference(
        image, Image.new("RGB", image.size, bg)
    ).convert("L")
    mask = diff.point(lambda value: 255 if value > threshold else 0)
    box = mask.getbbox()
    if not box:
        return
    left, top, right, bottom = box
    width, height = image.size
    box = (max(0, left - pad), max(0, top - pad),
           min(width, right + pad), min(height, bottom + pad))
    image.crop(box).save(path)


_STYLE_NOISE = (
    # 以上三项经像素级截图对比验证为 0 差异；字体抗锯齿（font-smoothing）
    # 会改变字重，其余样式可能影响布局，均不可删。
    "box-sizing: border-box;",
    "widows: 2; orphans: 2;",
    "overflow-wrap: break-word;",
)


def _slim_styles(fragment: str) -> str:
    """删除对微信渲染无影响的内联样式，为 20000 字符限制腾出余量。

    只在 style 属性值内做字符串替换，不改动任何标签结构（历史教训：
    破坏性裁剪曾导致 section 标签失衡、草稿渲染全乱）。
    """
    def clean(match: re.Match) -> str:
        value = match.group(1)
        for noise in _STYLE_NOISE:
            value = value.replace(noise, "")
        return f'style="{value}"'

    return re.sub(r'style="([^"]*)"', clean, fragment)


def _svg_to_png_data_url(svg: str, fg: str = "#333333", bg: str = "#ffffff",
                        scale: int = 2) -> str:
    """用 Chrome 无头模式把单个 SVG 渲染成 PNG data URL（2x 高清）。

    bmmd 的 Mermaid SVG 样式依赖 --fg/--bg 等 CSS 变量，而这些变量只在其
    完整页面里定义；单独拿出 SVG 渲染时必须补上，否则 fill 解析失败，
    节点会回退成 SVG 默认的黑色实心块。另外 mermaid 自算的 viewBox 宽度
    有时装不下实际字体下的文字，因此给足渲染余量后再裁剪。
    """
    chrome = _find_chrome()
    if not chrome:
        raise RuntimeError("未找到 Chrome/Chromium，无法把 Mermaid 图转成图片")
    match = _VIEWBOX_RE.search(svg)
    width, height = (float(match.group(1)), float(match.group(2))) if match else (720.0, 360.0)
    width = max(120.0, min(width, 1400.0))
    height = max(60.0, min(height, 2000.0))
    # 留 50% 余量，避免字体差异导致文字溢出被裁断
    win_w = int(width * 1.5) + 80
    win_h = int(height * 1.5) + 80

    page = (
        '<!DOCTYPE html><html><head><meta charset="utf-8"><style>'
        f":root{{--fg:{fg};--bg:{bg};}}"
        "html,body{margin:0;padding:0;background:#ffffff}"
        "svg{display:block}"
        f"</style></head><body>{svg}</body></html>"
    )
    with tempfile.TemporaryDirectory(prefix="bmmd-svg-") as tmp:
        html_path = Path(tmp) / "svg.html"
        png_path = Path(tmp) / "svg.png"
        html_path.write_text(page, encoding="utf-8")
        subprocess.run(
            [
                chrome, "--headless", "--disable-gpu", "--hide-scrollbars",
                f"--force-device-scale-factor={scale}",
                f"--window-size={win_w},{win_h}",
                f"--screenshot={png_path}",
                html_path.as_uri(),
            ],
            capture_output=True, text=True, timeout=60, cwd=str(SKILL_ROOT),
        )
        if not png_path.is_file() or png_path.stat().st_size == 0:
            raise RuntimeError("Chrome 渲染 Mermaid 图失败")
        _trim_png(png_path)
        data = png_path.read_bytes()
    return "data:image/png;base64," + base64.b64encode(data).decode("ascii")


def _rasterize_mermaid(fragment: str, palette: dict[str, str] | None = None) -> str:
    """把 Mermaid figure 里的内联 SVG 换成 PNG 图片（微信兼容）。

    微信编辑器会改写内联 SVG（导致回读不一致且样式丢失），转换失败时
    直接移除图形并留一行提示，避免把 SVG 直推微信。
    """
    palette = palette or {}
    fg = palette.get("fg", "#333333")
    bg = palette.get("bg", "#ffffff")

    def replace(match: re.Match) -> str:
        figure = match.group(0)
        svg_match = _SVG_RE.search(figure)
        if not svg_match:
            return figure
        svg = svg_match.group(0)
        digest = hashlib.sha256(f"{svg}|{fg}|{bg}".encode("utf-8")).hexdigest()
        if digest not in _png_cache:
            try:
                _png_cache[digest] = _svg_to_png_data_url(svg, fg, bg)
            except Exception:
                return (
                    '<p style="text-align:center;margin:18px 0;color:#9ca3af;'
                    'font-size:13px">（流程图已省略）</p>'
                )
        return (
            '<p style="text-align:center;margin:18px 0">'
            f'<img src="{_png_cache[digest]}" '
            'style="max-width:100%;height:auto;display:block;margin:0 auto"></p>'
        )

    return _MERMAID_FIGURE_RE.sub(replace, fragment)


def _mermaid_svg(code: str, style: str) -> str | None:
    """把一段 Mermaid 源码渲染成 SVG（按 样式+源码 缓存，避免重复调 bmmd CLI）。"""
    key = f"{style}|{code.strip()}"
    if key in _svg_cache:
        return _svg_cache[key]
    markdown = f"```mermaid\n{code.strip()}\n```\n"
    try:
        fragment = render_fragment(markdown, style)
    except Exception:
        return None
    figure = _MERMAID_FIGURE_RE.search(fragment)
    svg_match = _SVG_RE.search(figure.group(0)) if figure else None
    if not svg_match:
        return None
    _svg_cache[key] = svg_match.group(0)
    return _svg_cache[key]


def _svg_png_bytes(svg: str, fg: str, bg: str) -> bytes | None:
    """SVG → PNG 字节（按 内容+配色 缓存）。"""
    digest = hashlib.sha256(f"{svg}|{fg}|{bg}".encode("utf-8")).hexdigest()
    if digest not in _png_bytes_cache:
        try:
            data_url = _svg_to_png_data_url(svg, fg, bg)
        except Exception:
            return None
        _png_bytes_cache[digest] = base64.b64decode(data_url.split(",", 1)[1])
    return _png_bytes_cache[digest]


def render_mermaid_png_bytes(code: str, style: str = DEFAULT_STYLE,
                             fg: str = "#333333", bg: str = "#ffffff") -> bytes | None:
    """把一段 Mermaid 源码渲染成 PNG 字节（供 Word 文档嵌入）。

    无 Chrome 或渲染失败时返回 None，调用方应回退成普通代码块。
    """
    if not _find_chrome():
        return None
    svg = _mermaid_svg(code, style)
    if not svg:
        return None
    return _svg_png_bytes(svg, fg, bg)


def render_mermaid_image(code: str, style: str = DEFAULT_STYLE,
                         fg: str = "#333333", bg: str = "#ffffff") -> str:
    """把一段 Mermaid 源码渲染成居中的 <img>（PNG data URL）HTML 片段。

    供内置排版引擎复用 bmmd 的 Mermaid 渲染能力。无 Chrome 或渲染失败时
    返回空字符串，调用方应回退成普通代码块。
    """
    png = render_mermaid_png_bytes(code, style, fg, bg)
    if not png:
        return ""
    data_url = "data:image/png;base64," + base64.b64encode(png).decode("ascii")
    return (
        '<p style="text-align:center;margin:18px 0">'
        f'<img src="{data_url}" '
        'style="max-width:100%;height:auto;display:block;margin:0 auto"></p>'
    )


def _find_node_and_bmmd() -> list[str]:
    """定位稳定的 Node 与 bmmd 执行命令。

    优先寻找系统稳定的 Node 解释器（如 Homebrew 安装的 Node）直接执行已缓存的 bmmd.mjs，
    避免 Node 22 在子进程模式下的 InitializeOncePerProcessInternal 断言崩溃，
    并大幅提升 CLI 唤起速度。
    """
    node_candidates = [
        "/opt/homebrew/bin/node",
        "/usr/local/bin/node",
        shutil.which("node"),
    ]
    node = next((n for n in node_candidates if n and Path(n).is_file() and os.access(n, os.X_OK)), None)

    # 1. 优先尝试本地缓存或全局安装的 bmmd.mjs
    mjs_candidates = list(Path.home().glob(".npm/_npx/*/node_modules/bmmd/bin/bmmd.mjs"))
    if node and mjs_candidates:
        latest_mjs = max(mjs_candidates, key=lambda p: p.stat().st_mtime)
        return [node, str(latest_mjs)]

    # 2. 尝试全局 bmmd 命令
    bmmd_bin = shutil.which("bmmd")
    if bmmd_bin:
        return [bmmd_bin]

    # 3. 兜底使用 npx（优先用 brew 的 npx）
    npx_candidates = [
        "/opt/homebrew/bin/npx",
        "/usr/local/bin/npx",
        shutil.which("npx"),
    ]
    npx_bin = next((n for n in npx_candidates if n and Path(n).is_file() and os.access(n, os.X_OK)), None)
    if npx_bin:
        return [npx_bin, "-y", "bmmd"]

    raise RuntimeError("未找到 Node.js 或 bmmd，请先安装 Node.js")


def render_fragment(markdown: str, style: str = DEFAULT_STYLE,
                    code_theme: str | None = None) -> str:
    """调用 bmmd CLI 把 Markdown 渲染成微信平台 HTML 片段。

    机器上没有 Chrome 时无法把 Mermaid 转成图片，此时把 mermaid 围栏降级为
    普通代码块（微信兼容），避免直推 SVG 导致草稿回读失败。
    """
    if not _find_chrome():
        markdown = re.sub(r"^(\s*)```mermaid\b", r"\1```", markdown, flags=re.M)
    base_cmd = _find_node_and_bmmd()
    style = style if style in STYLES else DEFAULT_STYLE
    cmd = base_cmd + ["render", "--platform", "wechat", "--markdown-style", style]
    if code_theme and code_theme in CODE_THEMES:
        cmd += ["--code-theme", code_theme]

    with tempfile.NamedTemporaryFile(
        "w", suffix=".md", prefix="bmmd-", encoding="utf-8", delete=False
    ) as handle:
        handle.write(markdown)
        tmp_path = handle.name
    try:
        result = subprocess.run(
            cmd + [tmp_path],
            capture_output=True, text=True, timeout=120, cwd=str(SKILL_ROOT),
        )
    finally:
        Path(tmp_path).unlink(missing_ok=True)

    if result.returncode != 0:
        err_text = (result.stderr or "").strip()
        logger.error("bmmd 渲染失败 (code %d):\n%s", result.returncode, err_text)
        lines = [line.strip() for line in err_text.splitlines() if line.strip()]
        # 过滤掉以数字和 0x 十六进制开头的栈回溯行（避免提取到 dyld/abort 栈底）
        meaningful = [
            l for l in lines
            if not re.match(r"^\d+:\s+0x[0-9a-fA-F]+", l)
            and not l.startswith("stack backtrace:")
        ]
        msg = meaningful[0] if meaningful else (lines[0] if lines else f"进程异常退出（exit {result.returncode}）")
        raise RuntimeError(f"bmmd 渲染失败：{msg}")
    fragment = result.stdout.strip()
    if not fragment:
        raise RuntimeError("bmmd 未输出内容")
    return fragment


def _inline_local_images(fragment: str) -> str:
    """把片段中引用的本地图片换成 base64 Data URL（推送管线只认内嵌图片）。"""
    def replace(match: re.Match) -> str:
        tag = match.group(0)
        src_match = re.search(r'src="([^"]+)"', tag)
        if not src_match:
            return tag
        src = src_match.group(1)
        if src.startswith(("data:", "http://", "https://")):
            return tag
        resolved = typeset_mod._resolve_local_image(src)
        return tag.replace(src_match.group(0), f'src="{resolved}"')
    return re.sub(r"<img[^>]*>", replace, fragment)


def _extract_palette(fragment: str) -> dict[str, str]:
    """从 bmmd 输出根节点的内联样式里提取配色（背景色 / 主文字色）。"""
    match = re.search(r"<section[^>]*>", fragment)
    defaults = {"bg": "#ffffff", "fg": "#333333"}
    if not match:
        return defaults
    tag = match.group(0)
    bg = re.search(r"background-color:\s*([#\w]+)", tag)
    fg = re.search(r"color:\s*([#\w]+)", tag)
    return {
        "bg": bg.group(1) if bg else defaults["bg"],
        "fg": fg.group(1) if fg else defaults["fg"],
    }


def _is_dark(hex_color: str) -> bool:
    """按亮度粗略判断颜色是否为深色（用于选封面图与文字对比色）。"""
    value = hex_color.lstrip("#")
    if len(value) != 6:
        return False
    try:
        r, g, b = (int(value[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return False
    return (0.299 * r + 0.587 * g + 0.114 * b) < 128


def _cover_card(title: str, hook: str, subtitle: str, palette: dict[str, str],
                cover_data_url: str) -> str:
    """封面卡：顶部横幅大图 + 删除线钩子句 + 主标题 + 副标题，配色跟随样式。"""
    bg, fg = palette["bg"], palette["fg"]
    dark = _is_dark(bg)
    card_bg = "#ffffff" if not dark else bg
    muted = "#8a8a86" if not dark else "rgba(255,255,255,0.55)"
    border = f"rgba(0,0,0,0.08)" if not dark else "rgba(255,255,255,0.14)"
    title_color = "#1a1a1a" if not dark else "#f5f5f5"

    hook_html = (
        f'<p style="font-size:15px;color:{muted};margin:0 0 6px;'
        f'text-decoration:line-through;letter-spacing:0.5px">{hook}</p>'
        if hook.strip() else ""
    )
    subtitle_html = (
        f'<p style="font-size:13px;color:{muted};margin:0;line-height:1.7">{subtitle}</p>'
        if subtitle.strip() else ""
    )
    return (
        f'<section style="margin:0 0 28px;background:{card_bg};border:1.5px solid {border};'
        f"border-radius:16px;overflow:hidden;box-shadow:0 4px 20px rgba(0,0,0,0.05)\">"
        f'<img src="{cover_data_url}" style="width:100%;height:auto;display:block">'
        f'<section style="padding:20px 22px 20px">'
        f"{hook_html}"
        f'<p style="font-size:21px;font-weight:800;color:{title_color};margin:0;line-height:1.3">{title}</p>'
        f"{subtitle_html}"
        f"</section></section>"
    )


def typeset(markdown: str, *, title: str, author: str, hook: str = "",
            subtitle: str = "", style: str = DEFAULT_STYLE,
            code_theme: str | None = None, cover_data_url: str | None = None) -> str:
    """Markdown → 完整公众号排版 HTML 文档（bmmd 样式 + 封面卡 + data-copy-root）。"""
    fragment = render_fragment(markdown, style, code_theme)
    palette = _extract_palette(fragment)
    fragment = _rasterize_mermaid(fragment, palette)
    fragment = _slim_styles(fragment)
    fragment = _inline_local_images(fragment)
    dark = _is_dark(palette["bg"])

    title = (title or "").strip() or "未命名文章"
    author = (author or "").strip() or "小崽子AI"
    style_name = STYLES.get(style, style)
    if not cover_data_url:
        cover_bg = palette["bg"] if dark else "#ffffff"
        cover_fg = "#f5f5f5" if dark else "#1a1a1a"
        cover_data_url = covers.article_cover_data_url(
            title, bg=cover_bg, fg=cover_fg, accent=palette["fg"], kicker=author
        )
    cover_card = _cover_card(title, hook, subtitle, palette, cover_data_url)
    signature = (
        f'<section style="margin:28px 0 4px;padding-top:16px;'
        f'border-top:1px solid {"rgba(255,255,255,0.15)" if dark else "#e5e5e5"}">'
        f'<p style="font-size:13px;color:{"rgba(255,255,255,0.5)" if dark else "#9ca3af"};'
        f'margin:0;text-align:right">— {author} · {style_name}排版</p></section>'
    )

    content = (
        f'<section data-copy-root="" style="max-width:677px;margin:0 auto;background:#ffffff;'
        f'padding:10px 0 20px">'
        f"{cover_card}"
        f"{fragment}"
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
    """正文节点内容字符数（与 wechat_api 推送限制口径一致）。"""
    return typeset_mod.content_length(html)
