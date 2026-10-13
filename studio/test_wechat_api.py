"""微信正文 HTML 与草稿接口错误诊断的离线回归测试。"""
from __future__ import annotations

import base64
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import wechat_api
from studio.server import typeset


class WeChatHtmlSafetyTests(unittest.TestCase):
    def test_code_block_special_characters_survive_typesetting(self):
        markdown = """## Worktrees\n\n```text\nproject <-- Agent 1\n<tag> & value\n```\n"""
        cover = "data:image/png;base64," + base64.b64encode(b"cover").decode()

        rendered = typeset.typeset(
            markdown,
            title="HTML 安全",
            author="测试",
            cover_data_url=cover,
        )
        root = wechat_api.extract_copy_root(rendered)
        code = root.xpath(".//pre/code")

        self.assertEqual(len(code), 1)
        self.assertEqual(code[0].text, "project <-- Agent 1\n<tag> & value")
        self.assertIn("&lt;-- Agent 1", rendered)
        self.assertIn("&lt;tag&gt; &amp; value", rendered)
        self.assertNotIn("<-- Agent 1", rendered)

    def test_heading_and_quote_special_characters_are_escaped(self):
        markdown = """# 2 < 3 & 5 > 4\n\n> 2 < 3 & 5 > 4\n"""
        rendered = typeset.typeset(
            markdown,
            title="HTML 安全",
            author="测试",
            cover_data_url="data:image/png;base64,Y292ZXI=",
        )

        self.assertGreaterEqual(rendered.count("2 &lt; 3 &amp; 5 &gt; 4"), 2)


class WeChatDraftErrorTests(unittest.TestCase):
    def test_draft_add_reports_wechat_business_error_before_media_id_check(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            html_path = root / "layout.html"
            html_path.write_text(
                '<section data-copy-root><img src="data:image/png;base64,Y292ZXI="></section>',
                encoding="utf-8",
            )
            prepared = {
                "content": '<section><img src="https://mmbiz.qpic.cn/test/0"></section>',
                "visible_text": "",
                "body_fingerprint": "fingerprint",
                "image_count": 1,
                "image_urls": ["https://mmbiz.qpic.cn/test/0"],
                "cover_path": root / "cover.png",
                "cover_sha256": "cover-sha",
            }
            prepared["cover_path"].write_bytes(b"cover")

            with patch.object(wechat_api, "get_access_token", return_value=("token", "appid")), \
                    patch.object(wechat_api, "prepare_content", return_value=prepared), \
                    patch.object(wechat_api, "upload_permanent_image", return_value="thumb"), \
                    patch.object(
                        wechat_api,
                        "post_json",
                        return_value={"errcode": 47001, "errmsg": "data format error"},
                    ):
                with self.assertRaisesRegex(wechat_api.WeChatError, "47001.*data format error"):
                    wechat_api.push_draft(html_path, "标题", "作者", root / "run")


if __name__ == "__main__":
    unittest.main()
