#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
tuiwen-jrtt-gen: 今日头条长图文 Markdown 转高质感 DOCX 发布文件排版引擎
支持自动解析标题、嵌入 16:9 封面图、渲染三线表格、头条红装饰小节、金句加粗与热议互动框。
"""

import os
import sys
import re
import argparse
import docx
from docx import Document
from docx.shared import Pt, Inches, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.oxml import parse_xml
from docx.oxml.ns import qn

# 样式常量
COLOR_PRIMARY = RGBColor(226, 44, 56)     # 头条红
COLOR_TITLE = RGBColor(34, 34, 34)        # 深黑标题
COLOR_TEXT = RGBColor(51, 51, 51)         # 正文灰黑
COLOR_MUTED = RGBColor(120, 120, 120)     # 辅助灰
FONT_NAME = 'PingFang SC'

def set_run_font(run, font_name=FONT_NAME, size_pt=11, bold=False, color_rgb=COLOR_TEXT, italic=False):
    run.font.name = font_name
    run.font.size = Pt(size_pt)
    run.bold = bold
    run.italic = italic
    run.font.color.rgb = color_rgb
    run._element.rPr.rFonts.set(qn('w:eastAsia'), font_name)

def add_p(doc, align=WD_ALIGN_PARAGRAPH.LEFT, space_before=0, space_after=6, line_spacing=1.35):
    p = doc.add_paragraph()
    p.alignment = align
    p.paragraph_format.space_before = Pt(space_before)
    p.paragraph_format.space_after = Pt(space_after)
    p.paragraph_format.line_spacing = line_spacing
    return p

def add_h2(doc, text):
    p = add_p(doc, space_before=16, space_after=8)
    run_tag = p.add_run("▌ ")
    set_run_font(run_tag, size_pt=14, bold=True, color_rgb=COLOR_PRIMARY)
    run_text = p.add_run(text)
    set_run_font(run_text, size_pt=14, bold=True, color_rgb=COLOR_TITLE)
    return p

def add_formatted_runs(p, text, default_size=11, default_color=COLOR_TEXT, default_bold=False):
    """解析 Markdown 中的 **粗体** 并添加对应 run"""
    tokens = re.split(r'(\*\*.*?\*\*)', text)
    for token in tokens:
        if token.startswith('**') and token.endswith('**'):
            content = token[2:-2]
            r = p.add_run(content)
            set_run_font(r, size_pt=default_size, bold=True, color_rgb=default_color)
        elif token:
            r = p.add_run(token)
            set_run_font(r, size_pt=default_size, bold=default_bold, color_rgb=default_color)

def parse_markdown_to_docx(md_path: str, docx_path: str, cover_image_path: str = None):
    if not os.path.exists(md_path):
        raise FileNotFoundError(f"Markdown file not found: {md_path}")

    with open(md_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    doc = Document()

    # 页面边距设置 (0.85 英寸)
    for section in doc.sections:
        section.top_margin = Inches(0.8)
        section.bottom_margin = Inches(0.8)
        section.left_margin = Inches(0.85)
        section.right_margin = Inches(0.85)

    normal_style = doc.styles['Normal']
    normal_style.font.name = FONT_NAME
    normal_style.font.size = Pt(11)
    normal_style.font.color.rgb = COLOR_TEXT
    normal_style._element.rPr.rFonts.set(qn('w:eastAsia'), FONT_NAME)

    # 提取标题与可能内嵌的封面图
    title_text = ""
    extracted_cover = cover_image_path
    clean_lines = []

    # 表格缓冲器
    in_table = False
    table_rows = []

    # 互动框缓冲器
    in_interactive = False
    interactive_lines = []

    # 过滤与解析行
    for line in lines:
        stripped = line.strip()

        # 检查是否为忽略的发布渠道元文本
        if "发布渠道：" in stripped or "深度图文专栏" in stripped:
            continue

        # 过滤多余元标签
        if stripped.startswith('> **敲定发布主标题**') or stripped.startswith('> 敲定发布主标题'):
            continue

        # 检查主标题
        if not title_text:
            if stripped.startswith('# '):
                title_text = stripped[2:].strip()
                continue

        # 检查内嵌图片
        img_match = re.match(r'!\[.*?\]\((.*?)\)', stripped)
        if img_match and not extracted_cover:
            img_rel = img_match.group(1).strip()
            # 尝试定位绝对路径
            if os.path.isabs(img_rel):
                extracted_cover = img_rel
            else:
                candidate = os.path.join(os.path.dirname(md_path), img_rel)
                if os.path.exists(candidate):
                    extracted_cover = candidate
            continue
        elif img_match:
            continue

        clean_lines.append(line)

    # 1. 写入主标题
    if title_text:
        p_title = add_p(doc, space_before=4, space_after=12, line_spacing=1.2)
        r_t = p_title.add_run(title_text)
        set_run_font(r_t, size_pt=18, bold=True, color_rgb=COLOR_TITLE)

    # 2. 插入封面图片
    if extracted_cover and os.path.exists(extracted_cover):
        p_img = add_p(doc, align=WD_ALIGN_PARAGRAPH.CENTER, space_before=4, space_after=14)
        run_img = p_img.add_run()
        run_img.add_picture(extracted_cover, width=Inches(6.6))

        # 装饰细分割线
        p_hr = add_p(doc, space_after=12)
        p_hr_border = parse_xml(r'<w:pBdr xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                                r'<w:bottom w:val="single" w:sz="6" w:space="1" w:color="E5E5E5"/>'
                                r'</w:pBdr>')
        p_hr._p.get_or_add_pPr().append(p_hr_border)

    def flush_table():
        nonlocal in_table, table_rows
        if not table_rows:
            in_table = False
            return
        
        # 过滤分隔行 (| --- |)
        filtered = [r for r in table_rows if not re.match(r'^[\|\s\-\:\+]+$', r.strip())]
        if not filtered:
            table_rows = []
            in_table = False
            return

        headers = [c.strip() for c in filtered[0].strip('|').split('|')]
        body_rows = [[c.strip() for c in r.strip('|').split('|')] for r in filtered[1:]]

        tbl = doc.add_table(rows=len(body_rows) + 1, cols=len(headers))
        tbl.alignment = WD_TABLE_ALIGNMENT.CENTER

        # 表头
        for col_idx, h in enumerate(headers):
            cell = tbl.cell(0, col_idx)
            cell.text = h
            shading = parse_xml(r'<w:shd xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" w:fill="F4F5F7"/>')
            cell._tc.get_or_add_tcPr().append(shading)
            p_cell = cell.paragraphs[0]
            p_cell.alignment = WD_ALIGN_PARAGRAPH.CENTER
            for r in p_cell.runs:
                set_run_font(r, size_pt=10, bold=True, color_rgb=COLOR_TITLE)

        # 内容
        for row_idx, r_data in enumerate(body_rows):
            for col_idx in range(len(headers)):
                cell = tbl.cell(row_idx + 1, col_idx)
                val = r_data[col_idx] if col_idx < len(r_data) else ""
                cell.text = ""
                p_cell = cell.paragraphs[0]
                p_cell.alignment = WD_ALIGN_PARAGRAPH.LEFT if col_idx == 0 else WD_ALIGN_PARAGRAPH.CENTER
                add_formatted_runs(p_cell, val, default_size=10)

        # 三线表边框
        tblBorders = parse_xml(
            r'<w:tblBorders xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            r'<w:top w:val="single" w:sz="4" w:space="0" w:color="E0E0E0"/>'
            r'<w:bottom w:val="single" w:sz="6" w:space="0" w:color="B0B0B0"/>'
            r'<w:left w:val="none"/>'
            r'<w:right w:val="none"/>'
            r'<w:insideH w:val="single" w:sz="4" w:space="0" w:color="EEEEEE"/>'
            r'<w:insideV w:val="none"/>'
            r'</w:tblBorders>'
        )
        tbl._tbl.tblPr.append(tblBorders)
        add_p(doc, space_after=6)
        table_rows = []
        in_table = False

    def flush_interactive():
        nonlocal in_interactive, interactive_lines
        if not interactive_lines:
            in_interactive = False
            return
        callout_p = add_p(doc, space_before=10, space_after=10)
        callout_p.paragraph_format.left_indent = Inches(0.2)
        callout_p.paragraph_format.right_indent = Inches(0.2)

        callout_border = parse_xml(r'<w:pBdr xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                                   r'<w:left w:val="single" w:sz="24" w:space="8" w:color="E22C38"/>'
                                   r'</w:pBdr>')
        callout_shd = parse_xml(r'<w:shd xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" w:fill="FAF0F0"/>')
        callout_p._p.get_or_add_pPr().append(callout_border)
        callout_p._p.get_or_add_pPr().append(callout_shd)

        r_title = callout_p.add_run("【互动探讨 · 头条热议】\n")
        set_run_font(r_title, size_pt=10.5, bold=True, color_rgb=COLOR_PRIMARY)
        
        full_text = " ".join([l.strip() for l in interactive_lines])
        add_formatted_runs(callout_p, full_text, default_size=10.5, default_color=COLOR_TITLE)
        interactive_lines = []
        in_interactive = False

    # 逐行正文解析
    for line in clean_lines:
        s = line.strip()

        # 表格处理
        if s.startswith('|') and s.endswith('|'):
            in_table = True
            table_rows.append(s)
            continue
        elif in_table:
            flush_table()

        # 互动框识别
        if s.startswith('### 互动') or s.startswith('**你觉得') or '欢迎在评论区留下你的看法' in s:
            in_interactive = True
            if not s.startswith('### 互动'):
                interactive_lines.append(s)
            continue
        elif in_interactive:
            if s and not s.startswith('#'):
                interactive_lines.append(s)
                continue
            else:
                flush_interactive()

        if not s:
            continue

        # 二级标题
        if s.startswith('## ') or s.startswith('### '):
            h_text = re.sub(r'^#+\s*', '', s)
            if '模块' in h_text or '爆款标题' in h_text:
                continue
            add_h2(doc, h_text)
            continue

        # 分割线
        if re.match(r'^\-{3,}$', s):
            continue

        # 列表条目
        if s.startswith('- ') or re.match(r'^\d+\.\s', s):
            p = add_p(doc, space_after=4)
            p.paragraph_format.left_indent = Inches(0.2)
            bullet_char = "●  " if s.startswith('- ') else ""
            if bullet_char:
                r_dot = p.add_run(bullet_char)
                set_run_font(r_dot, size_pt=9, bold=True, color_rgb=COLOR_PRIMARY)
                content = s[2:].strip()
            else:
                num_m = re.match(r'^(\d+\.)\s*(.*)', s)
                r_num = p.add_run(num_m.group(1) + " ")
                set_run_font(r_num, bold=True, color_rgb=COLOR_PRIMARY)
                content = num_m.group(2).strip()
            add_formatted_runs(p, content)
            continue

        # 普通段落
        p = add_p(doc, space_after=8)
        add_formatted_runs(p, s)

    # 结束收尾
    if in_table:
        flush_table()
    if in_interactive:
        flush_interactive()

    doc.save(docx_path)
    try:
        print(f"Successfully generated DOCX file at: {docx_path}")
    except OSError:
        pass

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Convert Toutiao Markdown to high-quality formatted DOCX.")
    parser.add_argument("--input", required=True, help="Path to input Markdown file")
    parser.add_argument("--output", required=True, help="Path to output DOCX file")
    parser.add_argument("--cover", required=False, help="Path to cover image")
    args = parser.parse_args()

    parse_markdown_to_docx(args.input, args.output, args.cover)
