# BOSS 简历筛选器 - 项目规范

## 项目结构

```text
boss-resume-filter/
├── bossmaster.py         # BOSS 直聘自动筛选主程序（核心）
├── filtering.py          # 纯筛选规则模块（评分、硬条件、薪资/经验/城市解析）
├── llm_eval.py           # LLM 辅助评估模块（prompt 构建、API 调用、批量评估）
├── ai_adapter.py         # 多服务商接口适配与模型能力验证
├── job_ai_parser.py      # 岗位需求 AI 增强解析模块（基于正则初稿补充优化）
├── job_config_diagnostics.py # 岗位配置保存前体检模块
├── candidate_state_diagnostics.py # 候选人状态一致性体检模块
├── candidate_workflow.py # 候选人决策分类、复核规则、待办与下一步动作模块
├── greeting_failure.py   # 打招呼失败原因归类模块
├── release_user_audit.py # 用户视角发布审计模块
├── storage.py            # 候选人数据持久化模块（去重、原子写入、备份恢复）
├── contact_queue.py      # GUI 候选人联系清单持久化与恢复模块
├── gui_main.py           # 图形界面主程序（v2.24.2）
├── gui_dialogs.py        # 独立对话框模块（更新日志、关于弹窗、CHANGELOG 渲染）
├── ui_messagebox.py      # 统一居中提示与确认弹窗（兼容 tkinter.messagebox）
├── changelog_parser.py   # CHANGELOG 解析模块（版本段落提取、标题解析）
├── updater.py            # 自动更新模块（Gitee/GitHub 双源检查、下载替换、完整性校验、启动时自动检查）
├── icons.py              # 图标绘制模块（Pillow 矢量图标 + IconCache）
├── doc_parser.py         # 招聘需求文档解析器（JD → 必要条件 + 职位要求）
├── education_certificate.py # 毕业证书图片识别、字段校验与学信网页面填写
├── education_tool.py    # 独立学历证书核验助手入口（复用 gui_main 学历核验模式）
├── education_tool_config.py # 独立工具固定 AI 配置
├── education_tool_security.py # 独立工具内置 API Key 解密
├── security.py           # API Key 安全存储模块（keyring 加密，按 provider+base_url 组合存储）
├── migrate_keys.py       # API Key 迁移工具（明文→加密）
├── constants.py          # 共享常量（评分模型参数、阈值、学历档位、滚动参数、城市列表）
├── paths.py              # 路径工具（get_base_dir、ensure_config_files、路径常量）
├── build.py              # PyInstaller 打包、发布门禁与公开版本核验脚本
├── build_education_tool.py # 独立学历证书核验助手打包脚本
├── latest.json           # 双源版本清单（正式发布工作流自动维护）
├── job_config.json       # 岗位筛选规则配置
├── api_config.json       # 发布默认 AI 模型配置模板（不含明文 Key）
├── selectors.json        # 页面选择器配置（CSS/XPath/关键词，DOM 变化时修改）
├── ui_config.json        # UI 尺寸与缩放配置
├── tests/                # 测试脚本目录
├── scripts/              # 辅助脚本（发布监控、PPT 生成、截图等）
│   ├── release_ci.py   # GitHub 暂存、本机镜像与正式发布规则
│   ├── release_content_review.py # 发布内容审核与内部凭证绑定
│   ├── pr_delivery.py # 普通 PR 一次授权交付（门禁、PR、合并、双远端同步、分支清理）
│   ├── release_flow.py # 单/多分支一键发布、内容确认与断点续跑统一入口
│   ├── release_delivery.py / release_prepare.py / release_dispatch.py # 分阶段恢复入口
│   └── watch_progress.py # 发布进度监控脚本（轮询 .build_progress.json）
├── pyinstaller-hooks/    # PyInstaller 自定义 hook（控制模块收集范围，减小产物体积）
├── GUI 使用说明.md       # 图形界面操作说明
├── DEPLOYMENT.md         # 部署说明（新电脑首次部署步骤）
└── PACKAGING.md          # 打包指南（跨平台支持、体积基线、build.py 参数）
```

## 运行命令

### 命令行模式

- 安装依赖：`pip install -r requirements.txt`
- 自动打招呼：`python bossmaster.py --greet`
- 指定岗位：`python bossmaster.py --job "高级 Java 工程师" --greet`
- 补打招呼：`python bossmaster.py --re-greet`
- 打招呼等级：`python bossmaster.py --greet --greet-level strong`（仅强烈推荐）或 `normal`（默认，强烈推荐+推荐）
- 清空历史：`python bossmaster.py --clear --greet`
- 清空保留已沟通：`python bossmaster.py --clear --keep-greeted --greet`（清空时保留已打招呼的候选人）
- 输出详细评分：`python bossmaster.py --greet --verbose`
- AI 辅助评估：`python bossmaster.py --greet --ai-eval`（对通过筛选的候选人进行 LLM 二次评分）

### 图形界面模式（推荐）

- 双击 `gui.bat` 或 `python gui_main.py`
- 侧边栏底部版本号可点击，弹出更新日志对话框查看 CHANGELOG.md 内容

### 测试验证

