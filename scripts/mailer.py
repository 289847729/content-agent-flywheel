#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
mailer.py: 定时发布邮件发送模块
支持使用 Python 原生 smtplib 发送带附件的定时发布邮件。
正文第一行强制包含格式：定时：YYYY-MM-DD HH:00
"""

import os
import sys
import mimetypes
import smtplib
from pathlib import Path
from email import encoders
from email.mime.base import MIMEBase
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.header import Header
from email.utils import formataddr

# 加载配置
SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
import config


def get_default_smtp_server(email_addr: str) -> tuple[str, int]:
    """根据发件人邮箱后缀推断默认 SMTP 服务器与端口。"""
    domain = email_addr.split("@")[-1].lower() if "@" in email_addr else ""
    if "163.com" in domain:
        return "smtp.163.com", 465
    elif "126.com" in domain:
        return "smtp.126.com", 465
    elif "qq.com" in domain:
        return "smtp.qq.com", 465
    elif "gmail.com" in domain:
        return "smtp.gmail.com", 587
    elif "outlook.com" in domain or "hotmail.com" in domain:
        return "smtp.office365.com", 587
    elif "exmail.qq.com" in domain:
        return "smtp.exmail.qq.com", 465
    return "smtp.163.com", 465


def send_article_email(
    to_addr: str,
    title: str,
    schedule_time: str,
    body_text: str = "",
    attachment_paths: list[str] | None = None
) -> dict:
    """
    发送定时发布邮件。
    邮件格式：
    - 收件人：to_addr (如 mengting9909@gmail.com)
    - 标题：文章标题
    - 正文第一行：定时：2026-10-02 18:00
      下面是正文内容
    - 附件：各平台生成的成果文件（.docx, .md, .jpg）
    """
    smtp_user = config.get("SMTP_USER")
    smtp_pass = config.get("SMTP_PASSWORD")

    if not smtp_user or not smtp_pass:
        raise RuntimeError(
            "未配置发信邮箱凭据！请先在 config.env 中添加 SMTP_USER 与 SMTP_PASSWORD。\n"
            "（例如：SMTP_USER=xxx@163.com，SMTP_PASSWORD=邮箱授权码）"
        )

    inferred_server, inferred_port = get_default_smtp_server(smtp_user)
    smtp_server = config.get("SMTP_SERVER") or inferred_server
    smtp_port_raw = config.get("SMTP_PORT")
    smtp_port = int(smtp_port_raw) if smtp_port_raw else inferred_port
    from_name = config.get("WECHAT_AUTHOR") or "内容飞轮助手"

    # 1. 组装正文，确保第一行为固定格式：定时：YYYY-MM-DD HH:00
    cleaned_schedule = schedule_time.strip()
    if not cleaned_schedule.startswith("定时："):
        cleaned_schedule = f"定时：{cleaned_schedule}"

    full_body = f"{cleaned_schedule}\n\n{body_text.strip()}".strip()

    # 2. 构造 MIME 消息
    msg = MIMEMultipart()
    msg["From"] = formataddr((str(Header(from_name, "utf-8")), smtp_user))
    msg["To"] = to_addr
    msg["Subject"] = Header(title, "utf-8").encode()

    # 添加文本正文
    msg.attach(MIMEText(full_body, "plain", "utf-8"))

    # 3. 挂载附件
    attached_files = []
    if attachment_paths:
        for p_str in attachment_paths:
            p = Path(p_str)
            if not p.is_file():
                continue
            ctype, encoding = mimetypes.guess_type(str(p))
            if ctype is None or encoding is not None:
                ctype = "application/octet-stream"
            maintype, subtype = ctype.split("/", 1)
            try:
                with open(p, "rb") as fp:
                    part = MIMEBase(maintype, subtype)
                    part.set_payload(fp.read())
                encoders.encode_base64(part)
                # 解决中文文件名在邮件客户端中的乱码问题
                filename_header = Header(p.name, "utf-8").encode()
                part.add_header("Content-Disposition", "attachment", filename=p.name)
                part.add_header("Content-Disposition", f'attachment; filename*="utf-8\'\'{p.name}"')
                msg.attach(part)
                attached_files.append(p.name)
            except Exception as exc:
                print(f"[Warn] 挂载附件失败 {p}: {exc}", file=sys.stderr)

    # 4. 连接 SMTP 服务器并发送
    try:
        if smtp_port == 465:
            server = smtplib.SMTP_SSL(smtp_server, smtp_port, timeout=30)
        else:
            server = smtplib.SMTP(smtp_server, smtp_port, timeout=30)
            server.starttls()

        server.login(smtp_user, smtp_pass)
        server.sendmail(smtp_user, [to_addr], msg.as_string())
        server.quit()
    except smtplib.SMTPAuthenticationError as exc:
        raise RuntimeError(f"发信邮箱登录认证失败：请检查 SMTP_USER 与授权码是否正确 ({exc})") from exc
    except Exception as exc:
        raise RuntimeError(f"邮件发送失败：{exc}") from exc

    return {
        "status": "success",
        "to": to_addr,
        "title": title,
        "schedule": cleaned_schedule,
        "attached_files": attached_files,
    }


if __name__ == "__main__":
    if len(sys.argv) < 4:
        print("用法: python mailer.py <收件人> <标题> <定时时间(如: 2026-10-02 18:00)> [正文] [附件路径...]")
        sys.exit(1)
    to = sys.argv[1]
    sub = sys.argv[2]
    sched = sys.argv[3]
    body = sys.argv[4] if len(sys.argv) > 4 else ""
    attachments = sys.argv[5:] if len(sys.argv) > 5 else []
    res = send_article_email(to, sub, sched, body, attachments)
    print(res)
