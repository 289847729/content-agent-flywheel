"""微信公众号草稿箱客户端 —— 公众号内容飞轮的推送环节。

独立实现官方接口流程（正文图片上传、封面永久素材、draft/add、draft/get 回读校验），
供 Qoder 编排流程时直接调用。依赖 lxml 解析排版 HTML。

用法示例：
    python wechat_api.py push --html /path/to/排版稿.html --title "文章标题" \
        --author 小崽子AI --outdir /path/to/outputs/runs/2026-09-14-001
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import mimetypes
import re
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen

from lxml import html as lxml_html

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config  # noqa: E402

API_BASE = "https://api.weixin.qq.com"
MAX_CONTENT_CHARACTERS = 20_000
MAX_CONTENT_BYTES = 1024 * 1024
MAX_IMAGE_BYTES = 10 * 1024 * 1024
DATA_IMAGE_RE = re.compile(
    r"^data:(image/(?:png|jpe?g|gif|webp));base64,([A-Za-z0-9+/=\r\n]+)$",
    re.IGNORECASE,
)
MIME_EXTENSIONS = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
}


class WeChatError(RuntimeError):
    """微信 API 业务错误，可安全展示给用户。"""


def _http_request(request: Request, timeout: int = 60) -> dict:
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        raise WeChatError(f"微信请求失败：{exc}") from exc


def get_access_token(cache_path: Path | None = None) -> tuple[str, str]:
    """获取 access_token（2 小时有效）；提供缓存路径时复用未过期的缓存。"""
    app_id = config.require("WECHAT_APP_ID")
    secret = config.require("WECHAT_APP_SECRET")
    if cache_path and cache_path.is_file():
        cached = json.loads(cache_path.read_text(encoding="utf-8"))
        if cached.get("app_id") == app_id and float(cached.get("expires_at", 0)) > time.time():
            return cached["access_token"], app_id
    request = Request(
        f"{API_BASE}/cgi-bin/token?{urlencode({'grant_type': 'client_credential', 'appid': app_id, 'secret': secret})}"
    )
    response = _http_request(request, timeout=30)
    token = response.get("access_token")
    if not token:
        errcode = response.get("errcode")
        if errcode == 40164:
            errmsg = response.get("errmsg", "")
            raise WeChatError(f"微信 API 错误 40164（IP 白名单限制）：{errmsg}。请确认已在公众号后台完成扫码保存（微信白名单通常有 1~3 分钟同步生效延迟）。")
        raise WeChatError(f"获取 access_token 失败：{response}")
    if cache_path:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps({
            "app_id": app_id,
            "access_token": token,
            "expires_at": time.time() + int(response.get("expires_in", 7200)) - 300,
        }), encoding="utf-8")
    return token, app_id


def upload_article_image(image_path: Path, token: str) -> str:
    """上传图文正文图片，返回微信托管 URL。"""
    mime = mimetypes.guess_type(image_path.name)[0] or "application/octet-stream"
    if not mime.startswith("image/"):
        raise WeChatError(f"正文图片格式不受支持：{mime}")
    boundary = f"----flywheel-{uuid.uuid4().hex}"
    header = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="media"; filename="{image_path.name}"\r\n'
        f"Content-Type: {mime}\r\n\r\n"
    ).encode("utf-8")
    body = (
        header
        + image_path.read_bytes()
        + f"\r\n--{boundary}--\r\n".encode("utf-8")
    )
    request = Request(
        f"{API_BASE}/cgi-bin/media/uploadimg?{urlencode({'access_token': token})}",
        data=body,
        method="POST",
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Accept": "application/json",
        },
    )
    response = _http_request(request, timeout=60)
    url = str(response.get("url") or "")
    parsed = urlsplit(url)
    if (
        parsed.scheme not in {"http", "https"}
        or (parsed.hostname or "").lower() != "mmbiz.qpic.cn"
        or not parsed.path
    ):
        raise WeChatError(f"正文图片上传后未返回微信官方图片 URL：{response}")
    return urlunsplit(("https", parsed.netloc, parsed.path, parsed.query, parsed.fragment))


def upload_permanent_image(image_path: Path, token: str) -> str:
    """上传封面为永久素材，返回 media_id。"""
    mime = mimetypes.guess_type(image_path.name)[0] or "application/octet-stream"
    boundary = f"----flywheel-{uuid.uuid4().hex}"
    header = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="media"; filename="{image_path.name}"\r\n'
        f"Content-Type: {mime}\r\n\r\n"
    ).encode("utf-8")
    body = (
        header
        + image_path.read_bytes()
        + f"\r\n--{boundary}--\r\n".encode("utf-8")
    )
    request = Request(
        f"{API_BASE}/cgi-bin/material/add_material?{urlencode({'access_token': token, 'type': 'image'})}",
        data=body,
        method="POST",
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Accept": "application/json",
        },
    )
    response = _http_request(request, timeout=60)
    media_id = str(response.get("media_id") or "")
    if not media_id:
        raise WeChatError(f"封面上传未返回 media_id：{response}")
    return media_id


def post_json(path: str, token: str, payload: dict, timeout: int = 60) -> dict:
    request = Request(
        f"{API_BASE}{path}?{urlencode({'access_token': token})}",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    return _http_request(request, timeout=timeout)


# ---- 排版稿处理 ----

def _canonical_visible_text(root) -> str:
    text = "".join(root.itertext())
    return re.sub(r"\s+", " ", text).strip()


def _text_fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _image_paths_in_document_order(root) -> list[str]:
    tree = root.getroottree()
    return [tree.getpath(image) for image in root.xpath(".//img")]


def extract_copy_root(source: str):
    """提取公众号排版稿的正文根节点（兼容 data-copy-root / gzh-content / article）。"""
    try:
        document = lxml_html.fromstring(source)
    except Exception as exc:
        raise WeChatError(f"排版 HTML 无法解析：{exc}") from exc

    def pick(candidates, label: str):
        if len(candidates) == 1:
            return candidates[0]
        if len(candidates) > 1:
            raise WeChatError(f"排版稿包含多个 {label} 正文节点，无法安全推送")
        return None

    root = pick(document.xpath('//*[@data-copy-root]'), "data-copy-root")
    if root is None:
        root = pick(document.xpath('//*[@id="gzh-content"]'), "gzh-content")
    if root is None:
        root = pick(document.xpath('//*[@id="article"]'), "article")
    if root is None:
        raise WeChatError("排版稿缺少可识别的正文节点（data-copy-root、gzh-content 或 article）")
    return root


def _decode_data_image(src: str, run_dir: Path, index: int) -> tuple[Path, str]:
    match = DATA_IMAGE_RE.fullmatch(src.strip())
    if not match:
        raise WeChatError(f"第 {index} 张正文图片不是受支持的内嵌图片")
    mime = match.group(1).lower().replace("image/jpg", "image/jpeg")
    try:
        data = base64.b64decode(match.group(2), validate=True)
    except ValueError as exc:
        raise WeChatError(f"第 {index} 张正文图片数据损坏") from exc
    if not data:
        raise WeChatError(f"第 {index} 张正文图片为空")
    if len(data) > MAX_IMAGE_BYTES:
        raise WeChatError(f"第 {index} 张正文图片超过 10 MiB")
    digest = hashlib.sha256(data).hexdigest()
    extension = MIME_EXTENSIONS.get(mime)
    if not extension:
        raise WeChatError(f"第 {index} 张正文图片格式不受支持：{mime}")
    image_path = run_dir / f"image-{index:02d}-{digest[:12]}{extension}"
    image_path.write_bytes(data)
    return image_path, digest


def _wechat_image_urls_equivalent(expected: str, actual: str) -> bool:
    """接受微信 draft/get 对官方图片执行的 src/data-src 与 /0→/640 规范化。"""
    expected_url = urlsplit(str(expected or "").strip())
    actual_url = urlsplit(str(actual or "").strip())
    if (
        expected_url.scheme not in {"http", "https"}
        or actual_url.scheme not in {"http", "https"}
        or (expected_url.hostname or "").lower() != "mmbiz.qpic.cn"
        or (actual_url.hostname or "").lower() != "mmbiz.qpic.cn"
        or expected_url.query != actual_url.query
        or expected_url.fragment != actual_url.fragment
    ):
        return False
    if expected_url.path == actual_url.path:
        return True
    return expected_url.path.endswith("/0") and actual_url.path == expected_url.path[:-2] + "/640"


def prepare_content(root, run_dir: Path, token: str) -> dict:
    """上传全部正文图片并替换 Data URL，校验替换过程不改变可见内容。"""
    before_text = _canonical_visible_text(root)
    images = root.xpath(".//img")
    if not images:
        raise WeChatError("公众号正文没有可作为封面的图片")
    before_image_order = _image_paths_in_document_order(root)

    uploaded_by_digest: dict[str, str] = {}
    image_paths: list[Path] = []
    image_hashes: list[str] = []
    for index, image in enumerate(images, start=1):
        src = str(image.get("src") or "")
        image_path, digest = _decode_data_image(src, run_dir, index)
        image_paths.append(image_path)
        image_hashes.append(digest)
        if digest not in uploaded_by_digest:
            uploaded_by_digest[digest] = upload_article_image(image_path, token)
        image.set("src", uploaded_by_digest[digest])

    after_text = _canonical_visible_text(root)
    if before_text != after_text:
        raise WeChatError("正文图片 URL 替换改变了文章可见文字")
    if len(root.xpath(".//img")) != len(images):
        raise WeChatError("正文图片 URL 替换改变了图片数量")
    if _image_paths_in_document_order(root) != before_image_order:
        raise WeChatError("正文图片 URL 替换改变了图片顺序")

    content = lxml_html.tostring(root, encoding="unicode", method="html")
    if len(content) > MAX_CONTENT_CHARACTERS:
        raise WeChatError(f"公众号正文超过 {MAX_CONTENT_CHARACTERS} 字符限制，请精简排版后重试")
    if len(content.encode("utf-8")) >= MAX_CONTENT_BYTES:
        raise WeChatError("公众号正文超过 1 MiB 限制")
    return {
        "content": content,
        "visible_text": before_text,
        "body_fingerprint": _text_fingerprint(before_text),
        "image_count": len(images),
        "image_urls": [str(image.get("src") or "") for image in root.xpath(".//img")],
        "cover_path": image_paths[0],
        "cover_sha256": image_hashes[0],
    }


def verify_draft_round_trip(response: dict, *, title: str, expected_text: str,
                            expected_image_count: int, expected_image_urls: list[str]) -> dict:
    """用 draft/get 回读校验标题、文字、图片数量与顺序。"""
    items = response.get("news_item") or response.get("articles") or []
    if not isinstance(items, list) or len(items) != 1:
        raise WeChatError("微信草稿回读结果不是单篇图文")
    article = items[0]
    actual_title = str(article.get("title") or "").strip()
    if actual_title != title:
        raise WeChatError("微信草稿回读标题与本地标题不一致")
    try:
        content_root = lxml_html.fromstring(str(article.get("content") or "<section></section>"))
    except Exception as exc:
        raise WeChatError("微信草稿回读正文无法解析") from exc
    actual_text = _canonical_visible_text(content_root)
    actual_images = content_root.xpath(".//img")
    actual_image_urls = [
        str(image.get("src") or image.get("data-src") or "") for image in actual_images
    ]
    if actual_text != expected_text:
        raise WeChatError("微信草稿回读正文与本地正文不一致")
    if len(actual_images) != expected_image_count:
        raise WeChatError("微信草稿回读图片数量与本地正文不一致")
    if not all(
        _wechat_image_urls_equivalent(expected, actual)
        for expected, actual in zip(expected_image_urls, actual_image_urls)
    ):
        raise WeChatError("微信草稿回读图片或图片顺序与本地正文不一致")
    return {
        "status": "pass",
        "title_equal": True,
        "body_equal": True,
        "image_count_equal": True,
        "image_order_equal": True,
        "body_fingerprint": _text_fingerprint(actual_text),
        "image_count": len(actual_images),
    }


def push_draft(html_path: Path, title: str, author: str, outdir: Path, update_media_id: str | None = None) -> dict:
    """推送排版稿到公众号草稿箱，返回运行记录（含草稿 media_id 与校验结果）。

    update_media_id 非空时走 draft/update 更新已有草稿，否则 draft/add 新建。
    """
    outdir.mkdir(parents=True, exist_ok=True)
    token_cache = outdir / "api-token.json"

    source = html_path.read_text(encoding="utf-8")
    root = extract_copy_root(source)
    token, app_id = get_access_token(token_cache)
    prepared = prepare_content(root, outdir, token)
    (outdir / "prepared-body.html").write_text(prepared["content"], encoding="utf-8")

    cover_media_id = upload_permanent_image(prepared["cover_path"], token)
    article = {
        "title": title,
        "author": author,
        "digest": "",
        "content": prepared["content"],
        "thumb_media_id": cover_media_id,
        "need_open_comment": 0,
        "only_fans_can_comment": 0,
    }
    if update_media_id:
        updated = post_json(
            "/cgi-bin/draft/update",
            token,
            {"media_id": update_media_id, "index": 0, "articles": article},
            timeout=60,
        )
        if str(updated.get("errcode", 0)) != "0":
            raise WeChatError(f"微信草稿更新失败：{updated}")
        media_id = update_media_id
    else:
        created = post_json("/cgi-bin/draft/add", token, {"articles": [article]}, timeout=60)
        if str(created.get("errcode", 0)) != "0":
            errcode = created.get("errcode")
            errmsg = created.get("errmsg") or "未知错误"
            raise WeChatError(f"微信草稿创建失败（errcode={errcode}）：{errmsg}")
        media_id = str(created.get("media_id") or "")
        if not media_id:
            raise WeChatError(f"微信草稿创建响应没有 media_id：{created}")

    fetched = post_json("/cgi-bin/draft/get", token, {"media_id": media_id}, timeout=60)
    verification = verify_draft_round_trip(
        fetched,
        title=title,
        expected_text=prepared["visible_text"],
        expected_image_count=prepared["image_count"],
        expected_image_urls=prepared["image_urls"],
    )

    record = {
        "status": "success",
        "action": "update" if update_media_id else "add",
        "title": title,
        "author": author,
        "draft_media_id": media_id,
        "app_id_hint": app_id[:6] + "***" + app_id[-4:] if len(app_id) > 12 else "***",
        "body_fingerprint": prepared["body_fingerprint"],
        "cover_sha256": prepared["cover_sha256"],
        "image_count": prepared["image_count"],
        "verification": verification,
        "pushed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    (outdir / "push-result.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    try:
        token_cache.unlink(missing_ok=True)
    except OSError:
        pass
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description="微信公众号草稿箱推送客户端")
    parser.add_argument("--html", required=True, help="排版稿 HTML 路径")
    parser.add_argument("--title", required=True, help="文章标题")
    parser.add_argument("--author", default=config.wechat_author(), help="作者名")
    parser.add_argument("--outdir", required=True, help="运行产物输出目录")
    parser.add_argument("--update-media-id", default=None, help="更新已有草稿而非新建（传入草稿 media_id）")
    args = parser.parse_args()
    try:
        record = push_draft(
            Path(args.html).expanduser(), args.title, args.author, Path(args.outdir).expanduser(),
            args.update_media_id,
        )
        print(json.dumps(record, ensure_ascii=False, indent=2))
        return 0
    except WeChatError as exc:
        print(f"❌ {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
