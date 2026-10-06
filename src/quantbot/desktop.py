from __future__ import annotations

import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .pipeline import run_pipeline


def _resource(relative: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
    return base / relative


def _workspace() -> Path:
    root = Path.home() / "Documents" / "QuantBotWorkspace"
    root.mkdir(parents=True, exist_ok=True)
    config = root / "config.toml"
    if not config.exists():
        shutil.copy2(_resource("configs/default.toml"), config)
    return root


def _open_path(path: Path) -> None:
    os.startfile(str(path.resolve()))


class QuantBotApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Codex QuantBot · 模拟研究终端")
        self.geometry("920x650")
        self.minsize(760, 520)
        self.workspace = _workspace()
        self.config_path = tk.StringVar(value=str(self.workspace / "config.toml"))
        self.status = tk.StringVar(value="就绪（仅研究/模拟盘）")
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.last_output: Path | None = None
        self._build_ui()
        self.after(100, self._poll)

    def _build_ui(self) -> None:
        style = ttk.Style(self)
        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 18, "bold"))
        container = ttk.Frame(self, padding=20)
        container.pack(fill="both", expand=True)
        ttk.Label(container, text="Codex QuantBot", style="Title.TLabel").pack(anchor="w")
        ttk.Label(container, text="数据 → 因子 → 策略 → 含成本回测 → 风控 → 审计报告（不连接实盘）").pack(anchor="w", pady=(2, 18))
        config = ttk.LabelFrame(container, text="运行配置", padding=12)
        config.pack(fill="x")
        ttk.Entry(config, textvariable=self.config_path).pack(side="left", fill="x", expand=True)
        ttk.Button(config, text="选择…", command=self._choose).pack(side="left", padx=(8, 0))
        ttk.Button(config, text="编辑", command=lambda: _open_path(Path(self.config_path.get()))).pack(side="left", padx=(8, 0))
        actions = ttk.Frame(container)
        actions.pack(fill="x", pady=14)
        self.run_button = ttk.Button(actions, text="运行模拟研究", command=self._run)
        self.run_button.pack(side="left")
        ttk.Button(actions, text="打开报告", command=self._open_report).pack(side="left", padx=8)
        ttk.Button(actions, text="打开工作目录", command=lambda: _open_path(self.workspace)).pack(side="left")
        self.progress = ttk.Progressbar(actions, mode="indeterminate", length=180)
        self.progress.pack(side="right")
        metrics = ttk.LabelFrame(container, text="最近一次指标", padding=10)
        metrics.pack(fill="x", pady=(0, 12))
        self.metric_text = tk.StringVar(value="尚未运行")
        ttk.Label(metrics, textvariable=self.metric_text).pack(anchor="w")
        logs = ttk.LabelFrame(container, text="运行日志", padding=8)
        logs.pack(fill="both", expand=True)
        self.log = tk.Text(logs, wrap="word", height=16, state="disabled", font=("Consolas", 10))
        self.log.pack(fill="both", expand=True)
        ttk.Label(container, textvariable=self.status).pack(anchor="w", pady=(10, 0))

    def _choose(self) -> None:
        path = filedialog.askopenfilename(title="选择 TOML 配置", filetypes=[("TOML", "*.toml")])
        if path:
            self.config_path.set(path)

    def _append(self, text: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _run(self) -> None:
        config = Path(self.config_path.get())
        if not config.exists():
            messagebox.showerror("配置错误", "配置文件不存在。")
            return
        self.run_button.configure(state="disabled")
        self.progress.start(12)
        self.status.set("正在运行…")
        self._append(f"开始：{config}")
        threading.Thread(target=self._worker, args=(config,), daemon=True).start()

    def _worker(self, config: Path) -> None:
        try:
            os.chdir(self.workspace)
            output = run_pipeline(config)
            metrics = json.loads((output / "metrics.json").read_text(encoding="utf-8"))
            self.events.put(("done", (output, metrics)))
        except Exception as exc:
            self.events.put(("error", exc))

    def _poll(self) -> None:
        try:
            kind, payload = self.events.get_nowait()
        except queue.Empty:
            self.after(100, self._poll)
            return
        self.progress.stop()
        self.run_button.configure(state="normal")
        if kind == "done":
            output, metrics = payload
            self.last_output = Path(output)
            self.metric_text.set("总收益 {total_return:.2%}  |  年化 {annual_return:.2%}  |  夏普 {sharpe:.2f}  |  最大回撤 {max_drawdown:.2%}  |  成本 {total_cost:.4f}".format(**metrics))
            self.status.set("完成（仅模拟研究）")
            self._append(f"完成：{self.last_output.resolve()}")
        else:
            self.status.set("运行失败")
            self._append(f"错误：{payload}")
            messagebox.showerror("运行失败", str(payload))
        self.after(100, self._poll)

    def _open_report(self) -> None:
        if not self.last_output:
            messagebox.showinfo("尚无报告", "请先运行一次模拟研究。")
            return
        _open_path(self.last_output / "report.html")


def main() -> None:
    QuantBotApp().mainloop()


if __name__ == "__main__":
    main()

