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
├── gui_main.py           # 图形界面主程序（v2.21）
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
├── build.py              # PyInstaller 打包脚本（支持 --release 一键发布）
├── build_education_tool.py # 独立学历证书核验助手打包脚本
├── latest.json           # 双源版本清单（正式发布工作流自动维护）
├── job_config.json       # 岗位筛选规则配置
├── api_config.json       # 发布默认 AI 模型配置模板（不含明文 Key）
├── selectors.json        # 页面选择器配置（CSS/XPath/关键词，DOM 变化时修改）
├── ui_config.json        # UI 尺寸与缩放配置
├── tests/                # 测试脚本目录
├── scripts/              # 辅助脚本（发布监控、PPT 生成、截图等）
│   ├── release_ci.py   # GitHub Actions 正式发布编排与线上验收
│   ├── pr_delivery.py # 普通 PR 一次授权交付（门禁、PR、合并、双远端同步、分支清理）
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
- 用户明确说“正式发布 vX.Y”后，该一次授权覆盖 `Build & Release` 内部的严格门禁、tag/清单推送、GitHub/Gitee Release 和线上验收，不再逐步确认
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
  - 目标：简洁专业、对普通用户友好（不是大白话，避免过度通俗化）
  - **保留**：用户日常接触（AI、API、API Key、浏览器、Chrome、Excel、配置文件、JSON、智能体、大模型）+ 行业通用词（参数、持久化、覆盖率、解析、过滤、字段、格式）
  - **禁止**：变量名 / 函数名 / 字段名（反引号标识）、纯内部机制（正则 / keyring / DPI / sha256 / locale-data / listener / srcdoc）、开发者黑话（OR/AND 条件、provider+base_url、阶段 1.6、闸门解耦、风控面）
  - 避免自造怪词（如把「参数」翻成「联系凭证」反而更不专业）
  - 分类基准：问题修复仅指上一版本已存在、用户可感知、非本次开发引入的缺陷
  - `build.py --check` 自动扫描规则 4（STYLE_KEYWORDS + 反引号）；`--strict-changelog` 升级为硬门禁
- 发布前 `build.py --check` 验证一致性

#### 发布命令与门禁

- `python build.py --check [--strict-changelog]`：仅发布前检查；严格模式将 CHANGELOG 启发式覆盖、README 逐条镜像和 latest.json 同步提示升级为硬失败
- `python build.py --sync-release-notes`：修正 CHANGELOG 后同步 GitHub + Gitee Release 说明，不重新打包
- `python build.py`：自动打包（Windows EXE / macOS .app+ZIP+DMG），`IS_MAC`/`IS_WIN` 自动检测
- `gh workflow run release.yml --ref master -f version=X.Y -f authorization="正式发布 vX.Y" -f dry_run=false`：唯一正式发布入口；严格门禁→双平台构建→双 Release→latest.json→线上验收
- `python build.py --verify-release X.Y.Z`：只读核验双远端分支/tag、GitHub/Gitee Release、附件完整性和 latest.json，不打包不推送
- 发布前必须执行 `/neat-freak` 并润色 CHANGELOG 当前版本段落；`gui_main.py` 的 `__version__` 是唯一版本号来源
- `.build_state.json` 指纹未变时复用产物，`--force-build` 强制重建；Windows 使用 `--onefile --noconsole`，macOS 使用 `--onedir --windowed`
- `_preflight_checks()` 验证依赖、敏感文件、源码编译、文档同步和回归测试；依赖变更须同步 `build.py:REQUIRED_IMPORTS`
- 本地 `build.py --release` 已停用，`--ci --release` 仅供 Actions 构建任务使用；同名 tag 只能在指向同一发布提交时断点续跑，不得移动或 `--force`
- CHANGELOG 硬门禁包括当前版本、分类顺序、README 入口、历史完整性、源码编译和回归测试；正反向覆盖等启发式检查默认提示、严格模式阻断

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
- **联系清单**：GUI 的所有联系动作统一进入“联系候选人”工作台，不提供绕过清单的立即发送入口。加入前过滤已沟通、需人工确认、分数不足或状态不适合发送的候选人，并给出跳过汇总。工作台支持暂停、移除、失败重试，以及对待核实记录明确确认已发送或未发送
- **持久化与恢复**：`contact_queue.json` 只保存候选人/岗位身份和清单意图，不复制候选人资料；程序重启后恢复待发送、发送失败和待核实项目。上次退出时处于发送中的项目必须恢复为待核实，禁止自动重发
- **发送前复核**：每次真正发送前重新读取 `candidates_all.json` 并复核最新状态；已沟通、已屏蔽、待人工确认或已不符合联系条件的候选人必须跳过。无 `greet_context` 的候选人只能在对应岗位推荐页发送，岗位不一致时保留待发送，不得跨岗位定位
- 命令行 `--greet` 保留筛选后直接联系的既有行为；上述联系清单闭环专用于 GUI

