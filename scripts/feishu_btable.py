"""飞书多维表格（Bitable）客户端 —— 公众号内容飞轮的选题库读写层。

纯标准库实现，供 Qoder 编排流程时直接调用：
- 获取 tenant_access_token
- 读取数据表字段定义（校验选题库表结构）
- 批量写入选题记录（状态默认「待定」）
- 按状态/标题查询记录
- 更新记录状态与回填字段

用法示例：
    python feishu_btable.py list --status 选中
    python feishu_btable.py add --json /path/to/topics.json
    python feishu_btable.py update --record-id recXXX --status 已推送
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

# 允许被飞轮脚本与 Qoder 编排直接 import 使用
sys.path.insert(0, str(Path(__file__).resolve().parent))

import config  # noqa: E402

API_BASE = "https://open.feishu.cn/open-apis"

# 选题库标准字段。脚本会与真实表字段做兼容校验：
# 名称一致即可，多余字段不影响运行。
TOPIC_FIELDS = {
    "选题标题": "text",
    "关键词": "text",
    "内容方向": "text",
    "流量预估": "text",
    "状态": "select",
    "备注": "text",
    "文章标题": "text",  # 写稿后回填，可选
    "稿件路径": "text",  # 写稿后回填，可选
    "入库日期": "text",  # 自动填入库当天日期，供「今天」筛选，可选
}
REQUIRED_FIELDS = ["选题标题", "关键词", "状态", "备注"]


class FeishuError(RuntimeError):
    """飞书 API 业务错误，可安全展示给用户。"""


def _post_json(path: str, payload: dict, token: str | None = None, timeout: int = 30) -> dict:
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(
        f"{API_BASE}{path}",
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers=headers,
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        raise FeishuError(f"飞书请求失败 {path}: {exc}") from exc


def _put_json(path: str, payload: dict, token: str, timeout: int = 30) -> dict:
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(
        f"{API_BASE}{path}",
        data=json.dumps(payload).encode("utf-8"),
        method="PUT",
        headers=headers,
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        raise FeishuError(f"飞书请求失败 {path}: {exc}") from exc


def _get_json(path: str, token: str, timeout: int = 30) -> dict:
    request = Request(
        f"{API_BASE}{path}",
        method="GET",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        raise FeishuError(f"飞书请求失败 {path}: {exc}") from exc


def _raise_on_code(response: dict, action: str) -> None:
    code = response.get("code")
    if code != 0:
        raise FeishuError(f"飞书 API 错误 {code}（{action}）：{response.get('msg', '未知错误')}")


class FeishuBitable:
    """选题库读写客户端；Token 进程内缓存，避免频繁申请。"""

    def __init__(self, app_id: str | None = None, app_secret: str | None = None,
                 app_token: str | None = None, table_id: str | None = None):
        self.app_id = app_id or config.require("FEISHU_APP_ID")
        self.app_secret = app_secret or config.require("FEISHU_APP_SECRET")
        self.app_token = app_token or config.require("FEISHU_APP_TOKEN")
        self.table_id = table_id or config.require("FEISHU_TABLE_ID")
        self._token: str | None = None
        self._token_expires_at = 0.0

    # ---- 鉴权 ----

    def get_token(self) -> str:
        if self._token and time.time() < self._token_expires_at - 60:
            return self._token
        response = _post_json(
            "/auth/v3/tenant_access_token/internal",
            {"app_id": self.app_id, "app_secret": self.app_secret},
        )
        _raise_on_code(response, "获取 tenant_access_token")
        token = response.get("tenant_access_token")
        if not token:
            raise FeishuError("飞书未返回 tenant_access_token")
        self._token = token
        self._token_expires_at = time.time() + int(response.get("expire", 7200))
        return token

    # ---- 表结构 ----

    def _bitable_path(self) -> str:
        return f"/bitable/v1/apps/{self.app_token}/tables/{self.table_id}"

    def list_fields(self) -> list[dict]:
        response = _get_json(f"{self._bitable_path()}/fields?page_size=100", self.get_token())
        _raise_on_code(response, "读取多维表格字段")
        return response.get("data", {}).get("items", [])

    def verify_fields(self) -> list[str]:
        """校验表结构，返回缺失的必要字段名列表。"""
        fields = {item.get("field_name"): item for item in self.list_fields()}
        missing = [name for name in REQUIRED_FIELDS if name not in fields]
        for name, expected_type in TOPIC_FIELDS.items():
            field = fields.get(name)
            if not field:
                continue
            actual_type = str(field.get("type"))
            if expected_type == "select" and actual_type != "3":
                raise FeishuError(f"字段「{name}」应为单选类型，实际为 {actual_type}")
            if expected_type == "text" and actual_type != "1":
                raise FeishuError(f"字段「{name}」应为文本类型，实际为 {actual_type}")
        return missing

    # ---- 记录读写 ----

    def add_records(self, topics: list[dict], status: str = "待定") -> list[dict]:
        """批量写入选题。topic 键使用中文字段名，未提供状态时用默认值；
        未提供入库日期时自动填当天日期。"""
        if not topics:
            return []
        today = datetime.now().strftime("%Y-%m-%d")
        records = []
        for topic in topics:
            fields = dict(topic)
            fields.setdefault("状态", status)
            fields.setdefault("入库日期", today)
            records.append({"fields": fields})
        response = _post_json(
            f"{self._bitable_path()}/records/batch_create",
            {"records": records},
            self.get_token(),
        )
        _raise_on_code(response, "批量写入选题")
        return response.get("data", {}).get("records", [])

    def list_records(self, status: str | None = None, keyword: str | None = None,
                     page_size: int = 100) -> list[dict]:
        """查询选题；可按单选状态过滤（CurrentValue.[状态]="..."）。"""
        query = {"page_size": str(page_size)}
        if status:
            query["filter"] = f'CurrentValue.[状态]="{status}"'
        response = _get_json(
            f"{self._bitable_path()}/records?{urlencode(query)}", self.get_token()
        )
        _raise_on_code(response, "查询选题记录")
        records = response.get("data", {}).get("items", [])
        if keyword:
            keyword = keyword.strip().lower()
            records = [
                record for record in records
                if keyword in str(record.get("fields", {}).get("选题标题", "")).lower()
            ]
        return records

    def get_record(self, record_id: str) -> dict:
        """按记录 ID 读取单条选题。"""
        response = _get_json(
            f"{self._bitable_path()}/records/{record_id}", self.get_token()
        )
        _raise_on_code(response, "读取选题记录")
        return response.get("data", {}).get("record", {})

    def update_record(self, record_id: str, fields: dict) -> dict:
        response = _put_json(
            f"{self._bitable_path()}/records/{record_id}",
            {"fields": fields},
            self.get_token(),
            timeout=30,
        )
        _raise_on_code(response, "更新选题记录")
        return response.get("data", {}).get("record", {})


def _fields_summary(record: dict) -> dict:
    fields = record.get("fields", {})
    return {
        "record_id": record.get("record_id", ""),
        "选题标题": fields.get("选题标题", ""),
        "关键词": fields.get("关键词", ""),
        "状态": fields.get("状态", ""),
        "备注": fields.get("备注", ""),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="飞书多维表格选题库客户端")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("verify", help="校验表结构是否满足选题库要求")

    list_parser = subparsers.add_parser("list", help="查询选题记录")
    list_parser.add_argument("--status", help="按状态过滤，如 待定/选中/已推送")
    list_parser.add_argument("--keyword", help="按选题标题关键字过滤")

    add_parser = subparsers.add_parser("add", help="批量写入选题")
    add_parser.add_argument("--json", required=True, help="选题 JSON 文件路径（列表）")
    add_parser.add_argument("--status", default="待定", help="写入后的初始状态")

    update_parser = subparsers.add_parser("update", help="更新单条记录")
    update_parser.add_argument("--record-id", required=True, help="飞书记录 ID")
    update_parser.add_argument("--status", help="更新状态，如 已推送")
    update_parser.add_argument("--article-title", dest="article_title", help="回填文章标题")
    update_parser.add_argument("--article-path", dest="article_path", help="回填稿件路径")
    update_parser.add_argument("--note", help="追加备注")

    args = parser.parse_args()
    try:
        client = FeishuBitable()
        if args.command == "verify":
            missing = client.verify_fields()
            if missing:
                print(f"表结构不完整，缺少字段：{missing}")
                return 1
            print("表结构校验通过，可以开始跑飞轮")
            return 0
        if args.command == "list":
            for record in client.list_records(status=args.status, keyword=args.keyword):
                print(json.dumps(_fields_summary(record), ensure_ascii=False))
            return 0
        if args.command == "add":
            topics = json.loads(Path(args.json).read_text(encoding="utf-8"))
            if not isinstance(topics, list):
                raise FeishuError("选题 JSON 必须是列表")
            records = client.add_records(topics, status=args.status)
            print(json.dumps(
                {"写入数量": len(records), "状态": args.status}, ensure_ascii=False
            ))
            return 0
        if args.command == "update":
            fields: dict = {}
            if args.status:
                fields["状态"] = args.status
            if args.article_title:
                fields["文章标题"] = args.article_title
            if args.article_path:
                fields["稿件路径"] = args.article_path
            if args.note:
                fields["备注"] = args.note
            if not fields:
                raise FeishuError("update 至少需要 --status/--article-title/--article-path/--note 之一")
            client.update_record(args.record_id, fields)
            print(json.dumps({"record_id": args.record_id, "updated": fields}, ensure_ascii=False))
            return 0
    except FeishuError as exc:
        print(f"❌ {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
