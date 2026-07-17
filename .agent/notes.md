# Agent Notes

本文件存放低频但重要的项目级工程背景。它是项目手册的一部分，可以进 git；不要把会话记忆、临时调试日志或自动生成的 agent 记忆放在这里。

## GUI / Tk / DPI

### macOS .app 路径解析与首次配置

`sys.executable` 在 .app 中指向 `.app/Contents/MacOS/BOSS_ResumeFilter`，配置文件在 .app 旁边，需向上追溯 3 层。DMG 只含 .app 和 Applications 快捷方式，配置文件不在 DMG 中，首次启动时从 `sys._MEIPASS` 复制。Windows EXE 直接用 `sys.executable.parent`。路径逻辑统一在 `paths.py:get_base_dir()` 中维护。

### Tk 对话框 `wait_window()` 嵌套事件循环崩溃

`wait_window()` 在 `root.after()` 回调中创建嵌套事件循环，macOS 上与 Cocoa scroll hook 和浏览器轮询冲突导致崩溃。正确做法是用 `grab_set()` 实现模态（不阻塞主事件循环），`protocol("WM_DELETE_WINDOW")` + `_close_dialog()` 清理引用。`self.root.update()` 也有重入风险，应移除。

### Windows DPI 缩放（System DPI Aware 方案）

保持 System DPI Aware，启动时调用 `_enable_high_dpi_awareness()`，优先用 `SetProcessDPIAware()` / `SetProcessDpiAwareness(1)`，避免 Windows 对 Tk 窗口做位图缩放导致字体模糊。不要启用 Per-Monitor DPI V2；Tk 8.6 在 V2 下坐标和布局容易错乱。

`_resolve_display_scale()` 同时兼容两种环境：System DPI Aware 下 Tk 已报告真实 DPI，优先使用 `root.tk.call('tk', 'scaling')` 推导的 DPI；DPI Unaware 或异常回退时，用 `EnumDisplaySettingsW(None, -1)` 获取物理像素宽度，与 Tk 虚拟屏幕宽度比值计算真实 `display_scale`。布局/间距/图标/窗口/rowheight 统一使用 `dpi_scale × zoom_factor`，字体使用 `font_scale`。macOS 不受 Windows DPI 感知设置影响。

### macOS Tk 8.6 字体物理像素减半

Apple Silicon 报告 DPI 72，Intel Mac venv 报告 96（系统 Tk 8.5 报告 144 不受影响）。阈值 `< 80` 区分需补偿环境：`self.font_boost = 1.65 if (sys.platform == 'darwin' and self.root.winfo_fpixels('1i') < 80) else 1.0`，然后 `self.font_scale = self.dpi_scale * self.zoom_factor * self.font_boost`。`font_scale` 仅用于字体，布局/间距/图标/窗口/rowheight 仍用 `dpi_scale × zoom_factor`。

### 字体常量与 Combobox 规范

- `FONT_FAMILY`/`FONT_FAMILY_SEMIBOLD` 跨平台字体常量（Windows: Microsoft YaHei UI, macOS: PingFang SC, Linux: Helvetica）
- 7 个字体变量：`font_title`(28pt) / `font_section`(16pt) / `font_label`(13pt) / `font_stat`(36pt) / `font_stat_label`(15pt) / `font_log`(11pt) / `font_table`(12pt)
- `font_scale`（含 font_boost）用于字体；`dpi_scale × zoom_factor` 用于布局/间距/图标/rowheight
- Combobox 下拉列表字体：`option_add('*TCombobox*Listbox.font', font, 80)`；所有 Combobox 禁用滚轮：`bind_class('TCombobox', '<MouseWheel>', lambda e: 'break')`

### macOS aqua 主题 ttk 控件灰色背景

macOS aqua 的 ttk 控件默认背景是 `systemWindowBackgroundColor`（灰色），三层原因：

1. `ttk.LabelFrame` 灰色：`Labelframe.border` 硬编码灰色，`style.configure` 无效。解决方案：用 `_create_card()` 替代。
2. `ttk.Label` 灰色：`style.configure('TLabel', background=self.colors['bg_card'])` 解决。
3. 输入框灰色：macOS aqua 忽略 `style.configure` 的 `fieldbackground`，必须用 `style.map`（Combobox `readonly`、Spinbox/Entry `!disabled`）。

架构约定：

- `TFrame` 默认白底（`bg_card`），页面级灰底容器用 `Page.TFrame`（`bg_main`）
- `_create_scroll_container` 的容器 frame 必须加 `style='TFrame'`
- `_create_page_header(parent, title, subtitle=None)` 统一创建页面标题

### 更新弹窗必须使用 GUI 缩放参数

`updater.py` 的 `show_update_dialog()` 接收 `gui` 参数，使用 `gui.font_scale`/`gui.dpi_scale`/`gui.zoom_factor` 计算字体和布局。不能硬编码字号，否则高 DPI 下字体模糊或过小。

