# BOSS 简历筛选器 - 打包部署指南

## 跨平台支持

| 平台 | 输出格式 | 用途 |
|------|---------|------|
| Windows | `BOSS_ResumeFilter.exe` | 单文件可执行程序 |
| Windows | `EducationCertificateTool.exe` | 独立学历证书核验助手 |
| macOS | `BOSS_ResumeFilter.app` | 应用包 |
| macOS | `BOSS_ResumeFilter.dmg` | 安装包（用户拖拽安装） |
| macOS | `BOSS_ResumeFilter_mac.zip` | 自动更新用 |

`build.py` 自动检测当前平台，无需额外参数。

独立学历核验助手在本机复用仓库隔离的 `pack_venv` 打包环境，不使用系统 Python 或 Anaconda 环境直接打包：

```powershell
.\pack_venv\Scripts\python.exe build_education_tool.py --check
.\pack_venv\Scripts\python.exe build_education_tool.py
```

GitHub Actions 没有本机 `pack_venv`，Windows 正式发布任务会在临时的 `actions/setup-python` 环境安装锁定的构建依赖，并使用受环境校验保护的 `build_education_tool.py --ci`。本机不能用 `--ci` 绕过隔离打包环境。

**体积基线（v2.11）**：Windows 使用 `--onefile` 单文件 EXE，macOS 使用 `--onedir` 生成 `.app` 后再压缩为 ZIP/DMG。两者压缩结构和平台运行库不同，Windows EXE 约 36.4MB、macOS ZIP/DMG 约 31-33MB 属正常范围；不要把 macOS 安装包较小误判为缺依赖或未重建。

### 正式发布（Actions 构建 + 本机镜像）

正常发布的唯一用户入口是本机 `scripts/release_flow.py`。它把开发分支提交、版本材料、候选 PR、CI 等待、内容确认、正式发布、线上验收和已发布分支清理串成一个可恢复状态机；唯一正常停点是最终标题和正文确认。候选准备时会记录候选新增历史中已经包含的 `codex/*` 阶段分支并展示清理名单；验收成功后若当前工作区仍在候选分支且没有其他 `master` worktree，会自动切回 `master` 再继续正式发布。`release_prepare.py`、`release_delivery.py`、`pr_delivery.py` 与 `release_dispatch.py` 仅保留为分阶段恢复入口。

Windows 项目代码通过根目录 `subprocess_utils.py` 统一控制子进程：发布和测试从现有控制台运行时，Git、Python、Ruff、PyInstaller、GitHub CLI 及其认证、Git、telemetry 后代全部继承同一终端，不再创建新窗口；打包后的无控制台 GUI 启动 WMIC、taskkill、更新器等命令时才使用 `CREATE_NO_WINDOW`。stdout/stderr 仍由父进程回放，Chrome 等确需显示的图形程序显式放行。禁止为发布 CLI 使用 `CREATE_NEW_CONSOLE`，Windows Terminal 可能把它作为新窗口激活到前台。调用 `release_flow.py` 的外层编排器仍须使用 `windowsHide`、`Start-Process -WindowStyle Hidden` 或等价机制；仓库内脚本无法隐藏调用者在启动前已经创建的 PowerShell/终端窗口。

```bash
# 准备单分支候选 PR，等待 CI 后展示最终版本内容并停止
python scripts/release_flow.py --version 2.24 \
  --notes-file "<项目目录外的发布说明文件>" \
  --execute \
  --authorization="一键发布版本 v2.24"

# 用户确认已展示内容后，自动合并并发布到完整验收成功
python scripts/release_flow.py --version 2.24 \
  --confirm \
  --approved-content-sha="<由编排器从预览结果后台传入>" \
  --authorization="确认发布 v2.24"
```

确定性发布规则仍统一放在 `scripts/release_ci.py`，不是两套实现；`.github/workflows/release.yml` 只运行其中的 GitHub 暂存阶段，本机驱动器运行 Gitee 镜像和最终发布阶段。不要把 Actions 暂存任务成功误判为正式版本已经公开。

**自动流程：**

