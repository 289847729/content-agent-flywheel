"""多平台全自动封面：handraw-style 选风格/构图/配色，Gemini 负责实际生图。"""
from __future__ import annotations

import base64
import hashlib
import json
import re
import shutil
import tempfile
import threading
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

from . import covers, llm

HANDRAW_ROOT = Path(__file__).resolve().parents[2] / ".agents" / "vendor" / "handraw-style"
HANDRAW_REFERENCES = HANDRAW_ROOT / "skills" / "handdraw-style-prompter" / "references"
PLATFORM_CACHE_DIR = Path(__file__).resolve().parents[2] / "outputs" / "平台封面"
SPEC_VERSION = 1

PLATFORM_SPECS = {
    "xhs": {
        "name": "小红书",
        "audience": "移动端快速浏览的科技、效率和数码读者；先被画面和短标题吸引，再决定是否收藏和阅读。",
        "ratio": "3:4 vertical",
        "size": "1024x1536",
        "target": (1080, 1440),
        "label": "小红书 · 一图读懂",
        "layouts": "social-card or infographic layouts, preferably a clear social cover with one dominant visual and short information hierarchy",
    },
    "toutiao": {
        "name": "今日头条",
        "audience": "信息流中的大众科技读者；需要一眼看懂事件、主体和冲突，画面要有新闻感、对比感和可读标题。",
        "ratio": "16:9 horizontal",
        "size": "1536x864",
        "target": (1280, 720),
        "label": "今日头条 · 深度解读",
        "layouts": "editorial social-card, infographic, comparison or narrative layouts that work as a strong horizontal news thumbnail",
    },
    "wechat": {
        "name": "微信公众号",
        "audience": "深度长文阅读的科技、互联网与职场读者；需要在订阅号常读常看列表中一眼看出高级感与专业度，画面要求高空间留白与精美手绘质感，主体居中，边缘留足呼吸感。",
        "ratio": "2.35:1 horizontal banner",
        "size": "1536x864",
        "target": (900, 383),
        "label": "微信公众号 · 深度专题",
        "layouts": "editorial cover, hero illustration or cinematic wide composition with clear center focus and spacious side margins for 2.35:1 banner cropping",
    },
}

ACCENT_BY_CATEGORY = {
    "blue": "#2563EB", "green": "#16A34A", "red": "#DC2626", "orange": "#EA580C",
    "yellow": "#CA8A04", "purple": "#9333EA", "pink": "#DB2777", "brown": "#92400E",
    "gray": "#475569", "neutral": "#475569",
}

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