## Packaging / Release

### PyInstaller 版本号读取

不能从 `sys._MEIPASS` 读取 `gui_main.py` 源文件，因为源码被编译进 PYZ 归档，文件不存在。应该直接 `import gui_main` 读取模块属性，兼容所有打包模式（源码 / Windows EXE / macOS .app）。

### CHANGELOG 分类原则

三类：新增功能 / 体验优化 / 问题修复。问题修复仅指旧版本已存在且影响用户的 bug，不含当前版本新功能引入的问题。

CHANGELOG 只包含用户可感知的变更，以下内容不应出现：新功能开发过程中的中间 UI 调整（属于新功能本身）、打包脚本/CI/发布流程优化（用户无感知）、当前版本新功能引入的 bug 修复（不算“问题修复”）。`build.py --check` 会自动审查条目质量。

### Gitee Release API 限制

PATCH release 必须带 `tag_name` 和 `body`（只传 `name` 返回 400）。releases 列表不返回附件 ID，删除附件需通过 `GET /releases/{id}/attach_files`。版本号参数需先移除 `v` 前缀（`v2.9` → `2.9`），否则 tag 变成 `vv2.9`。

### CI 模式下 babel locale-data 路径查找

CI 用 `.venv-ci`，本地打包用 `pack_venv`。`build.py` 中 babel locale-data 搜索路径必须同时覆盖两种虚拟环境目录，否则 CI 构建的 Mac 产物缺少 locale .dat，`tkcalendar.DateEntry` 运行时 `FileNotFoundError`。

## Packaging / Build 体积优化

当前 Windows 约 36.4MB，macOS ZIP/DMG 约 31-33MB。

- **PIL**：精确 `--hidden-import` 仅收集 Image/ImageDraw/ImageTk，排除 `_avif`/`_webp`
- **babel locale-data**：自定义 hook（`pyinstaller-hooks/hook-babel.py`）排除全部 1086 个 locale .dat，按需添加 9 个（zh/en 系列）
- **排除模块**：保留 `scipy`、`lxml.objectify` 等无运行期入口模块；`pandas` 不再是直接打包依赖，Excel 导出保持 `openpyxl` 直写；`numpy`/`numpy.libs` 仅为 openpyxl 可选支持和环境残留，打包时应排除；**不要排除** `sqlite3`（DataRecorder/DrissionPage 顶层依赖）、`lxml.html`（DrissionPage 顶层依赖）
- **体积判断**：Windows `--onefile` 单文件 EXE 通常比 macOS `--onedir` 后的 ZIP/DMG 大；不要用 macOS 32MB 反推 Windows。当前 Windows EXE 约 36.4MB、macOS ZIP/DMG 约 31-33MB 属正常范围
- **CI 双平台构建**：首次发布并行构建 Windows 和 macOS；断点续跑仅在附件完整时复用，macOS 必须同时有 ZIP 和 DMG

## Gitee Release 上传与校验

- GitHub Actions 的 Windows 发布任务集中下载 Windows/macOS 产物，按 EXE→ZIP→DMG 串行镜像到 Gitee；任一失败立即停止
- 上传前比较远端附件和产物，同一提交断点续跑时复用已验证附件；上传/下载超时 600s，4xx 不重试
- 发布主流程只校验附件齐全和 size 与 GitHub 一致，不回下载大文件；需要逐文件 SHA256 时手动运行 `python build.py --verify-gitee-integrity X.Y.Z`
- **Gitee Token**：正式发布使用 Actions Repository Secret `GITEE_TOKEN`；本地手工核验/补传使用同名环境变量

## API / BOSS Page

### 候选人提取以页面可见集合为准

推荐页提取统一以 DOM 滚动得到的页面可见候选人为准。接口监听或后置请求只用于补全这些候选人的结构化字段，不能把页面未出现的候选人直接加入结果。刷新前后必须校验岗位标识；岗位变化时停止扫描并提示用户切回目标岗位。

### 运行前页面状态与选择器检查

运行控制要先区分未登录、非推荐页、账号没有已发布职位和正常空列表。账号没有已发布职位或页面明确为空时，卡片选择器检查应标记为跳过而不是失败；否则会把业务前置条件缺失误报成页面结构异常。扫描开始前仍必须校验目标岗位，岗位不一致由 GUI 明确询问用户后再继续。

## AI Provider / Keyring

### provider 显示名称与内部键不一致

GUI `api_provider_var.get()` 返回显示名称（「通义千问」），keyring 存内部键（`qwen`）。调用前必须通过 `DISPLAY_TO_KEY` 映射转换。`get_api_key(provider, base_url)` 按 provider + base_url 组合查找，新 key 找不到时自动回退旧格式（仅 provider）。
