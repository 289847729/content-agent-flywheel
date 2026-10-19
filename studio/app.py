"""公众号内容飞轮 · 可视化操作台 —— FastAPI 本地服务。

启动：python3 studio/app.py（或 uvicorn studio.app:app --host 127.0.0.1 --port 8765）
打开 http://127.0.0.1:8765 即可在浏览器里走完「自动筛选 → 手动挑选 →
文案生成 → 文案预览 → 发送公众号」五阶段流程。

凭据只留在后端 config.env，浏览器不接触任何密钥；只绑定 127.0.0.1。
"""
from __future__ import annotations

import sys
import threading
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT_DIR / "scripts"))

import config  # noqa: E402
import feishu_btable  # noqa: E402
import wechat_api  # noqa: E402

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from server import bmmd_typeset, llm, pipeline  # noqa: E402

STATIC_DIR = Path(__file__).resolve().parent / "static"
HOST = "127.0.0.1"
PORT = 8765

class NoCacheStaticFiles(StaticFiles):
    """本地开发工具：静态资源禁用缓存，刷新即拿到最新版本。"""

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response


app = FastAPI(title="公众号内容飞轮操作台")
app.mount("/static", NoCacheStaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})


def ok(**data) -> dict:
    return {"ok": True, **data}


def fail(exc: Exception) -> JSONResponse:
    return JSONResponse({"ok": False, "error": str(exc)})


def topic_record(record: dict) -> dict:
    fields = record.get("fields", {})
    return {
        "record_id": record.get("record_id", ""),
        "选题标题": fields.get("选题标题", ""),
        "关键词": fields.get("关键词", ""),
        "内容方向": fields.get("内容方向", ""),
        "流量预估": fields.get("流量预估", ""),
        "状态": fields.get("状态", ""),
        "备注": fields.get("备注", ""),
        "文章标题": fields.get("文章标题", ""),
        "稿件路径": fields.get("稿件路径", ""),
        "入库日期": fields.get("入库日期", ""),
    }


# ---- 健康检查 ----

@app.get("/api/health")
def health():
    data = {
        "feishu_ready": all(
            config.get(key) for key in ("FEISHU_APP_ID", "FEISHU_APP_SECRET",
                                        "FEISHU_APP_TOKEN", "FEISHU_TABLE_ID")
        ),
        "wechat_ready": all(config.get(key) for key in ("WECHAT_APP_ID", "WECHAT_APP_SECRET")),
        "llm_ready": bool(config.get("LLM_API_KEY")),
        "author": config.wechat_author(),
        "table_check": None,
    }
    if data["feishu_ready"]:
        try:
            data["table_check"] = feishu_btable.FeishuBitable().verify_fields()
        except Exception as exc:
            data["table_check"] = {"error": str(exc)}
    return ok(**data)


# ---- 阶段 1/2：选题筛选与挑选 ----

@app.get("/api/topics")
def list_topics(status: str = "", keyword: str = ""):
    try:
        client = feishu_btable.FeishuBitable()
        records = client.list_records(
            status=status or None, keyword=keyword or None, page_size=500
        )
        items = [topic_record(record) for record in records]
        # 跳过多维表格里的空白行（无字段值）
        items = [item for item in items if item["选题标题"] or item["备注"]]
        return ok(items=items)
    except Exception as exc:
        return fail(exc)


class SelectRequest(BaseModel):
    record_ids: list[str]


class TopicsAddRequest(BaseModel):
    topics: list[dict]


