from __future__ import annotations

from html import escape
from pathlib import Path


def write_html_report(path: Path, run_id: str, metrics: dict[str, float], data_hash: str) -> None:
    rows = "".join(f"<tr><th>{escape(k)}</th><td>{v:.6f}</td></tr>" for k, v in metrics.items())
    html = f"""<!doctype html><html lang='zh-CN'><meta charset='utf-8'><title>QuantBot {escape(run_id)}</title>
<style>body{{font:16px system-ui;max-width:900px;margin:40px auto;color:#17202a}}table{{border-collapse:collapse;width:100%}}th,td{{padding:10px;border-bottom:1px solid #ddd;text-align:left}}.warn{{background:#fff3cd;padding:14px}}</style>
<h1>量化研究报告</h1><p>运行 ID：{escape(run_id)}</p><p>数据指纹：<code>{escape(data_hash)}</code></p>
<p class='warn'>仅供研究与模拟交易，不构成投资建议，未连接实盘订单。</p><table>{rows}</table></html>"""
    path.write_text(html, encoding="utf-8")