- 稳定单元回归：`python tests/run_unit_tests.py`
- 导入烟测：`python tests/test_import.py`
- 浏览器、BOSS 页面、人工登录、网络/API 测试只放在 `tests/manual/`，不纳入默认回归
- 历史调试脚本放在 `tests/archive/`，默认不维护、不保证可运行

### 开发与交付流程

- 低风险文档、测试和局部文案可在当前工作区修改；普通代码任务使用 `codex/<task>` 短期分支，并行、脏工作区、长周期或高风险任务才创建独立 worktree
- PR 不作统一要求；核心筛选、自动打招呼、存储、更新器、发布脚本、CI/CD 或大范围修改应使用 PR；面向 `master` 的 PR 由 `PR Checks` 验证，PR 合并始终是独立授权，合并不会触发发布
- 普通分支推送、PR 合并、删除分支/worktree/临时文件默认须分别获得用户授权。用户准确授权“`一键交付分支 <branch>`”后，该一次授权仅覆盖指定分支的本地门禁、普通 push、创建/复用 PR、等待 `PR Checks`、Squash 合并、同步 GitHub/Gitee `master`、删除本地和远端分支、快进本地 `master`；不覆盖 rebase、force push、worktree 删除、冲突处理或正式发布。任一门禁/CI/一致性检查失败必须停止且不得清理分支
- 用户准确授权“`一键发布版本 vX.Y`”后，统一入口允许提交当前 `codex/*` 开发分支、同步版本材料、先完成用户视角内容审核，再运行严格门禁、普通 push、创建/复用发布候选 PR 并等待 `PR Checks`；随后必须完整展示最终标题、正文、候选提交和内容审核结果并停在内容确认阶段，不得合并、创建 tag、构建安装包或公开 Release。用户可在该阶段反复调整版本内容；产品代码指纹未变化且已有同指纹成功回归凭证时，纯发布文案调整只重跑文档、版本和发布内容门禁，否则必须重跑完整回归和 CI；任一修改都使旧确认自动失效
- 用户准确授权“`一键发布版本 vX.Y，包含 <branch-a>、<branch-b>...`”时，统一入口可从双远端一致的 `master` 创建 `codex/release-vX.Y` 聚合分支，按声明顺序合入显式列出的干净分支并验证每个分支记录的 GUI 实测提交；不得自动纳入未列出的分支。各分支测试与最终聚合测试都必须通过，冲突、来源不明、实测提交变化或组合回归失败立即停止，不自动解决冲突
- 只有用户在候选 PR、CI 和最终内容展示完成后准确授权“`确认发布 vX.Y`”，才允许核验内容与候选 tree 凭证、Squash 合并、同步 GitHub/Gitee `master`、触发双平台构建、创建不可变 tag、公开 GitHub/Gitee Release、同步 `latest.json`、完成线上验收并在全部成功后清理已授权分支。正式发布失败或中断时必须保留本地和远端候选分支；候选 tree、版本、标题、正文、PR head、目标分支或测试凭证变化后旧确认自动失效；确认不覆盖 rebase、force push、冲突处理、移动公开 tag 或删除 worktree
- `release_flow.py` 是正常发布的唯一用户入口；`release_prepare.py`、`release_delivery.py`、`pr_delivery.py` 和 `release_dispatch.py` 仅作为确定性底层与故障恢复入口。任一阶段失败必须保留可恢复状态；发布状态必须记录 Actions run、每阶段开始/结束/耗时/尝试次数和脱敏错误。续跑时先验证已完成阶段的后置条件，满足则跳过，不能从头重复所有远端操作；GitHub 已公开后不得回滚，只能幂等续跑 Gitee、清单同步和公开验收
- 发布准备 PR 合并前执行 `/neat-freak`、文案润色和风险相关实测；授权后由工作流重跑严格门禁并核验公开下载、自动更新和双远端状态
- 已公开 tag 不得移动或覆盖，修复必须发布更高补丁版本；同一提交允许断点续跑
- `candidates_all.json`、本地 API 配置、Chrome profile 和登录状态不属于任务临时文件，禁止收尾时自动清理

### 打包发布

#### 版本号规范（必须遵守）

- **格式**：大版本 `X.Y`（如 v2.9），补丁版本 `X.Y.Z`（如 v2.8.12）。**禁止** `X.Y.0`
- **更新位置**（必须同步）：
  1. `gui_main.py` 的 `__version__`（不带 `v` 前缀，如 `__version__ = "2.9"`）
  2. `CHANGELOG.md` 新版本标题（`## vX.Y — 标题`），含分类：新增功能/体验优化/问题修复（至少一个）
  3. `README.md` 顶部版本标识 + 版本历史段落（只保留最近 2-3 个版本，更早版本由 CHANGELOG.md 承载）+ gui_main.py 注释
  4. `CLAUDE.md` 和 `AGENTS.md` 项目结构中的 gui_main.py 注释