@app.post("/api/topics/add")
def add_topics(request: TopicsAddRequest):
    """批量入库：热点在对话里搜好，粘贴 JSON 数组写入飞书。
    自动补状态「待定」与入库日期；只接受选题表已有字段。"""
    if not request.topics:
        return fail(ValueError("没有要入库的选题"))
    try:
        allowed = set(feishu_btable.TOPIC_FIELDS.keys())
        topics = [
            {key: value for key, value in topic.items() if key in allowed}
            for topic in request.topics
            if isinstance(topic, dict)
        ]
        topics = [topic for topic in topics if topic.get("选题标题") or topic.get("备注")]
        if not topics:
            return fail(ValueError("没有有效的选题记录（至少要有「选题标题」或「备注」）"))
        client = feishu_btable.FeishuBitable()
        created = client.add_records(topics, status="待定")
        return ok(created=len(created))
    except Exception as exc:
        return fail(exc)


class ScanHotRequest(BaseModel):
    hours: float = 24.0
    limit: int = 5
    add_feishu: bool = True


@app.post("/api/topics/scan-hot")
def scan_hot_topics(request: ScanHotRequest):
    """一键扫描过去 24 小时海外科技/AI 一手热点（Hacker News + HF Papers），提炼选题并写入飞书。"""
    try:
        from fetch_daily_hot import fetch_daily_hot_topics
        topics = fetch_daily_hot_topics(hours=request.hours, limit=request.limit)
        if not topics:
            return fail(ValueError("未抓取到有效的海外一手热点，请检查网络连接"))
        created_count = 0
        if request.add_feishu:
            client = feishu_btable.FeishuBitable()
            created = client.add_records(topics, status="待定")
            created_count = len(created)
        return ok(topics=topics, created=created_count)
    except Exception as exc:
        return fail(exc)


class ScanSkillsRequest(BaseModel):
    limit: int = 5
    add_feishu: bool = True


@app.post("/api/topics/scan-skills")
def scan_skills_topics(request: ScanSkillsRequest):
    """一键扫描高热度实用 AI Skill / MCP 神器，提炼爆款选题并写入飞书。"""
    try:
        from fetch_hot_skills import fetch_hot_skills_topics
        topics = fetch_hot_skills_topics(limit=request.limit)
        if not topics:
            return fail(ValueError("未抓取到有效的开源实用 Skill，请检查网络连接"))
        created_count = 0
        if request.add_feishu:
            client = feishu_btable.FeishuBitable()
            created = client.add_records(topics, status="待定")
            created_count = len(created)
        return ok(topics=topics, created=created_count)
    except Exception as exc:
        return fail(exc)


class ScanAihotRequest(BaseModel):
    limit: int = 3
    add_feishu: bool = True


@app.post("/api/topics/scan-aihot")
def scan_aihot_topics(request: ScanAihotRequest):
    """一键抓取 aihot.news 热门 AI 榜单前 N 条文章（默认前 3 条），提炼爆款选题并写入飞书。"""
    try:
        from fetch_aihot import fetch_aihot_top_topics
        topics = fetch_aihot_top_topics(limit=request.limit)
        if not topics:
            return fail(ValueError("未抓取到 aihot.news 榜单数据，请检查网络连接"))
        created_count = 0
        if request.add_feishu:
            client = feishu_btable.FeishuBitable()
            created = client.add_records(topics, status="待定")
            created_count = len(created)
        return ok(topics=topics, created=created_count)
    except Exception as exc:
        return fail(exc)


@app.post("/api/topics/select")
def select_topics(request: SelectRequest):
    if not request.record_ids:
        return fail(ValueError("没有勾选任何选题"))
    try:
        client = feishu_btable.FeishuBitable()
        for record_id in request.record_ids:
            client.update_record(record_id, {"状态": "选中"})
        return ok(updated=len(request.record_ids))
    except Exception as exc:
        return fail(exc)


# ---- 阶段 3：文案生成 ----

@app.get("/api/style-refs")
def get_style_refs():
    try:
        return ok(items=llm.style_refs())
    except Exception as exc:
        return fail(exc)


@app.get("/api/templates")
def get_templates():
    try:
        return ok(items=llm.get_templates())
    except Exception as exc:
        return fail(exc)


class GenerateRequest(BaseModel):
    record_id: str
    template: str = "auto"


