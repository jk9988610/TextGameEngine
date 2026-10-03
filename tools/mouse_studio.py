# -*- coding: utf-8 -*-
"""
mouse_studio.py —— 带可视化界面的鼠标录制/区域订阅工具（只读，不注入输入）。

功能：
  1. 控制面板：开始/停止录制、打开输出目录
  2. 「框选区域」：全屏半透明遮罩，按住左键拖一个矩形 = 监视区域（橡皮筋选区）
  3. 只记录落在该矩形【内部】的鼠标事件（按下/点击/拖拽/滚轮），区域外事件忽略
  4. 每次命中事件同步截该区域的图；事件实时显示在列表（类型/坐标/位移/截图名）
  5. 输出目录、单张截图路径可直接点开查看

产物：tools/watch_sessions/<时间戳>/{events.jsonl, shot_*.png, summary.txt}
坐标：进程声明 PerMonitorV2，全部物理像素（日志里是全虚拟屏坐标，不受裁剪影响）。

用法：  .\\.venv\\Scripts\\python.exe tools\\mouse_studio.py
"""
import ctypes
import json
import subprocess
import sys
import time
import tkinter as tk
from datetime import datetime
from pathlib import Path

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    pass

from pynput import mouse
import mss

ROOT = Path(__file__).resolve().parent.parent
SESS_ROOT = ROOT / "tools" / "watch_sessions"
SESS_ROOT.mkdir(parents=True, exist_ok=True)


class RegionPicker:
    """全屏顶层窗口，橡皮筋拖一个矩形；返回 {left,top,width,height} 物理像素"""

    def __init__(self, on_done):
        self.on_done = on_done
        self.top = tk.Toplevel()
        self.top.attributes("-fullscreen", True)
        self.top.attributes("-alpha", 0.28)
        self.top.configure(bg="black", cursor="crosshair")
        self.top.attributes("-topmost", True)
        self.canvas = tk.Canvas(self.top, bg="black", highlightthickness=0,
                                cursor="crosshair")
        self.canvas.pack(fill="both", expand=True)
        self.canvas.create_text(
            self.top.winfo_screenwidth() // 2, 34,
            text="按住左键拖出监视区域 · ESC 取消",
            fill="white", font=("Microsoft YaHei", 16))
        self.x0 = self.y0 = None
        self.rect = None
        self.canvas.bind("<ButtonPress-1>", self._down)
        self.canvas.bind("<B1-Motion>", self._move)
        self.canvas.bind("<ButtonRelease-1>", self._up)
        self.top.bind("<Escape>", lambda e: self._cancel())

    def _down(self, e):
        self.x0, self.y0 = e.x_root, e.y_root
        if self.rect: self.canvas.delete(self.rect)
        self.rect = self.canvas.create_rectangle(e.x, e.y, e.x, e.y,
            outline="#4d9fff", width=2)

    def _move(self, e):
        if self.x0 is None: return
        self.canvas.coords(self.rect,
            self.x0, self.y0, e.x_root, e.y_root)

    def _up(self, e):
        x1, y1 = min(self.x0, e.x_root), min(self.y0, e.y_root)
        x2, y2 = max(self.x0, e.x_root), max(self.y0, e.y_root)
        self.top.destroy()
        if x2 - x1 < 6 or y2 - y1 < 6:
            self.on_done(None)              # 太小，视为取消
        else:
            self.on_done({"left": x1, "top": y1,
                          "width": x2 - x1, "height": y2 - y1})

    def _cancel(self):
        self.top.destroy(); self.on_done(None)


class Recorder:
    def __init__(self, app, region, out_dir):
        self.app = app
        self.region = region
        self.out_dir = out_dir
        self.log = out_dir / "events.jsonl"
        self.sct = mss.MSS()
        self.seq = 0
        self.stats = {"click": 0, "drag": 0, "scroll": 0, "down_only": 0}
        self.down = None          # {button,x,y} 已在区域内按下
        self.dragging = None      # 超过阈值后记拖拽
        self.listener = mouse.Listener(
            on_click=self._click, on_move=self._move, on_scroll=self._scroll)
        self.listener.start()

    def inside(self, x, y):
        r = self.region
        return (r["left"] <= x <= r["left"] + r["width"] and
                r["top"] <= y <= r["top"] + r["height"])

    def shot(self):
        self.seq += 1
        name = f"shot_{self.seq:04d}.png"
        img = self.sct.grab(self.region)
        mss.tools.to_png(img.rgb, img.size, output=str(self.out_dir / name))
        return name

    def emit(self, kind, **kw):
        rec = {"t": round(time.time(), 3), "kind": kind, **kw}
        with self.log.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        self.app.add_row(rec)

    def stop(self):
        self.listener.stop()
        (self.out_dir / "summary.txt").write_text(
            f"区域：{self.region}\n统计：{json.dumps(self.stats, ensure_ascii=False)}\n"
            f"截图：{self.seq} 张\n", encoding="utf-8")

    # ---- 仅当事件发生在区域内才记录；按下在区域内、抬起在区域外也算（保留完整动作） ----
    def _click(self, x, y, button, pressed):
        b = str(button).replace("Button.", "")
        if pressed:
            if self.inside(x, y):
                self.down = {"button": b, "x": x, "y": y}
                self.dragging = None
        else:
            if not self.down:
                return
            sx, sy = self.down["x"], self.down["y"]
            if self.dragging or abs(x - sx) + abs(y - sy) > 8:
                self.stats["drag"] += 1
                self.emit("drag_end", button=b, x0=sx, y0=sy, x=x, y=y,
                          dx=x - sx, dy=y - sy, shot=self.shot())
            else:
                self.stats["click"] += 1
                self.emit("click", button=b, x=x, y=y, shot=self.shot())
            self.down, self.dragging = None, None

    def _move(self, x, y):
        if self.down and self.dragging is None and \
           abs(x - self.down["x"]) + abs(y - self.down["y"]) > 8:
            self.dragging = (x, y)
            self.down_only = self.down
            self.emit("drag_start", button=self.down["button"],
                      x=self.down["x"], y=self.down["y"])

    def _scroll(self, x, y, dx, dy):
        if not self.inside(x, y):
            return
        self.stats["scroll"] += 1
        self.emit("scroll", x=x, y=y, dx=dx, dy=dy, shot=self.shot())


