# scripts 目录说明

本目录放辅助脚本，不属于主程序运行路径。正式交付脚本应有对应单元测试；仅浏览器/人工工具不进入稳定回归。

默认验收命令仍然是：

```powershell
python tests/run_unit_tests.py
python tests/test_import.py
```

## 活跃脚本

- `release_ci.py`：`Build & Release` 的确定性发布编排，负责一次授权、严格门禁、双远端发布、断点续跑和线上验收；行为测试在 `tests/unit/test_release_ci.py`。
- `watch_progress.py`：轮询 `.build_progress.json` 并输出本地打包状态，保留作为手工构建辅助工具。

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
