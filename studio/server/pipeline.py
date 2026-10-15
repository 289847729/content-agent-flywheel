"""流程编排 —— 保存初稿、回填飞书、排版、推送草稿箱、回填状态。

页面操作台各环节的落盘与外部副作用都收在这里，
复用 scripts/feishu_btable.py 与 scripts/wechat_api.py，不复制业务逻辑。
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

# 技能脚本路径引入 (tuiwen-xhs-gen & tuiwen-jrtt-gen)
_ROOT = Path(__file__).resolve().parents[2]
_XHS_SCRIPTS = _ROOT / ".agents" / "skills" / "tuiwen-xhs-gen" / "scripts"
_JRTT_SCRIPTS = _ROOT / ".agents" / "skills" / "tuiwen-jrtt-gen" / "scripts"
if str(_XHS_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_XHS_SCRIPTS))
if str(_JRTT_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_JRTT_SCRIPTS))

import feishu_btable  # noqa: E402
import markdown_to_docx  # noqa: E402
import wechat_api  # noqa: E402
import article_extractor  # noqa: E402

from . import article_cover, bmmd_typeset, covers, docx_export, llm, platform_cover, typeset  # noqa: E402

SKILL_ROOT = Path(__file__).resolve().parents[2]
OUTPUTS = SKILL_ROOT / "outputs"
RUNS = OUTPUTS / "runs"
XHS_DIR = OUTPUTS / "小红书"
JRTT_DIR = OUTPUTS / "今日头条"

# 批量发布作业只使用进程内线程；状态文件是故意的简单 JSON，便于用户查看、备份和测试。
_BATCH_LOCK = threading.RLock()


ILLEGAL_FILENAME = re.compile(r'[\\/:*?"<>|\r\n]+')


def sanitize_filename(name: str, max_chars: int = 60) -> str:
    name = ILLEGAL_FILENAME.sub("-", name.strip()).strip().strip(".")
    if len(name) > max_chars:
        name = name[:max_chars].rstrip(" -_.")
    return name or "未命名"


def _write_json(path: Path, data: dict) -> None:
    """原子写入批量状态，避免 GET 读到半个 JSON。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _batch_update(status_path: Path, update) -> dict:
    with _BATCH_LOCK:
        current = json.loads(status_path.read_text(encoding="utf-8"))
        update(current)
        current["updated_at"] = datetime.now().isoformat(timespec="seconds")
        _write_json(status_path, current)
        return current


def _error(exc: Exception) -> str:
    return f"{type(exc).__name__}: {exc}".strip()


def _item_paths(value) -> list[str]:
    if not isinstance(value, dict):
        return []
    paths = []
    for key, item in value.items():
        if key.endswith("path") and isinstance(item, str) and item:
            paths.append(item)
        elif key in {"files", "image_files"} and isinstance(item, list):
            paths.extend(str(path) for path in item if path)
    return list(dict.fromkeys(paths))


def _batch_item(record_id: str) -> dict:
    return {
        "record_id": record_id,
        "status": "pending",
        "title": "",
        "steps": {},
        "platforms": {
            "wechat": {"status": "pending", "artifacts": []},
            "xhs": {"status": "pending", "artifacts": []},
            "toutiao": {"status": "pending", "artifacts": []},
        },
        "artifacts": [],
        "errors": [],
    }


def _set_item(status_path: Path, index: int, mutate) -> dict:
    def update(status):
        mutate(status["items"][index])
    return _batch_update(status_path, update)


