# -*- coding: utf-8 -*-
"""
QA 统一入口：发现并运行 qa/m*.py 全部回归套件，最后汇总并全局清理。

用法：
  python -m qa.run_all            # 跑全部
  python -m qa.m3_shop_gold       # 只跑 M3
"""
import glob
import importlib
import os
import sys
import traceback

# 同时支持 python qa/run_all.py 直接启动
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from qa.tge_api import QaRunner, ping, cleanup_qa_saves, cleanup_backups, DEFAULT_BASE


def discover_suites():
    """按文件名排序加载所有 m*.py 套件（m3_xxx → m4_xxx 顺序执行）。"""
    suites = []
    qa_dir = os.path.dirname(os.path.abspath(__file__))
    for path in sorted(glob.glob(os.path.join(qa_dir, "m*.py"))):
        mod_name = "qa." + os.path.splitext(os.path.basename(path))[0]
        mod = importlib.import_module(mod_name)
        suites.append((getattr(mod, "SUITE", mod_name), mod.run))
    return suites


def main() -> int:
    if not ping():
        print(f"[FATAL] 服务器未响应：{DEFAULT_BASE}")
        print("请先在仓库根目录启动：.venv\\Scripts\\python.exe app.py")
        print("（或设置 TGE_BASE 指向已运行的服务器）")
        return 2

    runners = []
    for title, run_fn in discover_suites():
        print("\n" + "#" * 60)
        print(f"# 套件：{title}")
        print("#" * 60)
        r = QaRunner(title)
        try:
            run_fn(r)
        except Exception:
            traceback.print_exc()
            r.failed += 1
        runners.append(r)

    # 全局扫尾：历史残留 qa_ 存档 + 编辑器备份文件
    removed_saves = cleanup_qa_saves()
    removed_baks = cleanup_backups()

    print("\n" + "=" * 60)
    print("回归汇总")
    print("=" * 60)
    total_p = total_f = 0
    for r in runners:
        print(("  OK  " if r.failed == 0 else " FAIL ") + r.report())
        total_p += r.passed
        total_f += r.failed
    print("-" * 60)
    print(f"合计：{total_p} passed / {total_f} failed"
          f"（清理存档 {removed_saves} 行、.bak 文件 {removed_baks} 个）")
    return 1 if total_f else 0


if __name__ == "__main__":
    sys.exit(main())
