"""把信息图内嵌到两篇推送版 HTML（锚点插入 + TARS 篇样式精简腾头寸）。

插入前自动备份 .bak；锚点必须唯一，脚本会断言。
"""
from __future__ import annotations

from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "outputs"


def fig_html(name: str, margin: str) -> str:
    b64 = (OUT / "assets" / "infographics" / f"{name}.b64").read_text(encoding="ascii").strip()
    return (
        f'<p style="margin:6px {margin} 24px;text-align:center">'
        f'<img src="data:image/png;base64,{b64}" width="1200" '
        f'style="max-width:100%;height:auto;display:block;margin:0 auto"></p>'
    )


JOBS = {
    "2026-09-15-GPT-6-Astra发布两周_排版_石墨极简_push.html": {
        "margin": "10px",
        "inserts": [
            ("所以如果你没有付费账号，现在还摸不到它。</p>", "astra-timeline"),
            ("别把厂商叙事当事实</span>。</p>", "astra-numbers"),
            ("离你的日常工作更近</p>", "astra-alt"),
        ],
    },
    "2026-09-15-Agent-TARS实测_排版_摸鱼绿_push.html": {
        "margin": "20px",
        "inserts": [
            ('动鼠标”。</strong></p>', "tars-workflow"),
            ("第一次跑之前专门拔了一个</span>。</p>", "tars-install"),
            ("更适合盯着它干活。</p></section>", "tars-osworld"),
        ],
        "trims": [
            (
                "border-radius:10px;padding:10px;margin-right:8px;box-shadow:0 2px 6px rgba(0,0,0,0.04)",
                "border-radius:10px;padding:10px;margin-right:8px",
            ),
            (
                "background:linear-gradient(120deg,#FDE68A 0%,rgba(255,255,255,0) 100%);"
                "padding:0 4px;border-radius:2px;font-weight:600;color:#111827",
                "background:#FDE68A;padding:0 4px;border-radius:2px;font-weight:600;color:#111827",
            ),
            (
                'style="margin:0 20px 14px;font-size:14px;line-height:1.9;text-align:justify"',
                'style="margin:0 20px 14px;font-size:14px;text-align:justify"',
            ),
            ("font-weight:600;color:#9CA3AF;letter-spacing:1.5px", "font-weight:600;color:#9CA3AF"),
        ],
    },
}

if __name__ == "__main__":
    for fname, cfg in JOBS.items():
        path = OUT / fname
        text = path.read_text(encoding="utf-8")
        path.with_suffix(path.suffix + ".bak").write_text(text, encoding="utf-8")
        for old, new in cfg.get("trims", []):
            n = text.count(old)
            text = text.replace(old, new)
            print(f"  trim x{n}: {old[:50]}...")
        for anchor, fig in cfg["inserts"]:
            block = fig_html(fig, cfg["margin"])
            if block in text:
                print(f"  skip {fig}（已插入过）")
                continue
            n = text.count(anchor)
            assert n == 1, f"锚点应唯一（当前 {n} 次）：{anchor[:50]}"
            text = text.replace(anchor, anchor + "\n" + block)
            print(f"  insert {fig} -> {anchor[:40]}...")
        path.write_text(text, encoding="utf-8")
        print(f"{fname}: 完成，{len(text)} chars（含内嵌图，推送时会被替换为短 URL）")