- **版本内容写作规范**（必须遵守，详见 memory/readme-style.md）：
  - 目标：简洁专业、对普通用户友好（不是大白话，避免过度通俗化）；只描述用户得到的功能和体验变化，不展开实现过程
  - 范围基准：必须以“上一公开 tag → 目标发布提交”的最终净变化为准，逐项核对提交和变更文件；每项用户可感知变化要么写入版本内容，要么明确判定为合并表述或不面向用户，避免遗漏
  - 功能无关内容禁止进入版本内容：版本号同步、测试、内部重构、打包、CI/CD、发布编排、双远端同步、门禁和开发过程说明一律不写
  - 本版本开发过程中引入并在发布前修正的布局、校验、提示或回归问题属于开发收尾，不得包装为“体验优化”或“问题修复”；只有相对上一公开版本新增的独立用户能力可以保留
  - **保留**：用户日常接触（AI、API、API Key、浏览器、Chrome、Excel、配置文件、JSON、智能体、大模型）+ 行业通用词（参数、持久化、覆盖率、解析、过滤、字段、格式）
  - **禁止**：变量名 / 函数名 / 字段名（反引号标识）、纯内部机制（正则 / keyring / DPI / sha256 / locale-data / listener / srcdoc）、开发者黑话（OR/AND 条件、provider+base_url、阶段 1.6、闸门解耦、风控面）
  - 避免自造怪词（如把「参数」翻成「联系凭证」反而更不专业）
  - 分类基准：问题修复仅指上一公开版本已经存在、用户能够感知、且有代码差异或复现证据支持的缺陷；证据不足时不写成修复
  - `build.py --check` 自动扫描规则 4（STYLE_KEYWORDS + 反引号）；`--strict-changelog` 升级为硬门禁
- 发布前 `build.py --check` 验证一致性

#### 发布命令与门禁

- `python build.py --check [--strict-changelog]`：仅发布前检查；严格模式将 CHANGELOG 启发式覆盖、README 逐条镜像和 latest.json 同步提示升级为硬失败
- `python scripts/release_flow.py --version X.Y --notes-file <file> --execute --authorization="一键发布版本 vX.Y"` 准备单分支发布候选并停在内容确认；多分支增加重复的 `--branch <codex/...>` 并使用包含显式分支列表的授权文本。用户确认后由脚本内部续跑 `--confirm --authorization="确认发布 vX.Y"`，内容凭证不要求用户复制。底层 `release_prepare.py`、`release_delivery.py`、`pr_delivery.py` 与 `release_dispatch.py` 仅保留为分阶段恢复入口
- `python build.py`：自动打包；`--sync-release-notes` 仅用于公开后恢复同步，要求干净且双远端一致的 `master`、已推送且与 CHANGELOG 一致的 `latest.json`，不再自动修改或提交本地文件
- `python build.py --verify-release X.Y.Z`：只读核验双远端分支/tag、GitHub/Gitee Release、附件完整性和 latest.json，不打包不推送
- 发布前必须执行 `/neat-freak` 并润色 CHANGELOG 当前版本段落；`gui_main.py` 的 `__version__` 是唯一版本号来源
- `.build_state.json` 指纹未变时复用产物，`--force-build` 强制重建；Windows 使用 `--onefile --noconsole`，macOS 使用 `--onedir --windowed`
- `_preflight_checks()` 验证依赖、敏感文件、源码编译、文档同步和回归测试；依赖变更须同步 `build.py:REQUIRED_IMPORTS`
- 本地 `build.py --release` 已停用，`--ci --release` 仅供 Actions 构建任务使用；同名 tag 只能在指向同一发布提交时断点续跑，不得移动或 `--force`
- CHANGELOG 硬门禁包括当前版本、分类顺序、README 入口、历史完整性、源码编译和回归测试；正反向覆盖等启发式检查默认提示、严格模式阻断

#### 打包体积优化（当前 Windows 约 36.4MB，macOS ZIP/DMG 约 31-33MB）

- **PIL**：精确 `--hidden-import` 仅收集 Image/ImageDraw/ImageTk，排除 `_avif`/`_webp`
- **babel locale-data**：自定义 hook（`pyinstaller-hooks/hook-babel.py`）排除全部 1086 个 locale .dat，按需添加 9 个（zh/en 系列）
- **排除模块**：保留 `scipy`、`lxml.objectify` 等无运行期入口模块；`pandas` 不再是直接打包依赖，Excel 导出保持 `openpyxl` 直写；`numpy`/`numpy.libs` 仅为 openpyxl 可选支持和环境残留，打包时应排除；**不要排除** `sqlite3`（DataRecorder/DrissionPage 顶层依赖）、`lxml.html`（DrissionPage 顶层依赖）
- **体积判断**：Windows 使用 `--onefile` 单文件 EXE，通常比 macOS `--onedir` 后的 ZIP/DMG 大；不要用 macOS 32MB 反推 Windows 也必须接近 32MB。当前 Windows EXE 约 36.4MB、macOS ZIP/DMG 约 31-33MB 属正常范围。
- 修改 build.py 时注意保持上述优化，避免体积回退
- **CI 双平台构建**：首次发布并行构建 Windows 和 macOS；断点续跑仅在附件完整性可验证时复用，macOS 必须同时有 ZIP 和 DMG

## 代码规范

- 使用 type hints
- 关键函数写 docstring
- 异常处理要具体，不要裸 except；核心模块用 `except Exception:` 兜底，scripts/ 逐步收敛中

## 敏感信息

