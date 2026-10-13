"""Finder 文件定位接口测试，不实际打开 Finder。"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from studio import app as web
from studio.server import pipeline


class FinderRevealTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.outputs = Path(temporary.name) / "outputs"
        self.outputs.mkdir()
        self.file = self.outputs / "小红书笔记.md"
        self.file.write_text("文章正文", encoding="utf-8")
        patcher = patch.object(pipeline, "OUTPUTS", self.outputs)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_reveals_and_selects_file_in_finder(self):
        with patch.object(pipeline.sys, "platform", "darwin"), patch.object(pipeline.subprocess, "run") as run:
            result = pipeline.reveal_file(str(self.file))
        self.assertEqual(result, {"path": str(self.file.resolve()), "name": "小红书笔记.md"})
        run.assert_called_once_with(
            ["open", "-R", str(self.file.resolve())], check=True, timeout=10,
            stdout=pipeline.subprocess.DEVNULL, stderr=pipeline.subprocess.PIPE,
            text=True,
        )

    def test_rejects_path_outside_outputs_before_opening_finder(self):
        outside = self.outputs.parent / "secret.txt"
        outside.write_text("secret", encoding="utf-8")
        with patch.object(pipeline.sys, "platform", "darwin"), patch.object(pipeline.subprocess, "run") as run:
            with self.assertRaisesRegex(ValueError, "非法路径"):
                pipeline.reveal_file(str(outside))
        run.assert_not_called()

    def test_non_macos_reports_clear_error(self):
        with patch.object(pipeline.sys, "platform", "linux"), patch.object(pipeline.subprocess, "run") as run:
            with self.assertRaisesRegex(RuntimeError, "macOS"):
                pipeline.reveal_file(str(self.file))
        run.assert_not_called()

    def test_missing_file_is_not_opened(self):
        with patch.object(pipeline.sys, "platform", "darwin"), patch.object(pipeline.subprocess, "run") as run:
            with self.assertRaisesRegex(ValueError, "文件不存在"):
                pipeline.reveal_file(str(self.outputs / "missing.md"))
        run.assert_not_called()

    def test_api_delegates_to_finder_service(self):
        with patch.object(web.pipeline, "reveal_file", return_value={"path": str(self.file), "name": self.file.name}) as reveal:
            response = web.reveal_file(web.RevealFileRequest(path=str(self.file)))
        self.assertEqual(response, {"ok": True, "path": str(self.file), "name": self.file.name})
        reveal.assert_called_once_with(str(self.file))

    def test_api_returns_error_without_a_success_payload(self):
        with patch.object(web.pipeline, "reveal_file", side_effect=RuntimeError("Finder 打开失败")):
            response = web.reveal_file(web.RevealFileRequest(path=str(self.file)))
        data = json.loads(response.body)
        self.assertFalse(data["ok"])
        self.assertNotIn("path", data)
        self.assertIn("Finder 打开失败", data["error"])


if __name__ == "__main__":
    unittest.main()