@lru_cache(maxsize=1)
def _catalogs() -> tuple[list[dict], list[dict], list[dict]]:
    """读取 handraw-style 官方生成索引，不把数百张参考图塞进运行时提示词。"""
    try:
        styles = json.loads((HANDRAW_REFERENCES / "styles.json").read_text(encoding="utf-8"))
        layouts = json.loads((HANDRAW_REFERENCES / "layouts.json").read_text(encoding="utf-8"))
        colors = json.loads((HANDRAW_REFERENCES / "colors.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise llm.LLMError(
            "未找到 handraw-style 本地风格库，请确认 .agents/vendor/handraw-style 已安装。"
        ) from exc
    return styles, layouts, colors


def _catalog_prompt(items: list[dict], keys: tuple[str, ...], trait_limit: int = 180) -> str:
    compact = []
    for item in items:
        row = {key: item.get(key, "") for key in keys}
        if row.get("traits"):
            row["traits"] = str(row["traits"])[:trait_limit]
        if row.get("keywords"):
            row["keywords"] = list(row["keywords"])[:8]
        compact.append(row)
    return json.dumps(compact, ensure_ascii=False, separators=(",", ":"))


def _clean_source(markdown: str) -> str:
    text = re.sub(r"!\[[^\]]*\]\([^\n]*\)", "", markdown)
    text = re.sub(r"<img\b[^>]*>", "", text, flags=re.I)
    text = re.sub(r"data:image/[^\s)]+", "", text, flags=re.I)
    return text.strip()[:10000]


def _positive_traits(traits: str) -> str:
    """handraw traits 中只保留可视的正向描述，不把避免项原样喂给生图模型。"""
    parts = re.split(r"[；;。]", traits or "")
    negative = ("避免", "不要", "不准", "禁止", "严禁", "不能")
    return "；".join(part.strip() for part in parts if part.strip() and not any(word in part for word in negative))


def _find(items: list[dict], key: str, value: str) -> dict:
    return next((item for item in items if str(item.get(key)) == value), {})


def _layout_prompt(layout: dict) -> str:
    prompt_file = str(layout.get("prompt_file") or "")
    if prompt_file:
        path = HANDRAW_REFERENCES / prompt_file
        try:
            return path.read_text(encoding="utf-8").strip()[:5000]
        except OSError:
            pass
    keywords = "、".join(str(value) for value in layout.get("keywords", []))
    return f"{layout.get('name', '')}。构图关键词：{keywords}。"


def _recent_choices(platform: str) -> list[dict]:
    result = []
    for path in sorted(PLATFORM_CACHE_DIR.glob(f"{platform}-*.json"), reverse=True):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            result.append({key: data.get(key, "") for key in ("style_id", "layout_id", "color_id")})
        except (OSError, ValueError):
            continue
        if len(result) >= 6:
            break
    return result


def _plan_cover(platform: str, title: str, markdown: str, generated_copy: dict) -> dict:
    spec = PLATFORM_SPECS[platform]
    styles, layouts, colors = _catalogs()
    # 全库候选交给模型实时匹配；返回的只是索引 ID，具体 prompt 在本地确定性读取。
    user_content = f"""【平台】{spec['name']}
【平台读者】{spec['audience']}
【画幅】{spec['ratio']}
【母稿标题】{title}
【当前平台标题】{generated_copy.get('title') or title}
【当前平台正文】
{_clean_source(generated_copy.get('body_markdown') or markdown)}
【已有封面事实配置，仅作内容参考】
{json.dumps(generated_copy.get('cover_config') or {}, ensure_ascii=False)}
【近期已使用的 handraw 组合，请尽量避免重复】
{json.dumps(_recent_choices(platform), ensure_ascii=False)}

【handraw-style 风格索引】
{_catalog_prompt(styles, ('number', 'reference', 'generation_name', 'traits'))}

【handraw-style 构图索引】
{_catalog_prompt(layouts, ('id', 'category', 'name', 'name_en', 'keywords'))}

【handraw-style 主题色索引】
{_catalog_prompt(colors, ('id', 'category_zh', 'name_zh', 'name_en', 'prompt_zh', 'prompt_en'))}
"""
    system = f"""你是{spec['name']}科技内容的视觉总监。根据平台读者、当前平台文案和正文核心内容，从 handraw-style 的完整索引中动态选择一组封面视觉方案。

硬性要求：
1. 必须选择索引中真实存在的 style_id、layout_id、color_id，不能自造编号。
2. 不要机械套用最近组合；风格、构图和配色都要服务于本篇文案的核心冲突、收益或观点。
3. {spec['audience']}
4. 选择 {spec['layouts']}，但要避免密集小字；图片文字由程序后置叠加，生图阶段只负责无文字主题插画。
5. visual_subject 必须是可以画出来的具体主体；visual_metaphor 要表达文章核心关系，不能只写“科技感背景”。
6. 不伪造正文没有的数字、品牌关系、产品界面和测试结果。封面可以使用文章真实提到的产品名作为文字叠加，但生图图层不生成文字。

只输出 JSON：
{{
  "summary": "从当前平台文案提炼的核心卖点，1~2 句",
  "audience_hook": "平台读者最可能被什么吸引",
  "style_id": "handraw 索引编号",
  "layout_id": "handraw 构图编号",
  "color_id": "handraw 配色编号",
  "visual_subject": "单一主视觉及必要辅助元素",
  "visual_metaphor": "画面表达的核心关系",
  "composition": "结合选定构图的具体画面安排",
  "short_title": "封面上叠加的短标题，中文不超过 16 字",
  "subtitle": "封面辅助短句，不超过 20 字，可为空"
}}"""
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user_content}]
    for attempt in range(2):
        data = llm.parse_json_object(llm.chat(messages, max_tokens=8192, json_mode=True))
        if (_find(styles, "number", str(data.get("style_id")))
                and _find(layouts, "id", str(data.get("layout_id")))
                and _find(colors, "id", str(data.get("color_id")))
                and all(isinstance(data.get(key), str) and data[key].strip() for key in (
                    "visual_subject", "visual_metaphor", "composition", "short_title"))):
            return data
        if attempt == 0:
            messages[-1] = {"role": "user", "content": user_content + "\n请修正：所有 ID 必须来自上面的索引，且返回完整 JSON。"}
    raise llm.LLMError(f"{spec['name']}封面规划失败：模型没有返回有效的 handraw 风格、构图或配色。")


