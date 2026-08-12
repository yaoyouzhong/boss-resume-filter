"""GUI 页面构建/切换基准：量化各页面首次构建与重复切换耗时。

测量协议：同一会话内第 1 次冷跑丢弃，再连跑 3 次取中位数；
跨会话绝对值受冷/热缓存影响抖动可达数倍，判定只用同会话比率。
"""
import os
import sys
import time
import tkinter as tk
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

os.environ["BOSS_RESUME_FILTER_DISABLE_DATA_MIGRATION"] = "1"
os.environ["BOSS_RESUME_FILTER_DISABLE_GUARD_PERSISTENCE"] = "1"
os.environ["BOSS_RESUME_FILTER_DISABLE_STARTUP_UPDATE"] = "1"

import gui_main


def main():
    root = tk.Tk()
    root.withdraw()
    t0 = time.perf_counter()
    app = gui_main.BossFilterGUI(root)
    t1 = time.perf_counter()
    print(f"App.__init__: {(t1 - t0) * 1000:.0f} ms")

    pages = [
        ("home", "create_home_page"),
        ("config", "create_config_page"),
        ("run", "create_run_page"),
        ("result", "create_result_page"),
        ("stats", "create_stats_page"),
    ]
    for name, creator in pages:
        fn = getattr(app, creator, None)
        if fn is None:
            continue
        start = time.perf_counter()
        try:
            fn()
        except Exception as exc:
            print(f"{name}: FAILED {exc}")
            continue
        print(f"create_{name}: {(time.perf_counter() - start) * 1000:.0f} ms")

    # 重复切换（页面已缓存）
    shows = [
        ("home", app.show_page_home),
        ("config", app.show_page_config),
        ("run", app.show_page_run),
        ("result", app.show_page_result),
        ("stats", app.show_page_stats),
    ]
    for round_no in range(2):
        for name, show in shows:
            start = time.perf_counter()
            try:
                show()
                root.update_idletasks()
            except Exception as exc:
                print(f"show_{name} r{round_no}: FAILED {exc}")
                continue
            print(f"show_{name} r{round_no}: {(time.perf_counter() - start) * 1000:.0f} ms")

    root.destroy()


if __name__ == "__main__":
    main()
