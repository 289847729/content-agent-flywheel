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
        self.old_outputs = pipeline.OUTPUTS
        test_outputs = Path(self.tempdir.name) / "outputs"
        test_outputs.mkdir(parents=True, exist_ok=True)
        pipeline.OUTPUTS = test_outputs
        pipeline.RUNS = test_outputs / "runs"

    def tearDown(self):
        pipeline.RUNS = self.old_runs
        pipeline.OUTPUTS = self.old_outputs
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

    def test_batch_item_output_directory_structure_and_artifacts(self):
        old_outputs = pipeline.OUTPUTS
        test_outputs = Path(self.tempdir.name) / "outputs"
        test_outputs.mkdir(parents=True, exist_ok=True)
        pipeline.OUTPUTS = test_outputs
        try:
            # 1. 验证 allocate_batch_item_dir：空目录复用；目录有内容时按 01, 02 递增
            today_str = pipeline.datetime.now().strftime("%Y-%m-%d")
            dir1 = pipeline.allocate_batch_item_dir(today_str)
            self.assertEqual(dir1.name, "01")
            # 此时 dir1 为空，再次请求分配应复用 01，防止产生废弃空洞
            dir1_reuse = pipeline.allocate_batch_item_dir(today_str)
            self.assertEqual(dir1_reuse, dir1)

            # 2. 验证 save_draft、xhs、toutiao 能够统一落入指定的 target_dir
            with patch.object(pipeline.feishu_btable.FeishuBitable, "update_record"):
                draft = pipeline.save_draft("rec1", "测试标题", "正文内容", target_dir=dir1)
            self.assertTrue(Path(draft["path"]).is_file())
            self.assertEqual(Path(draft["path"]).parent, dir1)
            self.assertTrue(draft["path"].endswith("-公众号初稿.md"))

            def fake_docx(md, out, cov):
                Path(out).write_text("dummy docx")

            with patch.object(pipeline.llm, "generate_toutiao_copy", return_value={"title": "头条测试标题", "body_markdown": "正文"}), \
                 patch.object(pipeline.platform_cover, "ensure_platform_cover", return_value={"path": str(dir1 / "cover.jpg"), "data_url": ""}), \
                 patch.object(pipeline.markdown_to_docx, "parse_markdown_to_docx", side_effect=fake_docx):
                jrtt = pipeline.build_toutiao_copy_and_save("测试标题", "正文", target_dir=dir1)
            self.assertTrue(jrtt["docx_path"].endswith("-今日头条文案.docx"))
            self.assertEqual(Path(jrtt["docx_path"]).parent, dir1)
            self.assertEqual(Path(jrtt["md_path"]).parent, dir1)
            self.assertEqual(Path(jrtt["cover_path"]).parent, dir1)

            # 3. 验证 run_batch_publish 将各平台参数 target_dir 正确传递
            target_dirs_passed = []
            def fake_save_draft(record_id, title, markdown, target_dir=None):
                target_dirs_passed.append(("draft", str(target_dir)))
                return {"path": f"{target_dir}/draft.md"}
            def fake_push(record_id, title, author, theme, markdown, engine="builtin", target_dir=None):
                target_dirs_passed.append(("wechat", str(target_dir)))
                return {"draft_media_id": "media-1"}
            def fake_xhs(title, markdown, run_dir, target_dir=None):
                target_dirs_passed.append(("xhs", str(target_dir)))
                return {"md_path": f"{target_dir}/xhs.md"}
            def fake_toutiao(title, markdown, run_dir, target_dir=None):
                target_dirs_passed.append(("toutiao", str(target_dir)))
                return {"docx_path": f"{target_dir}/jrtt.docx"}

            with patch.object(pipeline.feishu_btable.FeishuBitable, "get_record", return_value={"fields": {"选题标题": "测试三平台"}}), \
                 patch.object(pipeline.llm, "resolve_template", return_value="deepdive"), \
                 patch.object(pipeline.llm, "generate_draft", return_value="正文内容"), \
                 patch.object(pipeline, "save_draft", side_effect=fake_save_draft), \
                 patch.object(pipeline, "push_draft_article", side_effect=fake_push), \
                 patch.object(pipeline, "build_xhs_copy_and_save", side_effect=fake_xhs), \
                 patch.object(pipeline, "build_toutiao_copy_and_save", side_effect=fake_toutiao):
                res = pipeline.run_batch_publish(["rec-3plat"])

            self.assertEqual(res["status"], "success")
            item = res["items"][0]
            # 此时新的子目录应该是 02
            self.assertTrue(item["item_dir"].endswith("/02"))
            # 确认 draft、wechat、xhs、toutiao 全部传递了同一个 target_dir (03)
            all_dirs = [d[1] for d in target_dirs_passed]
            self.assertEqual(len(all_dirs), 4)
            self.assertTrue(all(d == item["item_dir"] for d in all_dirs))
        finally:
            pipeline.OUTPUTS = old_outputs


if __name__ == "__main__":
    unittest.main()