@app.post("/api/generate")
def generate(request: GenerateRequest):
    try:
        client = feishu_btable.FeishuBitable()
        record = client.get_record(request.record_id)
        fields = record.get("fields") or {}
        chosen_tpl = llm.resolve_template(fields, request.template or "auto")
        markdown = llm.generate_draft(fields, template=chosen_tpl)
        return ok(markdown=markdown, fields=topic_record(record), template=chosen_tpl,
                  template_name=llm.TEMPLATES[chosen_tpl])
    except Exception as exc:
        return fail(exc)


class CompressRequest(BaseModel):
    title: str = ""
    markdown: str


@app.post("/api/compress-draft")
def compress_draft_api(request: CompressRequest):
    """将超长初稿智能精炼至微信安全容量（1100~1300字）。"""
    try:
        if not request.markdown.strip():
            raise ValueError("初稿内容为空，无法精简")
        title = request.title.strip() or "未命名文章"
        compressed = llm.compress_draft(title, request.markdown)
        return ok(markdown=compressed)
    except Exception as exc:
        return fail(exc)





class SaveDraftRequest(BaseModel):
    record_id: str
    title: str
    markdown: str


@app.post("/api/save-draft")
def save_draft(request: SaveDraftRequest):
    try:
        return ok(**pipeline.save_draft(request.record_id, request.title, request.markdown))
    except Exception as exc:
        return fail(exc)


# ---- 阶段 4：文案预览 ----

class PreviewRequest(BaseModel):
    markdown: str
    title: str
    author: str = ""
    theme: str = "moyu-green"
    hook: str = ""
    subtitle: str = ""
    engine: str = "builtin"       # builtin 内置三主题 | bmmd 引擎
    style: str = "kami"           # bmmd 排版样式 ID（16 款）
    code_theme: str = ""          # bmmd 代码高亮主题（空 = 随样式）


@app.get("/api/bmmd-styles")
def bmmd_styles():
    """bmmd 引擎可用的排版样式与代码高亮主题（供前端下拉框）。"""
    return ok(styles=bmmd_typeset.STYLES, code_themes=bmmd_typeset.CODE_THEMES,
              default=bmmd_typeset.DEFAULT_STYLE)


@app.post("/api/preview")
def preview(request: PreviewRequest):
    try:
        if not request.markdown.strip():
            raise ValueError("初稿内容为空，先回到「文案生成」生成初稿")
        cover = pipeline.article_cover.ensure_article_cover(request.title, request.markdown)
        html, chars = pipeline.build_typeset_html(
            request.markdown, request.title, request.author, request.theme,
            request.hook, request.subtitle, request.engine, request.style,
            request.code_theme, cover_data_url=cover["data_url"],
        )
        return ok(html=html, chars=chars, limit=wechat_api.MAX_CONTENT_CHARACTERS,
                  cover={key: cover[key] for key in ("key", "core_theme", "visual_subject", "style", "cached", "path")})
    except Exception as exc:
        return fail(exc)


# ---- 阶段 5：发送公众号 ----

class PushRequest(BaseModel):
    record_id: str
    title: str
    author: str = ""
    theme: str = "moyu-green"
    markdown: str = ""
    imported_html: str = ""
    hook: str = ""
    subtitle: str = ""
    engine: str = "builtin"
    style: str = "kami"
    code_theme: str = ""


@app.post("/api/push")
def push(request: PushRequest):
    try:
        record = pipeline.push_draft_article(
            request.record_id, request.title, request.author, request.theme,
            request.markdown, request.imported_html, request.hook, request.subtitle,
            request.engine, request.style, request.code_theme,
        )
        return ok(record=record)
    except Exception as exc:
        return fail(exc)


# ---- 批量发布 ----

class BatchPublishRequest(BaseModel):
    record_ids: list[str]
    template: str = "auto"
    theme: str = "moyu-green"
    engine: str = "builtin"
    author: str = ""


