# scripts 目录说明

本目录放辅助脚本，不属于主程序运行路径。正式交付脚本应有对应单元测试；仅浏览器/人工工具不进入稳定回归。

默认验收命令仍然是：

```powershell
python tests/run_unit_tests.py
python tests/test_import.py
```

## 活跃脚本

- `release_ci.py`：正式发布的确定性规则实现；Actions 只调用严格门禁和 GitHub Draft 暂存，本机核对 GitHub 附件完整性元数据后立即公开主源，再自动清理 Gitee 历史版本附件、下载并镜像本次版本、同步清单和完成一次权威线上验收；阶段耗时、Actions run、附件进度和脱敏错误同时写入状态文件与发布日志；行为测试在 `tests/unit/test_release_ci.py`。
- `release_flow.py`：正常发布唯一入口；支持单分支和显式多分支聚合，把候选 PR、内容确认、Squash tree 校验、正式发布与断点续跑串成一个状态机；发布失败保留候选分支，完整线上验收后才清理；行为测试在 `tests/unit/test_release_flow.py`。
- `product_fingerprint.py`：计算排除公开发布文案后的产品代码指纹；本机门禁和 PR Checks 仅在同一指纹已经成功回归时复用测试证据。
- `release_content_review.py`：对最终标题和正文做固定用户视角审核；确认前绑定候选 tree，合并后绑定正式发布提交。
- `release_delivery.py`：旧版版本准备与 PR 组合入口，仅保留为分阶段恢复入口；行为测试在 `tests/unit/test_release_delivery.py`。
- `release_prepare.py`：版本号、CHANGELOG、README 与项目版本注释的本地准备和严格门禁，保留为分阶段执行及故障恢复入口。
- `release_dispatch.py`：正式发布底层入口，负责预检、触发或跳过 Actions 暂存、本机公开 GitHub 主源、Gitee 镜像和最终验收。
- 根目录 `subprocess_utils.py`：Windows 项目的统一隐藏启动代理；Git、Python、Ruff、PyInstaller、GitHub CLI、WMIC、taskkill 等控制台子进程使用无窗口方式运行，stdout/stderr 由父进程回放，GitHub CLI 同时关闭额外 telemetry/时区探测子进程；Chrome 等需要展示界面的程序显式放行。
- `pr_delivery.py`：普通开发分支的一次授权交付编排，负责本地门禁、push、PR、CI 等待、Squash 合并、双远端同步和安全分支清理；默认只预览，行为测试在 `tests/unit/test_pr_delivery.py`。
- `watch_progress.py`：轮询 `.build_progress.json` 并输出本地打包状态，保留作为手工构建辅助工具。

版本准备、一键交付和正式发布入口会优先使用项目 `pack_venv`；即使通过系统 Python 或 Anaconda Python 启动，也会在执行仓库检查前自动切换。
Windows 上的外层编排器还必须用 `windowsHide`、`Start-Process -WindowStyle Hidden` 或等价机制启动顶层 Python；项目代理负责隐藏其后全部子进程，但无法反向隐藏调用者已经创建的 PowerShell/终端窗口。

### 单分支一键发布

```powershell
python scripts/release_flow.py `
  --version 2.24 `
  --notes-file "<项目目录外的发布说明文件>" `
  --execute `
  --authorization "一键发布版本 v2.24"
```

脚本完成版本材料、严格门禁、push、候选 PR 和 CI 后展示最终内容并停止。需要调整时更新项目外说明文件并重跑同一命令；同一 PR 会更新，旧内容凭证失效。确认后执行：

```powershell
python scripts/release_flow.py --version 2.24 --confirm --approved-content-sha "<由编排器后台传入>" --authorization "确认发布 v2.24"
```

确认后自动完成 Squash 合并、双远端同步、双平台构建、GitHub/Gitee Release、`latest.json` 和线上验收。多分支使用重复的 `--branch` 与 `--tested-branch branch=commit_sha`；每个分支必须有独立干净的 worktree，脚本会先在各自目录重跑稳定回归和导入烟测，再验证聚合结果。分支顺序同时进入精确授权文本。任一失败保留状态，不自动处理冲突、rebase、force push 或删除 worktree。

### 普通 PR 一键交付

先执行只读预览和本地门禁：

```powershell
python scripts/pr_delivery.py --branch codex/<task>
```

用户准确授权“`一键交付分支 codex/<task>`”后执行完整交付：

```powershell
python scripts/pr_delivery.py `
  --branch codex/<task> `
  --execute `
  --authorization "一键交付分支 codex/<task>"
```

流程遇到分叉、冲突、脏工作区、测试/CI 失败或双远端不一致时停止并保留分支；不会 rebase、force push、删除 worktree 或正式发布。

## archive/ — 历史脚本归档

不再维护但保留参考价值的脚本。包括：

- **BOSS 职位提取实验**：`extract_jobs*.py`、`fetch_jobs_sync.py`、`inspect_page.py`、`js_extraction_helper.py` — 依赖浏览器、登录态、页面 DOM，主程序 `bossmaster.py` 已完全替代
- **反爬/RPA 早期方案**：`rpa_simulation.py`、`rpa_advanced.py`、`bypass_antispider.py` — 方案探索，主线已迁移到 `bossmaster.py` 的反爬对抗逻辑
- **手动提取指南**：`manual_guide.py`、`manual_extraction_guide.py`、`enhanced_manual_guide.py` — 早期手动职位提取说明，GUI 已自动化
- **早期修复版本**：`bossmaster_fixed.py` — 候选人提取修复版本，逻辑已合入主程序
- **配置生成器**：`config_generator.py` — 交互式生成 `job_config.json`，GUI 已替代
- **一次性脚本**：`analyze_candidates.py`、`open_url.py`

归档脚本不保证可运行。如需恢复某个能力，先从归档中取出并重新验证当前 BOSS 页面、登录和反爬行为。

## 使用规则

- 新增脚本前先判断能否放进主程序、`tests/manual/` 或 `tests/archive/`；只有确实是本地辅助工具时才放这里。
- 脚本必须能说明用途、依赖、运行前提和输出结果；不要只留下临时试验代码。
- 依赖浏览器、BOSS 登录、真实网络、反爬调试或人工操作的脚本，默认视为手工工具，不进入稳定回归。
- 不再有效但仍有历史参考价值的脚本，迁移到 `archive/`；迁移前不要删除。
- 运行脚本时默认从仓库根目录执行，避免相对路径写到错误位置。