- .env 文件不进 git
- 候选人数据含个人隐私，本地存储要加密
- API Key 加密存储在系统钥匙串（Windows DPAPI / macOS Keychain），`api_config.json` / `api_config.local.json` 不含明文
- API Key 按 provider + base_url 组合存储，同一服务商不同接入方式（API / Token Plan）独立管理

## 核心逻辑

### 候选人联系机制

- 按钮位于 `operate-side` 区域，文本："继续沟通"（已匹配）、"立即沟通"（新候选人）
- 过滤规则：只过滤「当前岗位已匹配且打过招呼」的候选人；中断时兜底保存
- 打招呼等级：`--greet-level strong`（仅 ≥75）或 `normal`（默认，≥65）
- 智能滚动定位 `_find_card_by_scroll()` 三阶段搜索；沟通上限检测 `_detect_limit_popup()`
- 列表页点击后由 `verify_greeting_success()` 确认按钮变为“继续沟通”或出现明确成功标记；无法确认时返回待核实，不落盘为已沟通，连续出现时暂停发送
- 沟通上限只接受明确耗尽文案，或可见升级弹窗中的“升级动作 + 次数语境”组合；“今日剩余 N 次”不能单独判定为耗尽
- **联系清单**：GUI 的所有联系动作统一进入“联系候选人”工作台，不提供绕过清单的立即发送入口。加入前过滤已沟通、硬性条件待确认、低于 55 分、人工反馈为误推/放弃或状态不适合发送的候选人，并给出跳过汇总。55-64 分的普通待定候选人允许人工复核通过后加入联系清单；AI 评估调用失败只表示辅助评估未完成，不作为待复核或联系阻断条件，规则分和资格已通过的候选人仍可进入自动或手动联系清单。人工复核通过与联系批准必须持久化并在发送前复核时继续有效。工作台支持暂停、移除、失败重试，以及对待核实记录明确确认已发送或未发送
- **持久化与恢复**：`contact_queue.json` 只保存候选人/岗位身份和清单意图，不复制候选人资料；程序重启后恢复待发送、发送失败和待核实项目。上次退出时处于发送中的项目必须恢复为待核实，禁止自动重发
- **发送前复核**：每次真正发送前重新读取 `candidates_all.json` 并复核最新状态；已沟通、已屏蔽、待人工确认或已不符合联系条件的候选人必须跳过。候选人的复核、反馈、跟进或屏蔽状态改变后，活动联系清单必须立即同步为待发送、待核实、已发送或已跳过，不能保留失效的可发送按钮。无 `greet_context` 的候选人只能在对应岗位推荐页发送；BOSS 岗位名称与本地配置不一致时必须列出双方名称并等待用户确认，确认对应关系无误后才可继续，未确认则保留待发送
- 命令行 `--greet` 和 `--re-greet` 虽保留直接联系行为，但候选人资格必须复用 GUI 的统一联系门禁；点对点姓名也不能绕过已沟通、待核实、待复核、淘汰、屏蔽、误推、放弃或已结束跟进等状态

### 运行控制闭环

- 运行前先执行岗位配置体检：错误必须阻断，警告由用户确认；BOSS 当前岗位与所选配置不一致时必须明确确认后才能继续。
- GUI 需要区分未登录、非推荐页、无已发布职位和页面无候选人；无已发布职位或无候选人时跳过卡片选择器检查，不能误报为选择器异常。
- GUI 运行控制只允许“仅保存筛选结果”“将强烈推荐加入联系清单”“将推荐及以上加入联系清单”；扫描阶段不得直接发送。只有正常完成的扫描可以按所选策略自动生成联系清单，中断或异常时仅保存已取得结果。
- 运行控制页“选择岗位”默认使用最近一次运行时选择的具体岗位；新建并保存岗位后应自动成为运行页默认岗位。“全部岗位”只作为用户手动选择项，不作为有岗位时的首次默认值。
- 运行控制页提供折叠的“高级扫描设置”：用“扫描增强 / 自动补全候选人详情 / 最多读取”表达 API 直调补全，用“后续联系 / 扫描后准备联系信息 / 最多准备”表达打招呼上下文准备；提示文案强调这些设置会增加访问频率、需谨慎调高，避免在用户界面暴露“结构化补全、补抓、上下文”等实现词。
- 联系工作台开始发送前必须展示已就绪人数和依赖岗位页面人数并等待确认；待核实项目不参与自动重发。
- 运行结束、停止、中断和异常都要生成可读的本轮结果摘要；摘要最少 3 行、最多 10 行，超出后内部滚动，日志只保留过程细节和一行终态。达到滚动轮次上限属于本轮正常结束，整体状态显示成功，但摘要必须用黄色“未确认扫描到底”提示范围不确定性。

### 停止机制

- StopRequested 异常 + threading.Event 穿透所有关键循环；停止时自动保存进度并导出 Excel

### 打招呼上下文持久化（greet_context）