### 运行控制闭环

- 运行前先执行岗位配置体检：错误必须阻断，警告由用户确认；BOSS 当前岗位与所选配置不一致时必须明确确认后才能继续。
- GUI 需要区分未登录、非推荐页、无已发布职位和页面无候选人；无已发布职位或无候选人时跳过卡片选择器检查，不能误报为选择器异常。
- GUI 运行控制只允许“仅保存筛选结果”“将强烈推荐加入联系清单”“将推荐及以上加入联系清单”；扫描阶段不得直接发送。只有正常完成的扫描可以按所选策略自动生成联系清单，中断或异常时仅保存已取得结果。
- 联系工作台开始发送前必须展示已就绪人数和依赖岗位页面人数并等待确认；待核实项目不参与自动重发。
- 运行结束、停止、中断和异常都要生成可读的本轮结果摘要；摘要最少 3 行、最多 10 行，超出后内部滚动，日志只保留过程细节和一行终态。达到滚动轮次上限属于本轮正常结束，整体状态显示成功，但摘要必须用黄色“未确认扫描到底”提示范围不确定性。

### 停止机制

- StopRequested 异常 + threading.Event 穿透所有关键循环；停止时自动保存进度并导出 Excel

### 打招呼上下文持久化（greet_context）

- 阶段 1.6 在筛选完成后，从候选人详情页 API（`/wapi/zpjob/view/geek/info`）捕获 `jid/lid/securityId/expectId`，存为 `greet_context` 字段
- GUI 手动打招呼时优先用 `send_greeting_with_context()` 直发 `/wapi/zpjob/chat/start`，失败回退 `send_greeting_on_list_page()`（列表按钮路径）
- 阶段 1.6 仅对 `match_score >= GREET_CONTEXT_MIN_SCORE (55)` 且未打过招呼的候选人抓取，单轮硬上限 `GREET_CONTEXT_CAPTURE_LIMIT (30)` 人
- `qualification_status == "manual_review"` 的候选人**不跳过**上下文采集，但禁止自动打招呼；跨会话/去重合并时保留 `greet_context` 字段

### 浏览器自动检测

- 运行页每 2 秒轮询 Chrome 连接状态；手动检测时自动启动 Chrome（动态端口 + 独立 profile，保留登录态）
- `_browser_check_running` 互斥标志防重复启动；端口预检防止自动启动
- 页面整体刷新或局部重绘造成的临时上下文失效应等待后重试，不能记为选择器失败；Chrome 关闭或页面连接断开应直接转为浏览器未连接状态，不能弹出选择器异常
- 联系工作台在发送确认前完成浏览器预检：优先复用现有连接，无可用连接时自动启动独立 Chrome 并打开推荐牛人页；登录未完成时持续等待并显示准备状态，浏览器与页面就绪后才允许用户确认发送

### 反爬对抗

- **随机延迟**：`_human_delay(center, spread)` 所有 sleep 带随机抖动
- **验证码检测**：`_detect_captcha()` 关键词 + CSS 选择器检测，暂停等待用户完成验证（5 分钟超时）
- **API 熔断**：`ApiRiskBlocked` 异常，BOSS API 返回 403/412/429 时立即停止扫描，不降级 DOM
- **API 读取限速**：API 直调默认约 2-4 秒随机间隔；单次最多读取 `API_CANDIDATE_LIMIT_DEFAULT`（默认 400，对应最多补全 20 页）人，达到上限停止继续翻页
- **打招呼限速**：每 `GREET_BATCH_SIZE` 人暂停随机间隔；每轮上限 `AUTO_GREET_RUN_LIMIT`（默认 50）

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

候选人提取使用 **DOM 滚动提取**（`_extract_cards_batch()`），以页面可见候选人为准，接口仅补全已存在候选人的结构化字段。详细架构背景见 `.agent/notes.md`。

`filter_candidate()` 接受可选 `structured_fields` 参数，优先使用结构化值，fallback 到正则文本解析。

### 滚动提前终止

三策略：`atBottom` 标记、文本匹配"到底"/"没有更多"、连续 5 轮无新候选人兜底。批量提取：`_extract_cards_batch()` 单次 JS 提取所有卡片

### 评分体系