def _run_batch_item(status_path: Path, index: int, options: dict, retry: bool = False) -> None:
    """执行单条记录；每一步单独记录，单个平台失败不会影响其它记录/平台。"""
    status = json.loads(status_path.read_text(encoding="utf-8"))
    item = status["items"][index]
    record_id = item["record_id"]
    fields = item.get("fields") or {}
    markdown = item.get("markdown", "")
    title = item.get("title", "")

    def step(name, action):
        nonlocal item, markdown, title, fields
        old = item.get("steps", {}).get(name, {})
        if retry and old.get("status") == "success":
            return old.get("result") or {}
        try:
            result = action() or {}
            def mark(target):
                target.setdefault("steps", {})[name] = {"status": "success", "result": result}
                target["errors"] = [e for e in target.get("errors", []) if (e.get("step") if isinstance(e, dict) else None) != name]
            _set_item(status_path, index, mark)
            item = json.loads(status_path.read_text(encoding="utf-8"))["items"][index]
            return result
        except Exception as exc:
            message = _error(exc)
            def mark_failed(target):
                target.setdefault("steps", {})[name] = {"status": "failed", "error": message}
                target.setdefault("errors", []).append({"step": name, "error": message})
                target["status"] = "partial"
            _set_item(status_path, index, mark_failed)
            raise

    try:
        record = step("read_feishu", lambda: feishu_btable.FeishuBitable().get_record(record_id))
        if record:
            fields = record.get("fields") or {}
            title = (fields.get("文章标题") or fields.get("选题标题") or title or "").strip()
            def save_record(target):
                target["fields"] = fields
                target["title"] = title
            _set_item(status_path, index, save_record)

        def generate():
            chosen = llm.resolve_template(fields, options.get("template", "auto"))
            return {"markdown": llm.generate_draft(fields, template=chosen), "template": chosen}
        generated = step("generate_markdown", generate)
        markdown = generated.get("markdown") or markdown
        def save_generated(target):
            target["title"] = title
            target["markdown"] = markdown
        _set_item(status_path, index, save_generated)
        saved = step("save_draft", lambda: save_draft(record_id, title, markdown))
        def save_artifacts(target):
            target["artifacts"] = list(dict.fromkeys(target.get("artifacts", []) + _item_paths(saved)))
        _set_item(status_path, index, save_artifacts)
    except Exception:
        # 基础步骤失败时，后续平台标为 blocked，但其它 item 继续。
        def blocked(target):
            for platform in target["platforms"].values():
                if platform.get("status") == "pending":
                    platform["status"] = "blocked"
            target["status"] = "failed"
        _set_item(status_path, index, blocked)
        return

    def platform(name, action):
        current = json.loads(status_path.read_text(encoding="utf-8"))["items"][index]
        if retry and current["platforms"].get(name, {}).get("status") == "success":
            return
        try:
            result = action()
            paths = _item_paths(result)
            def success(target):
                target["platforms"][name] = {"status": "success", "result": result, "artifacts": paths}
                target["artifacts"] = list(dict.fromkeys(target.get("artifacts", []) + paths))
                target["errors"] = [e for e in target.get("errors", []) if (e.get("platform") if isinstance(e, dict) else None) != name]
            _set_item(status_path, index, success)
        except Exception as exc:
            message = _error(exc)
            def failed(target):
                target["platforms"][name] = {"status": "failed", "error": message, "artifacts": []}
                target.setdefault("errors", []).append({"platform": name, "error": message})
            _set_item(status_path, index, failed)

    run_dir = str(Path(status_path).parent)
    platform("wechat", lambda: push_draft_article(
        record_id, title, options.get("author", ""), options.get("theme", "moyu-green"), markdown,
        engine=options.get("engine", "builtin"),
    ))
    platform("xhs", lambda: build_xhs_copy_and_save(title, markdown, run_dir))
    platform("toutiao", lambda: build_toutiao_copy_and_save(title, markdown, run_dir))
    def complete(target):
        states = [value["status"] for value in target["platforms"].values()]
        if all(value == "success" for value in states):
            target["status"] = "success"
            target["errors"] = []
        else:
            target["status"] = "partial"
    _set_item(status_path, index, complete)


def run_batch_publish(record_ids: list[str], *, template: str = "auto", theme: str = "moyu-green",
                      engine: str = "builtin", author: str = "", job_id: str = "") -> dict:
    """可测试的同步批量编排入口；HTTP 层在后台线程调用它。"""
    if not record_ids:
        raise ValueError("record_ids 不能为空")
    job_id = prepare_batch_publish(record_ids, template=template, theme=theme, engine=engine, author=author, job_id=job_id)
    status_path = RUNS / job_id / "batch-status.json"
    options = {"template": template, "theme": theme, "engine": engine, "author": author}
    for index in range(len(record_ids)):
        _run_batch_item(status_path, index, options)
    def finish(data):
        states = [item["status"] for item in data["items"]]
        data["status"] = "success" if all(value == "success" for value in states) else ("failed" if all(value == "failed" for value in states) else "partial")
    return _batch_update(status_path, finish)


def prepare_batch_publish(record_ids: list[str], *, template: str = "auto", theme: str = "moyu-green",
                          engine: str = "builtin", author: str = "", job_id: str = "") -> str:
    """创建可立即 GET 的作业状态文件，供 HTTP 层再启动后台线程。"""
    if not record_ids:
        raise ValueError("record_ids 不能为空")
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    job_id = job_id or f"batch-{stamp}"
    run_dir = RUNS / job_id
    now = datetime.now().isoformat(timespec="seconds")
    _write_json(run_dir / "batch-status.json", {
        "job_id": job_id, "status": "running", "created_at": now, "updated_at": now,
        "options": {"template": template, "theme": theme, "engine": engine, "author": author},
        "items": [_batch_item(record_id) for record_id in record_ids], "errors": [], "run_dir": str(run_dir),
    })
    return job_id


def retry_batch_publish(job_id: str) -> dict:
    """只执行状态中失败的平台/步骤；已成功步骤通过 status 跳过。"""
    status_path = RUNS / job_id / "batch-status.json"
    if not status_path.is_file():
        raise ValueError("批量作业不存在")
    status = json.loads(status_path.read_text(encoding="utf-8"))
    status["status"] = "running"
    _write_json(status_path, status)
    for index, item in enumerate(status["items"]):
        if item.get("status") in {"failed", "partial"}:
            _run_batch_item(status_path, index, status.get("options", {}), retry=True)
    def finish(data):
        states = [item["status"] for item in data["items"]]
        if all(value == "success" for value in states):
            data["status"] = "success"
            data["errors"] = []
        elif all(value == "failed" for value in states):
            data["status"] = "failed"
        else:
            data["status"] = "partial"
    return _batch_update(status_path, finish)


def get_batch_status(job_id: str) -> dict:
    status_path = RUNS / job_id / "batch-status.json"
    if not status_path.is_file():
        raise ValueError("批量作业不存在")
    return json.loads(status_path.read_text(encoding="utf-8"))


def mark_batch_failed(job_id: str, exc: Exception) -> None:
    """兜底记录线程级异常，避免客户端看到永久 running。"""
    status_path = RUNS / job_id / "batch-status.json"
    if not status_path.is_file():
        return
    message = _error(exc)
    def update(status):
        status["status"] = "failed"
        status.setdefault("errors", []).append({"error": message})
    _batch_update(status_path, update)