1. 确认事件来源为手动触发、分支为 `master`、授权文本与版本完全一致。
2. 锁定不可变的发布提交，执行 `build.py --check --strict-changelog` 等价的完整严格门禁。
3. Windows 和 macOS 独立并行构建，任一构建失败都不进入发布任务。
4. 两端产物齐全后创建不可移动的 GitHub tag，建立 GitHub Draft Release，上传并校验两个 Windows EXE、macOS ZIP 和 DMG；Actions 到此结束。
5. 本机核对 GitHub Draft 的四个附件均具备 Actions 已验证的 size 和 SHA256 元数据，随后立即将 GitHub Draft Release 转为正式版本；不再等待本机重复下载同一批附件后才公开主源。
6. Gitee tag 就绪后自动删除其他历史版本的附件，仅保留 Release 页面和 tag；随后本机按 BOSS EXE→学历核验 EXE→ZIP→DMG 从 GitHub 下载并校验，每个附件校验完成后立即进入 Gitee 单路直连上传队列，使已下载附件的上传与下一个 GitHub 代理下载重叠。2026-08-13 使用 v2.28.1 三个真实产物交叉实测 1/2/3 路上传，两轮平均分别为 115.300s、113.985s、114.096s，总吞吐差异约 1%，并发没有实质收益，因此正式流程保持单路上传。GitHub Actions 禁止上传 Gitee 大文件。清理或上传中断不回退已经公开的 GitHub 主发布，同一命令按逐附件阶段凭证续跑。
7. Gitee Release 附件齐全且 size 与 GitHub 一致后，本机生成 `latest.json` 的双源下载地址和 SHA256，提交并推送到 GitHub/Gitee `master`。
8. 只读核验双远端分支/tag/Release/附件/清单，并实际请求八个公开下载地址和两份在线清单。

发布状态写入 `.release_state.json`，记录 Actions run、每个阶段的开始/结束时间、耗时、尝试次数、每个附件的 GitHub 下载与 Gitee 上传状态和脱敏错误；同一份可读时间线追加到 `logs/release-vX.Y.log`。正式发布失败时保留候选分支，只有最终线上验收通过后才清理。相同产品代码指纹已经通过回归时，纯发布文案调整复用该测试证据；代码、依赖、测试或构建输入变化时自动恢复完整回归。

**断点续跑：**`.release_flow_state.json` 记录候选分支、PR、候选提交、tree、内容摘要和当前阶段，`.release_state.json` 记录正式发布阶段以及逐附件下载、上传进度；两者都不保存 Token。候选内容变化后重新执行准备命令会更新同一 PR、重跑 CI 并产生新摘要。GitHub Draft 的本版本附件已经完整时，确认命令重跑会跳过 Actions，直接从本机 Gitee 镜像阶段继续；本机下载使用 `.part` 文件续传，已上传且尺寸一致的 Gitee 附件直接复用。已存在的同提交 tag 不重建，同名 tag 指向其他提交时立即中止。

**停滞与重试：**GitHub Release 和 Actions 状态查询遇到瞬态网络失败会按上限重试。Actions 连续 30 分钟没有 job/step 阶段变化时，本机停止等待并保留远端任务，排查后可用同一版本安全续跑。发布暂存 job 只安装 `requirements-release.txt`，双平台构建使用 `requirements-build.txt` 作为独立 pip 缓存键，避免每次重新下载完整发布依赖。

**Dry Run：**将 `dry_run` 设为 `true` 时只执行授权校验和严格门禁，不构建、不创建 tag、不推送、不发布。

**多分支聚合：**重复传入 `--branch` 显式声明纳入顺序，并为每个分支传入与手工 GUI 实测一致的 `--tested-branch branch=commit_sha`。每个分支必须有独立且干净的 worktree；脚本先在各自目录运行稳定回归和导入烟测，再从同步的 `master` 创建 `codex/release-vX.Y`，逐个合入并对组合结果重新执行完整门禁。冲突、实测 SHA 变化、分支独立测试或组合测试失败都立即停止。候选提交历史中已包含且不属于发布前 `master` 的其他本地 `codex/*` 分支会自动进入清理计划，但不会自动成为新的合并输入。公开验收后，脚本仅在记录的分支头未变化、远端分支缺失或仍指向同一提交、关联 worktree 干净时自动解除占用并删除本地及已有远端分支；worktree 目录本身始终保留。示例授权：`一键发布版本 v2.24，包含 codex/a、codex/b`。

**凭据配置：**Actions 只使用 `GITHUB_TOKEN` 创建 tag、Draft Release 和上传附件，不保存也不使用 `GITEE_TOKEN`。正式发布前，本机必须提供具有 Gitee projects 权限的 `GITEE_TOKEN`；缺失或 Gitee API 不可达时会在触发 Actions 前停止。本地 `build.py --release` 仍停用，正常发布统一从 `release_flow.py` 进入。

Release 页面最终包含：

- `BOSS_ResumeFilter.exe` — Windows 用户
- `EducationCertificateTool.exe` — 只使用学历核验功能的 Windows 用户
- `BOSS_ResumeFilter.dmg` — macOS 用户（手动安装）
- `BOSS_ResumeFilter_mac.zip` — macOS 自动更新用

