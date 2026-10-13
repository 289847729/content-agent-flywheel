"""自动封面与预览/导出/推送同图回归验证；外部模型与发布接口均为模拟。"""
from __future__ import annotations

import base64
import io
import json
import tempfile
import unittest
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import MagicMock, patch

from lxml import html as lxml_html
from PIL import Image

from studio.server import article_cover, bmmd_typeset, llm, pipeline

PLAN = {
    "core_theme": "多智能体协作中的任务路由",
    "visual_subject": "一个中央路由节点与三个协作模块",
    "visual_metaphor": "中央节点将不同任务分发到适合的模块",
    "style": "blueprint_engineering",
    "composition": "中央横向排列，关键节点保持在横幅安全区",
    "palette": "藏蓝、青色、少量琥珀色",
}
MARKDOWN = "任务需要按能力分配。\n\n## 路由\n分发任务。\n\n## 协作\n传递结果。\n\n## 验收\n检查输出。"
REVIEW = {"matches_theme": True, "has_readable_text": False, "subject_clear": True, "subject_clipped": False, "revision_hint": ""}


def png_bytes():
    buffer = io.BytesIO()
    Image.new("RGB", (1200, 800), "#194b65").save(buffer, "PNG")
    return buffer.getvalue()


def first_image(html):
    return lxml_html.fromstring(html).xpath("//img")[0].get("src")


class AutomaticCoverTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        patcher = patch.object(article_cover, "COVER_DIR", self.root / "covers")
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch.object(llm.config, "get", return_value=None)
        patcher.start()
        self.addCleanup(patcher.stop)
        def respond(messages, **kwargs):
            data = REVIEW if messages[0]["content"] == article_cover.REVIEW_SYSTEM_PROMPT else PLAN
            return json.dumps(data, ensure_ascii=False)

        patcher = patch.object(llm, "chat", side_effect=respond)
        self.chat = patcher.start()
        self.addCleanup(patcher.stop)

        def generate(prompt, output_path, size, *, strict):
            self.assertTrue(strict)
            Path(output_path).write_bytes(png_bytes())
            return True

        patcher = patch.object(llm, "generate_ai_image", side_effect=generate)
        self.generate = patcher.start()
        self.addCleanup(patcher.stop)

    def test_uses_current_body_and_reuses_the_same_image(self):
        cover = article_cover.ensure_article_cover("任务协作", MARKDOWN)
        reused = article_cover.ensure_article_cover("任务协作", MARKDOWN)
        self.assertFalse(cover["cached"])
        self.assertTrue(reused["cached"])
        self.assertEqual(cover["data_url"], reused["data_url"])
        self.assertEqual(self.chat.call_count, 2)  # 一次主题提炼 + 一次视觉自检
        self.generate.assert_called_once()
        self.assertIn(MARKDOWN, self.chat.call_args_list[0].args[0][1]["content"])
        self.assertIn(PLAN["visual_subject"], self.generate.call_args.args[0])
        with Image.open(cover["path"]) as image:
            self.assertEqual(image.size, (900, 383))
            self.assertEqual(image.format, "JPEG")

    def test_title_and_body_changes_regenerate_but_line_endings_do_not(self):
        first = article_cover.ensure_article_cover("任务协作", MARKDOWN)
        equivalent = article_cover.ensure_article_cover("任务协作", MARKDOWN.replace("\n", "\r\n"))
        changed_body = article_cover.ensure_article_cover("任务协作", MARKDOWN + "\n新增成本分析。")
        changed_title = article_cover.ensure_article_cover("新的任务协作", MARKDOWN)
        self.assertEqual(first["key"], equivalent["key"])
        self.assertNotEqual(first["key"], changed_body["key"])
        self.assertNotEqual(first["key"], changed_title["key"])
        self.assertEqual(self.generate.call_count, 3)

    def test_parallel_requests_generate_only_once(self):
        with ThreadPoolExecutor(max_workers=3) as executor:
            covers = list(executor.map(lambda _: article_cover.ensure_article_cover("同一文章", MARKDOWN), range(3)))
        self.assertEqual(self.generate.call_count, 1)
        self.assertEqual(len({cover["data_url"] for cover in covers}), 1)

    def test_corrupted_cache_regenerates(self):
        cover = article_cover.ensure_article_cover("任务协作", MARKDOWN)
        Path(cover["path"]).write_bytes(b"broken image")
        self.assertFalse(article_cover.ensure_article_cover("任务协作", MARKDOWN)["cached"])
        self.assertEqual(self.generate.call_count, 2)

    def test_invalid_plan_is_repaired_automatically(self):
        self.chat.side_effect = [json.dumps({"style": []}), json.dumps(PLAN), json.dumps(REVIEW)]
        self.assertEqual(article_cover.ensure_article_cover("任务协作", MARKDOWN)["style"], PLAN["style"])
        self.assertEqual(self.chat.call_count, 3)

    def test_image_failure_does_not_create_a_text_cover(self):
        self.generate.side_effect = llm.LLMError("image service unavailable")
        with self.assertRaises(llm.LLMError):
            article_cover.ensure_article_cover("任务协作", MARKDOWN)
        self.assertEqual(list(article_cover.COVER_DIR.iterdir()), [])

    def test_base64_images_do_not_hide_body_from_theme_planning(self):
        source = "![cover](data:image/png;base64," + "A" * 15000 + ")\n\n" + MARKDOWN
        article_cover.ensure_article_cover("任务协作", source)
        prompt = self.chat.call_args_list[0].args[0][1]["content"]
        self.assertIn(MARKDOWN, prompt)
        self.assertNotIn("base64", prompt)

    def test_visual_self_check_regenerates_labeled_image_automatically(self):
        bad = {**REVIEW, "has_readable_text": True, "revision_hint": "移除英文标注"}
        self.chat.side_effect = [json.dumps(PLAN), json.dumps(bad), json.dumps(REVIEW)]
        cover = article_cover.ensure_article_cover("任务协作", MARKDOWN)
        self.assertTrue(cover["review"]["passed"])
        self.assertEqual(self.generate.call_count, 2)
        self.assertIn("移除英文标注", self.generate.call_args.args[0])

    def test_repeated_failed_visual_check_is_not_cached(self):
        bad = {**REVIEW, "subject_clipped": True}
        self.chat.side_effect = [json.dumps(PLAN), json.dumps(bad), json.dumps(bad)]
        with self.assertRaisesRegex(llm.LLMError, "仍未通过"):
            article_cover.ensure_article_cover("任务协作", MARKDOWN)
        self.assertEqual(self.generate.call_count, 2)
        self.assertEqual(list(article_cover.COVER_DIR.iterdir()), [])

    def test_both_engines_and_theme_changes_share_the_ai_cover(self):
        cover = article_cover.ensure_article_cover("任务协作", MARKDOWN)
        builtin, _ = pipeline.build_typeset_html(MARKDOWN, "任务协作", "作者甲", "moyu-green")
        alternate, _ = pipeline.build_typeset_html(MARKDOWN, "任务协作", "作者乙", "graphite-minimal")
        with patch.object(bmmd_typeset, "render_fragment", return_value="<section><p>正文</p></section>"):
            bmmd, _ = pipeline.build_typeset_html(MARKDOWN, "任务协作", "作者甲", "moyu-green", engine="bmmd")
        self.assertTrue(all(first_image(html) == cover["data_url"] for html in (builtin, alternate, bmmd)))
        self.assertEqual(self.generate.call_count, 1)

    def test_export_and_docx_use_the_preview_cover(self):
        cover = article_cover.ensure_article_cover("任务协作", MARKDOWN)
        with patch.object(pipeline, "RUNS", self.root / "runs"):
            package = pipeline.build_publish_package("任务协作", MARKDOWN)
        output = Path(package["dir"])
        self.assertEqual((output / "cover.jpg").read_bytes(), Path(cover["path"]).read_bytes())
        self.assertEqual(first_image((output / "wechat.html").read_text()), cover["data_url"])
        with zipfile.ZipFile(output / "article.docx") as document:
            images = [name for name in document.namelist() if name.startswith("word/media/")]
            self.assertIn(Path(cover["path"]).read_bytes(), [document.read(name) for name in images])
        self.assertEqual(self.generate.call_count, 1)

    def test_docx_fallback_also_keeps_the_ai_cover(self):
        cover = article_cover.ensure_article_cover("任务协作", MARKDOWN)
        with patch.object(pipeline, "RUNS", self.root / "runs"), patch.object(
            pipeline.docx_export, "html_to_docx_bytes", side_effect=ValueError("fixture")
        ):
            package = pipeline.build_publish_package("任务协作", MARKDOWN)
        with zipfile.ZipFile(Path(package["dir"]) / "article.docx") as document:
            images = [document.read(name) for name in document.namelist() if name.startswith("word/media/")]
            self.assertIn(Path(cover["path"]).read_bytes(), images)

    def test_push_renders_and_archives_the_same_cover(self):
        cover = article_cover.ensure_article_cover("任务协作", MARKDOWN)
        with patch.object(pipeline, "RUNS", self.root / "runs"), patch.object(
            pipeline.wechat_api, "push_draft", return_value={"draft_media_id": "mock"}
        ) as push, patch.object(pipeline.feishu_btable, "FeishuBitable"):
            result = pipeline.push_draft_article("record", "任务协作", "作者", "moyu-green", MARKDOWN)
        layout = Path(push.call_args.args[0]).read_text()
        self.assertEqual(first_image(layout), cover["data_url"])
        self.assertEqual((Path(result["run_dir"]) / "cover.jpg").read_bytes(), Path(cover["path"]).read_bytes())
        self.assertEqual(self.generate.call_count, 1)