_BLOCK_LINE_RE = re.compile(r"^\s*(?:[-*+]\s|\d+\.\s|\||>|#{1,6}\s)")
_FENCE_RE = re.compile(r"^\s*(?:```|~~~)")
_TABLE_ROW_RE = re.compile(r"^\s*\|")
_TABLE_SEP_CELL_RE = re.compile(r":?-+:?")


def _is_table_sep(line: str) -> bool:
    """判断是否为 Markdown 表格分隔行（|---|---| 或 |:---:|）。"""
    cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
    return bool(cells) and all(
        cell and _TABLE_SEP_CELL_RE.fullmatch(cell) for cell in cells
    )


def _fix_tables(lines: list[str]) -> list[str]:
    """为缺失分隔行的 Markdown 表格补上分隔行。

    手写或模型生成的表格经常只有表头 + 数据行，缺 `|---|` 分隔行，
    Markdown 会把它当成普通段落（整块变成一句话），这里按表头列数自动补全。
    """
    out: list[str] = []
    i = 0
    in_fence = False
    while i < len(lines):
        if _FENCE_RE.match(lines[i]):
            out.append(lines[i])
            in_fence = not in_fence
            i += 1
            continue
        if in_fence or not _TABLE_ROW_RE.match(lines[i]):
            out.append(lines[i])
            i += 1
            continue
        j = i
        while j < len(lines) and _TABLE_ROW_RE.match(lines[j]):
            j += 1
        block = lines[i:j]
        if len(block) >= 2 and not _is_table_sep(block[1]):
            header = block[0]
            columns = max(1, header.strip().strip("|").count("|") + 1)
            out.append(header)
            out.append("|" + "|".join([" --- "] * columns) + "|")
            out.extend(block[1:])
        else:
            out.extend(block)
        i = j
    return out


def normalize_markdown(markdown: str) -> str:
    """排版前归一化：块级元素（列表/表格/引用/标题/代码块）前后补空行。

    初稿习惯用「行尾双空格 + 换行」写短句，紧贴段落开头的 `- 列表` 会被
    Markdown 当成段落延续而丢失列表样式，这里统一修正；代码围栏内部原样保留。
    """
    lines = markdown.replace("\r\n", "\n").split("\n")
    lines = _fix_tables(lines)
    out: list[str] = []
    in_fence = False
    for line in lines:
        if in_fence:
            out.append(line)
            if _FENCE_RE.match(line):
                in_fence = False
            continue
        if _FENCE_RE.match(line):
            if out and out[-1].strip():
                out.append("")
            out.append(line)
            in_fence = True
            continue
        is_block = bool(_BLOCK_LINE_RE.match(line))
        if is_block and line.endswith("  "):
            line = line.rstrip()  # 块行内部不需要硬换行
        if out and out[-1].strip():
            prev_is_block = bool(_BLOCK_LINE_RE.match(out[-1]))
            if is_block != prev_is_block:
                out.append("")
        out.append(line)
    return "\n".join(out)


def save_draft(record_id: str, title: str, markdown: str) -> dict:
    """保存初稿到 outputs/{日期}-{标题}.md，并回填飞书（文章标题、稿件路径）。"""
    title = (title or "").strip()
    if not title:
        raise ValueError("标题不能为空")
    if not markdown.strip():
        raise ValueError("初稿内容为空")
    date = datetime.now().strftime("%Y-%m-%d")
    path = OUTPUTS / f"{date}-{sanitize_filename(title)}.md"
    path.write_text(markdown.strip() + "\n", encoding="utf-8")

    client = feishu_btable.FeishuBitable()
    client.update_record(record_id, {"文章标题": title, "稿件路径": str(path)})
    return {"path": str(path), "title": title, "date": date}


def build_typeset_html(markdown: str, title: str, author: str, theme: str,
                       hook: str = "", subtitle: str = "", engine: str = "builtin",
                       style: str = bmmd_typeset.DEFAULT_STYLE,
                       code_theme: str = "", cover_data_url: str = "") -> tuple[str, int]:
    """Markdown → 排版 HTML（含封面卡），返回 (html, 正文字符数)。

    engine=builtin 用内置三主题；engine=bmmd 用 bmmd 引擎（16 款样式）。
    """
    if not cover_data_url:
        cover_data_url = article_cover.ensure_article_cover(title, markdown)["data_url"]
    markdown = normalize_markdown(markdown)
    if engine == "bmmd":
        try:
            html = bmmd_typeset.typeset(
                markdown, title=title, author=author, hook=hook, subtitle=subtitle,
                style=style, code_theme=code_theme or None, cover_data_url=cover_data_url,
            )
        except Exception as exc:
            logger.warning("bmmd 排版引擎异常，自动降级至内置主题「%s」：%s", theme, exc)
            html = typeset.typeset(
                markdown, title=title, author=author, theme=theme, hook=hook, subtitle=subtitle,
                cover_data_url=cover_data_url,
            )
    else:
        html = typeset.typeset(
            markdown, title=title, author=author, theme=theme, hook=hook, subtitle=subtitle,
            cover_data_url=cover_data_url,
        )
    return html, typeset.content_length(html)