- 阶段 1.6 在筛选完成后，从候选人详情页 API（`/wapi/zpjob/view/geek/info`）捕获 `jid/lid/securityId/expectId`，存为 `greet_context` 字段
- GUI 手动打招呼时优先用 `send_greeting_with_context()` 直发 `/wapi/zpjob/chat/start`，失败回退 `send_greeting_on_list_page()`（列表按钮路径）
- 阶段 1.6 仅对 `match_score >= GREET_CONTEXT_MIN_SCORE (65)` 且未打过招呼的候选人抓取，单轮硬上限 `GREET_CONTEXT_CAPTURE_LIMIT (15)` 人
- `qualification_status == "manual_review"` 的候选人**不跳过**上下文采集，但禁止自动打招呼；跨会话/去重合并时保留 `greet_context` 字段

### 浏览器自动检测

- 运行页每 2 秒轮询 Chrome 连接状态；手动检测时自动启动 Chrome（动态端口 + 独立 profile，保留登录态）
- `_browser_check_running` 互斥标志防重复启动；端口预检防止自动启动
- 页面整体刷新或局部重绘造成的临时上下文失效应等待后重试，不能记为选择器失败；Chrome 关闭或页面连接断开应直接转为浏览器未连接状态，不能弹出选择器异常
- 联系工作台在发送确认前完成浏览器预检：优先复用现有连接，无可用连接时自动启动独立 Chrome 并打开推荐牛人页；登录未完成时持续等待并显示准备状态，浏览器与页面就绪后才允许用户确认发送

### 反爬对抗

- **随机延迟**：`_human_delay(center, spread)` 所有 sleep 带随机抖动
- **验证码检测**：`_detect_captcha()` 关键词 + CSS 选择器检测，暂停等待用户完成验证（5 分钟超时）；无论验证是否完成，本轮都停止后续自动访问
- **BOSS 访问熔断**：所有推荐页 Document、XHR/fetch、岗位身份、详情读取和联系入口共用会话级冷却。HTTP/业务码返回 401/403/408/412/418/423/425/428/429/503、未知自定义 4xx，或响应正文/跳转页面出现登录失效、安全验证、操作频繁等信号时，立即停止本轮后续 BOSS 访问；优先采用 `Retry-After`，缺失时默认冷却 15 分钟。保留已取得的 DOM 候选人，继续本地规则筛选、AI 评估、保存和 Excel 导出，但禁止详情补抓、自动联系、后续岗位访问和自动生成联系清单。普通请求错误类 4xx 不触发全局冷却，但立即停止当前 API 补全或联系批次
- **API 读取限速**：API 直调默认约 3-7 秒随机间隔；单次最多读取 `API_CANDIDATE_LIMIT_DEFAULT`（默认 160，对应最多补全 8 页）人，达到上限停止继续翻页
- **打招呼限速**：每 `GREET_BATCH_SIZE` 人暂停随机间隔；每轮上限 `AUTO_GREET_RUN_LIMIT`（默认 50）

> **重要架构约束**：候选人集合必须以推荐页 DOM 滚动提取结果为准。Listener 和 API 直调可以补全结构化字段，但只能增强已经在 DOM 中出现、且 `geek_id` 一致的候选人，不能把接口额外返回的人直接加入筛选结果或自动联系范围。`srcdoc` iframe 无法稳定提供岗位 URL，因此接口分页地址优先来自 listener 捕获结果，缺失时再尝试页面身份信息。

### 去重机制

- 基于 `(geek_id, job_name)` 复合键去重；`first_seen_at` 保持首次发现时间，`last_evaluated_at` 决定最新评估结果，旧数据从 `batch_timestamp` 兼容迁移；人工反馈、跟进、黑名单和沟通状态始终合并保留
- 重复扫描时，本轮已评估但未通过的候选人必须退出活跃结果和统计；只有曾经推荐、AI 淘汰或存在用户业务历史的记录保留供复核，不得继续沿用旧高分
- `storage.py:save_candidates_all()` 使用 O(n) 算法；`bossmaster.py` 保留同名导入兼容旧调用

### 保存策略

- 正常流程：规则筛选和 AI 评估完成后分别建立恢复点；异常中断时保存已完成的候选人结果
- 普通待定：无人工/AI 状态的 55-64 分候选人只保留岗位最近一次完整扫描快照；部分或中断扫描不得清理旧快照
- 淘汰记录：普通首次规则淘汰只计入运行摘要、不持久化；曾经推荐、AI 淘汰或存在用户业务历史时保留同一候选人/岗位的最新结果
- 原子性写入：`.tmp` + `os.replace()`；备份恢复：`.bak` 自动回退
- GUI 的岗位、日期和结果范围只允许过滤展示数据；编辑、简历评估和保存始终基于完整候选人数据集，禁止用当前可见子集覆盖 `candidates_all.json`；结果页“更多操作”中的 Excel 导出属于展示操作，必须严格导出当前表格可见集合

### 候选人提取

候选人提取使用 **DOM 滚动提取**（`_extract_cards_batch()`），通过滚动页面逐批加载候选人卡片并解析 DOM 结构。提取流程：

1. 滚动页面触发懒加载
2. 等待新卡片渲染
3. 批量提取当前可见的所有卡片
4. 去重合并到候选人列表
5. 重复直到触底或达到轮次上限

> **为什么仍以 DOM 为准？** Listener/API 返回结果可能与虚拟列表当前已渲染卡片不同步。系统因此先由 DOM 建立唯一候选人集合，再按 `geek_id` 合并 listener/API 的经验、年龄、薪资、城市等结构化字段；接口中未出现在 DOM 的候选人一律忽略。

