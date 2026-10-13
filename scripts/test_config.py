"""配置文件白名单回归测试。"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import config


class ConfigImageKeysTests(unittest.TestCase):
    def test_image_settings_are_read_from_temporary_config_file(self):
        values = {
            "IMAGE_GEN_API_BASE": "https://image.test/v1",
            "IMAGE_GEN_API_KEY": "temporary-image-key",
            "IMAGE_COVER_MODEL": "temporary-cover-model",
            "IMAGE_COVER_SIZE": "1536x1024",
            "IMAGE_WECHAT_COVER_SIZE": "900x383",
            "IMAGE_XHS_COVER_SIZE": "1024x1536",
            "IMAGE_JRTT_COVER_SIZE": "1536x864",
        }
        content = "\n".join(f"{key}={value}" for key, value in values.items()) + "\n"

        with tempfile.TemporaryDirectory() as directory:
            env_file = Path(directory) / "config.env"
            env_file.write_text(content, encoding="utf-8")
            with patch.object(config, "ENV_FILE", env_file), patch.dict(
                os.environ, {key: "" for key in values}
            ):
                config._last_mtime = 0
                config._config.clear()
                self.assertEqual(
                    {key: config.get(key) for key in values},
                    values,
                )


if __name__ == "__main__":
    unittest.main()