def push_draft_article(record_id: str, title: str, author: str, theme: str,
                       markdown: str = "", imported_html: str = "",
                       hook: str = "", subtitle: str = "", engine: str = "builtin",
                       style: str = bmmd_typeset.DEFAULT_STYLE,
                       code_theme: str = "") -> dict:
    """排版并推送草稿箱，成功后回填飞书状态「已推送」。

    imported_html 非空时直接推送外部排版稿（如 gzh-design-skill 产出），
    否则按 engine 现场排版（builtin 内置主题 / bmmd 引擎）。
    """
    title = (title or "").strip()
    if not title:
        raise ValueError("标题不能为空")
    author = (author or "").strip() or "小崽子AI"

    outdir = RUNS / datetime.now().strftime("%Y-%m-%d-%H%M%S")
    outdir.mkdir(parents=True, exist_ok=True)
    if imported_html.strip():
        html = imported_html
    else:
        if not markdown.strip():
            raise ValueError("初稿内容为空，先回到「文案生成」生成初稿")
        cover = article_cover.ensure_article_cover(title, markdown)
        (outdir / "cover.jpg").write_bytes(Path(cover["path"]).read_bytes())
        (outdir / "cover-plan.json").write_text(
            json.dumps({key: value for key, value in cover.items() if key != "data_url"}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        html, _ = build_typeset_html(
            markdown, title, author, theme, hook, subtitle, engine, style, code_theme,
            cover_data_url=cover["data_url"],
        )
    layout_path = outdir / "layout.html"
    layout_path.write_text(html, encoding="utf-8")
    if markdown.strip():
        (outdir / "article.md").write_text(markdown, encoding="utf-8")

    record = wechat_api.push_draft(layout_path, title, author, outdir)
    client = feishu_btable.FeishuBitable()
    client.update_record(record_id, {"状态": "已推送"})
    record["run_dir"] = str(outdir)
    return record


def get_matrix_status(run_dir: str) -> dict:
    """读取指定推送运行目录下的平台矩阵状态文件 matrix-status.json。"""
    if not run_dir:
        return {}
    status_file = Path(run_dir) / "matrix-status.json"
    if status_file.is_file():
        try:
            return json.loads(status_file.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def update_matrix_status(run_dir: str, platform: str, data: dict) -> dict:
    """更新指定推送运行目录下某一平台（xhs / toutiao）的状态。"""
    if not run_dir:
        return {}
    r_path = Path(run_dir)
    if not r_path.is_dir():
        return {}
    status_file = r_path / "matrix-status.json"
    current = get_matrix_status(run_dir)
    current[platform] = {**current.get(platform, {}), **data}
    status_file.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
    return current


def list_runs() -> list[dict]:
    """扫描 outputs/runs/*/push-result.json，按时间倒序返回历史推送记录。"""
    runs = []
    for result_path in sorted(RUNS.glob("*/push-result.json"), reverse=True):
        try:
            record = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        record["run_dir"] = str(result_path.parent)
        record["platforms"] = get_matrix_status(record["run_dir"])
        runs.append(record)
    return runs


# ---- 公众号文章离线导出包 ----

def build_publish_package(title: str, markdown: str, author: str = "",
                          theme: str = "moyu-green", hook: str = "", subtitle: str = "",
                          engine: str = "builtin", style: str = bmmd_typeset.DEFAULT_STYLE,
                          code_theme: str = "") -> dict:
    """把当前公众号母稿打包落盘到 outputs/runs/。

    产出：
      - article.md      公众号母稿（Markdown 源稿，存档 / 二次编辑）
      - article.docx    公众号排版稿 Word 版（内容与 wechat.html 完全一致）
       - wechat.html     公众号排版 HTML（可直接粘进公众号编辑器）
       - cover.jpg       与预览、推送一致的 AI 主题封面
       - cover-plan.json 主题、构图、提示词与自动视觉自检结果
       - manifest.json   清单（文件列表 + 元信息）
    """
    title = (title or "").strip()
    if not title:
        raise ValueError("标题不能为空")
    if not markdown.strip():
        raise ValueError("初稿内容为空，先回到「文案生成」生成初稿")
    author = (author or "").strip() or "小崽子AI"

    outdir = RUNS / f"{datetime.now().strftime('%Y-%m-%d-%H%M%S')}-{sanitize_filename(title)}"
    outdir.mkdir(parents=True, exist_ok=True)
    files: list[dict] = []
    warnings: list[str] = []

    def _save(name: str, data: bytes) -> None:
        path = outdir / name
        path.write_bytes(data)
        files.append({"name": name, "path": str(path), "size": len(data)})

    # 同一母稿的预览、导出和推送共享内容寻址的 AI 封面。
    cover = article_cover.ensure_article_cover(title, markdown)
    cover_bytes = Path(cover["path"]).read_bytes()
    cover_metadata = {key: value for key, value in cover.items() if key != "data_url"}
    _save("cover.jpg", cover_bytes)
    _save("cover-plan.json", json.dumps(cover_metadata, ensure_ascii=False, indent=2).encode("utf-8"))

    # 公众号母稿：Markdown 源稿 + 公众号排版 HTML + Word 存档
    _save("article.md", (markdown.strip() + "\n").encode("utf-8"))
    html, chars = build_typeset_html(
        markdown, title, author, theme, hook, subtitle, engine, style, code_theme,
        cover_data_url=cover["data_url"],
    )
    _save("wechat.html", html.encode("utf-8"))
    try:
        docx_bytes = docx_export.html_to_docx_bytes(html, title)
    except Exception as exc:  # 排版稿转 docx 失败时回退母稿版，不阻断打包
        warnings.append(f"docx（公众号版）转换失败，已回退母稿版：{exc}")
        docx_bytes = docx_export.markdown_to_docx_bytes(
            markdown, title,
            cover_bytes=cover_bytes,
        )
    _save("article.docx", docx_bytes)

    manifest = {
        "title": title,
        "author": author,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "engine": engine,
        "theme": theme,
        "chars": chars,
        "warnings": warnings,
        "cover": cover_metadata,
        "dir": str(outdir),
        "files": files,
    }
    (outdir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return manifest


def find_markdown_for_title(title: str, run_dir: str = "") -> str:
    """若调用方未传母稿 Markdown，尝试从 run_dir 或 outputs/ 目录中按标题自动寻找匹配的源稿。
    支持自动穿透：
    1. 若提供了 run_dir，优先读 run_dir / 'article.md'；
    2. 在 outputs/*.md 寻找文件名中包含标题或安全标题的文件；
    3. 若提供了 run_dir 且未找到 md，从 run_dir / 'prepared-body.html' 或 'layout.html' 提取富文本内容；
    4. 在 outputs/runs/*/article.md 中搜索匹配标题的文件。
    """
    title = (title or "").strip()
    safe = sanitize_filename(title) if title else ""

    # 1. 优先从指定的 run_dir 寻找
    if run_dir:
        r_path = Path(run_dir)
        if r_path.is_dir():
            md_file = r_path / "article.md"
            if md_file.is_file():
                content = md_file.read_text(encoding="utf-8").strip()
                if content:
                    return content
            for f in r_path.glob("*.md"):
                content = f.read_text(encoding="utf-8").strip()
                if content:
                    return content

    # 2. 从 outputs/*.md 中寻找匹配
    if title:
        for p in sorted(OUTPUTS.glob("*.md"), reverse=True):
            if p.is_file() and (safe in p.stem or title in p.stem or p.stem in safe or p.stem in title):
                content = p.read_text(encoding="utf-8").strip()
                if content:
                    return content

    # 3. 若有 run_dir 且未找到 md，从历史 HTML 提取结构化纯文本正文
    if run_dir:
        r_path = Path(run_dir)
        for html_name in ("prepared-body.html", "layout.html", "wechat.html"):
            html_file = r_path / html_name
            if html_file.is_file():
                try:
                    from lxml import html as lxml_html
                    root = lxml_html.fromstring(html_file.read_text(encoding="utf-8"))
                    text = " ".join(root.itertext())
                    text = re.sub(r"[ \t]+", " ", text)
                    text = re.sub(r"\n\s*\n", "\n\n", text).strip()
                    if text and len(text) > 100:
                        return text
                except Exception:
                    pass

    # 4. 在历史所有 runs 目录中寻找匹配标题的 article.md
    if title:
        for p in sorted(RUNS.glob("*/article.md"), reverse=True):
            if safe in str(p.parent) or title in str(p.parent):
                content = p.read_text(encoding="utf-8").strip()
                if content:
                    return content

    return ""


def import_external_article(url: str) -> dict:
    """根据传入的 X (Twitter) 推文或文章链接抓取文案与全部原图，生成标准 Run 产物。"""
    return article_extractor.import_article_to_run(url)


def build_xhs_copy_and_save(title: str, markdown: str = "", run_dir: str = "") -> dict:
    """根据母稿生成小红书爆款文案，渲染 3:4 置顶封面图，打包装载博主推文全部原图图集，落盘 outputs/小红书/。"""
    title = (title or "").strip()
    if not title:
        raise ValueError("母稿标题不能为空")
    if not markdown.strip():
        markdown = find_markdown_for_title(title, run_dir)
    if not markdown.strip():
        raise ValueError("母稿内容为空，且 outputs/ 下未找到匹配的母稿 Markdown 或 HTML 文件")

    date_str = datetime.now().strftime("%Y-%m-%d")
    XHS_DIR.mkdir(parents=True, exist_ok=True)
    images_dir = XHS_DIR / "images"
    images_dir.mkdir(parents=True, exist_ok=True)

    # 1. LLM 提炼文案与封面配置
    xhs_data = llm.generate_xhs_copy(title, markdown)
    xhs_title = (xhs_data.get("title") or title).strip()
    cover_cfg = xhs_data.get("cover_config") or {}
    body_md = (xhs_data.get("body_markdown") or "").strip()

    # 2. handraw-style 选择风格/构图/配色，Gemini 生成主题插画并叠加平台标题
    safe_title = sanitize_filename(xhs_title)
    cover_filename = f"{date_str}-{safe_title}_cover.jpg"
    cover_path = images_dir / cover_filename
    cover = platform_cover.ensure_platform_cover(
        "xhs", xhs_title, markdown, {**xhs_data, "cover_config": cover_cfg}, cover_path,
    )
    base64_data_url = cover["data_url"]

    # 3. 收集该文章包含的全部博主推文原图（与封面合并为轮播图集）
    tweet_images: list[str] = []
    if run_dir:
        img_dir = Path(run_dir) / "images"
        if img_dir.is_dir():
            for p in sorted(img_dir.glob("image_*")):
                tweet_images.append(str(p.resolve()))

    all_upload_images = [str(cover_path)] + tweet_images

    # 4. 组装小红书标准 Markdown（首行嵌入 Base64 置顶封面）
    full_markdown = f"# {xhs_title}\n\n![小红书置顶封面图]({base64_data_url})\n\n{body_md}\n"
    md_filename = f"{date_str}-{safe_title}-小红书笔记.md"
    md_path = XHS_DIR / md_filename
    md_path.write_text(full_markdown, encoding="utf-8")

    if run_dir:
        update_matrix_status(run_dir, "xhs", {
            "status": "素材已生成",
            "title": xhs_title,
            "md_path": str(md_path),
            "cover_path": str(cover_path),
            "image_files": all_upload_images,
            "files": [str(md_path)] + all_upload_images,
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        })

    return {
        "title": xhs_title,
        "markdown": full_markdown,
        "body_markdown": body_md,
        "md_path": str(md_path),
        "md_filename": md_filename,
        "cover_path": str(cover_path),
        "cover_filename": cover_filename,
        "base64_cover": base64_data_url,
        "cover_plan": {key: value for key, value in cover.items() if key not in {"data_url", "prompt"}},
        "image_files": all_upload_images,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def build_toutiao_copy_and_save(title: str, markdown: str = "", run_dir: str = "") -> dict:
    """根据母稿生成今日头条深度长图文，嵌入博主推文全部原图，生成 16:9 横版吸睛封面，并排版导出 DOCX 文件。"""
    title = (title or "").strip()
    if not title:
        raise ValueError("母稿标题不能为空")
    if not markdown.strip():
        markdown = find_markdown_for_title(title, run_dir)
    if not markdown.strip():
        raise ValueError("母稿内容为空，且 outputs/ 下未找到匹配的母稿 Markdown 或 HTML 文件")

    date_str = datetime.now().strftime("%Y-%m-%d")
    JRTT_DIR.mkdir(parents=True, exist_ok=True)

    # 1. 扫描推文原图并准备嵌入
    tweet_images: list[str] = []
    if run_dir:
        img_dir = Path(run_dir) / "images"
        if img_dir.is_dir():
            for p in sorted(img_dir.glob("image_*")):
                tweet_images.append(str(p.resolve()))

    # 2. LLM 提炼爆款标题、章节结构与封面配置
    jrtt_data = llm.generate_toutiao_copy(title, markdown)
    jrtt_title = llm.normalize_toutiao_title((jrtt_data.get("title") or title).strip(), max_chars=30)
    jrtt_data["title"] = jrtt_title
    cover_cfg = jrtt_data.get("cover_config") or {}
    body_md = (jrtt_data.get("body_markdown") or "").strip()

    # 若有推文原图且 body_md 中尚未显式引用，将其附加到正文，确保进入 DOCX 与头条发文编辑器
    if tweet_images and "![" not in body_md:
        gallery_md = "\n\n### 🖼️ 原推配图与视觉证据\n\n"
        for idx, img_p in enumerate(tweet_images, 1):
            gallery_md += f"![原推配图{idx}]({img_p})\n\n"
        body_md += gallery_md

    # 3. handraw-style 选择风格/构图/配色，Gemini 生成 16:9 主题插画并叠加平台标题
    safe_title = sanitize_filename(jrtt_title)
    cover_filename = f"{date_str}-{safe_title}-今日头条封面.jpg"
    cover_path = JRTT_DIR / cover_filename
    cover = platform_cover.ensure_platform_cover(
        "toutiao", jrtt_title, markdown, {**jrtt_data, "cover_config": cover_cfg}, cover_path,
    )

    # 4. 组装 Markdown 并落盘
    full_markdown = f"# {jrtt_title}\n\n![今日头条封面图]({cover_filename})\n\n{body_md}\n"
    md_filename = f"{date_str}-{safe_title}-今日头条文案.md"
    md_path = JRTT_DIR / md_filename
    md_path.write_text(full_markdown, encoding="utf-8")

    # 5. 自动编译导出高质量排版 DOCX 文件（带三线表、红标装饰、互动框、原推图）
    docx_filename = f"{date_str}-{safe_title}-今日头条文案.docx"
    docx_path = JRTT_DIR / docx_filename
    docx_ok = False
    try:
        markdown_to_docx.parse_markdown_to_docx(str(md_path), str(docx_path), str(cover_path))
        docx_ok = docx_path.is_file()
    except Exception as exc:
        logger.warning("编译今日头条 DOCX 文件发生异常，降级保留 Markdown 与封面: %s", exc)

    all_files = [str(docx_path), str(md_path), str(cover_path)] if docx_ok else [str(md_path), str(cover_path)]
    all_files += tweet_images

    if run_dir:
        update_matrix_status(run_dir, "toutiao", {
            "status": "素材已生成",
            "title": jrtt_title,
            "docx_path": str(docx_path) if docx_ok else "",
            "md_path": str(md_path),
            "cover_path": str(cover_path),
            "image_files": tweet_images,
            "files": all_files,
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        })

    return {
        "title": jrtt_title,
        "markdown": full_markdown,
        "body_markdown": body_md,
        "md_path": str(md_path),
        "md_filename": md_filename,
        "docx_path": str(docx_path) if docx_ok else "",
        "docx_filename": docx_filename if docx_ok else "",
        "cover_path": str(cover_path),
        "cover_filename": cover_filename,
        "cover_plan": {key: value for key, value in cover.items() if key not in {"data_url", "prompt"}},
        "image_files": tweet_images,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def send_matrix_email(
    to_addr: str,
    title: str,
    schedule_time: str,
    run_dir: str,
    platform: str,
    body_text: str = "",
    attachment_paths: list[str] | None = None
) -> dict:
    """已停用：多平台不再通过邮件触发定时发布。"""
    raise RuntimeError("小红书和今日头条自动投递已停用，请使用生成的本地文件并人工发布。")
    import mailer

    status_data = get_matrix_status(run_dir)
    plat_info = status_data.get(platform, {})
    if not attachment_paths:
        attachment_paths = plat_info.get("files", [])

    if not body_text and plat_info.get("md_path") and Path(plat_info["md_path"]).is_file():
        body_text = Path(plat_info["md_path"]).read_text(encoding="utf-8")

    result = mailer.send_article_email(
        to_addr=to_addr,
        title=title,
        schedule_time=schedule_time,
        body_text=body_text,
        attachment_paths=attachment_paths,
    )

    if run_dir:
        update_matrix_status(run_dir, platform, {
            "status": "已发送邮件",
            "schedule_time": schedule_time,
            "sent_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "to_addr": to_addr,
        })

    return result


def run_file(path: str) -> Path:
    """校验并返回 outputs 目录下的文件路径（供下载接口，防止路径穿越）。"""
    target = Path(path).resolve()
    outputs = OUTPUTS.resolve()
    if outputs not in target.parents:
        raise ValueError("非法路径：只允许下载 outputs 目录内的文件")
    if not target.is_file():
        raise ValueError("文件不存在")
    return target


def reveal_file(path: str) -> dict:
    """在本机 Finder 中打开并选中 outputs 目录内的文件。"""
    target = run_file(path)
    if sys.platform != "darwin":
        raise RuntimeError("Finder 定位仅支持运行 Studio 的 macOS 主机")
    try:
        subprocess.run(
            ["open", "-R", str(target)],
            check=True,
            timeout=10,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("未找到 macOS open 命令，无法打开 Finder") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or "").strip()
        raise RuntimeError(f"Finder 打开失败{f'：{detail}' if detail else ''}") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("Finder 打开超时，请稍后重试") from exc
    return {"path": str(target), "name": target.name}


def check_chrome_status() -> dict:
    """检查本地 Chrome CDP 调试端口可用性。"""
    try:
        from scripts.toutiao_publisher import check_chrome_cdp_available, DEFAULT_CDP_URL, get_chrome_launch_command
    except ImportError:
        from toutiao_publisher import check_chrome_cdp_available, DEFAULT_CDP_URL, get_chrome_launch_command
    ok = check_chrome_cdp_available()
    return {
        "available": ok,
        "cdp_url": DEFAULT_CDP_URL,
        "launch_cmd": get_chrome_launch_command(),
    }


def push_toutiao_draft_action(title: str, markdown: str = "", run_dir: str = "", schedule_time: str = "") -> dict:
    """已停用：今日头条仅生成本地文案文件，不再自动推送。"""
    raise RuntimeError("今日头条自动推送已停用，请下载生成的文案文件后人工发布。")
    try:
        from scripts.toutiao_publisher import (
            publish_to_toutiao_draft,
            check_chrome_cdp_available,
            DEFAULT_CDP_URL,
            get_chrome_launch_command,
        )
    except ImportError:
        from toutiao_publisher import (
            publish_to_toutiao_draft,
            check_chrome_cdp_available,
            DEFAULT_CDP_URL,
            get_chrome_launch_command,
        )

    title = (title or "").strip()
    if not title:
        raise ValueError("文章标题不能为空")

    if not check_chrome_cdp_available(DEFAULT_CDP_URL):
        cmd = get_chrome_launch_command()
        raise ConnectionError(
            f"未检测到运行中的 Chrome 调试端口 ({DEFAULT_CDP_URL})。\n"
            f"请先打开终端执行以下命令启动带端口的 Chrome 并登录头条：\n\n  {cmd}\n"
        )

    # 1. 查找是否已存在今日头条产物（.md 和 封面.jpg）
    status_data = get_matrix_status(run_dir) if run_dir else {}
    plat_info = status_data.get("toutiao", {})

    md_path = plat_info.get("md_path")
    cover_path = plat_info.get("cover_path")
    actual_title = plat_info.get("title") or title

    if not (md_path and Path(md_path).is_file() and cover_path and Path(cover_path).is_file()):
        # 优先在 outputs/今日头条/ 查找已生成的同名文案与封面
        safe = sanitize_filename(title)
        matched_md = None
        matched_cover = None
        for p in JRTT_DIR.glob("*-今日头条文案.md"):
            if safe in p.stem or title in p.stem or p.stem in title:
                matched_md = p
                break
        for p in JRTT_DIR.glob("*-今日头条封面.jpg"):
            if safe in p.stem or title in p.stem or p.stem in title:
                matched_cover = p
                break

        if matched_md and matched_cover:
            md_path = str(matched_md)
            cover_path = str(matched_cover)
            lines = matched_md.read_text(encoding="utf-8").splitlines()
            for line in lines:
                if line.startswith("# "):
                    actual_title = line[2:].strip()
                    break
        else:
            # 如果未找到头条专属产物，自动调用技能生成
            toutiao_gen = build_toutiao_copy_and_save(title, markdown, run_dir)
            actual_title = toutiao_gen.get("title") or title
            md_path = toutiao_gen.get("md_path")
            cover_path = toutiao_gen.get("cover_path")

    md_content = Path(md_path).read_text(encoding="utf-8") if md_path and Path(md_path).is_file() else (markdown or "")
    if not md_content:
        raise ValueError("未能获取今日头条文案正文内容")

    # 2. 执行自动化填报并定时/即时发布
    result = publish_to_toutiao_draft(
        title=actual_title,
        markdown_content=md_content,
        cover_image_path=cover_path,
        schedule_time=schedule_time or None,
        cdp_url=DEFAULT_CDP_URL,
    )

    # 3. 回填矩阵状态
    status_label = "已定时发布" if schedule_time else "已发布"
    if run_dir:
        update_matrix_status(run_dir, "toutiao", {
            "status": status_label,
            "schedule_time": schedule_time or "",
            "published_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "page_url": result.get("page_url", ""),
            "title": actual_title,
            "location": "上海",
            "statement": "个人观点，仅供参考",
        })

    return result


def push_xhs_draft_action(title: str, markdown: str = "", run_dir: str = "", action: str = "publish", schedule_time: str = "") -> dict:
    """已停用：小红书仅生成本地文案与图片文件，不再自动推送。"""
    raise RuntimeError("小红书自动推送已停用，请下载生成的文案文件后人工发布。")
    try:
        from scripts.xhs_publisher import (
            publish_to_xhs,
            check_chrome_cdp_available,
            DEFAULT_CDP_URL,
            get_chrome_launch_command,
        )
    except ImportError:
        from xhs_publisher import (
            publish_to_xhs,
            check_chrome_cdp_available,
            DEFAULT_CDP_URL,
            get_chrome_launch_command,
        )

    title = (title or "").strip()
    if not title:
        raise ValueError("文章标题不能为空")

    if not check_chrome_cdp_available(DEFAULT_CDP_URL):
        cmd = get_chrome_launch_command()
        raise ConnectionError(
            f"未检测到运行中的 Chrome 调试端口 ({DEFAULT_CDP_URL})。\n"
            f"请先打开终端执行以下命令启动带端口的 Chrome 并登录小红书：\n\n  {cmd}\n"
        )

    # 1. 查找是否已存在小红书产物（.md 笔记和 3:4 封面.jpg）
    status_data = get_matrix_status(run_dir) if run_dir else {}
    plat_info = status_data.get("xhs", {})

    md_path = plat_info.get("md_path")
    cover_path = plat_info.get("cover_path")
    actual_title = plat_info.get("title") or title

    if not (md_path and Path(md_path).is_file() and cover_path and Path(cover_path).is_file()):
        safe = sanitize_filename(title)
        matched_md = None
        matched_cover = None
        for p in XHS_DIR.glob("*-小红书笔记.md"):
            if safe in p.stem or title in p.stem or p.stem in title:
                matched_md = p
                break

        images_dir = XHS_DIR / "images"
        if images_dir.exists():
            for p in images_dir.glob("*_cover.jpg"):
                if safe in p.stem or title in p.stem or p.stem in title:
                    matched_cover = p
                    break

        if matched_md:
            md_path = str(matched_md)
            lines = matched_md.read_text(encoding="utf-8").splitlines()
            for line in lines:
                if line.startswith("# "):
                    actual_title = line[2:].strip()
                    break

            if not matched_cover:
                # 尝试从 Markdown 内嵌的 Base64 提取封面图
                md_text = matched_md.read_text(encoding="utf-8")
                b64_match = re.search(r'!\[.*?\]\(data:image/[^;]+;base64,([A-Za-z0-9+/=]+)\)', md_text)
                if b64_match:
                    import base64
                    images_dir.mkdir(parents=True, exist_ok=True)
                    cover_file = images_dir / f"{matched_md.stem}_cover.jpg"
                    cover_file.write_bytes(base64.b64decode(b64_match.group(1)))
                    matched_cover = cover_file

            if matched_cover:
                cover_path = str(matched_cover)

        if not (md_path and Path(md_path).is_file() and cover_path and Path(cover_path).is_file()):
            xhs_gen = build_xhs_copy_and_save(title, markdown, run_dir)
            actual_title = xhs_gen.get("title") or title
            md_path = xhs_gen.get("md_path")
            cover_path = xhs_gen.get("cover_path")

    md_content = Path(md_path).read_text(encoding="utf-8") if md_path and Path(md_path).is_file() else (markdown or "")
    if not md_content:
        raise ValueError("未能获取小红书文案正文内容")

    # 2. 执行自动化填报并定时/即时发布
    upload_images = plat_info.get("image_files") or ([cover_path] if cover_path else [])
    result = publish_to_xhs(
        title=actual_title,
        markdown_content=md_content,
        cover_image_path=cover_path,
        image_paths=upload_images,
        schedule_time=schedule_time or None,
        action=action,
        cdp_url=DEFAULT_CDP_URL,
    )

    # 3. 回填矩阵状态
    status_label = "已定时发布" if schedule_time else ("已发布" if action == "publish" else "已存草稿")
    if run_dir:
        update_matrix_status(run_dir, "xhs", {
            "status": status_label,
            "schedule_time": schedule_time or "",
            "published_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "page_url": result.get("page_url", ""),
            "title": actual_title,
            "location": result.get("location", "上海"),
            "style_name": result.get("style_name", ""),
            "topics": result.get("topics", []),
            "action": action,
        })

    return result