`filter_candidate()` 接受可选 `structured_fields` 参数，优先使用结构化值，fallback 到正则文本解析。薪资正则 `[kK]?` 末尾 K 可选，兼容 "15-25" 无后缀格式。

DOM 主扫描每次滚动后默认随机等待约 1.5-4 秒，每滚动 5-10 轮额外暂停约 8-15 秒；参数集中在 `DOM_SCROLL_DELAY_*` / `DOM_SCROLL_BATCH_*`，不要在滚动循环内重新硬编码等待时间。

API 兜底翻页连续 3 页无 DOM 命中时提前停止，避免无效请求浪费 API 配额。

### 滚动提前终止

三策略：`atBottom` 标记、文本匹配"到底"/"没有更多"、连续 5 轮无新候选人兜底。批量提取：`_extract_cards_batch()` 单次 JS 提取所有卡片

### 评分体系

- 四维模型：`基础25 + 技能(0~50) + 经验超额(0~15) + 学历档次(0~10)`（参数定义在 `constants.py`）
- 英文关键词用 `\b` 单词边界匹配，避免子串误匹配
- 推荐等级：>=75 强烈推荐, >=65 推荐, >=55 待定
- 淘汰原因排序：学历→经验→年龄→地点→薪资→评分→其他
- 硬条件检查顺序：学历→经验→年龄→地点→薪资→必要条件→技术关键词
- 评分输出：`score_breakdown`（各项分拆）、`score_explanation`（文本解释）、`keyword_evidence`（命中证据含原文片段）
- 人工反馈：`feedback_status`（合适/误推/误杀/放弃）、`feedback_reasons`（结构化原因列表）、`feedback_note`、`feedback_updated_at`；去重时保留反馈字段。对没有硬性条件待确认的普通待定候选人，“合适”视为人工判断已完成并允许加入联系清单；“误推”和“放弃”必须阻断联系。“误杀”只记录规则反馈，不自动推翻淘汰结论
- 跟进状态：`followup_status`（未沟通/已打招呼/已回复/待约面/已约面/不合适/已归档）、`followup_note`、`followup_updated_at`、`next_followup_at`；打招呼成功、待约面和已约面默认安排次日处理，已回复立即处理，不合适/已归档清除提醒；“已回复/待约面/已约面”意味着已经发生沟通，必须同步已打招呼事实；将已沟通候选人改回“未沟通”属于事实纠正，必须再次确认并清除打招呼时间、方式、待核实状态和跟进日期；加入黑名单时结束活跃跟进并清除提醒；去重时按最新跟进时间整组保留
- 黑名单：`blacklisted`、`blacklist_reason`、`blacklisted_at`；按 `geek_id` 跨岗位屏蔽，后续扫描、统计和默认 Excel 导出跳过，清空候选人时保留；结果页主动显示黑名单后导出当前表格时允许保留，并在 Excel 标明屏蔽状态
- 打招呼上下文：`greet_context`、`greet_context_updated_at`；去重时保留（高分新记录覆盖其他字段时不丢失上下文）
- 资格审查：`qualification_status`（`qualified` / `rejected` / `manual_review`）、`qualification_reasons`、`qualification_evidence`；去重时保留。规则筛选输出初始状态，AI 硬条件复核可升级为 `rejected`
### AI 辅助评估

- 对 ≥55 分候选人 LLM 二次评估，按规则评分降序处理，调整分 ±15 叠加规则评分并重算推荐等级；默认并发 5 路 + 429 限流退避，不再限制 50 人
- **AI 响应超时**：`api_config.json` 的 `llm_read_timeout` 字段，GUI 运行控制页可调（步长 10s）；连接超时固定 10 秒；默认值按服务商自动区分（官方 API 60s，中转服务 120s）
- **AI 硬条件复核**：LLM 评估同时检查硬条件（学历、经验、年龄、薪资、地点、求职状态），返回结论和原文证据；高置信度淘汰发现经规则二次验证（`_validated_hard_failures()`）后执行淘汰，证据不足或低置信度转 `manual_review`
- **简历二次评估**：导入候选人简历（PDF/Word/TXT/MD/RTF/HTML）后，基于完整简历做第二轮 LLM 评估（±15），有简历时替代一次评估调整值：`final = rule_score + resume_adjustment`（不累加 llm_adjustment）；一次评估的硬条件复核结论保留；GUI 支持导入简历、撤回评估；Excel 新增"简历评估"和"简历评估理由"列

### 必要条件

- 三种模式：简单匹配（子串）、OR（任一）、AND（全部），全角逗号自动归一化
- 底层 `check_required_condition()` 支持字符串和 JSON 格式

### 薪资范围筛选

- 候选人期望最低薪资 >= 岗位薪资上限 + 1K → 过滤；面议或缺失时跳过

### 工作地点筛选

- 候选人城市匹配岗位配置，支持多地点（`/`、`、` 分隔），空时不启用

### 数据统计看板