_ACTIVE_BATCH_JOBS: set[str] = set()
_ACTIVE_BATCH_LOCK = threading.Lock()


def _start_batch_job(job_id: str, request: BatchPublishRequest) -> None:
    """后台线程入口；编排函数本身保持同步，方便单元测试直接调用。"""
    with _ACTIVE_BATCH_LOCK:
        _ACTIVE_BATCH_JOBS.add(job_id)
    try:
        pipeline.run_batch_publish(
            request.record_ids, template=request.template or "auto", theme=request.theme or "moyu-green",
            engine=request.engine or "builtin", author=request.author.strip() or config.wechat_author(), job_id=job_id,
        )
    except Exception as exc:
        pipeline.mark_batch_failed(job_id, exc)
    finally:
        with _ACTIVE_BATCH_LOCK:
            _ACTIVE_BATCH_JOBS.discard(job_id)


@app.post("/api/batch-publish")
def batch_publish(request: BatchPublishRequest):
    if not request.record_ids:
        return fail(ValueError("record_ids 不能为空"))
    try:
        author = request.author.strip() or config.wechat_author()
        job_id = pipeline.prepare_batch_publish(
            request.record_ids, template=request.template or "auto", theme=request.theme or "moyu-green",
            engine=request.engine or "builtin", author=author,
        )
        thread = threading.Thread(
            target=_start_batch_job, args=(job_id, request), name=f"{job_id}-worker", daemon=True,
        )
        thread.start()
        return ok(job_id=job_id, status="running", status_path=str(pipeline.RUNS / job_id / "batch-status.json"))
    except Exception as exc:
        return fail(exc)


@app.get("/api/batch-publish/{job_id}")
def batch_publish_status(job_id: str):
    try:
        status_data = pipeline.get_batch_status(job_id)
        with _ACTIVE_BATCH_LOCK:
            is_active = job_id in _ACTIVE_BATCH_JOBS
        if status_data.get("status") == "running" and not is_active:
            # 自愈机制：如果标记为 running 但内存中无活跃 worker 线程，自动修正为真实状态，解除死锁
            items = status_data.get("items", [])
            item_statuses = [it.get("status") for it in items]
            if all(s == "success" for s in item_statuses):
                new_status = "success"
            elif all(s in {"failed", "blocked"} for s in item_statuses):
                new_status = "failed"
            else:
                new_status = "partial"
            def update_status(cur):
                cur["status"] = new_status
            status_data = pipeline._batch_update(pipeline.RUNS / job_id / "batch-status.json", update_status)
        return ok(**status_data)
    except Exception as exc:
        return fail(exc)


@app.post("/api/batch-publish/{job_id}/retry")
def batch_publish_retry(job_id: str):
    try:
        current = pipeline.get_batch_status(job_id)
        with _ACTIVE_BATCH_LOCK:
            is_active = job_id in _ACTIVE_BATCH_JOBS
        if is_active:
            raise ValueError("批量作业仍在运行中，请等待完成后重试")
        def retry_worker():
            with _ACTIVE_BATCH_LOCK:
                _ACTIVE_BATCH_JOBS.add(job_id)
            try:
                pipeline.retry_batch_publish(job_id)
            except Exception as exc:
                pipeline.mark_batch_failed(job_id, exc)
            finally:
                with _ACTIVE_BATCH_LOCK:
                    _ACTIVE_BATCH_JOBS.discard(job_id)
        thread = threading.Thread(target=retry_worker, name=f"{job_id}-retry", daemon=True)
        thread.start()
        return ok(job_id=job_id, status="running")
    except Exception as exc:
        return fail(exc)


@app.get("/api/runs")
def runs():
    try:
        return ok(items=pipeline.list_runs())
    except Exception as exc:
        return fail(exc)


# ---- 公众号文章离线导出包 ----

class PublishPackageRequest(BaseModel):
    record_id: str = ""
    title: str
    markdown: str
    author: str = ""
    theme: str = "moyu-green"
    hook: str = ""
    subtitle: str = ""
    engine: str = "builtin"
    style: str = "kami"
    code_theme: str = ""


