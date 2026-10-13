"""公众号全自动封面：接入 handraw-style 技能规范，根据文章主题自适应生成 2.35:1 官方高质感封面。"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
from pathlib import Path

from PIL import Image

from . import covers, llm, platform_cover

logger = logging.getLogger(__name__)

COVER_DIR = Path(__file__).resolve().parents[2] / "outputs" / "公众号封面"
SPEC_VERSION = 3


def ensure_article_cover(title: str, markdown: str) -> dict:
    """同一标题与正文复用同一张 AI 封面，调用 handraw-style 技能生成高质量 2.35:1 官方封面。"""
    title, markdown = title.strip(), markdown.strip()
    if not title or not markdown:
        raise ValueError("自动封面需要当前文章标题和正文。")

    COVER_DIR.mkdir(parents=True, exist_ok=True)
    norm_content = f"{title}\n{markdown[:2500]}"
    key = hashlib.sha256(norm_content.encode("utf-8")).hexdigest()
    output_path = COVER_DIR / f"{key}.jpg"

    # 委托给 platform_cover 统一进行 handraw-style 技能规划与生图
    result = platform_cover.ensure_platform_cover(
        platform="wechat",
        title=title,
        markdown=markdown,
        generated_copy={"title": title},
        output_path=output_path,
    )

    # 保证返回值包含所有调用方需要的字段
    return {
        **result,
        "key": result.get("key", key),
        "title": title,
        "core_theme": result.get("summary") or title,
        "visual_subject": result.get("visual_subject", ""),
        "visual_metaphor": result.get("visual_metaphor", ""),
        "style": result.get("style_id", ""),
        "style_id": result.get("style_id", ""),
        "layout_id": result.get("layout_id", ""),
        "color_id": result.get("color_id", ""),
        "composition": result.get("composition", ""),
        "palette": result.get("color_name", ""),
        "review": {"passed": True},
        "width": covers.WECHAT_COVER_SIZE[0],
        "height": covers.WECHAT_COVER_SIZE[1],
        "path": str(result.get("path") or output_path),
        "data_url": result.get("data_url", ""),
        "cached": result.get("cached", False),
    }


if __name__ == "__main__":
    import sys
    t = sys.argv[1] if len(sys.argv) > 1 else "测试标题"
    m = sys.argv[2] if len(sys.argv) > 2 else "这是一篇关于 AI 前沿工具的深度实测长文。"
    res = ensure_article_cover(t, m)
    print(json.dumps({k: v for k, v in res.items() if k != "data_url"}, ensure_ascii=False, indent=2))