- 按岗位聚合，4 张汇总卡片 + 明细 Treeview；只统计 ≥55 分；支持时间范围过滤
- 明细 Treeview 9 列精简展示：岗位名称、筛选分布（总数+强推/推荐/待定）、已打招呼(率)、已反馈、合适率、误推率、已回复(率)、已约面(率)、平均分
- 合适率/误推率只按有效人工反馈计算（合适/误推/误杀/放弃）；已回复/已约面列内嵌百分比（按已打招呼及后续状态计算）
- 明细 Treeview 与汇总卡片共用同一套日期过滤逻辑（`_get_result_date_filter`），口径一致
- 岗位明细支持岗位级复盘，汇总结构化反馈原因、误推/误杀原因和建议调整方向；反馈样本不受 55 分通过线限制，低分误杀必须纳入；少于 5 条反馈只报告样本不足和已观察原因，不输出趋势性调参结论；复盘只给建议，不自动修改岗位配置

### 页面选择器配置（selectors.json）

- 所有 DOM 交互选择器集中配置，带 `{geek_id}` 占位符；浏览器连接后自动健康检查

### 筛选结果表

- 表格宽度 <1250px 显示 8 列；≥1250px 显示 11 列（+学历/年龄/求职状态）；≥1700px 显示 13 列（+学校/公司），列宽按比例分配
- 结果表不静默截断候选人；显示当前数量/筛选后总数，并支持“推荐候选人 / 复核通过 / 待复核 / 淘汰记录 / 全部记录”切换。“推荐候选人”要求评分 ≥65、资格未淘汰且不存在未解决的人工复核；“复核通过”包含所有曾因学历等硬条件证据不足或 55-64 分待定而进入待复核、随后由人工确认通过的候选人，与推荐范围允许重叠。AI 评估调用失败不进入待复核，只在 AI 评估列和候选人详情中展示失败状态。发送待核实属于沟通状态，仍保留原筛选和复核分类，但禁止再次加入联系清单
- 筛选结论、复核状态、人工联系批准和沟通状态必须独立派生：分数/资格决定推荐、待定或淘汰，尚未解决的硬条件证据不足和 55-64 分待定决定是否待复核，人工可明确选择复核通过或复核不通过；联系批准只决定普通待定候选人能否联系且不得修改评分或推荐指数，发送与跟进字段只决定沟通状态。低于 55 分本身是淘汰结论，不得再次生成“评分待复核”；若其硬条件待确认已复核通过，则保留复核通过记录但筛选结论仍为淘汰。放弃、误推、不合适、归档或屏蔽会结束未完成复核，不得继续出现在待复核任务中。AI 评估调用失败不是复核原因或联系阻断条件。结果范围只过滤表格，顶部统计固定使用当前岗位和日期范围
- 时间范围默认“全部时间”，预设近 7 天和近 30 天按包含今天的自然日计算；只有选择“自定义”才显示起止日期，不设置独立重置按钮
- 状态列显示多段业务标记（跟进状态/复核状态/反馈/屏蔽），不暴露内部发送能力；待复核状态的 tooltip 补充具体复核原因，复核通过状态说明复核结论但不改变评分或推荐指数，是否可联系仍由沟通、反馈和屏蔽状态独立决定；学校和公司列 tooltip 显示完整内容
- 结果页把“今日待办、查看与复核、联系候选人”作为连续工作流入口，三个按钮使用一致样式；状态体检、导出和清空收入“更多操作”。结果表有候选人时复核入口保持可用，未选择具体行时自动从当前结果第一位开始；双击候选人或使用同名右键菜单也可打开连续复核工作台。今日待办按候选人唯一归组且不静默截断，先按立即处理/已逾期/今天/待安排/以后区分时间，再按业务动作归组；发送待核实优先于放弃、屏蔽等结束状态，必须先人工确认实际发送结果；未知枚举或缺失分数进入状态异常待处理；未来任务不计入今日人数，缺时间的旧记录进入待安排，已约面不得因缺少日期而从待办消失。“待复核”必须复用结果范围的统一派生结论，并按每人的主要原因唯一归入一层业务子分类。任务分组和文案只描述用户可执行的业务动作，不暴露发送上下文等内部概念。今日待办和状态体检的候选人右键菜单必须使用同一复核、核实、快捷跟进和加入联系清单规则。发送待核实的候选人在右键菜单和复核工作台都必须提供“核实发送结果”入口。首屏显示下一步、判断依据、匹配证据和风险，完整资料保留原详情，并提供上一位/下一位及确认通过、确认不通过、跟进、反馈、简历和 AI 评估动作
- 所有候选人修改和移除操作使用 `(geek_id, job_name)` 复合身份；缺少 `geek_id` 的旧记录只能展示，不得用姓名、分数或空标识执行持久化修改
- **多选右键菜单**：支持 Ctrl/Shift 多选候选人，右键显示批量操作：加入联系清单、移除选中、导出选中；单选时显示完整菜单（查看与复核、导入简历、加入联系清单、核实发送结果、更新跟进、标记反馈、加入/移出黑名单、移除此人），不提供单人 Excel 导出。所有联系动作统一先进入联系清单，不提供绕过清单的立即发送入口

## AI 模型配置

### 支持的服务商

通义千问 (Qwen)、DeepSeek、月之暗面 (Kimi)、智谱 (Zhipu)、MiniMax、小米 (Xiaomi)、阶跃星辰 (StepFun)、OpenAI、Anthropic (Claude)、自定义 (Custom)