**完整性校验：**发布主流程校验 Gitee 附件齐全且 size 与 GitHub 一致，避免重复回下载全部大文件。需要逐文件 SHA256 审计时运行 `python build.py --verify-gitee-integrity X.Y.Z`。

`latest.json` 字段说明：
- `downloads`：GitHub 下载链接（国际）
- `downloads_cn`：Gitee 下载链接（国内优先，`updater.py` 优先使用此字段）
- `assets`：产物元数据（`size` 和 `sha256`），v2.32 起记录两个 Windows EXE、macOS ZIP 和 DMG；BOSS 自动更新仍只使用主程序 EXE 和 macOS ZIP

---

## 方案一：Windows 打包（单文件 EXE）

### 1. 环境准备

#### 开发机（打包用）
- Windows 10/11
- Python 3.9+（推荐 3.11）
- pip 包管理器

#### 目标机（运行用）
- Windows 10/11
- Chrome 浏览器（必需）
- 无需安装 Python

### 2. 打包步骤

#### 步骤 1：安装打包工具

```bash
# 进入项目目录
cd boss-resume-filter

# 安装打包工具
pip install pyinstaller

# 安装项目依赖
pip install -r requirements.txt
```

#### 步骤 2：执行打包

```bash
# 仅执行发布前检查：不打包、不提交、不推送
python build.py --check

# 严格发布文案检查：将 CHANGELOG 启发式覆盖、README 逐条镜像、latest.json 同步也作为硬门禁
python build.py --check --strict-changelog

# 使用自动打包脚本（推荐）
python build.py

# 单分支：准备候选 PR、等待 CI、展示最终内容
python scripts/release_flow.py --version 2.24 --notes-file "<项目目录外的发布说明文件>" --execute --authorization "一键发布版本 v2.24"

# 多分支：每个分支都必须带匹配其 HEAD 的 GUI 实测凭证
python scripts/release_flow.py --version 2.24 --notes-file "<项目目录外的发布说明文件>" --branch codex/a --branch codex/b --tested-branch "codex/a=<sha-a>" --tested-branch "codex/b=<sha-b>" --execute --authorization "一键发布版本 v2.24，包含 codex/a、codex/b"

# 内容确认后自动合并、构建、双源发布、清单同步和线上验收
python scripts/release_flow.py --version 2.24 --confirm --approved-content-sha "<由编排器后台传入>" --authorization "确认发布 v2.24"

# 发布完成后只读核验 GitHub/Gitee、附件和 latest.json
python build.py --verify-release 2.5

# 或手动打包（不推荐，缺少依赖检查和 PIL 完整收集）
pyinstaller --onefile --noconsole \
    --collect-all PIL \
    --collect-submodules tkinter \
    --hidden-import=tkinter \
    --hidden-import=tkinter.ttk \
    --hidden-import=tkinter.font \
    --hidden-import=tkinter.filedialog \
    --hidden-import=tkinter.messagebox \
    --name "BOSS_ResumeFilter" \
    gui_main.py
```

`--check` 会验证：

- 核心依赖可导入
- `.storage/` 未被 Git 跟踪
- `.env`、`candidates_all.json`、`candidates_all.xlsx` 未被 Git 跟踪
- `api_config.json` 不含明文 `api_key` / `api_key_ref`
- 核心源码可通过 `py_compile`
- `python tests/run_unit_tests.py` 通过
- `python tests/test_import.py` 通过
- 工作区干净

发布候选提交还必须按 `tests/gui_browser_acceptance_matrix.json` 留存 GUI 实测凭证：

- Windows 当前机器先运行 `python tests/manual/run_gui_acceptance_matrix.py`，确认 7 个页面及结果列策略通过
- 1080P 最大化、4K 非最大化、macOS Retina 分别在对应显示环境做视觉验收
- 真实 Chrome 验收未连接、登录页、推荐页加载中、无已发布职位、无候选人和正常候选人页面
- 自动化矩阵只能证明状态逻辑和列策略，不能代替对应系统、DPI、字体和真实 BOSS 页面上的最终视觉/连接检查

`release_flow.py` 要求开发修改已经由 Codex 完成语义审查和提交；确定性脚本不自行猜测哪些脏文件属于本次任务，也不自动合并未显式声明的兄弟分支。它在当前单分支或显式多分支聚合结果上同步版本材料、运行严格门禁、推送候选 PR 并等待 CI，然后停在内容确认，同时展示公开验收后将自动清理的候选分支和候选历史内阶段分支。内容调整继续使用同一 PR；确认前不得合并，确认后才连续执行正式发布。底层脚本保留用于失败后的分阶段恢复。

