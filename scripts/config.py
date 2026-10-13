"""公众号内容飞轮的共享配置 —— 从 config.env 加载凭据，环境变量优先。

config.env 位于技能根目录，包含飞书与微信两套凭据，
由 .gitignore 排除并强制 600 权限。
"""
from __future__ import annotations

import os
import stat
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = SKILL_ROOT / "config.env"
ENV_KEYS = {
    "FEISHU_APP_ID",
    "FEISHU_APP_SECRET",
    "FEISHU_APP_TOKEN",
    "FEISHU_TABLE_ID",
    "WECHAT_APP_ID",
    "WECHAT_APP_SECRET",
    "WECHAT_AUTHOR",
    "LLM_API_BASE",
    "LLM_API_KEY",
    "LLM_MODEL",
    "IMAGE_MODEL",
    "IMAGE_GEN_API_BASE",
    "IMAGE_GEN_API_KEY",
    "IMAGE_COVER_MODEL",
    "IMAGE_COVER_SIZE",
    "IMAGE_WECHAT_COVER_SIZE",
    "IMAGE_XHS_COVER_SIZE",
    "IMAGE_JRTT_COVER_SIZE",
    "SMTP_SERVER",
    "SMTP_PORT",
    "SMTP_USER",
    "SMTP_PASSWORD",
    "SMTP_FROM",
}
WECHAT_DEFAULT_AUTHOR = "小崽子AI"

_config: dict[str, str] = {}
_last_mtime: float = 0.0


def _load_env_file() -> None:
    global _last_mtime
    if not ENV_FILE.is_file():
        return
    try:
        mtime = ENV_FILE.stat().st_mtime
        if mtime == _last_mtime:
            return
        _last_mtime = mtime
    except OSError:
        pass
    current_mode = stat.S_IMODE(ENV_FILE.stat().st_mode)
    if current_mode & 0o077:
        try:
            ENV_FILE.chmod(0o600)
        except OSError as exc:
            raise RuntimeError(f"config.env 权限不安全且无法自动修复：{ENV_FILE}") from exc
    _config.clear()
    for raw_line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        key, separator, value = line.partition("=")
        key = key.strip()
        if not separator or key not in ENV_KEYS:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        if value:
            _config[key] = value


_load_env_file()


def get(key: str) -> str:
    _load_env_file()
    return os.environ.get(key, "").strip() or _config.get(key, "").strip()



def require(key: str) -> str:
    value = get(key)
    if not value:
        raise RuntimeError(
            f"缺少 {key}，请先按 README 配置 {ENV_FILE}（或设置同名环境变量）"
        )
    return value


def wechat_author() -> str:
    return get("WECHAT_AUTHOR") or WECHAT_DEFAULT_AUTHOR
