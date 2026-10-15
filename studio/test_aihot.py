import unittest
from unittest.mock import patch, MagicMock
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import fetch_aihot
from studio.app import scan_aihot_topics, ScanAihotRequest


class TestAihotFetch(unittest.TestCase):

    @patch("fetch_aihot._fetch_url_json")
    @patch("fetch_aihot._fetch_url_text")
    @patch("studio.server.llm.chat")
    @patch("studio.server.llm.parse_json_object")
    def test_fetch_aihot_top_topics_mock(self, mock_parse_json, mock_chat, mock_fetch_text, mock_fetch_json):
        # 1. 模拟 hot-topics 与 story 接口数据
        def fake_fetch_json(url, *args, **kwargs):
            if "hot-topics" in url:
                return {
                    "items": [
                        {
                            "rank": 1,
                            "title": "Anthropic 宣布放弃 Git 转向内部版本控制系统",
                            "sourceCount": 18,
                            "participantCount": 5200,
                            "links": {
                                "story": "https://aihot.virxact.com/story/story-12345",
                                "original": "https://example.com/anthropic-git"
                            },
                            "source": {"name": "Anthropic Blog"}
                        }
                    ]
                }
            return None

        mock_fetch_json.side_effect = fake_fetch_json
        mock_fetch_text.return_value = "### 事件综述：Anthropic 团队披露了放弃传统 Git 架构的内幕，改用新型代码快照系统。\n\n### 报道时间线"
        mock_chat.return_value = '{"title": "深度解析Anthropic弃用Git内幕：代码快照系统颠覆传统开发流"}'
        mock_parse_json.return_value = {
            "title": "深度解析Anthropic弃用Git内幕：代码快照系统颠覆传统开发流",
            "keywords": "Anthropic, Git, 架构",
            "category": "技术突破"
        }

        topics = fetch_aihot.fetch_aihot_top_topics(limit=1)

        self.assertEqual(len(topics), 1)
        item = topics[0]
        self.assertIn("Anthropic", item["选题标题"])
        self.assertIn("Git", item["关键词"])
        self.assertEqual(item["流量预估"], "高")
        self.assertEqual(item["状态"], "待定")
        self.assertIn("Top 1", item["备注"])
        self.assertIn("Anthropic 团队披露", item["备注"])

    @patch("studio.app.feishu_btable.FeishuBitable")
    @patch("fetch_aihot.fetch_aihot_top_topics")
    def test_scan_aihot_api_function(self, mock_fetch, mock_feishu_cls):
        mock_fetch.return_value = [
            {
                "选题标题": "测试热点标题：解析最新大模型突破",
                "关键词": "AI, 突破",
                "内容方向": "行业热点",
                "流量预估": "高",
                "状态": "待定",
                "入库日期": "2026-10-15",
                "备注": "测试备注"
            }
        ]
        mock_client = MagicMock()
        mock_client.add_records.return_value = [{"record_id": "rec_1"}]
        mock_feishu_cls.return_value = mock_client

        req = ScanAihotRequest(limit=1, add_feishu=True)
        res = scan_aihot_topics(req)

        self.assertTrue(res["ok"])
        self.assertEqual(res["created"], 1)
        self.assertEqual(len(res["topics"]), 1)


if __name__ == "__main__":
    unittest.main()