Release 标题和说明必须先写在 `CHANGELOG.md` 对应版本段落中。`scripts/release_ci.py` 会自动提取该段落作为 GitHub/Gitee Release 内容；如果缺少对应版本，或未按以下顺序分类，发布会直接中断：

- 新增功能
- 体验优化
- 问题修复

版本内容以“上一公开 tag → 目标发布提交”的最终净变化为准。编制时必须逐项核对预览输出中的提交和变更文件，确认所有用户可感知变化已经写入或合并表述，没有遗漏。版本号、测试、内部重构、打包、CI/CD、发布编排、双远端同步和门禁等功能无关内容不得列入；本版本开发过程中引入并在发布前修正的问题也不得拆成独立优化或修复项。文案只说明用户得到的变化，保持简洁专业，不展开技术实现。

默认 `python build.py --check` 只把确定性发布契约作为硬门禁；CHANGELOG 条目质量、正反向关键词覆盖、README 与 CHANGELOG 逐条一致、latest.json release_notes 同步属于提示项。需要把这些提示也升级为硬门禁时，显式增加 `--strict-changelog`。

如果打包环境来自 Anaconda，`build.py` 会自动定位并打包：

- `Lib/tkinter`
- `DLLs/_tkinter.pyd`
- `Library/lib/tcl8.6`
- `Library/lib/tk8.6`（打包到 EXE 内部的 `tcl/tk8.6`）
- `Library/bin/tcl86t.dll`
- `Library/bin/tk86t.dll`

不要绕过 `build.py` 直接手写 PyInstaller 命令，否则容易生成启动时报 `No module named 'tkinter'` 的 EXE。

#### 步骤 3：获取输出

打包完成后，`dist/` 目录下会生成：

```
dist/
├── BOSS_ResumeFilter.exe   <-- 主程序
├── job_config.json        <-- 岗位配置
├── selectors.json         <-- 页面选择器配置（DOM 变化时可直接编辑）
└── README.md             <-- 说明文档
```

CHANGELOG.md 通过 `--add-data` 嵌入 EXE 内部（PyInstaller 解压到 `_MEIPASS`），无需单独分发。

### 3. 部署到目标电脑

**注意：首次在新电脑部署需要重新配置 API Key。**
详细步骤见 [DEPLOYMENT.md](DEPLOYMENT.md)。

#### 方式 A：复制文件夹（推荐）

1. 将 `dist/` 目录复制到目标电脑任意位置
2. 双击 `BOSS_ResumeFilter.exe` 运行

#### 方式 B：创建快捷方式

```bash
# 在桌面创建快捷方式（手动操作）
1. 右键 BOSS_ResumeFilter.exe
2. 发送到 -> 桌面快捷方式
```

### 4. 首次运行配置

#### 步骤 1：配置岗位规则

编辑 `job_config.json`：

```json
{
    "job_requirements": {
        "高级 Java 工程师": {
            "min_exp": 5,
            "edu": "本科",
            "keywords": ["Java", "Spring Boot", "MySQL", "Redis"],
            "required_conditions": ["统招本科", {"type": "or", "items": ["activiti", "camunda"]}]
        }
    }
}
```

#### 步骤 2：配置 AI 模型（可选）

```
1. 打开程序 -> 系统设置
2. 选择服务商（qwen/deepseek/kimi 等）
3. 输入 API Key 和 Base URL
4. 测试连接 -> 保存配置
```

#### 步骤 3：开始使用

```
1. 确保 Chrome 浏览器已安装
2. 登录 BOSS 直聘网站
3. 导航到推荐页面
4. 在程序中选择岗位 -> 点击"开始"
```

### 5. 常见问题

#### Q1: 打包后 EXE 文件太大？

解决方案：使用 UPX 压缩

```bash
# 下载 UPX
# https://github.com/upx/upx/releases

# 使用 UPX 压缩
upx --best "dist/BOSS_ResumeFilter.exe"
```

#### Q2: 目标电脑提示缺少 DLL？

原因：某些依赖未正确打包

解决方案：

```bash
# 使用 --collect-all 指定完整收集
pyinstaller --onefile --noconsole \
    --collect-all PIL \
    --collect-submodules tkinter \
    --hidden-import=tkinter \
    --hidden-import=tkinter.ttk \
    --hidden-import=tkinter.font \
    --hidden-import=tkinter.filedialog \
    --hidden-import=tkinter.messagebox \
    --name "BOSS_ResumeFilter" \
    gui_main.py
```

