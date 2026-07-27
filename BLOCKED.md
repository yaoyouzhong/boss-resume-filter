# 待裁决清单

## 2026-07-27：任务 0 性能基线与任务书不一致（硬停止）

按任务书要求，在新建分支 `codex/gui-perf` 后首先运行：

```text
python tests/manual/bench_page_switch.py
```

实际完整输出：

```text
App.__init__: 1116 ms
create_home: 127 ms
create_config: 957 ms
create_run: 540 ms
create_result: 495 ms
create_stats: 145 ms
show_home r0: 887 ms
show_config r0: 748 ms
show_run r0: 21 ms
show_result r0: 57 ms
show_stats r0: 25 ms
show_home r1: 12 ms
show_config r1: 12 ms
show_run r1: 21 ms
show_result r1: 5 ms
show_stats r1: 4 ms
```

与任务书管理者基线相比：

| 指标 | 任务书基线 | 本次实测 | 差异 |
|---|---:|---:|---:|
| `App.__init__` | 839 ms | 1116 ms | +277 ms（+33%） |
| `create_config_page` | 556 ms | 957 ms | +401 ms（+72%） |
| `create_run_page` | 349 ms | 540 ms | +191 ms（+55%） |
| `create_result_page` | 272 ms | 495 ms | +223 ms（+82%） |
| `create_home_page` | 30 ms | 127 ms | +97 ms（+323%） |
| `create_stats_page` | 41 ms | 145 ms | +104 ms（+254%） |

命令退出码为 0，但关键数字全部明显偏高，不能视为正常测量抖动。任务书明确规定“命令不存在或对不上就停，证据写 `BLOCKED.md` 最上面”，因此已停止任务 0；尚未运行 `tests/run_unit_tests.py`、`tests/test_import.py`，也未创建控件树基线、修改 GUI 代码或开始任何修改-验证循环。

待裁决：确认应以任务书数字继续作为固定基线，还是允许在同一环境重复测量并建立新的可比基线。
