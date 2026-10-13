import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from studio.server import pipeline


class BatchPublishOrchestrationTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.old_runs = pipeline.RUNS
        pipeline.RUNS = Path(self.tempdir.name) / "runs"

    def tearDown(self):
        pipeline.RUNS = self.old_runs
        self.tempdir.cleanup()

    def test_multiple_items_are_independent_and_status_is_persisted(self):
        records = {
            "one": {"record_id": "one", "fields": {"选题标题": "第一篇"}},
            "two": {"record_id": "two", "fields": {"选题标题": "第二篇"}},
        }
        calls = []

        def get_record(record_id):
            calls.append(("read", record_id))
            return records[record_id]

        def generate(fields, template="auto"):
            calls.append(("generate", fields["选题标题"]))
            if fields["选题标题"] == "第一篇":
                raise RuntimeError("第一篇生成失败")
            return "## A\n\n## B\n\n## C\n\n第二篇正文"

        with patch.object(pipeline.feishu_btable.FeishuBitable, "get_record", side_effect=get_record), \
             patch.object(pipeline.llm, "generate_draft", side_effect=generate), \
             patch.object(pipeline.llm, "resolve_template", return_value="deepdive"), \
             patch.object(pipeline, "save_draft", return_value={"path": "/tmp/draft.md"}) as save_draft, \
             patch.object(pipeline, "push_draft_article", return_value={"draft_media_id": "media-2"}), \
             patch.object(pipeline, "build_xhs_copy_and_save", return_value={"md_path": "/tmp/xhs.md"}), \
             patch.object(pipeline, "build_toutiao_copy_and_save", return_value={"docx_path": "/tmp/jrtt.docx"}):
            result = pipeline.run_batch_publish(["one", "two"])

        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["items"][0]["status"], "failed")
        self.assertEqual(result["items"][1]["status"], "success")
        self.assertIn(("read", "two"), calls)
        status_path = Path(result["run_dir"]) / "batch-status.json"
        self.assertTrue(status_path.is_file())
        saved = json.loads(status_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["items"][1]["platforms"]["wechat"]["status"], "success")
        self.assertTrue(save_draft.called)

    def test_retry_does_not_repeat_successful_platform(self):
        counts = {"wechat": 0, "xhs": 0, "toutiao": 0}
        fail_xhs = {"value": True}

        def push(*args, **kwargs):
            counts["wechat"] += 1
            return {"draft_media_id": "media-1"}

        def xhs(*args, **kwargs):
            counts["xhs"] += 1
            if fail_xhs["value"]:
                raise RuntimeError("xhs temporary failure")
            return {"md_path": "/tmp/xhs.md", "cover_path": "/tmp/xhs.jpg"}

        def toutiao(*args, **kwargs):
            counts["toutiao"] += 1
            return {"md_path": "/tmp/jrtt.md", "docx_path": "/tmp/jrtt.docx", "cover_path": "/tmp/jrtt.jpg"}

        with patch.object(pipeline.feishu_btable.FeishuBitable, "get_record", return_value={"fields": {"选题标题": "重试篇"}}), \
             patch.object(pipeline.llm, "resolve_template", return_value="deepdive"), \
             patch.object(pipeline.llm, "generate_draft", return_value="## A\n\n## B\n\n## C"), \
             patch.object(pipeline, "save_draft", return_value={"path": "/tmp/draft.md"}), \
             patch.object(pipeline, "push_draft_article", side_effect=push), \
             patch.object(pipeline, "build_xhs_copy_and_save", side_effect=xhs), \
             patch.object(pipeline, "build_toutiao_copy_and_save", side_effect=toutiao):
            first = pipeline.run_batch_publish(["retry-me"])
            self.assertEqual(first["status"], "partial")
            fail_xhs["value"] = False
            second = pipeline.retry_batch_publish(first["job_id"])

        self.assertEqual(second["status"], "success")
        self.assertEqual(counts, {"wechat": 1, "xhs": 2, "toutiao": 1})


if __name__ == "__main__":
    unittest.main()
