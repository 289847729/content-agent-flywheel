"""公众号写作策略回归验证，不调用 LLM、飞书或微信。"""
from __future__ import annotations

import json
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from studio.server import llm


COMPLETE_DRAFT = """这次更新的重点是使用成本和限制。

> **TL;DR**：先看已知变化，再判断是否适合自己的任务。

## 已知变化

根据提供的材料，说明已经确认的功能变化。

## 使用边界

区分厂商宣称与已有测试结果，保留不确定性。

## 下一步如何验证

根据任务选择一个小样本，记录成本与错误率后再决定是否迁移。
""".strip()


class ChatCompletionTests(unittest.TestCase):
    def setUp(self):
        settings = {"LLM_API_BASE": "https://llm.test/v1", "LLM_MODEL": "test-model"}
        for patcher in (
            patch.object(llm.config, "get", side_effect=settings.get),
            patch.object(llm.config, "require", return_value="test-key"),
            patch("time.sleep"),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch.object(llm, "urlopen")
        self.urlopen = patcher.start()
        self.addCleanup(patcher.stop)
        self.messages = [{"role": "user", "content": "请生成完整内容。"}]

    @staticmethod
    def response(content, finish="stop"):
        response = MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = json.dumps({
            "choices": [{"message": {"content": content}, "finish_reason": finish}],
        }).encode("utf-8")
        return response

    def payloads(self):
        return [json.loads(call.args[0].data) for call in self.urlopen.call_args_list]

    def test_partial_length_response_is_regenerated_with_more_budget(self):
        self.urlopen.side_effect = [self.response("只有半段 | 表头", "length"), self.response("完整答复。")]
        result = llm.chat(self.messages, max_tokens=8192, max_retries=0)
        self.assertEqual(result, "完整答复。")
        self.assertEqual([payload["max_tokens"] for payload in self.payloads()], [8192, 16384])
        self.assertTrue(all(payload["messages"] == self.messages for payload in self.payloads()))

    def test_reasoning_only_length_response_also_gets_more_budget(self):
        self.urlopen.side_effect = [self.response(None, "length"), self.response("完整答复。")]
        self.assertEqual(llm.chat(self.messages, max_tokens=8192), "完整答复。")
        self.assertEqual(self.urlopen.call_count, 2)

    def test_repeated_length_response_is_an_error_not_partial_success(self):
        self.urlopen.side_effect = [self.response("残片一", "length"), self.response("残片二", "length")]
        with self.assertRaises(llm.LLMTruncatedError) as raised:
            llm.chat(self.messages, max_tokens=8192)
        self.assertEqual(self.urlopen.call_count, 2)
        self.assertNotIn("残片二", str(raised.exception))
        self.assertNotIn("网络连通性", str(raised.exception))

    def test_budget_ceiling_prevents_unbounded_regeneration(self):
        self.urlopen.return_value = self.response("截断内容", "length")
        with self.assertRaises(llm.LLMTruncatedError):
            llm.chat(self.messages, max_tokens=16384)
        self.assertEqual(self.urlopen.call_count, 1)

    def test_network_retry_does_not_use_up_the_length_retry(self):
        self.urlopen.side_effect = [
            ConnectionResetError("fixture"),
            self.response("残片", "length"),
            self.response("完整答复。"),
        ]
        self.assertEqual(llm.chat(self.messages, max_tokens=8192, max_retries=1), "完整答复。")
        self.assertEqual([payload["max_tokens"] for payload in self.payloads()], [8192, 8192, 16384])

    def test_network_failure_still_has_a_retry_limit(self):
        self.urlopen.side_effect = ConnectionResetError("fixture")
        with self.assertRaises(llm.LLMError):
            llm.chat(self.messages, max_retries=1)
        self.assertEqual(self.urlopen.call_count, 2)

    def test_json_mode_is_preserved_when_regenerating(self):
        self.urlopen.side_effect = [self.response('{"body":', "length"), self.response('{"body":"完整"}')]
        result = llm.chat(self.messages, json_mode=True)
        self.assertEqual(json.loads(result), {"body": "完整"})
        self.assertTrue(all(payload["response_format"] == {"type": "json_object"} for payload in self.payloads()))

    def test_incomplete_filtered_output_is_not_returned(self):
        self.urlopen.return_value = self.response("中止前的内容", "content_filter")
        with self.assertRaisesRegex(llm.LLMError, "content_filter"):
            llm.chat(self.messages)
        self.assertEqual(self.urlopen.call_count, 1)

    def test_compatible_service_may_omit_finish_reason(self):
        self.urlopen.return_value = self.response("完整答复。", None)
        self.assertEqual(llm.chat(self.messages), "完整答复。")


class DraftIntegrityTests(unittest.TestCase):
    def test_detects_truncated_table_code_and_missing_chapters(self):
        cases = {
            "表头截断": COMPLETE_DRAFT + "\n\n| 评估维度 | 当前披露 | 上一代轻量",
            "单元格截断": COMPLETE_DRAFT + "\n\n| 项目 | 结果 |\n| --- | --- |\n| 项目一 |",
            "代码截断": COMPLETE_DRAFT + "\n\n```python\nprint('unfinished')",
            "章节不足": "开场。\n\n## 第一个章节\n\n只有一节。",
        }
        for name, markdown in cases.items():
            with self.subTest(name=name):
                self.assertTrue(llm._draft_incomplete_reason(markdown))

    def test_valid_tables_and_code_endings_are_accepted(self):
        endings = (
            "\n\n| 项目 | 结果 |\n| --- | --- |\n| 示例 | a \\| b |",
            "\n\n项目 | 结果\n--- | ---\n示例 | 可用",
            "\n\n```python\n## 代码内标题不是文章章节\nprint('done')\n```",
            "\n\n- 完成后保存检查记录",
        )
        for ending in endings:
            with self.subTest(ending=ending):
                self.assertEqual(llm._draft_incomplete_reason(COMPLETE_DRAFT + ending), "")

    def test_partial_body_is_rewritten_once_without_mutating_original_prompt(self):
        messages = [{"role": "user", "content": "选题与事实材料。"}]
        partial = "## 已知变化\n\n| 评估维度 | 当前披露 | 上一代轻量"
        with patch.object(llm, "chat", side_effect=[partial, COMPLETE_DRAFT]) as chat:
            result = llm._complete_markdown_draft(messages, max_tokens=8192)
        self.assertEqual(result, COMPLETE_DRAFT)
        self.assertEqual(chat.call_count, 2)
        self.assertEqual([call.kwargs["max_tokens"] for call in chat.call_args_list], [8192, 16384])
        self.assertEqual(messages[0]["content"], "选题与事实材料。")
        self.assertNotIn("完整性修复要求", chat.call_args_list[0].args[0][0]["content"])
        self.assertIn("完整性修复要求", chat.call_args_list[1].args[0][0]["content"])

    def test_still_incomplete_after_rewriting_raises_an_error(self):
        with patch.object(llm, "chat", return_value="## 只有一节\n\n内容。") as chat:
            with self.assertRaisesRegex(llm.LLMError, "仍不完整"):
                llm._complete_markdown_draft([{"role": "user", "content": "选题。"}], max_tokens=8192)
        self.assertEqual(chat.call_count, 2)

    def test_compression_uses_the_same_integrity_checks(self):
        with patch.object(llm, "chat", side_effect=["只有开场。", COMPLETE_DRAFT]) as chat:
            self.assertEqual(llm.compress_draft("测试标题", COMPLETE_DRAFT * 2), COMPLETE_DRAFT)
        self.assertEqual([call.kwargs["max_tokens"] for call in chat.call_args_list], [4096, 8192])
        self.assertTrue(all(call.kwargs["temperature"] == 0.5 for call in chat.call_args_list))


class TemplateSelectionTests(unittest.TestCase):
    def test_auto_matches_article_task(self):
        cases = {
            "新模型发布，官方公布基准测试结果": "news",
            "手把手搭建资料整理工作流": "tutorial",
            "Agent Skills 是什么，运行机制如何理解": "deepdive",
            "新工具实测：三个任务的表现": "review",
            "这个付费版本值不值得升级": "conclusion",
            "三个代码助手怎么选：成本与限制对比": "comparison",
            "如何看待 AI 编程的责任边界": "opinion",
            "资料整理项目复盘：哪些步骤值得保留": "retrospective",
            "Tool A VS Tool B": "comparison",
        }
        for title, expected in cases.items():
            with self.subTest(title=title):
                self.assertEqual(llm.detect_template({"选题标题": title}), expected)

    def test_news_is_not_hijacked_by_supplementary_keywords(self):
        topic = {
            "选题标题": "某公司官宣新模型上线",
            "内容方向": "产品新闻",
            "关键词": "模型，测试，对比，干货",
            "备注": "介绍官方测试结果，附配置教程链接、对比与避坑建议。",
        }
        self.assertEqual(llm.detect_template(topic), "news")

    def test_concept_is_not_hijacked_by_notes(self):
        topic = {"选题标题": "MCP 是什么", "备注": "可以附上配置教程和测试建议。"}
        self.assertEqual(llm.detect_template(topic), "deepdive")

    def test_direction_and_supplementary_fields_can_select_a_style(self):
        for field in ("内容方向", "关键词", "备注"):
            with self.subTest(field=field):
                self.assertEqual(llm.detect_template({"选题标题": "AI 工具观察", field: "观点评论"}), "opinion")
        self.assertEqual(llm.detect_template({}), "deepdive")

    def test_explicit_style_wins_and_unknown_id_falls_back(self):
        topic = {"选题标题": "新模型发布"}
        for template in llm.TEMPLATES:
            with self.subTest(template=template):
                expected = "news" if template == "auto" else template
                self.assertEqual(llm.resolve_template(topic, template), expected)
        self.assertEqual(llm.resolve_template(topic, "obsolete-template"), "news")


class GenerateEndpointTests(unittest.TestCase):
    def test_api_reports_the_style_actually_used(self):
        from studio import app as web

        fields = {"选题标题": "新模型发布", "内容方向": "产品新闻"}
        record = {"record_id": "test-record", "fields": fields}
        choices = {"auto": "news", "opinion": "opinion", "obsolete-template": "news"}
        for requested, expected in choices.items():
            with self.subTest(requested=requested), patch.object(
                web.feishu_btable, "FeishuBitable"
            ) as client, patch.object(web.llm, "generate_draft", return_value="接口测试稿。") as generate:
                client.return_value.get_record.return_value = record
                result = web.generate(web.GenerateRequest(record_id="test-record", template=requested))
                generate.assert_called_once_with(fields, template=expected)
                self.assertTrue(result["ok"])
                self.assertEqual(result["template"], expected)
                self.assertEqual(result["template_name"], llm.TEMPLATES[expected])
        self.assertEqual(len(web.get_templates()["items"]), 9)  # 自动推荐 + 八种风格

    def test_truncation_error_is_not_reported_as_success(self):
        from studio import app as web

        with patch.object(web.feishu_btable, "FeishuBitable") as client, patch.object(
            web.llm, "generate_draft", side_effect=web.llm.LLMTruncatedError("模型输出被截断")
        ):
            client.return_value.get_record.return_value = {"fields": {"选题标题": "新模型发布"}}
            result = web.generate(web.GenerateRequest(record_id="test-record"))
        data = json.loads(result.body)
        self.assertFalse(data["ok"])
        self.assertNotIn("markdown", data)
        self.assertIn("截断", data["error"])


class AiImageGenerationTests(unittest.TestCase):
    @staticmethod
    def image_bytes():
        from PIL import Image

        stream = io.BytesIO()
        Image.new("RGB", (2, 2), "white").save(stream, format="PNG")
        return stream.getvalue()

    @staticmethod
    def http_response(payload):
        response = MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = json.dumps(payload).encode("utf-8")
        return response

    def test_strict_agy_is_first_and_valid_output_returns_without_api(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "cover.png"
            output.write_bytes(self.image_bytes())
            with patch.object(llm, "_generate_ai_image_via_agy", return_value=True) as agy, patch.object(
                llm, "urlopen"
            ) as urlopen:
                result = llm.generate_ai_image("prompt", output, strict=True)
        self.assertTrue(result)
        agy.assert_called_once_with("prompt", output)
        urlopen.assert_not_called()

    def test_strict_invalid_agy_output_falls_back_to_api(self):
        image = self.image_bytes()
        payload = {"data": [{"b64_json": __import__("base64").b64encode(image).decode()}]}
        settings = {
            "IMAGE_MODEL": "image-test",
            "IMAGE_GEN_API_BASE": "https://image.test/v1",
            "IMAGE_GEN_API_KEY": "image-key",
        }
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "cover.png"
            output.write_text("not an image", encoding="utf-8")
            with patch.object(llm.config, "get", side_effect=settings.get), patch.object(
                llm, "_generate_ai_image_via_agy", return_value=True
            ), patch.object(llm, "urlopen", return_value=self.http_response(payload)) as urlopen:
                self.assertTrue(llm.generate_ai_image("prompt", output, strict=True))
            self.assertEqual(urlopen.call_count, 1)
            self.assertEqual(output.read_bytes(), image)

    def test_strict_error_contains_agy_and_api_diagnostics(self):
        settings = {"IMAGE_GEN_API_KEY": "image-key"}
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "cover.png"
            with patch.object(llm.config, "get", side_effect=settings.get), patch.object(
                llm, "_generate_ai_image_via_agy", return_value=False
            ), patch.object(llm, "urlopen", side_effect=TimeoutError("api fixture")):
                with self.assertRaisesRegex(llm.LLMError, r"agy:.*API:.*TimeoutError"):
                    llm.generate_ai_image("prompt", output, strict=True)

    def test_non_strict_does_not_invoke_agy_and_returns_false_on_api_failure(self):
        settings = {"IMAGE_GEN_API_KEY": "image-key"}
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(llm.config, "get", side_effect=settings.get), patch.object(
                llm, "_generate_ai_image_via_agy"
            ) as agy, patch.object(llm, "urlopen", side_effect=OSError("api fixture")):
                self.assertFalse(llm.generate_ai_image("prompt", Path(directory) / "cover.png"))
        agy.assert_not_called()


class RecentOpeningTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.outputs = Path(temporary.name) / "outputs"
        self.outputs.mkdir()
        patcher = patch.object(llm, "OUTPUTS_DIR", self.outputs)
        patcher.start()
        self.addCleanup(patcher.stop)

    def write_draft(self, relative_path, markdown, modified):
        path = self.outputs / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(markdown, encoding="utf-8")
        os.utime(path, (modified, modified))
        return path

    def test_newest_first_with_export_copies_deduplicated(self):
        self.write_draft("2026-10-01-旧稿.md", "旧稿切入问题。", 100)
        self.write_draft("2026-10-02-新稿.md", "这次变化是**成本**。\n\n后文。", 200)
        self.write_draft("runs/2026-10-02-001/article.md", "# 导出标题\n\n这次变化是成本。\n\n后文。", 300)
        self.assertEqual(
            [item["snippet"] for item in llm.recent_openings()],
            ["这次变化是成本。", "旧稿切入问题。"],
        )

    def test_history_is_bounded_and_ignores_platform_derivatives(self):
        for index in range(7):
            self.write_draft(f"2026-10-0{index + 1}-稿件.md", f"第 {index} 篇的开场。", index + 1)
        self.write_draft("小红书/2026-10-08-笔记.md", "不应读取的小红书开头。", 100)
        self.write_draft("今日头条/2026-10-08-文案.md", "不应读取的头条开头。", 100)
        self.write_draft("runs/latest/xiaohongshu.md", "不应读取的衍生稿。", 100)
        self.write_draft("runs/test-imported/article.md", "不应读取的测试导出。", 100)
        self.write_draft(
            "runs/2026-10-08-tweet-20/article.md",
            "# 原始推文\n\n> **来源平台**：X\n\n### 📝 博主原文案\n\n不是账号文章的外部原文。",
            100,
        )
        self.write_draft("notes.md", "不应读取的笔记。", 100)
        openings = llm.recent_openings()
        self.assertEqual(len(openings), 5)
        self.assertEqual([item["snippet"] for item in openings], [f"第 {index} 篇的开场。" for index in (6, 5, 4, 3, 2)])

    def test_missing_output_directory_is_supported(self):
        with patch.object(llm, "OUTPUTS_DIR", self.outputs / "missing"):
            self.assertEqual(llm.recent_openings(), [])

    def test_empty_and_unreadable_drafts_do_not_block_history(self):
        self.write_draft("2026-10-01-正常.md", "可用的开头。", 100)
        self.write_draft("2026-10-02-空稿.md", "# 只有标题\n\n![封面](cover.png)", 200)
        unreadable = self.write_draft("2026-10-03-不可读.md", "不可读开头。", 300)
        original_open = Path.open

        def open_draft(path, *args, **kwargs):
            if path == unreadable:
                raise PermissionError("fixture")
            return original_open(path, *args, **kwargs)

        with patch.object(Path, "open", autospec=True, side_effect=open_draft):
            self.assertEqual([item["snippet"] for item in llm.recent_openings()], ["可用的开头。"])

    def test_excerpt_skips_export_metadata_and_stops_at_body(self):
        markdown = """\ufeff---
title: 导出标题
---
# 文章标题

![封面](cover.png)

```python
print('不是开头')
```

> **TL;DR**：导读。

真正的第一段，
延续第一段。

## 正文小节

不应进入开头的正文。
"""
        self.assertEqual(llm._opening_excerpt(markdown), "真正的第一段， 延续第一段。")

    def test_excerpt_is_truncated_to_prompt_budget(self):
        snippet = llm._opening_excerpt("字" * 500 + "\n\n另一段。")
        self.assertEqual(len(snippet), llm.RECENT_OPENING_CHARS)

    def test_generation_passes_style_and_history_to_llm(self):
        self.write_draft("2026-10-01-旧稿.md", "旧稿开场只用于避重复。", 100)
        refs = [{"name": "作者参考.md", "snippet": "作者语言节奏参考。"}]
        with patch.object(llm, "style_refs", return_value=refs), patch.object(
            llm, "chat", return_value=f"```markdown\n{COMPLETE_DRAFT}\n```"
        ) as chat:
            draft = llm.generate_draft({"选题标题": "新模型发布"}, template="comparison")
        self.assertEqual(draft, COMPLETE_DRAFT)
        chat.assert_called_once()
        messages = chat.call_args.args[0]
        self.assertEqual([message["role"] for message in messages], ["system", "user"])
        self.assertIn(llm.TEMPLATE_PROMPTS["comparison"], messages[0]["content"])
        self.assertIn("作者语言节奏参考。", messages[1]["content"])
        self.assertIn("旧稿开场只用于避重复。", messages[1]["content"])
        self.assertIn("不是本次事实来源", messages[1]["content"])
        self.assertEqual(chat.call_args.kwargs["max_tokens"], 8192)

    def test_normalize_toutiao_title_enforces_30_chars(self):
        short_title = "开源平替掀桌！真能省下上千元？"
        self.assertEqual(llm.normalize_toutiao_title(short_title), short_title)

        long_title = "单线程写代码已死！9.2万星开源ADE神器，用手机与终端随时调度多Agent军团并行干活？真相让人意想不到"
        normalized = llm.normalize_toutiao_title(long_title)
        self.assertLessEqual(len(normalized), 30)
        self.assertGreaterEqual(len(normalized), 15)

        clean_title = llm.normalize_toutiao_title("【今日头条 · 独家拆解】《新模型发布！性能暴涨》")
        self.assertEqual(clean_title, "新模型发布！性能暴涨")


if __name__ == "__main__":
    unittest.main()