def _build_prompt(platform: str, plan: dict) -> str:
    styles, layouts, colors = _catalogs()
    style = _find(styles, "number", str(plan["style_id"]))
    layout = _find(layouts, "id", str(plan["layout_id"]))
    color = _find(colors, "id", str(plan["color_id"]))
    traits = _positive_traits(str(style.get("traits", "")))
    spec = PLATFORM_SPECS[platform]
    return f"""Create a hand-drawn editorial cover illustration for {spec['name']}.
Platform and audience: {spec['audience']}
Core article summary: {plan.get('summary', '')}
Audience hook: {plan.get('audience_hook', '')}
Visual subject: {plan['visual_subject']}
Visual metaphor: {plan['visual_metaphor']}
Composition: {plan['composition']}
Handraw style: {style.get('generation_name', '')}; reference style label: {style.get('reference', '')}; positive traits: {traits}
Handraw layout: {layout.get('name', '')}; layout guidance: {_layout_prompt(layout)}
Handraw theme color: {color.get('name_en', '')} ({color.get('name_zh', '')})

Generate an actual illustrated scene, not a text-only poster or flat template. Keep the main subject large and recognizable at {spec['ratio']} thumbnail size. Leave a clean safe area for the application to add the headline after generation. Do not render any text, letters, numbers, logos, watermarks, labels, UI screenshots or invented data. Use the article theme as the only source of subject matter. The final image must have a deliberate hand-drawn editorial identity, not a generic neon AI background. Render only the image.
"""


def _accent(color: dict) -> str:
    category = str(color.get("category") or "").lower()
    return ACCENT_BY_CATEGORY.get(category, "#2563EB")


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> list[str]:
    lines, current = [], ""
    for char in text.strip():
        if char == "\n":
            lines.append(current)
            current = ""
        elif not current or draw.textlength(current + char, font=font) <= max_width:
            current += char
        else:
            lines.append(current)
            current = char
    if current:
        lines.append(current)
    return lines or [""]


def _font(size: int, bold: bool = False):
    """统一使用项目已有的中文字体候选；handraw 只提供视觉提示词，不带字体资产。"""
    candidates = covers._CJK_FONT_CANDIDATES
    path = next((candidate for candidate in candidates if Path(candidate).is_file()), None)
    if path:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            pass
    return ImageFont.load_default(size)


def _overlay_title(image: Image.Image, platform: str, plan: dict, color: dict) -> Image.Image:
    spec = PLATFORM_SPECS[platform]
    image = image.convert("RGBA")
    width, height = image.size
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    dark = (10, 14, 23, 218)
    accent = _accent(color)
    if platform == "xhs":
        top, pad, title_size = int(height * 0.68), 64, 70
        draw.rectangle((0, top, width, height), fill=dark)
        draw.rounded_rectangle((pad, top + 34, pad + 230, top + 82), radius=12, fill=accent)
        draw.text((pad + 18, top + 43), spec["label"], font=_font(25, bold=True), fill="#FFFFFF")
        title_font = _font(title_size, bold=True)
        title_lines = _wrap(draw, str(plan.get("short_title") or ""), title_font, width - pad * 2)
        y = top + 110
        for line in title_lines[:3]:
            draw.text((pad, y), line, font=title_font, fill="#FFFFFF")
            y += int(title_size * 1.18)
        subtitle = str(plan.get("subtitle") or "").strip()
        if subtitle:
            draw.text((pad, height - 82), subtitle[:24], font=_font(25), fill="#D1D5DB")
    else:
        top, pad, title_size = int(height * 0.54), 58, 58
        draw.rectangle((0, top, width, height), fill=dark)
        draw.rounded_rectangle((pad, top + 28, pad + 290, top + 76), radius=10, fill=accent)
        draw.text((pad + 18, top + 37), spec["label"], font=_font(24, bold=True), fill="#FFFFFF")
        title_font = _font(title_size, bold=True)
        title_lines = _wrap(draw, str(plan.get("short_title") or ""), title_font, width - pad * 2)
        y = top + 100
        for line in title_lines[:2]:
            draw.text((pad, y), line, font=title_font, fill="#FFFFFF")
            y += int(title_size * 1.18)
        subtitle = str(plan.get("subtitle") or "").strip()
        if subtitle:
            draw.text((pad, height - 62), subtitle[:30], font=_font(24), fill="#D1D5DB")
    return Image.alpha_composite(image, overlay).convert("RGB")


