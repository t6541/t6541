from __future__ import annotations

from html import escape
import json
import os
from pathlib import Path
import shutil
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs
import webbrowser

from .okx import OkxDemoClient
from .pipeline import run_pipeline


def _resource(relative: str) -> Path:
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2])) / relative


WORKSPACE = Path(os.environ.get("QUANTBOT_WORKSPACE", Path.home() / "Documents" / "QuantBotWorkspace"))
WORKSPACE.mkdir(parents=True, exist_ok=True)
CONFIG = WORKSPACE / "config.toml"
if not CONFIG.exists():
    shutil.copy2(_resource("configs/default.toml"), CONFIG)
os.chdir(WORKSPACE)

STATE: dict[str, object] = {"status": "就绪（仅研究/模拟盘）", "output": None, "metrics": None, "okx": "未检查"}
LOCK = threading.Lock()


def _page() -> bytes:
    with LOCK:
        status, output, metrics, okx = STATE["status"], STATE["output"], STATE["metrics"], STATE["okx"]
    metric_html = "尚未运行"
    if isinstance(metrics, dict):
        metric_html = " · ".join([f"总收益 {metrics['total_return']:.2%}", f"年化 {metrics['annual_return']:.2%}", f"夏普 {metrics['sharpe']:.2f}", f"最大回撤 {metrics['max_drawdown']:.2%}"])
    report = "<button disabled>打开报告</button>" if not output else "<form method='post' action='/open-report'><button>打开报告</button></form>"
    html = f"""<!doctype html><html lang='zh-CN'><meta charset='utf-8'><meta name='viewport' content='width=device-width'>
<title>Codex QuantBot</title><style>body{{font:16px system-ui;background:#f4f7fb;color:#162033;margin:0}}main{{max-width:980px;margin:36px auto;padding:0 20px}}section{{background:white;border-radius:14px;padding:20px;margin:14px 0;box-shadow:0 5px 22px #ccd5e366}}button{{background:#2459d3;color:white;border:0;border-radius:8px;padding:11px 16px;font-weight:650;cursor:pointer}}form{{display:inline;margin-right:8px}}code{{word-break:break-all}}.warn{{background:#fff4d6;border-left:4px solid #e2a400}}.ok{{color:#17663a}}</style>
<main><h1>Codex QuantBot</h1><p>ETH-USDT 永续 · 数据 → 因子 → 回测 → 风控 → OKX Demo Trading</p>
<section class='warn'><b>安全模式：</b>本程序固定使用欧易模拟盘请求头，不连接真实资金。100 倍杠杆可能快速耗尽全部保证金。</section>
<section><h2>研究工作流</h2><p>配置：<code>{escape(str(CONFIG))}</code></p><form method='post' action='/run'><button>运行模拟研究</button></form>{report}<form method='post' action='/open-workspace'><button>打开工作目录</button></form><p class='ok'>{escape(str(status))}</p><p>{escape(metric_html)}</p></section>
<section><h2>OKX 模拟接口</h2><p>ETH-USDT-SWAP · 全仓 · 请求杠杆 100×</p><form method='post' action='/okx-public'><button>检查公开合约</button></form><form method='post' action='/okx-account'><button>验证 Demo API</button></form><form method='post' action='/okx-leverage'><button>设置 Demo 100×</button></form><p>{escape(str(okx))}</p><small>私有操作从启动进程环境读取 OKX_API_KEY、OKX_SECRET_KEY、OKX_PASSPHRASE，不保存密钥。</small></section>
<p>本地服务只监听 127.0.0.1。关闭此页面后，可在任务管理器结束 CodexQuantBot.exe。</p></main></html>"""
    return html.encode("utf-8")


def _research() -> None:
    with LOCK:
        STATE["status"] = "正在运行…刷新页面查看进度"
    try:
        output = run_pipeline(CONFIG)
        metrics = json.loads((output / "metrics.json").read_text(encoding="utf-8"))
        with LOCK:
            STATE.update(status="完成（仅模拟研究）", output=output, metrics=metrics)
    except Exception as exc:
        with LOCK:
            STATE["status"] = f"失败：{exc}"


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path != "/":
            self.send_error(404)
            return
        data = _page()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self) -> None:
        try:
            if self.path == "/run":
                threading.Thread(target=_research, daemon=True).start()
            elif self.path == "/open-workspace":
                os.startfile(str(WORKSPACE))
            elif self.path == "/open-report" and STATE["output"]:
                os.startfile(str(Path(STATE["output"]) / "report.html"))
            elif self.path == "/okx-public":
                info = OkxDemoClient().public_instrument()
                with LOCK:
                    STATE["okx"] = f"公开接口正常；合约状态 {info['data'][0]['state']}，平台杠杆上限 {info['data'][0]['lever']}×"
            elif self.path == "/okx-account":
                result = OkxDemoClient().check_demo_account()
                with LOCK:
                    STATE["okx"] = f"Demo API 验证成功，返回 {len(result.get('data', []))} 条账户配置"
            elif self.path == "/okx-leverage":
                OkxDemoClient().set_leverage(100)
                with LOCK:
                    STATE["okx"] = "Demo 账户 100× 杠杆设置请求成功"
            else:
                self.send_error(404)
                return
        except Exception as exc:
            with LOCK:
                STATE["okx"] = f"操作失败：{exc}"
        self.send_response(303)
        self.send_header("Location", "/")
        self.end_headers()

    def log_message(self, format: str, *args) -> None:
        pass


def main() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    url = f"http://127.0.0.1:{server.server_port}/"
    if os.environ.get("QUANTBOT_SMOKE_TEST") == "1":
        if b"Codex QuantBot" not in _page():
            raise RuntimeError("Desktop control panel smoke test failed")
        server.server_close()
        return
    if os.environ.get("QUANTBOT_NO_BROWSER") != "1":
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    (WORKSPACE / "server.json").write_text(
        json.dumps({"url": url, "pid": os.getpid()}, ensure_ascii=False), encoding="utf-8"
    )
    server.serve_forever()


if __name__ == "__main__":
    main()