#### Q3: 配置文件路径问题？

确保 `job_config.json` 与 EXE 在同一目录：

```
BOSS_ResumeFilter.exe
job_config.json
```

#### Q4: 如何更新到新版本？

```bash
# 1. 备份旧数据
cp candidates_all.json candidates_all.json.bak

# 2. 替换 EXE 文件
# 3. 保留 job_config.json 和 candidates_all.json
```

### 6. 高级选项

#### 打包带图标

```bash
pyinstaller --onefile --noconsole \
    --icon=app.ico \
    --name "BOSS_ResumeFilter" \
    gui_main.py
```

#### 打包调试版本（带控制台）

```bash
pyinstaller --onefile --console \
    --name "BOSS_ResumeFilter_debug" \
    gui_main.py
```

#### 多文件模式（启动更快）

```bash
pyinstaller --onedir --noconsole \
    --name "BOSS_ResumeFilter" \
    gui_main.py
```

### 7. 依赖清单

打包时会自动包含以下依赖：

| 依赖 | 用途 |
|------|------|
| tkinter | GUI 框架 |
| DrissionPage | 浏览器自动化 |
| requests | HTTP 请求 |
| openpyxl | Excel 导出（直写，不依赖 pandas） |
| Pillow | 图标绘制（PIL.ImageDraw） |
| keyring | API Key 加密存储 |
| python-dotenv | 环境变量管理 |
| tkcalendar | 日期选择控件（筛选结果日期过滤） |

### 8. 最小化部署

如果目标电脑已有 Python，可以直接复制源码运行：

```bash
# 1. 复制源码到目标电脑
cp -r boss-resume-filter/ D:/

# 2. 安装依赖
cd D:/boss-resume-filter
pip install -r requirements.txt

# 3. 运行
python gui_main.py
```

---

## 方案二：macOS 打包（.app + DMG）

### 1. 环境准备

- macOS 10.15+
- Python 3.10+（推荐 Homebrew 安装）
- PyInstaller（`pip install pyinstaller`）

### 2. 打包步骤

```bash
# 创建虚拟环境（推荐）
python3 -m venv pack_venv
source pack_venv/bin/activate
pip install -r requirements.txt pyinstaller

# 执行打包（自动检测 macOS 平台）
python3 build.py

# 正式发布由 GitHub Actions 双平台构建，不在 Mac 本地执行
python3 scripts/release_dispatch.py --version 2.22 --execute --authorization="确认正式发布 v2.22" --approved-content-sha="<sha256>"
```

### 3. 输出文件

```
dist/
├── BOSS_ResumeFilter.app         ← 应用包（双击运行）
├── BOSS_ResumeFilter.dmg         ← 安装包（拖拽到 Applications）
├── BOSS_ResumeFilter_mac.zip     ← 自动更新用
├── job_config.json
├── selectors.json
└── README.md
```

### 4. 分发方式

**方式 A：DMG 安装包（推荐用户安装）**
1. 用户下载 `BOSS_ResumeFilter.dmg`
2. 双击打开 DMG
3. 拖拽 .app 到 Applications 文件夹
4. 首次运行需右键 → 打开（绕过 Gatekeeper）

**方式 B：自动更新（已安装用户）**
- 程序启动时自动检查 GitHub Release
- 发现新版本后下载 ZIP 并自动替换 .app
- 替换完成后自动重启应用

### 5. macOS 特殊说明

- **Tcl/Tk 收集**：macOS 上 Homebrew Python 的 Tcl/Tk 由 PyInstaller 自动收集，无需手动指定
- **分隔符差异**：`--add-data` 参数在 macOS 使用 `:` 分隔，Windows 使用 `;`（`build.py` 已自动处理）
- **PyInstaller 模式**：macOS 使用 `--onedir --windowed`（生成 .app bundle），Windows 使用 `--onefile --noconsole --runtime-tmpdir %LOCALAPPDATA%`（规避企业电脑 `%TEMP%` 策略限制）
- **DMG 图标布局**：`hdiutil create` 无法控制图标位置，Finder AppleScript 在 macOS 13+ 不稳定，使用 `dmgbuild` Python 库生成 DMG

### 6. Gatekeeper 与签名

未签名的 .app 首次运行时会被 macOS Gatekeeper 拦截：

**用户解决方法：**
1. 右键点击 .app → 选择「打开」
2. 在弹出的对话框中再次点击「打开」

**开发者签名（可选）：**
- 需要 Apple Developer 账号（$99/年）
- 使用 `codesign` 签名 + `notarize` 公证
- 签名后用户无需右键打开

---

**最后更新**: 2026-05-25