def _write_image(source_path: Path, target_path: Path, platform: str, plan: dict, color: dict) -> None:
    spec = PLATFORM_SPECS[platform]
    centering = (0.5, 0.5) if platform == "wechat" else (0.5, 0.43)
    with Image.open(source_path) as source:
        image = ImageOps.fit(
            ImageOps.exif_transpose(source).convert("RGB"), spec["target"],
            method=Image.Resampling.LANCZOS, centering=centering,
        )
    if platform != "wechat":
        image = _overlay_title(image, platform, plan, color)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(target_path, "JPEG", quality=94, optimize=True)


def _platform_image_size(platform: str) -> str:
    if platform == "xhs":
        return llm.config.get("IMAGE_XHS_COVER_SIZE") or PLATFORM_SPECS["xhs"]["size"]
    elif platform == "wechat":
        return llm.config.get("IMAGE_WECHAT_COVER_SIZE") or llm.config.get("IMAGE_COVER_SIZE") or PLATFORM_SPECS["wechat"]["size"]
    else:
        return llm.config.get("IMAGE_JRTT_COVER_SIZE") or PLATFORM_SPECS["toutiao"]["size"]


def ensure_platform_cover(platform: str, title: str, markdown: str, generated_copy: dict,
                          output_path: str | Path) -> dict:
    """为指定平台生成差异化封面，缓存 AI 原图规划并将成品复制到平台目录。"""
    if platform not in PLATFORM_SPECS:
        raise ValueError(f"不支持的平台封面：{platform}")
    title, markdown = title.strip(), markdown.strip()
    if not title or not markdown:
        raise ValueError("平台封面需要标题和正文内容。")
    img_size = _platform_image_size(platform)
    source = {
        "version": SPEC_VERSION, "platform": platform, "title": title,
        "markdown": _clean_source(markdown), "copy": generated_copy,
        "model": llm.config.get("IMAGE_MODEL") or llm.config.get("IMAGE_COVER_MODEL") or "gemini-3.1-flash-image",
        "size": img_size,
    }
    key = hashlib.sha256(json.dumps(source, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    with _locks_guard:
        lock = _locks.setdefault(f"platform:{key}", threading.Lock())
    target_path = Path(output_path)
    with lock:
        PLATFORM_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_path = PLATFORM_CACHE_DIR / f"{platform}-{key}.jpg"
        metadata_path = PLATFORM_CACHE_DIR / f"{platform}-{key}.json"
        metadata = {}
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            with Image.open(cache_path) as image:
                valid = image.size == PLATFORM_SPECS[platform]["target"]
            if not valid or metadata.get("source") != "handraw-style+gemini":
                metadata = {}
        except (OSError, ValueError):
            metadata = {}
        was_cached = bool(metadata)
        if not metadata:
            plan = _plan_cover(platform, title, markdown, generated_copy)
            styles, layouts, colors = _catalogs()
            color = _find(colors, "id", str(plan["color_id"]))
            prompt = _build_prompt(platform, plan)
            with tempfile.NamedTemporaryFile(dir=PLATFORM_CACHE_DIR, prefix=f".{platform}-{key}-", suffix=".source", delete=False) as source_file:
                source_path = Path(source_file.name)
            with tempfile.NamedTemporaryFile(dir=PLATFORM_CACHE_DIR, prefix=f".{platform}-{key}-", suffix=".jpg", delete=False) as target_file:
                generated_path = Path(target_file.name)
            try:
                if not llm.generate_ai_image(
                    prompt, source_path,
                    size=img_size,
                    strict=True,
                ):
                    raise llm.LLMError(f"{PLATFORM_SPECS[platform]['name']}封面生图服务未返回图片。")
                _write_image(source_path, generated_path, platform, plan, color)
                generated_path.replace(cache_path)
            finally:
                source_path.unlink(missing_ok=True)
                generated_path.unlink(missing_ok=True)
            metadata = {
                **plan, "key": key, "platform": platform, "source": "handraw-style+gemini",
                "prompt": prompt, "color_name": color.get("name_zh", ""),
                "target_size": list(PLATFORM_SPECS[platform]["target"]),
            }
            with tempfile.NamedTemporaryFile("w", dir=PLATFORM_CACHE_DIR, prefix=f".{platform}-{key}-", suffix=".json", encoding="utf-8", delete=False) as metadata_file:
                json.dump(metadata, metadata_file, ensure_ascii=False, indent=2)
                metadata_temp = Path(metadata_file.name)
            try:
                metadata_temp.replace(metadata_path)
            finally:
                metadata_temp.unlink(missing_ok=True)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(cache_path, target_path)
        data_url = "data:image/jpeg;base64," + base64.b64encode(cache_path.read_bytes()).decode("ascii")
        return {**metadata, "path": str(target_path), "cache_path": str(cache_path), "data_url": data_url,
                "cached": was_cached}