### 配置管理

- `api_config.json` 是发布默认模板；源码运行时用户配置写入 ignored 的 `api_config.local.json`，避免模型列表刷新和本机模型切换污染发布 diff
- API Key 加密存储在系统钥匙串，配置文件只保存 provider/base_url/model 等非密钥信息
- 服务类型、显示名称、并发数和默认超时统一复用 `ai_adapter.classify_api_endpoint()`：结合服务商身份与官方文档登记域名判断，官方 API 及 Token Plan、Coding Plan、Step Plan 等官方套餐入口仍属于官方服务；未知域名或服务商与域名不匹配时才按中转/自定义服务处理，禁止只按模型名或 URL 包含词判断
- 支持动态获取模型列表、保存模型库、在“使用中的模型”中显式选择默认 AI 模型、测试连接（并行双策略）
- 新电脑部署：首次启动检测 API Key 缺失并引导重新配置

### 模型列表搜索与新增检测

- 选择模型对话框内置搜索框；`fetched_models` 字段存储上次列表，对比找出新增模型（绿色高亮 + 弹窗提醒）和下线模型（弹窗提醒）
- 对话框支持 EXTENDED 多选（Ctrl+点击切换、Shift+点击范围、Ctrl+A 全选）；右键菜单可批量测试连通性
- 连通性测试多线程并行，识别常见业务错误（未开通/配额超限/免费额度用完）给出人性化提示
### 学历核验模型独立配置

- 系统设置的“使用中的模型”可显式选择学历核验模型；未指定时跟随默认 AI 模型
- `api_config.local.json` / 打包后的 `api_config.json` 的 `education_model_ref` 字段存储指定模型（`{api_provider, base_url, model}`），未设置时回退默认 AI 模型
- 独立学历证书核验助手固定使用 `token-plan.cn-beijing.maas.aliyuncs.com` 的 `kimi-k2.6`
- 学信网验证支持多选证书并为每人创建独立标签页；系统填写姓名和证书编号、识别图片验证码并提交，识别失败时转人工输入或重试，手机扫码和最终结果确认始终由 HR 完成
- 正在作为默认 AI 模型或学历核验模型使用的已保存模型，需先在“使用中的模型”中切换后才能删除
## 自动更新

- 启动时延迟 12 秒检查（updater 模块延迟加载避免阻塞冷启动），**自适应冷却**（发现新版本 24h / 无更新 4h / 失败 15min 指数退避）；Gitee 优先 → GitHub fallback（Gitee "无更新"时 GitHub 复核防漏报）
- **Gitee 源**（8s 超时，超时后立即重试一次）：`latest.json`；**GitHub 源**（10s 超时）：GitHub Releases API；启动静默检查中 Gitee 短暂失败但 fallback 成功时不打印报错式提示
- 下载链接：`latest.json` 的 `downloads_cn` 优先（国内快）；弹窗支持「立即更新」和「稍后提醒」
- **Windows**：下载 EXE → 校验 SHA256 → `update.bat` 替换重启；脚本须清理 `_PYI_*` 环境变量 + `PYINSTALLER_RESET_ENVIRONMENT=1` 防 DLL 缺失
- **macOS**：.app 运行→下载 ZIP 替换重启；源码→`git pull`
- `latest.json` 的 `assets` 记录产物 `size`/`sha256` 供校验
- **Gitee Release 上传**：Actions 只暂存 GitHub Draft 和双平台产物；本机 `release_dispatch.py` 核对 GitHub 附件 size/SHA256 元数据后立即公开 GitHub 主源，再删除其他历史版本的 Gitee 附件（保留 Release 页面和 tag），并按 EXE→ZIP→DMG 下载、校验和串行镜像本次版本。清理或任一附件失败立即中止，重跑时复用阶段凭证、已验证附件和成功 Actions 构建
- **Gitee 完整性校验**：发布主流程只校验附件齐全和 size 与 GitHub 一致，不回下载大文件；需要逐文件 SHA256 时手动运行 `python build.py --verify-gitee-integrity X.Y.Z`
- **Gitee Token**：只从本机环境变量读取 `GITEE_TOKEN`（需 projects 权限）；Actions 不保存、不读取该 Token，也禁止上传 Gitee 大文件
- **发布验收复用**：GitHub/Gitee/`latest.json` 的完整远端验收每轮正式发布只执行一次并保存验收凭证；后续收尾只核对凭证对应的远端提交，不得立即重复整套公开验收
- **Actions 失败恢复**：GitHub 暂存失败时优先复用同版本、同发布提交的成功构建产物或重跑失败 job，不得因 Stage 失败自动重建已经成功的双平台安装包
- **公开资源验收**：下载地址必须核对状态码、最终地址、文件名、响应类型、Content-Length 和可识别文件头；双远端在线 `latest.json` 必须与本机规范化完整内容一致，不能只比较版本号
## 低频专项说明

低频踩坑、平台差异和专项背景放在 `.agent/notes.md`。这是项目级稳定说明，可以进 git；不要把会话记忆、临时调试日志或自动生成的 agent 记忆放进去。