@app.post("/api/publish-package")
def publish_package(request: PublishPackageRequest):
    """把当前母稿组装成公众号离线导出包（.md/.docx/公众号HTML/清单）。"""
    try:
        package = pipeline.build_publish_package(
            request.title, request.markdown, request.author, request.theme,
            request.hook, request.subtitle, request.engine, request.style,
            request.code_theme,
        )
        return ok(package=package)
    except Exception as exc:
        return fail(exc)


@app.get("/api/run-file")
def run_file(path: str):
    """安全下载 outputs/ 目录内的产物文件（防路径穿越）。"""
    try:
        target = pipeline.run_file(path)
        return FileResponse(target, filename=target.name)
    except Exception as exc:
        return fail(exc)


class RevealFileRequest(BaseModel):
    path: str


@app.post("/api/reveal-file")
def reveal_file(request: RevealFileRequest):
    """在运行 Studio 的 macOS Finder 中定位并选中文案文件。"""
    try:
        return ok(**pipeline.reveal_file(request.path))
    except Exception as exc:
        return fail(exc)


# ---- 多平台矩阵衍生：小红书 & 今日头条 ----
from typing import Optional


class MultiPlatformRequest(BaseModel):
    title: str = ""
    markdown: str = ""
    record_id: Optional[str] = ""
    run_dir: Optional[str] = ""


@app.post("/api/generate-xhs")
def generate_xhs(request: MultiPlatformRequest):
    """根据母稿调用 tuiwen-xhs-gen 技能逻辑生成小红书爆款文案与置顶封面。"""
    try:
        data = pipeline.build_xhs_copy_and_save(request.title, request.markdown, request.run_dir or "")
        return ok(**data)
    except Exception as exc:
        return fail(exc)


@app.post("/api/generate-toutiao")
def generate_toutiao(request: MultiPlatformRequest):
    """根据母稿调用 tuiwen-jrtt-gen 技能逻辑生成今日头条长图文与 DOCX 排版文件。"""
    try:
        data = pipeline.build_toutiao_copy_and_save(request.title, request.markdown, request.run_dir or "")
        return ok(**data)
    except Exception as exc:
        return fail(exc)


def platform_push_disabled() -> JSONResponse:
    """旧页面/API 调用也必须在任何副作用前被拒绝。"""
    return JSONResponse(
        {"ok": False, "error": "小红书和今日头条自动推送已停用，仅支持生成并下载文案文件，请人工审核发布。"},
        status_code=410,
    )


@app.get("/api/chrome-status", deprecated=True)
def get_chrome_status():
    return platform_push_disabled()


@app.post("/api/push-toutiao", deprecated=True)
def push_toutiao():
    return platform_push_disabled()


@app.post("/api/push-xhs", deprecated=True)
def push_xhs():
    return platform_push_disabled()



class ImportLinkRequest(BaseModel):
    url: str


@app.post("/api/import-link")
def import_link(request: ImportLinkRequest):
    """根据传入的 X (Twitter) 推文或文章链接解析文案、下载全部原图并落地为标准 outputs/runs 记录。"""
    try:
        url = (request.url or "").strip()
        if not url:
            raise ValueError("请输入有效的推文或文章链接")
        data = pipeline.import_external_article(url)
        return ok(**data)
    except Exception as exc:
        return fail(exc)


class SendEmailRequest(BaseModel):
    to_addr: str = "mengting9909@gmail.com"
    title: str
    schedule_time: str
    run_dir: str = ""
    platform: str = "xhs"
    body_text: str = ""
    attachment_paths: list[str] = []


@app.post("/api/runs/send-email", deprecated=True)
def send_email_api():
    return platform_push_disabled()


def main() -> None:
    import uvicorn

    uvicorn.run(app, host=HOST, port=PORT)


if __name__ == "__main__":
    main()
