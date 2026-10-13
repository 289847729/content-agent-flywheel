"""HTML / Markdown → Word(.docx) 导出 —— 多平台发布包的存档件与「头条号/Word 粘贴」用稿。

两条路径共用同一套 lxml → python-docx 遍历：
  - html_to_docx_bytes：公众号排版 HTML 直转（封面卡、Mermaid 图、表格都在其中，
    与 wechat.html 内容完全一致）；
  - markdown_to_docx_bytes：Markdown 母稿直转（可补封面图、把 Mermaid 光栅化成图片）。
覆盖标题、段落、加粗/斜体、行内代码、无序/有序列表、引用、表格、代码块与图片。
"""
from __future__ import annotations

import base64
import io
import re
from pathlib import Path

import markdown as md_lib
from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Inches, Pt
from lxml import html as lxml_html

_IMAGE_DATA_RE = re.compile(r"^data:image/[^;]+;base64,(.+)$", re.S | re.I)
# 行首自带的列表符号（公众号排版稿用行内 span 写出来了）
_OWN_MARKER_RE = re.compile(r"^\s*(?:[•·▪◦●○]|[-*]|\d{1,2}[.、)）])\s")
_MERMAID_FENCE_RE = re.compile(
    r"^[ \t]*```mermaid[ \t]*\r?\n(.*?)^[ \t]*```[ \t]*$", re.S | re.M
)


def _png_data_url(data: bytes) -> str:
    """PNG 字节 → Data URL（复用 _load_image 的解析）。"""
    return "data:image/png;base64," + base64.b64encode(data).decode("ascii")


def _default_mermaid_renderer(code: str) -> bytes | None:
    """默认用 bmmd 引擎把 Mermaid 转成 PNG 字节；无 Chrome / 失败返回 None。"""
    try:
        from . import bmmd_typeset
    except Exception:
        return None
    return bmmd_typeset.render_mermaid_png_bytes(code)


def _embed_mermaid(markdown: str, renderer) -> str:
    """把 ```mermaid 围栏换成图片引用；渲染失败时保留原围栏（回退成代码块）。"""
    if renderer is None:
        return markdown

    def replace(match: re.Match) -> str:
        try:
            png = renderer(match.group(1).strip())
        except Exception:
            png = None
        if not png:
            return match.group(0)
        return f"![mermaid]({_png_data_url(png)})"

    return _MERMAID_FENCE_RE.sub(replace, markdown)


def _tag(node) -> str:
    """返回元素小写标签名；非元素节点返回空串。"""
    return node.tag.lower() if isinstance(node.tag, str) else ""


def _load_image(src: str) -> bytes | None:
    """data URL / 本地路径 → 图片字节；http(s) 或读不到时返回 None。"""
    if not src:
        return None
    match = _IMAGE_DATA_RE.match(src.strip())
    if match:
        try:
            return base64.b64decode(match.group(1))
        except Exception:
            return None
    if src.startswith(("http://", "https://")):
        return None
    path = Path(src).expanduser()
    candidates = [path] if path.is_absolute() else [Path.cwd() / path]
    for candidate in candidates:
        if candidate.is_file():
            return candidate.read_bytes()
    return None


def _set_cjk_font(document: Document) -> None:
    """把正文默认字体设为中英兼容，避免 Word 里中文回退成奇怪字形。"""
    from docx.oxml.ns import qn

    style = document.styles["Normal"]
    style.font.name = "微软雅黑"
    style.font.size = Pt(11)
    rpr = style.element.get_or_add_rPr()
    fonts = rpr.find(qn("w:rFonts"))
    if fonts is None:
        fonts = rpr.makeelement(qn("w:rFonts"), {})
        rpr.append(fonts)
    fonts.set(qn("w:eastAsia"), "微软雅黑")
    fonts.set(qn("w:ascii"), "微软雅黑")
    fonts.set(qn("w:hAnsi"), "微软雅黑")


def _emit_text(paragraph, text: str, *, bold=False, italic=False, mono=False) -> None:
    if not text:
        return
    run = paragraph.add_run(text)
    if bold:
        run.bold = True
    if italic:
        run.italic = True
    if mono:
        run.font.name = "Consolas"


def _emit_inline(paragraph, node, *, bold=False, italic=False) -> None:
    """把行内内容（文本 + strong/em/code/a/br/img）写入段落，保留嵌套格式。"""
    _emit_text(paragraph, node.text or "", bold=bold, italic=italic)
    for child in node:
        tag = _tag(child)
        if tag in ("strong", "b"):
            _emit_inline(paragraph, child, bold=True, italic=italic)
        elif tag in ("em", "i"):
            _emit_inline(paragraph, child, bold=bold, italic=True)
        elif tag == "code":
            _emit_text(paragraph, child.text or "", mono=True)
        elif tag == "br":
            paragraph.add_run().add_break()
        elif tag == "img":
            is_mermaid = (child.get("alt") or "").strip().lower() == "mermaid"
            _add_image(paragraph, child.get("src"), width_in=6.2 if is_mermaid else 6.0)
        elif tag == "a":
            _emit_inline(paragraph, child, bold=bold, italic=italic)
        else:
            _emit_inline(paragraph, child, bold=bold, italic=italic)
        _emit_text(paragraph, child.tail or "", bold=bold, italic=italic)