class PreviewEndpointTests(unittest.TestCase):
    def test_preview_injects_automatic_cover_and_reports_its_theme(self):
        from studio import app as web

        data_url = "data:image/png;base64," + base64.b64encode(png_bytes()).decode()
        cover = {**PLAN, "key": "test-cover", "cached": True, "path": "test-cover.jpg", "data_url": data_url}
        with patch.object(web.pipeline.article_cover, "ensure_article_cover", return_value=cover) as generate:
            result = web.preview(web.PreviewRequest(title="任务协作", markdown=MARKDOWN))
        self.assertTrue(result["ok"])
        self.assertEqual(first_image(result["html"]), data_url)
        self.assertEqual(result["cover"]["core_theme"], PLAN["core_theme"])
        self.assertNotIn("data_url", result["cover"])
        generate.assert_called_once_with("任务协作", MARKDOWN)

    def test_image_service_failure_is_reported_instead_of_successful_text_cover(self):
        from studio import app as web

        with patch.object(web.pipeline.article_cover, "ensure_article_cover", side_effect=llm.LLMError("生图不可用")):
            result = web.preview(web.PreviewRequest(title="任务协作", markdown=MARKDOWN))
        data = json.loads(result.body)
        self.assertFalse(data["ok"])
        self.assertNotIn("html", data)
        self.assertIn("生图不可用", data["error"])


class ImageTransportTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.output = Path(temporary.name) / "image.png"
        patcher = patch.object(llm.config, "get", side_effect={"LLM_API_BASE": "https://llm.test/v1", "LLM_API_KEY": "test-key"}.get)
        patcher.start()
        self.addCleanup(patcher.stop)

    @staticmethod
    def response(body):
        response = MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = body
        return response

    def test_base64_response_is_validated_and_saved(self):
        image = png_bytes()
        response = self.response(json.dumps({"data": [{"b64_json": base64.b64encode(image).decode()}]}).encode())
        with patch.object(llm, "urlopen", return_value=response):
            self.assertTrue(llm.generate_ai_image("concept", self.output, strict=True))
        self.assertEqual(self.output.read_bytes(), image)

    def test_url_response_is_downloaded_without_api_credentials(self):
        responses = [self.response(b'{"data":[{"url":"https://images.test/result.png"}]}'), self.response(png_bytes())]
        with patch.object(llm, "urlopen", side_effect=responses) as request:
            self.assertTrue(llm.generate_ai_image("concept", self.output, strict=True))
        self.assertEqual(request.call_args_list[1].args[0], "https://images.test/result.png")

    def test_bad_image_data_retries_then_fails_without_writing(self):
        response = self.response(b'{"data":[{"b64_json":"bm90IGFuIGltYWdl"}]}')
        with patch.object(llm, "urlopen", return_value=response) as request:
            with self.assertRaises(llm.LLMError):
                llm.generate_ai_image("concept", self.output, size="1536x1024", strict=True)
        self.assertEqual(request.call_count, 2)
        self.assertFalse(self.output.exists())
        self.assertEqual(json.loads(request.call_args.args[0].data)["size"], "1024x1024")


if __name__ == "__main__":
    unittest.main()
