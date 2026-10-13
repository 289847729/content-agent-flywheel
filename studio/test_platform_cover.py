"""handraw-style + Gemini 多平台封面回归测试，不调用真实模型。"""
from __future__ import annotations

import base64
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from studio.server import llm, platform_cover


MARKDOWN = """轻量模型正在进入高并发自动化流程。

## 速度与成本
降低延迟与调用开销。

## 任务分工
把简单任务交给轻量执行器。
"""


def png_bytes():
    buffer = io.BytesIO()
    Image.new("RGB", (1200, 900), "#24506A").save(buffer, "PNG")
    return buffer.getvalue()


class PlatformCoverTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        patcher = patch.object(platform_cover, "PLATFORM_CACHE_DIR", self.root / "cache")
        patcher.start()
        self.addCleanup(patcher.stop)
        styles, layouts, colors = platform_cover._catalogs()
        self.plan = {
            "summary": "轻量模型降低高并发自动化成本。",
            "audience_hook": "开发者关心速度和成本。",
            "style_id": styles[0]["number"],
            "layout_id": layouts[0]["id"],
            "color_id": colors[0]["id"],
            "visual_subject": "一个分流节点和多条自动化路径",
            "visual_metaphor": "低成本执行器把任务分发到不同路径",
            "composition": "主视觉居中，留出标题安全区",
            "short_title": "轻量模型怎么用",
            "subtitle": "速度、成本与分工",
        }

        def respond(messages, **kwargs):
            if messages[0]["content"].startswith("你是"):
                return json.dumps(self.plan, ensure_ascii=False)
            raise AssertionError("测试不应调用额外的规划接口")

        chat_patcher = patch.object(llm, "chat", side_effect=respond)
        self.chat_mock = chat_patcher.start()
        self.addCleanup(chat_patcher.stop)
        image_patcher = patch.object(llm, "generate_ai_image", side_effect=self._generate)
        self.image_mock = image_patcher.start()
        self.addCleanup(image_patcher.stop)

    @staticmethod
    def _generate(prompt, output_path, size, *, strict):
        assert strict
        Path(output_path).write_bytes(png_bytes())
        return True

    def test_handraw_catalogs_are_loaded_from_vendored_project(self):
        styles, layouts, colors = platform_cover._catalogs()
        self.assertGreaterEqual(len(styles), 200)
        self.assertGreaterEqual(len(layouts), 100)
        self.assertEqual(len(colors), 36)
        self.assertTrue(all(item.get("number") and item.get("generation_name") for item in styles))

    def test_xhs_uses_handraw_prompt_gemini_and_3_to_4_output(self):
        output = self.root / "小红书" / "images" / "cover.jpg"
        result = platform_cover.ensure_platform_cover(
            "xhs", "轻量模型怎么用", MARKDOWN, {"title": "轻量模型怎么用", "body_markdown": MARKDOWN}, output,
        )
        self.assertFalse(result["cached"])
        self.assertEqual(result["platform"], "xhs")
        self.assertEqual(result["target_size"], [1080, 1440])
        self.assertTrue(output.is_file())
        with Image.open(output) as image:
            self.assertEqual(image.size, (1080, 1440))
        prompt = self.image_mock.call_args.args[0]
        self.assertIn("hand-drawn", prompt)
        self.assertIn(self.plan["visual_subject"], prompt)
        self.assertIn("large and recognizable", prompt)

    def test_toutiao_uses_different_platform_ratio_and_title_overlay(self):
        output = self.root / "今日头条" / "cover.jpg"
        result = platform_cover.ensure_platform_cover(
            "toutiao", "轻量模型降低成本", MARKDOWN, {"title": "轻量模型降低成本", "body_markdown": MARKDOWN}, output,
        )
        self.assertEqual(result["platform"], "toutiao")
        self.assertEqual(result["target_size"], [1280, 720])
        with Image.open(output) as image:
            self.assertEqual(image.size, (1280, 720))
        self.assertIn("今日头条", result["prompt"])

    def test_same_request_reuses_cache_and_copies_to_new_platform_path(self):
        first_path = self.root / "one.jpg"
        second_path = self.root / "two.jpg"
        first = platform_cover.ensure_platform_cover("xhs", "同一标题", MARKDOWN, {"body_markdown": MARKDOWN}, first_path)
        second = platform_cover.ensure_platform_cover("xhs", "同一标题", MARKDOWN, {"body_markdown": MARKDOWN}, second_path)
        self.assertTrue(second["cached"])
        self.assertEqual(first["cache_path"], second["cache_path"])
        self.assertEqual(first_path.read_bytes(), second_path.read_bytes())
        self.assertEqual(self.image_mock.call_count, 1)
        self.assertEqual(self.chat_mock.call_count, 1)

    def test_platform_or_body_changes_use_a_new_plan(self):
        first = platform_cover.ensure_platform_cover("xhs", "标题", MARKDOWN, {"body_markdown": MARKDOWN}, self.root / "a.jpg")
        second = platform_cover.ensure_platform_cover("toutiao", "标题", MARKDOWN, {"body_markdown": MARKDOWN}, self.root / "b.jpg")
        third = platform_cover.ensure_platform_cover("xhs", "标题", MARKDOWN + "\n新增观点。", {"body_markdown": MARKDOWN}, self.root / "c.jpg")
        self.assertNotEqual(first["key"], second["key"])
        self.assertNotEqual(first["key"], third["key"])
        self.assertEqual(self.image_mock.call_count, 3)

    def test_missing_handraw_library_fails_clearly(self):
        with patch.object(platform_cover, "HANDRAW_REFERENCES", self.root / "missing"):
            with self.assertRaisesRegex(llm.LLMError, "handraw-style"):
                platform_cover._catalogs.cache_clear()
                platform_cover._catalogs()
        platform_cover._catalogs.cache_clear()

    def test_generated_data_url_is_the_same_as_saved_image(self):
        output = self.root / "cover.jpg"
        result = platform_cover.ensure_platform_cover("xhs", "标题", MARKDOWN, {"body_markdown": MARKDOWN}, output)
        self.assertEqual(result["data_url"].split(",", 1)[1], base64.b64encode(output.read_bytes()).decode("ascii"))


if __name__ == "__main__":
    unittest.main()