class App:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("鼠标录制器 · 区域订阅")
        self.root.geometry("720x460")
        self.root.configure(bg="#1e1f22")
        self.recorder = None
        self.region = None
        self.out_dir = None

        bar = tk.Frame(self.root, bg="#1e1f22")
        bar.pack(fill="x", padx=10, pady=8)
        tk.Button(bar, text="① 框选区域", command=self.pick_region,
                  width=12).pack(side="left", padx=3)
        self.region_lbl = tk.Label(bar, text="区域：未选择（默认全屏）",
                                   fg="#9aa0ab", bg="#1e1f22")
        self.region_lbl.pack(side="left", padx=8)
        self.btn_rec = tk.Button(bar, text="② 开始录制", command=self.toggle,
                                 width=12, state="disabled", bg="#4d9fff")
        self.btn_rec.pack(side="right", padx=3)
        tk.Button(bar, text="打开目录", command=self.open_dir,
                  width=10).pack(side="right", padx=3)

        self.listbox = tk.Listbox(self.root, bg="#141518", fg="#d7e2f2",
            font=("Consolas", 10), selectbackground="#2d4a6b", activestyle="none")
        self.listbox.pack(fill="both", expand=True, padx=10, pady=(0, 8))
        self.status = tk.Label(self.root, text="就绪", anchor="w",
                               fg="#9aa0ab", bg="#1e1f22")
        self.status.pack(fill="x", padx=10, pady=(0, 8))
        self.listbox.bind("<Double-Button-1>", self.open_shot)

    def pick_region(self):
        self.root.withdraw()
        self.root.after(180, lambda: RegionPicker(self._region_picked))

    def _region_picked(self, region):
        self.root.deiconify()
        self.region = region
        if region:
            self.region_lbl.config(
                text=f"区域：x={region['left']} y={region['top']} "
                     f"{region['width']}×{region['height']}")
        else:
            self.region_lbl.config(text="区域：未选择（默认全屏）")
        self.btn_rec.config(state="normal")

    def toggle(self):
        if self.recorder:
            self.recorder.stop()
            self.recorder = None
            self.btn_rec.config(text="② 开始录制", bg="#4d9fff")
            self.status.config(text=f"已停止：{self.out_dir}")
            return
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.out_dir = SESS_ROOT / stamp
        self.out_dir.mkdir(parents=True, exist_ok=True)
        region = self.region
        if region is None:   # 未框选则用虚拟全屏
            m = mss.MSS().monitors[0]
            region = {"left": m["left"], "top": m["top"],
                      "width": m["width"], "height": m["height"]}
        self.recorder = Recorder(self, region, self.out_dir)
        self.btn_rec.config(text="■ 停止", bg="#f87171")
        self.status.config(text=f"录制中 → {self.out_dir}")

    def add_row(self, rec):
        """pynput 线程回调，经 after 切回主线程刷新 UI"""
        self.root.after(0, lambda: self._insert(rec))

    def _insert(self, rec):
        kind = rec["kind"]
        if kind == "click":
            txt = f"[{rec['t']:.1f}] 点击 {rec['button']}  ({rec['x']},{rec['y']})  {rec.get('shot','')}"
        elif kind == "drag_start":
            txt = f"[{rec['t']:.1f}] 拖拽起 ({rec['x']},{rec['y']})"
        elif kind == "drag_end":
            txt = (f"[{rec['t']:.1f}] 拖拽止 ({rec['x']},{rec['y']}) "
                   f"位移 {rec['dx']},{rec['dy']}  {rec.get('shot','')}")
        else:
            txt = f"[{rec['t']:.1f}] 滚轮 ({rec['x']},{rec['y']}) dy={rec['dy']} {rec.get('shot','')}"
        self.listbox.insert("end", txt)
        self.listbox.see("end")

    def open_shot(self, _=None):
        sel = self.listbox.curselection()
        if not sel: return
        txt = self.listbox.get(sel[0])
        for part in txt.split():
            if part.startswith("shot_"):
                path = self.out_dir / part
                if path.exists():
                    subprocess.Popen(["cmd", "/c", "start", "", str(path)])

    def open_dir(self):
        target = self.out_dir or SESS_ROOT
        subprocess.Popen(["explorer", str(target)])

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    App().run()