def _add_image(paragraph, src: str | None, width_in: float = 6.0) -> None:
    data = _load_image(src or "")
    if not data:
        return
    run = paragraph.add_run()
    try:
        run.add_picture(io.BytesIO(data), width=Inches(width_in))
        paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    except Exception:
        paragraph.remove(run._element)


def _add_table(document: Document, node) -> None:
    rows = node.xpath(".//tr")
    if not rows:
        return
    columns = max(len(row.xpath("./th | ./td")) for row in rows)
    table = document.add_table(rows=0, cols=max(1, columns))
    table.style = "Table Grid"
    for row in rows:
        cells = row.xpath("./th | ./td")
        target = table.add_row().cells
        for index, cell in enumerate(cells):
            if index >= len(target):
                break
            paragraph = target[index].paragraphs[0]
            _emit_inline(paragraph, cell)
            if _tag(cell) == "th":
                for run in paragraph.runs:
                    run.bold = True


def _emit_list_item(document: Document, item, style: str) -> None:
    # 公众号排版稿会把列表符号（• / 1.）写成行内 span；此时再用 Word 列表样式
    # 会变成「• • 文本」双符号，退化为普通段落更贴近原稿。
    own_marker = bool(_OWN_MARKER_RE.match(item.text_content() or ""))
    style = style if not own_marker else None
    children = [_tag(child) for child in item]
    if any(tag in ("p", "ul", "ol") for tag in children):
        first = True
        for child in item:
            tag = _tag(child)
            if tag == "p":
                paragraph = document.add_paragraph(style=style if first else None)
                _emit_inline(paragraph, child)
                first = False
            elif tag in ("ul", "ol"):
                _walk_block(document, child)
            else:
                _walk_block(document, child)
    else:
        _emit_inline(document.add_paragraph(style=style), item)


def _walk_block(document: Document, node) -> None:
    tag = _tag(node)
    if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
        heading = document.add_heading(level=min(int(tag[1]), 4))
        _emit_inline(heading, node)
    elif tag == "p":
        _emit_inline(document.add_paragraph(), node)
    elif tag in ("ul", "ol"):
        style = "List Bullet" if tag == "ul" else "List Number"
        for child in node:
            if _tag(child) == "li":
                _emit_list_item(document, child, style)
    elif tag == "blockquote":
        paragraph = document.add_paragraph(style="Intense Quote")
        _emit_inline(paragraph, node)
    elif tag == "pre":
        code = "".join(node.itertext()).rstrip("\n")
        _emit_text(document.add_paragraph(), code, mono=True)
    elif tag == "table":
        _add_table(document, node)
    elif tag == "hr":
        paragraph = document.add_paragraph("· · ·")
        paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    elif tag == "img":
        is_mermaid = (node.get("alt") or "").strip().lower() == "mermaid"
        _add_image(document.add_paragraph(), node.get("src"),
                   width_in=6.2 if is_mermaid else 6.0)
    else:
        if node.text and node.text.strip():
            document.add_paragraph(node.text.strip())
        for child in node:
            _walk_block(document, child)


def _content_root(html: str):
    """取公众号排版稿的正文根节点：优先 data-copy-root，其次 body，最后整篇。"""
    document = lxml_html.fromstring(html)
    for xpath in ('//*[@data-copy-root]', '//*[@id="gzh-content"]', '//*[@id="article"]'):
        found = document.xpath(xpath)
        if found:
            return found[0]
    bodies = document.xpath("//body")
    return bodies[0] if bodies else document


def html_to_docx_bytes(html: str, title: str = "") -> bytes:
    """公众号排版 HTML → .docx 字节（内容与公众号稿完全一致）。

    HTML 由排版引擎产出：封面卡（封面图 + 钩子 + 标题）、正文、Mermaid 图
    （已光栅化成 PNG）、列表、表格都在其中。这里只按标签结构搬运内容、忽略
    内联样式，保证 docx 与 wechat.html「同源同内容」。
    """
    document = Document()
    _set_cjk_font(document)
    if title.strip():
        document.core_properties.title = title.strip()
    root = _content_root(html)
    for node in root.xpath(".//style | .//script | .//comment()"):
        parent = node.getparent()
        if parent is not None:
            parent.remove(node)
    for child in root:
        _walk_block(document, child)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def markdown_to_docx_bytes(markdown: str, title: str = "", cover_bytes: bytes | None = None,
                           mermaid_renderer=None) -> bytes:
    """Markdown 正文 → .docx 字节。

    cover_bytes 非空时在文首插入封面图；```mermaid 围栏默认渲染成 PNG 图片
    （mermaid_renderer 可覆盖渲染逻辑，返回 None 则回退成代码块）。
    """
    document = Document()
    _set_cjk_font(document)
    if cover_bytes:
        _add_image(document.add_paragraph(), _png_data_url(cover_bytes), width_in=6.2)
    if title.strip():
        document.core_properties.title = title.strip()
        heading = document.add_heading(title.strip(), level=0)
        heading.alignment = WD_ALIGN_PARAGRAPH.LEFT
    markdown = _embed_mermaid(markdown, mermaid_renderer or _default_mermaid_renderer)
    rendered = md_lib.markdown(markdown, extensions=["fenced_code", "tables"])
    root = lxml_html.fromstring(f"<div>{rendered}</div>")
    for child in root:
        _walk_block(document, child)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def save_docx(markdown: str, title: str, path: Path) -> Path:
    """把 Markdown 存成 .docx 文件，返回写入路径。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(markdown_to_docx_bytes(markdown, title))
    return path
