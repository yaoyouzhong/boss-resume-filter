# 测试目录说明

本目录分三类，避免把稳定回归、浏览器人工验证和历史实验脚本混在一起。

## 稳定单元测试

位置：`tests/unit/`

要求：
- 不依赖真实 `job_config.json`
- 不启动浏览器
- 不访问网络
- 不要求人工登录
- 输出只使用 ASCII 的 `PASS` / `FAIL`

运行：

```powershell
python tests/run_unit_tests.py
```

## 人工/集成测试

位置：`tests/manual/`

这类脚本可能依赖 Chrome、BOSS 页面、人工登录、调试端口或真实网络环境，不纳入默认回归。

### GUI / 浏览器环境验收矩阵

`tests/gui_browser_acceptance_matrix.json` 是显示环境和浏览器状态的覆盖清单。矩阵分三层：

- 稳定回归验证 1080P、4K、Retina 的结果列策略，以及未连接、登录页、非推荐页、加载中、无职位、无候选人、正常页面和访问冷却等状态
- 本机 Tk 烟测打开全部 7 个页面，验证实际布局尺寸、结果列策略和系统设置的数据安全入口；不连接 Chrome、不访问网络、不检查更新、不迁移真实数据
- 1080P、4K、macOS Retina 和真实 Chrome/BOSS 页面保留为发布前对应环境人工验收，不能用模拟结果替代

运行本机无网络 GUI 烟测：

```powershell
python tests/manual/run_gui_acceptance_matrix.py
```

默认报告写入忽略目录 `tmp/gui-acceptance-report.json`，只记录环境尺寸和通过状态，不记录候选人或岗位内容。

## 历史归档脚本

位置：`tests/archive/`

这里存放旧调试脚本和已失效脚本。归档脚本默认不维护、不保证可运行，只作为排查历史问题时的参考。