- 四维模型：`基础25 + 技能(0~50) + 经验超额(0~15) + 学历档次(0~10)`（参数定义在 `constants.py`）
- 英文关键词用 `\b` 单词边界匹配，避免子串误匹配
- 推荐等级：>=75 强烈推荐, >=65 推荐, >=55 待定
- 淘汰原因排序：学历→经验→年龄→地点→薪资→评分→其他
- 硬条件检查顺序：学历→经验→年龄→地点→薪资→必要条件→技术关键词
- 评分输出：`score_breakdown`（各项分拆）、`score_explanation`（文本解释）、`keyword_evidence`（命中证据含原文片段）
- 人工反馈：`feedback_status`（合适/误推/误杀/放弃）、`feedback_reasons`（结构化原因列表）、`feedback_note`、`feedback_updated_at`；去重时保留反馈字段
- 跟进状态：`followup_status`（未沟通/已打招呼/已回复/待约面/已约面/不合适/已归档）、`followup_note`、`followup_updated_at`；去重时保留
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
- 岗位明细支持岗位级复盘，汇总结构化反馈原因、误推/误杀原因和建议调整方向；复盘只给建议，不自动修改岗位配置

### 页面选择器配置（selectors.json）

- 所有 DOM 交互选择器集中配置，带 `{geek_id}` 占位符；浏览器连接后自动健康检查

### 筛选结果表

- 普通窗口 8 列；最大化显示 11 列（+学历/年龄/求职状态）；表格宽度 ≥1500px 时显示 13 列（+学校/公司），列宽按比例分配
- 结果表不静默截断候选人；显示当前数量/筛选后总数，并支持“推荐候选人 / 待复核 / 淘汰记录 / 全部记录”切换。推荐候选人要求评分 ≥65 且无人工确认或 AI 失败；发送待核实属于沟通状态，仍保留原筛选分类，但禁止再次加入联系清单
- 筛选结论、复核状态和沟通状态必须独立派生：分数/资格决定推荐、待定或淘汰，证据不足和 AI 失败决定是否待复核，发送与跟进字段只决定沟通状态。结果范围只过滤表格，顶部统计固定使用当前岗位和日期范围
- 时间范围默认“全部时间”，预设近 7 天和近 30 天按包含今天的自然日计算；只有选择“自定义”才显示起止日期，不设置独立重置按钮
- 状态列显示多段业务标记（跟进状态/需人工确认/反馈/屏蔽），不暴露内部发送能力；待复核状态的 tooltip 只补充具体复核原因，学校和公司列 tooltip 显示完整内容
- 结果页把“今日待办、查看与复核、联系候选人”作为连续工作流入口，三个按钮使用一致样式；状态体检、导出和清空收入“更多操作”。结果表有候选人时复核入口保持可用，未选择具体行时自动从当前结果第一位开始；双击候选人或使用同名右键菜单也可打开连续复核工作台。今日待办按候选人唯一归组且不静默截断，“待复核”必须复用结果范围的统一派生结论，并按每人的主要原因唯一归入一层业务子分类；其他任务组保持单层。任务分组和文案只描述用户可执行的业务动作，不暴露发送上下文等内部概念。今日待办和状态体检的候选人右键菜单必须使用同一复核、核实和加入联系清单规则。发送待核实的候选人在右键菜单和复核工作台都必须提供“核实发送结果”入口。首屏显示下一步、判断依据、匹配证据和风险，完整资料保留原详情，并提供上一位/下一位及确认通过、跟进、反馈、简历和 AI 评估动作
- 所有候选人修改和移除操作使用 `(geek_id, job_name)` 复合身份；缺少 `geek_id` 的旧记录只能展示，不得用姓名、分数或空标识执行持久化修改
- **多选右键菜单**：支持 Ctrl/Shift 多选候选人，右键显示批量操作：加入联系清单、移除选中、导出选中；单选时显示完整菜单（查看与复核、导入简历、加入联系清单、核实发送结果、更新跟进、标记反馈、加入/移出黑名单、移除此人），不提供单人 Excel 导出。所有联系动作统一先进入联系清单，不提供绕过清单的立即发送入口

## AI 模型配置

### 支持的服务商

通义千问 (Qwen)、DeepSeek、Kimi (月之暗面)、智谱 (Zhipu)、MiniMax、小米 (Xiaomi)、阶跃星辰 (StepFun)、OpenAI、Anthropic (Claude)、自定义 (Custom)

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
- Gitee Release 上传/校验细节见 `.agent/notes.md`
## 低频专项说明

低频踩坑、平台差异和专项背景放在 `.agent/notes.md`。这是项目级稳定说明，可以进 git；不要把会话记忆、临时调试日志或自动生成的 agent 记忆放进去。
