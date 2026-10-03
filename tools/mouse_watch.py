# -*- coding: utf-8 -*-
"""
mouse_watch.py —— 全局鼠标行为监视器（只读，不控制鼠标）。

用途：人工测试画布交互时，实时打印并落盘「文本事件 + 同步截图」，
      让排查基于真实操作证据，而不是合成事件或口头描述。

产物（tools/watch_sessions/<时间戳>/）：
  events.jsonl   每行一个事件：时间戳/类型/按键/坐标/拖拽位移/截图文件名
  shot_0001.png  每次点击抬起、拖拽结束、滚轮时的全屏截图（文件名与日志对应）
  summary.txt    会话结束时的统计

用法：
  .\\.venv\\Scripts\\python.exe tools\\mouse_watch.py                 # 全屏
  .\\.venv\\Scripts\\python.exe tools\\mouse_watch.py --region 0 0 1200 800
      # 只截矩形区域（物理像素，左上 x y + 宽 高）；鼠标事件仍记录全屏坐标，
      # 超出区域的点击照常记录，只是截图范围受限
  .\\.venv\\Scripts\\python.exe tools\\mouse_watch.py --list-monitors
      # 打印各显示器编号与物理像素边界，多屏时用来选 --monitor N

  常用做法：浏览器全屏后画布基本铺满，直接全屏最简单；
  只盯一块屏用 --monitor 1。坐标日志始终是全虚拟屏坐标，截图裁哪个区域不影响定位。

注意：
- 仅监听、仅截图，不注入任何输入（控制/回放是第二步，用 pyautogui 另做）。
- Windows DPI：进程声明 PerMonitorV2，坐标与截图使用物理像素，避免高分屏错位。
"""
import ctypes
import json
import sys
import time
from datetime import datetime
from pathlib import Path

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PER_MONITOR_AWARE
except Exception:
    pass

from pynput import mouse
import mss

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "tools" / "watch_sessions" / datetime.now().strftime("%Y%m%d_%H%M%S")
OUT_DIR.mkdir(parents=True, exist_ok=True)
LOG = OUT_DIR / "events.jsonl"

sct = mss.MSS()

# ---- 截图区域：默认全屏（monitor 0），可按 --monitor/--region 裁剪 ----
def parse_args():
    args = sys.argv[1:]
    if "--list-monitors" in args:
        for i, m in enumerate(sct.monitors):
            tag = "虚拟全屏" if i == 0 else f"显示器{i}"
            print(f"[{i}] {tag}: left={m['left']} top={m['top']} "
                  f"width={m['width']} height={m['height']}")
        sys.exit(0)
    monitor = 0
    region = None
    if "--monitor" in args:
        monitor = int(args[args.index("--monitor") + 1])
    if "--region" in args:
        k = args.index("--region")
        x, y, w, h = (int(v) for v in args[k+1:k+5])
        region = {"left": x, "top": y, "width": w, "height": h}
    return monitor, region

MONITOR, REGION = parse_args()

def shot_box():
    """返回本次截图的区域 dict：region 优先，否则指定显示器，否则虚拟全屏"""
    if REGION:
        return REGION
    mons = sct.monitors
    return mons[min(MONITOR, len(mons) - 1)]
seq = 0
stats = {"click": 0, "drag": 0, "scroll": 0}
down = None          # {button,x,y,t}
drag_start = None    # 按下时若移动超过阈值则记为拖拽


def take_shot():
    """区域截图，返回文件名（事件里只存文件名，日志和图片靠序号对齐）"""
    global seq
    seq += 1
    name = f"shot_{seq:04d}.png"
    box = shot_box()
    shot = sct.grab(box)
    mss.tools.to_png(shot.rgb, shot.size, output=str(OUT_DIR / name))
    return name


def emit(kind, **kw):
    rec = {"t": round(time.time(), 3), "kind": kind, **kw}
    line = json.dumps(rec, ensure_ascii=False)
    print(line, flush=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")
    return rec


def on_click(x, y, button, pressed):
    global down, drag_start
    b = str(button).replace("Button.", "")
    if pressed:
        down = {"button": b, "x": x, "y": y, "t": time.time()}
        drag_start = None
        emit("down", button=b, x=x, y=y)
    else:
        shot = None
        rec = None
        if drag_start:
            stats["drag"] += 1
            dx, dy = x - drag_start["x"], y - drag_start["y"]
            shot = take_shot()
            rec = emit("drag_end", button=b, x0=drag_start["x"], y0=drag_start["y"],
                       x=x, y=y, dx=dx, dy=dy, shot=shot)
        elif down:
            stats["click"] += 1
            shot = take_shot()
            rec = emit("click", button=b, x=x, y=y, shot=shot)
        down, drag_start = None, None
        return rec


def on_move(x, y):
    # 仅在按住键且超过 8px 时标记拖拽开始，避免普通移动刷屏
    global drag_start
    if down and drag_start is None:
        if abs(x - down["x"]) + abs(y - down["y"]) > 8:
            drag_start = {"button": down["button"], "x": down["x"], "y": down["y"]}
            emit("drag_start", button=down["button"], x=down["x"], y=down["y"])


def on_scroll(x, y, dx, dy):
    stats["scroll"] += 1
    shot = take_shot()
    emit("scroll", x=x, y=y, dx=dx, dy=dy, shot=shot)


def main():
    print(f"[mouse_watch] 输出目录：{OUT_DIR}")
    print("[mouse_watch] 监听中……现在去浏览器操作；Ctrl+C 结束并生成 summary。", flush=True)
    with mouse.Listener(on_click=on_click, on_move=on_move,
                        on_scroll=on_scroll) as lst:
        try:
            lst.join()
        except KeyboardInterrupt:
            pass
    (OUT_DIR / "summary.txt").write_text(
        f"会话：{OUT_DIR.name}\n截图区域：{shot_box()}\n"
        f"事件统计：{json.dumps(stats, ensure_ascii=False)}\n"
        f"截图：{seq} 张\n日志：{LOG.name}\n", encoding="utf-8")
    print(f"\n[mouse_watch] 结束。统计 {stats}，截图 {seq} 张，区域 {shot_box()}，目录 {OUT_DIR}")


if __name__ == "__main__":
    main()
