#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import sys
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from scripts import fetch_hot_skills


class FetchHotSkillsTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.history_file = Path(self.temp_dir.name) / "scanned_skills_history.json"
        self._orig_history_file = fetch_hot_skills.HISTORY_FILE
        fetch_hot_skills.HISTORY_FILE = self.history_file

    def tearDown(self):
        fetch_hot_skills.HISTORY_FILE = self._orig_history_file
        self.temp_dir.cleanup()

    def test_normalize_repo_key(self):
        # 1. 测试标准 URL 提取
        self.assertEqual(
            fetch_hot_skills._normalize_repo_key("https://github.com/anthropics/anthropic-quickstarts.git"),
            "anthropics/anthropic-quickstarts"
        )
        self.assertEqual(
            fetch_hot_skills._normalize_repo_key("https://github.com/modelcontextprotocol/servers/"),
            "modelcontextprotocol/servers"
        )
        # 2. 测试已经是 owner/repo 形式
        self.assertEqual(
            fetch_hot_skills._normalize_repo_key("Agent-Tools/Web-Scraper"),
            "agent-tools/web-scraper"
        )
        # 3. 空值处理
        self.assertEqual(fetch_hot_skills._normalize_repo_key(""), "")

    def test_load_excluded_repo_keys(self):
        # 写入本地历史文件
        self.history_file.write_text(json.dumps({
            "browser-use/browser-use": {
                "name": "browser-use/browser-use",
                "scanned_at": "2026-10-01"
            }
        }, ensure_ascii=False), encoding="utf-8")

        fake_records = [
            {
                "fields": {
                    "选题标题": "月省30刀！平替大厂付费工具",
                    "备注": "【开源地址: https://github.com/modelcontextprotocol/servers | Stars: 15k】",
                }
            },
            {
                "fields": {
                    "选题标题": "无链接普通记录",
                    "备注": "纯文本备注",
                }
            }
        ]

        with patch("feishu_btable.FeishuBitable") as mock_client:
            instance = mock_client.return_value
            instance.list_records.return_value = fake_records
            excluded, local_cnt, feishu_cnt = fetch_hot_skills.load_excluded_repo_keys()

        self.assertIn("browser-use/browser-use", excluded)
        self.assertIn("modelcontextprotocol/servers", excluded)
        self.assertEqual(local_cnt, 1)
        self.assertEqual(feishu_cnt, 1)

    def test_save_scanned_skills_to_history(self):
        topics = [
            {
                "url": "https://github.com/awesome-agent/my-skill",
                "name": "awesome-agent/my-skill",
                "选题标题": "开源新黑马！",
                "stars": 800,
                "velocity": 40.0,
            }
        ]
        fetch_hot_skills.save_scanned_skills_to_history(topics)
        self.assertTrue(self.history_file.is_file())

        data = json.loads(self.history_file.read_text(encoding="utf-8"))
        self.assertIn("awesome-agent/my-skill", data)
        self.assertEqual(data["awesome-agent/my-skill"]["stars"], 800)
        self.assertEqual(data["awesome-agent/my-skill"]["velocity"], 40.0)

    def test_github_trending_skills_velocity_and_deduplication(self):
        today = date.today()
        created_10_days_ago = (today - timedelta(days=10)).strftime("%Y-%m-%d") + "T10:00:00Z"
        created_1000_days_ago = (today - timedelta(days=1000)).strftime("%Y-%m-%d") + "T10:00:00Z"

        fake_github_response = {
            "items": [
                {
                    "full_name": "newbie/fast-growing-tool",
                    "name": "fast-growing-tool",
                    "html_url": "https://github.com/newbie/fast-growing-tool",
                    "description": "Local offline agent workflow tool",
                    "stargazers_count": 500,
                    "created_at": created_10_days_ago,
                    "pushed_at": created_10_days_ago,
                    "language": "Python",
                    "topics": ["agent", "local"],
                    "size": 100,
                },
                {
                    "full_name": "oldie/slow-tool",
                    "name": "slow-tool",
                    "html_url": "https://github.com/oldie/slow-tool",
                    "description": "An old mcp tool",
                    "stargazers_count": 600,
                    "created_at": created_1000_days_ago,
                    "pushed_at": created_10_days_ago,
                    "language": "Python",
                    "topics": ["mcp"],
                    "size": 100,
                },
                {
                    "full_name": "already/done-repo",
                    "name": "done-repo",
                    "html_url": "https://github.com/already/done-repo",
                    "description": "Already scanned before",
                    "stargazers_count": 2000,
                    "created_at": created_10_days_ago,
                    "pushed_at": created_10_days_ago,
                    "language": "Python",
                    "topics": ["agent"],
                    "size": 100,
                }
            ]
        }

        with patch("scripts.fetch_hot_skills.fetch_url_json", return_value=fake_github_response):
            # 将 already/done-repo 设为已被排除
            results = fetch_hot_skills.get_github_trending_skills(
                limit_per_query=5,
                excluded_keys={"already/done-repo"}
            )

        repo_names = [it["name"] for it in results]
        # 1. 验证已排除的项目被过滤
        self.assertNotIn("already/done-repo", repo_names)
        self.assertIn("newbie/fast-growing-tool", repo_names)
        self.assertIn("oldie/slow-tool", repo_names)

        # 2. 验证新兴爆发黑马排在老项目前面（即便老项目总 Star 更多）
        top_item = results[0]
        self.assertEqual(top_item["name"], "newbie/fast-growing-tool")
        self.assertGreaterEqual(top_item["velocity"], 45.0)
        self.assertIn("⚡", top_item["heat_badge"])

    def test_refine_skills_fallback_includes_heat_badges(self):
        raw_items = [
            {
                "source": "GitHub Skill",
                "name": "developer/super-tool",
                "stars": 600,
                "velocity": 30.0,
                "days_old": 20,
                "heat_badge": "⚡ 诞生仅20天破600★ (日均+30.0★)",
                "growth_level": "新星黑马",
                "url": "https://github.com/developer/super-tool",
                "summary": "Local terminal automation",
                "language": "Go",
            }
        ]
        # 当无 LLM API 时使用规则提炼
        with patch.object(fetch_hot_skills.config, "get", return_value=""):
            topics = fetch_hot_skills.refine_skills_with_llm(raw_items, limit=1)

        self.assertEqual(len(topics), 1)
        topic = topics[0]
        self.assertEqual(topic["流量预估"], "新星黑马")
        self.assertIn("⚡ 诞生仅20天破600★", topic["选题标题"])
        self.assertIn("⚡ 诞生仅20天破600★", topic["备注"])


if __name__ == "__main__":
    unittest.main()
