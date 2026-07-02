"""
BOSS 招聘系统操作指南 · 瑞士国际主义风 PPT 生成器
使用 guizang-ppt-skill 的 template-swiss.html + IKB 克莱因蓝主题
输出: docs/ppt-boss/index.html (单文件，浏览器直接打开)
"""
import os, shutil

SKILL = r"C:\Users\yaououzhong\.claude\skills\guizang-ppt-skill"
PROJECT = r"C:\Users\yaououzhong\Work\boss-resume-filter"
TEMPLATE = os.path.join(SKILL, "assets", "template-swiss.html")
OUT_DIR = os.path.join(PROJECT, "docs", "ppt-boss")
OUT_FILE = os.path.join(OUT_DIR, "index.html")

# ════════════════════════════════════════════════════════════
# 15 SLIDES
# ════════════════════════════════════════════════════════════

SLIDES = r"""
<!-- ═══════ P01 · COVER · IKB 满屏 + ASCII 呼吸场 ═══════ -->
<section class="slide accent" data-animate="hero">
  <div class="canvas-card">
    <canvas class="ascii-bg" aria-hidden="true"></canvas>
    <div class="chrome-min">
      <div class="l">BOSS 招聘系统 · 操作指南 v2.9</div>
      <div class="r">FIELD NOTE · 26.06.04 · 01 / 15</div>
    </div>
    <div style="flex:1;padding:0;display:grid;grid-template-rows:auto 1fr auto;gap:2.6vh">
      <div data-anim="kicker" class="t-meta" style="color:rgba(255,255,255,.78);letter-spacing:.22em">OPERATION MANUAL · 图形界面版</div>
      <h1 data-anim="title" style="align-self:center;font-family:var(--sans),var(--sans-zh);font-weight:200;font-size:min(11.6vw,19vh);line-height:.94;letter-spacing:-.025em;color:#fff">BOSS 招聘系统<br/><span style="font-style:italic;font-weight:300">操作指南</span></h1>
      <div data-anim="bottom" style="display:grid;grid-template-rows:auto auto;gap:1.6vh;border-top:1px solid rgba(255,255,255,.22);padding-top:2vh">
        <div data-anim="lead" class="lead" style="max-width:52ch;color:rgba(255,255,255,.86)">岗位配置 · 智能筛选 · AI 评估 · 自动打招呼 · 结果导出<br/>从配置到完成，一份可重复执行的招聘自动化流程。</div>
        <div style="display:flex;justify-content:space-between;align-items:end">
          <div class="t-meta" style="color:rgba(255,255,255,.6)">适用对象 · GUI 用户 · v2.9.2</div>
          <div class="t-meta" style="color:rgba(255,255,255,.6)">→ swipe / arrow keys</div>
        </div>
      </div>
    </div>
  </div>
</section>

<!-- ═══════ P02 · 6 大核心能力 · grid-3 × stat-card ═══════ -->
<section class="slide" data-animate="grid-reveal">
  <div class="canvas-card">
    <div class="chrome-min">
      <div class="l">Section · Core Capabilities</div>
      <div class="r">02 / 15</div>
    </div>
    <div style="flex:1;padding:0;display:grid;grid-template-rows:auto 1fr;gap:3vh">
      <div data-anim="head" style="display:flex;flex-direction:column;gap:1.4vh">
        <div class="t-meta">WHAT IT DOES</div>
        <h2 style="font-family:var(--sans),var(--sans-zh);font-weight:200;font-size:min(6.4vw,11.2vh);line-height:.96;letter-spacing:-.035em">系统能做什么</h2>
        <p class="lead" style="max-width:48ch;color:var(--text-secondary)">把候选人获取、筛选、评分、打招呼和导出串成一个可重复执行的流程。</p>
      </div>
      <div data-anim="grid" class="grid-3" style="gap:2.4vh 2vw;align-content:start">
        <div class="stat-card accent-top">
          <span class="stat-label"><i data-lucide="filter" style="width:1.2vw;height:1.2vw;vertical-align:-.2em;color:var(--accent)"></i> 01 · 规则筛选</span>
          <p class="stat-note">学历 / 经验 / 年龄 / 薪资 / 地点 / 必要条件，六大维度硬条件过滤</p>
        </div>
        <div class="stat-card accent-top">
          <span class="stat-label"><i data-lucide="search" style="width:1.2vw;height:1.2vw;vertical-align:-.2em;color:var(--accent)"></i> 02 · 技能评分</span>
          <p class="stat-note">关键词权重匹配，基础 25 + 技能 50 + 经验 15 + 学历 10，满分 100</p>
        </div>
        <div class="stat-card accent-top">
          <span class="stat-label"><i data-lucide="brain" style="width:1.2vw;height:1.2vw;vertical-align:-.2em;color:var(--accent)"></i> 03 · AI 二次评估</span>
          <p class="stat-note">大模型对通过筛选者做 ±15 分调整，50 人/次，并发 3 路 + 限流退避</p>
        </div>
        <div class="stat-card accent-top">
          <span class="stat-label"><i data-lucide="mouse-pointer-click" style="width:1.2vw;height:1.2vw;vertical-align:-.2em;color:var(--accent)"></i> 04 · 自动滚动提取</span>
          <p class="stat-note">智能滚动定位，批量 JS 提取候选人卡片，连续 5 轮无新数据自动终止</p>
        </div>
        <div class="stat-card accent-top">
          <span class="stat-label"><i data-lucide="send" style="width:1.2vw;height:1.2vw;vertical-align:-.2em;color:var(--accent)"></i> 05 · 自动打招呼</span>
          <p class="stat-note">按推荐等级阈值自动发送沟通消息，支持仅强烈推荐或推荐+强烈推荐</p>
        </div>
        <div class="stat-card accent-top">
          <span class="stat-label"><i data-lucide="file-spreadsheet" style="width:1.2vw;height:1.2vw;vertical-align:-.2em;color:var(--accent)"></i> 06 · 导出统计</span>
          <p class="stat-note">Excel 一键导出 + 按岗位 / 时间范围统计看板，数据驱动决策</p>
        </div>
      </div>
    </div>
  </div>
</section>

<!-- ═══════ P03 · 7 步工作流程 · timeline-h ═══════ -->
<section class="slide" data-animate="timeline-walk">
  <div class="canvas-card">
    <div class="chrome-min">
      <div class="l">Section · Workflow</div>
      <div class="r">03 / 15</div>
    </div>
    <div style="flex:1;padding:0;display:grid;grid-template-rows:auto 1fr;gap:5vh">
      <div data-anim="head" style="display:flex;flex-direction:column;gap:1.4vh">
        <div class="t-meta">END TO END</div>
        <h2 style="font-family:var(--sans),var(--sans-zh);font-weight:200;font-size:min(6.4vw,11.2vh);line-height:.96;letter-spacing:-.035em">工作流程</h2>
        <p class="lead" style="max-width:48ch;color:var(--text-secondary)">从启动到导出，7 步完成完整筛选流程。</p>
      </div>
      <div class="timeline-h" data-anim="axis">
        <span class="tl-h-axis"></span>
        <div class="tl-row">
          <div class="th-node up accent"><span class="yr">STEP</span><span class="dot"></span><div class="label"><span class="name">启动系统</span></div></div>
          <div class="th-node down"><span class="yr">STEP</span><span class="dot"></span><div class="label"><span class="name">配置岗位</span></div></div>
          <div class="th-node up"><span class="yr">STEP</span><span class="dot"></span><div class="label"><span class="name">配置 AI</span></div></div>
          <div class="th-node down accent"><span class="yr">STEP</span><span class="dot"></span><div class="label"><span class="name">连接浏览器</span></div></div>
          <div class="th-node up"><span class="yr">STEP</span><span class="dot"></span><div class="label"><span class="name">运行筛选</span></div></div>
          <div class="th-node down"><span class="yr">STEP</span><span class="dot"></span><div class="label"><span class="name">查看结果</span></div></div>
          <div class="th-node up accent"><span class="yr">STEP</span><span class="dot"></span><div class="label"><span class="name">导出统计</span></div></div>
        </div>
      </div>
    </div>
  </div>
</section>

<!-- ═══════ P04 · 主界面导航 · four-cards ═══════ -->
<section class="slide" data-animate="four-cards">
  <div class="canvas-card">
    <div style="height:3px;background:var(--accent);margin:-5.6vh -5vw 0;width:calc(100% + 10vw)"></div>
    <div class="chrome-min" style="margin-top:2.4vh">
      <div class="l">Section · Navigation</div>
      <div class="r">04 / 15</div>
    </div>
    <div style="flex:1;padding:0;display:grid;grid-template-rows:auto 1fr;gap:3vh">
      <div data-anim="head" style="display:flex;flex-direction:column;gap:1.4vh">
        <div class="t-meta">6 MODULES</div>
        <h2 style="font-family:var(--sans),var(--sans-zh);font-weight:200;font-size:min(6.4vw,11.2vh);line-height:.96;letter-spacing:-.035em">主界面导航</h2>
        <p class="lead" style="max-width:48ch;color:var(--text-secondary)">左侧固定导航栏 + 右侧内容区，6 个功能模块覆盖全流程。</p>
      </div>
      <div data-anim="cards" style="display:grid;grid-template-columns:repeat(3,1fr);gap:2.4vh 2vw;align-content:start">
        <div style="display:flex;flex-direction:column;gap:1vh;padding-top:2vh;border-top:2px solid var(--accent)">
          <div class="t-meta">— 01</div>
          <h3 style="font-family:var(--sans),var(--sans-zh);font-weight:300;font-size:min(2.6vw,4.6vh);line-height:1.1;letter-spacing:-.015em">首页</h3>
          <p style="font-family:var(--sans),var(--sans-zh);font-size:max(16px,.94vw);line-height:1.55;opacity:.78">全局候选人统计 + 快捷入口</p>
        </div>
        <div style="display:flex;flex-direction:column;gap:1vh;padding-top:2vh;border-top:2px solid var(--accent)">
          <div class="t-meta">— 02</div>
          <h3 style="font-family:var(--sans),var(--sans-zh);font-weight:300;font-size:min(2.6vw,4.6vh);line-height:1.1;letter-spacing:-.015em">岗位配置</h3>
          <p style="font-family:var(--sans),var(--sans-zh);font-size:max(16px,.94vw);line-height:1.55;opacity:.78">新建 / 修改 / 导入 / 导出岗位筛选规则</p>
        </div>
        <div style="display:flex;flex-direction:column;gap:1vh;padding-top:2vh;border-top:2px solid var(--accent)">
          <div class="t-meta">— 03</div>
          <h3 style="font-family:var(--sans),var(--sans-zh);font-weight:300;font-size:min(2.6vw,4.6vh);line-height:1.1;letter-spacing:-.015em">运行控制</h3>
          <p style="font-family:var(--sans),var(--sans-zh);font-size:max(16px,.94vw);line-height:1.55;opacity:.78">连接浏览器 + 选择岗位 + 运行参数</p>
        </div>
        <div style="display:flex;flex-direction:column;gap:1vh;padding-top:2vh;border-top:2px solid var(--accent)">
          <div class="t-meta">— 04</div>
          <h3 style="font-family:var(--sans),var(--sans-zh);font-weight:300;font-size:min(2.6vw,4.6vh);line-height:1.1;letter-spacing:-.015em">筛选结果</h3>
          <p style="font-family:var(--sans),var(--sans-zh);font-size:max(16px,.94vw);line-height:1.55;opacity:.78">候选人列表 + 右键操作 + 导出</p>
        </div>
        <div style="display:flex;flex-direction:column;gap:1vh;padding-top:2vh;border-top:2px solid var(--accent)">
          <div class="t-meta">— 05</div>
          <h3 style="font-family:var(--sans),var(--sans-zh);font-weight:300;font-size:min(2.6vw,4.6vh);line-height:1.1;letter-spacing:-.015em">数据统计</h3>
          <p style="font-family:var(--sans),var(--sans-zh);font-size:max(16px,.94vw);line-height:1.55;opacity:.78">按岗位 / 时间范围统计筛选结果</p>
        </div>
        <div style="display:flex;flex-direction:column;gap:1vh;padding-top:2vh;border-top:2px solid var(--accent)">
          <div class="t-meta">— 06</div>
          <h3 style="font-family:var(--sans),var(--sans-zh);font-weight:300;font-size:min(2.6vw,4.6vh);line-height:1.1;letter-spacing:-.015em">系统设置</h3>
          <p style="font-family:var(--sans),var(--sans-zh);font-size:max(16px,.94vw);line-height:1.55;opacity:.78">AI 服务商 + API Key + 模型列表</p>
        </div>
      </div>
    </div>
  </div>
</section>

<!-- ═══════ P05 · 岗位配置 · image-hero ═══════ -->
<section class="slide light" data-animate="image-hero">
  <div class="canvas-card" style="padding:0;display:flex;flex-direction:column;overflow:hidden">
    <div data-anim="img" style="position:relative;flex:0 0 55%;overflow:hidden;background:var(--grey-1)">
      <img src="images/05-job-config.png" alt="岗位配置界面" loading="eager"
           style="position:absolute;inset:0;width:100%;height:100%;object-fit:cover;object-position:center center">
      <div class="chrome-min" style="position:absolute;top:0;left:0;right:0;color:rgba(255,255,255,.9);padding:5.6vh 5vw 0">
        <div class="l">Section · Job Configuration</div>
        <div class="r">05 / 15</div>
      </div>
      <div data-anim="title-block" style="position:absolute;left:5vw;top:11vh;background:var(--paper);padding:3.2vh 3.2vw;max-width:36vw">
        <div style="font-family:var(--sans),var(--sans-zh);font-weight:200;font-size:min(5.2vw,9vh);line-height:1;letter-spacing:-.035em;color:var(--text-primary)">
          岗位<br>配置
        </div>
      </div>
    </div>
    <div data-anim="kpi" class="image-hero-body">
      <div style="max-width:48ch;font-family:var(--sans),var(--sans-zh);font-size:max(15px,1.3vw);line-height:1.55;font-weight:300;color:var(--text-primary);letter-spacing:-.005em">
        岗位规则直接决定候选人是否被淘汰。保存前必须人工检查——尤其是必要条件、薪资范围和工作地点。
      </div>
      <div class="image-hero-stats" style="gap:3vw">
        <div style="display:flex;flex-direction:column;gap:.6vh"><div style="height:1px;background:var(--ink)"></div><div class="t-meta">最低学历</div><div style="font-family:var(--sans),var(--sans-zh);font-weight:300;font-size:min(2.2vw,3.8vh);line-height:1.2;color:var(--text-primary)">低于即淘汰</div><div style="height:1px;background:var(--border-subtle);margin-top:auto"></div><p class="body-sm">硬条件过滤</p></div>
        <div style="display:flex;flex-direction:column;gap:.6vh"><div style="height:1px;background:var(--ink)"></div><div class="t-meta">技能权重</div><div style="font-family:var(--sans);font-weight:250;font-size:min(4.6vw,7.6vh);line-height:.95;letter-spacing:-.04em">1-3</div><div style="height:1px;background:var(--border-subtle);margin-top:auto"></div><p class="body-sm">3 档权重评分</p></div>
        <div style="display:flex;flex-direction:column;gap:.6vh"><div style="height:1px;background:var(--ink)"></div><div class="t-meta">必要条件</div><div style="font-family:var(--sans),var(--sans-zh);font-weight:200;font-size:min(3vw,5.2vh);line-height:1;color:var(--accent)">不满足即淘汰</div><div style="height:1px;background:var(--border-subtle);margin-top:auto"></div><p class="body-sm">如「统招本科」</p></div>
      </div>
    </div>
  </div>
</section>

<!-- ═══════ P06 · JD 解析 3 步 · sub-card ×3 ═══════ -->
<section class="slide grey" data-animate="grid-reveal">
  <div class="canvas-card">
    <div class="chrome-min">
      <div class="l">Section · JD Parsing</div>
      <div class="r">06 / 15</div>
    </div>
    <div style="flex:1;padding:0;display:grid;grid-template-rows:auto 1fr;gap:5vh">
      <div data-anim="head" style="display:flex;flex-direction:column;gap:1.4vh">
        <div class="t-meta">AUTO PARSE</div>
        <h2 style="font-family:var(--sans),var(--sans-zh);font-weight:200;font-size:min(6.4vw,11.2vh);line-height:.96;letter-spacing:-.035em">招聘需求自动解析</h2>
        <p class="lead" style="max-width:48ch;color:var(--text-secondary)">粘贴 JD → 系统自动提取 → 人工检查 → 保存。解析是辅助，不是最终裁决。</p>
      </div>
      <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:2vw;align-content:stretch" data-anim="cards">
        <div class="sub-card">
          <span class="nb-corner">01</span>
          <i data-lucide="file-text" class="lucide" style="color:var(--accent)"></i>
          <div class="ttl">粘贴招聘需求</div>
          <div class="desc">把 JD 文本粘贴到文本框。不确定格式？先点击「招聘需求示例」获取模板。</div>
        </div>
        <div class="sub-card">
          <span class="nb-corner">02</span>
          <i data-lucide="scan-search" class="lucide" style="color:var(--accent)"></i>
          <div class="ttl">解析 + 检查字段</div>
          <div class="desc">系统自动提取岗位名称、经验、学历、薪资、地点、技能关键词。逐项检查修正偏差。</div>
        </div>
        <div class="sub-card accent">
          <span class="nb-corner">03</span>
          <i data-lucide="save" class="lucide"></i>
          <div class="ttl">保存配置</div>
          <div class="desc">确认无误后保存。重点检查：薪资上下限、必要条件、技能关键词权重。</div>
        </div>
      </div>
    </div>
  </div>
</section>

<!-- ═══════ P07 · AI 模型配置 · duo-compare ═══════ -->
<section class="slide" data-animate="duo-mirror">
  <div class="canvas-card">
    <div class="chrome-min">
      <div class="l">Section · AI Configuration</div>
      <div class="r">07 / 15</div>
    </div>
    <div style="flex:1;padding:0;display:grid;grid-template-rows:auto 1fr;gap:3vh">
      <div data-anim="head" style="display:flex;flex-direction:column;gap:1.4vh">
        <div class="t-meta">OPTIONAL · LLM EVALUATION</div>
        <h2 style="font-family:var(--sans),var(--sans-zh);font-weight:200;font-size:min(6.4vw,11.2vh);line-height:.96;letter-spacing:-.035em">配置大模型</h2>
        <p class="lead" style="max-width:48ch;color:var(--text-secondary)">可选功能。启用 AI 辅助评估时，需要先完成模型配置和连通性测试。</p>
      </div>
      <div class="duo-compare" data-anim="compare">
        <div class="col">
          <div class="col-tag"><span class="num">01</span> CONFIGURE</div>
          <h3 class="col-ttl">配置 6 步</h3>
          <ul class="col-list">
            <li>选择服务商（通义千问 / DeepSeek / Kimi / OpenAI / Anthropic）</li>
            <li>填写 Base URL（系统给出默认地址）</li>
            <li>输入 API Key</li>
            <li>获取或输入模型名称</li>
            <li>点击「测试连接」</li>
            <li>测试通过 → 保存并添加到列表</li>
          </ul>
        </div>
        <span class="vrule"></span>
        <div class="col accent">
          <div class="col-tag"><span class="num">02</span> SECURITY</div>
          <h3 class="col-ttl">安全特性</h3>
          <ul class="col-list">
            <li>API Key 加密存储在系统钥匙串（Windows DPAPI / macOS Keychain）</li>
            <li>配置文件中不含明文 Key</li>
            <li>同一服务商按 provider + base_url 区分不同接入方式</li>
            <li>模型列表支持搜索、多选、批量连通性测试</li>
            <li>自动识别新增模型（绿色高亮）和下线模型（弹窗提醒）</li>
            <li>连通性失败时给出人性化排障提示</li>
          </ul>
        </div>
      </div>
    </div>
  </div>
</section>

<!-- ═══════ P08 · 浏览器连接 3 状态 · grid-3 × stack-block ═══════ -->
<section class="slide dark" data-animate="grid-reveal">
  <div class="canvas-card">
    <div class="chrome-min">
      <div class="l">Section · Browser Connection</div>
      <div class="r">08 / 15</div>
    </div>
    <div style="flex:1;padding:0;display:grid;grid-template-rows:auto 1fr;gap:5vh">
      <div data-anim="head" style="display:flex;flex-direction:column;gap:1.4vh">
        <div class="t-meta" style="color:rgba(255,255,255,.6)">CHROME DEVTOOLS PROTOCOL</div>
        <h2 style="font-family:var(--sans),var(--sans-zh);font-weight:200;font-size:min(6.4vw,11.2vh);line-height:.96;letter-spacing:-.035em;color:var(--paper)">连接 BOSS 页面</h2>
        <p class="lead" style="max-width:48ch;color:rgba(255,255,255,.7)">系统通过 Chrome DevTools 协议连接浏览器。出现验证码时，先在浏览器完成验证再回到系统。</p>
      </div>
      <div class="stack-row" data-anim="blocks">
        <div class="stack-block b-grey">
          <span class="layer-nb">STATUS 01</span>
          <div class="layer-ttl" style="font-size:max(17px,2vw)">未连接</div>
          <div class="layer-desc">系统没有连到 Chrome</div>
          <div class="layer-tag" style="color:var(--text-secondary)">→ 点击检测/连接浏览器</div>
        </div>
        <div class="stack-block b-accent">
          <span class="layer-nb">STATUS 02</span>
          <div class="layer-ttl" style="font-size:max(17px,2vw)">需导航</div>
          <div class="layer-desc">已连接 Chrome，但不在推荐牛人页面</div>
          <div class="layer-tag">→ 手工打开 BOSS 推荐页面</div>
        </div>
        <div class="stack-block b-ink">
          <span class="layer-nb">STATUS 03</span>
          <div class="layer-ttl" style="font-size:max(17px,2vw)">已连接</div>
          <div class="layer-desc">已连接到 BOSS 推荐页面</div>
          <div class="layer-tag" style="color:rgba(255,255,255,.5)">✓ 可以开始运行</div>
        </div>
      </div>
    </div>
  </div>
</section>

<!-- ═══════ P09 · 运行参数 · four-cards ═══════ -->
<section class="slide" data-animate="four-cards">
  <div class="canvas-card">
    <div style="height:3px;background:var(--accent);margin:-5.6vh -5vw 0;width:calc(100% + 10vw)"></div>
    <div class="chrome-min" style="margin-top:2.4vh">
      <div class="l">Section · Run Parameters</div>
      <div class="r">09 / 15</div>
    </div>
    <div style="flex:1;padding:0;display:grid;grid-template-rows:auto 1fr;gap:3vh">
      <div data-anim="head" style="display:flex;flex-direction:column;gap:1.4vh">
        <div class="t-meta">OPERATION GUIDE</div>
        <h2 style="font-family:var(--sans),var(--sans-zh);font-weight:200;font-size:min(6.4vw,11.2vh);line-height:.96;letter-spacing:-.035em">运行筛选</h2>
        <p class="lead" style="max-width:48ch;color:var(--text-secondary)">运行前确认：浏览器已连接 + 推荐页面已打开 + 岗位选择正确。新岗位首次建议先「仅筛选」。</p>
      </div>
      <div data-anim="cards" style="display:grid;grid-template-columns:repeat(4,1fr);gap:2vw;align-content:start">
        <div style="display:flex;flex-direction:column;gap:1vh;padding-top:2vh;border-top:1px solid var(--grey-2)">
          <div class="t-meta">— 01</div>
          <h3 style="font-family:var(--sans),var(--sans-zh);font-weight:300;font-size:min(2vw,3.6vh);line-height:1.1;letter-spacing:-.015em">选择岗位</h3>
          <p style="font-family:var(--sans),var(--sans-zh);font-size:max(16px,.88vw);line-height:1.55;opacity:.78">单岗位优先；「全部岗位」适合批量处理</p>
        </div>
        <div style="display:flex;flex-direction:column;gap:1vh;padding-top:2vh;border-top:1px solid var(--grey-2)">
          <div class="t-meta">— 02</div>
          <h3 style="font-family:var(--sans),var(--sans-zh);font-weight:300;font-size:min(2vw,3.6vh);line-height:1.1;letter-spacing:-.015em">滚动轮次</h3>
          <p style="font-family:var(--sans),var(--sans-zh);font-size:max(16px,.88vw);line-height:1.55;opacity:.78">安全扫描默认 100；API 模式默认 30</p>
        </div>
        <div style="display:flex;flex-direction:column;gap:1vh;padding-top:2vh;border-top:1px solid var(--grey-2)">
          <div class="t-meta">— 03</div>
          <h3 style="font-family:var(--sans),var(--sans-zh);font-weight:300;font-size:min(2vw,3.6vh);line-height:1.1;letter-spacing:-.015em">AI 辅助评估</h3>
          <p style="font-family:var(--sans),var(--sans-zh);font-size:max(16px,.88vw);line-height:1.55;opacity:.78">需要模型配置；增加耗时和 token 成本</p>
        </div>
        <div style="display:flex;flex-direction:column;gap:1vh;padding-top:2vh;border-top:1px solid var(--grey-2)">
          <div class="t-meta">— 04</div>
          <h3 style="font-family:var(--sans),var(--sans-zh);font-weight:300;font-size:min(2vw,3.6vh);line-height:1.1;letter-spacing:-.015em">自动打招呼</h3>
          <p style="font-family:var(--sans),var(--sans-zh);font-size:max(16px,.88vw);line-height:1.55;opacity:.78">首次测试建议先选「不打招呼」</p>
        </div>
      </div>
    </div>
  </div>
</section>

<!-- ═══════ P10 · 评分体系 · bar-towers ×4 ═══════ -->
<section class="slide" data-animate="measure-up">
  <div class="canvas-card">
    <div class="chrome-min">
      <div class="l">Section · Scoring Model</div>
      <div class="r">10 / 15</div>
    </div>
    <div style="flex:1;padding:0;display:grid;grid-template-rows:auto 1fr;gap:3vh">
      <div data-anim="head" style="display:flex;flex-direction:column;gap:1.4vh">
        <div class="t-meta">FOUR DIMENSIONS · MAX 100</div>
        <h2 style="font-family:var(--sans),var(--sans-zh);font-weight:200;font-size:min(6.4vw,11.2vh);line-height:.96;letter-spacing:-.035em">评分体系</h2>
      </div>
      <div class="bar-towers" data-anim="towers">
        <div class="bar-tower">
          <div class="cap"><i data-lucide="hexagon" style="width:1.6vw;height:1.6vw;stroke:currentColor;fill:none;stroke-width:1.6"></i></div>
          <div class="body-block h-1">
            <div class="lbl">基础分</div>
            <div class="nb">25</div>
            <div class="sub">固定基础分，所有候选人相同起点</div>
          </div>
        </div>
        <div class="bar-tower">
          <div class="cap"><i data-lucide="code" style="width:1.6vw;height:1.6vw;stroke:currentColor;fill:none;stroke-width:1.6"></i></div>
          <div class="body-block b-accent h-3">
            <div class="lbl">技能匹配</div>
            <div class="nb">0~50</div>
            <div class="sub">关键词权重 × 命中数，核心区分维度</div>
          </div>
        </div>
        <div class="bar-tower">
          <div class="cap"><i data-lucide="clock" style="width:1.6vw;height:1.6vw;stroke:currentColor;fill:none;stroke-width:1.6"></i></div>
          <div class="body-block h-2">
            <div class="lbl">经验超额</div>
            <div class="nb">0~15</div>
            <div class="sub">超出岗位要求的年限加分</div>
          </div>
        </div>
        <div class="bar-tower">
          <div class="cap"><i data-lucide="graduation-cap" style="width:1.6vw;height:1.6vw;stroke:currentColor;fill:none;stroke-width:1.6"></i></div>
          <div class="body-block h-1">
            <div class="lbl">学历档次</div>
            <div class="nb">0~10</div>
            <div class="sub">硕博 / 本科 / 大专分档</div>
          </div>
        </div>
      </div>
    </div>
  </div>
</section>

<!-- ═══════ P11 · 筛选结果 · duo-compare ═══════ -->
<section class="slide" data-animate="duo-mirror">
  <div class="canvas-card">
    <div class="chrome-min">
      <div class="l">Section · Results</div>
      <div class="r">11 / 15</div>
    </div>
    <div style="flex:1;padding:0;display:grid;grid-template-rows:auto 1fr;gap:3vh">
      <div data-anim="head" style="display:flex;flex-direction:column;gap:1.4vh">
        <div class="t-meta">CANDIDATE RESULTS</div>
        <h2 style="font-family:var(--sans),var(--sans-zh);font-weight:200;font-size:min(6.4vw,11.2vh);line-height:.96;letter-spacing:-.035em">筛选结果</h2>
      </div>
      <div class="duo-compare" data-anim="compare">
        <div class="col" style="gap:2.4vh">
          <div class="col-tag"><span class="num">01</span> RECOMMEND</div>
          <h3 class="col-ttl" style="font-size:min(3.6vw,6.4vh)">推荐等级</h3>
          <div style="display:flex;flex-direction:column;gap:1.6vh;margin-top:2vh">
            <div style="display:grid;grid-template-columns:auto 1fr auto;gap:1.2vw;align-items:center;padding:1.2vh 0;border-bottom:1px solid var(--grey-2)">
              <div style="background:var(--accent);color:var(--accent-on);padding:.3em .8em;font-family:var(--mono);font-size:14px;font-weight:600;letter-spacing:.1em">强烈推荐</div>
              <span style="font-family:var(--sans),var(--sans-zh);font-size:max(16px,.94vw);opacity:.78">自动打招呼（激进）</span>
              <span style="font-family:var(--sans);font-weight:250;font-size:min(2vw,3.6vh);color:var(--accent)">≥75</span>
            </div>
            <div style="display:grid;grid-template-columns:auto 1fr auto;gap:1.2vw;align-items:center;padding:1.2vh 0;border-bottom:1px solid var(--grey-2)">
              <div style="background:var(--ink);color:var(--paper);padding:.3em .8em;font-family:var(--mono);font-size:14px;font-weight:600;letter-spacing:.1em">推荐</div>
              <span style="font-family:var(--sans),var(--sans-zh);font-size:max(16px,.94vw);opacity:.78">自动打招呼（保守）</span>
              <span style="font-family:var(--sans);font-weight:250;font-size:min(2vw,3.6vh)">65-74</span>
            </div>
            <div style="display:grid;grid-template-columns:auto 1fr auto;gap:1.2vw;align-items:center;padding:1.2vh 0;border-bottom:1px solid var(--grey-2)">
              <div style="background:var(--grey-1);color:var(--ink);padding:.3em .8em;font-family:var(--mono);font-size:14px;font-weight:600;letter-spacing:.1em">待定</div>
              <span style="font-family:var(--sans),var(--sans-zh);font-size:max(16px,.94vw);opacity:.78">仅保存，不打招呼</span>
              <span style="font-family:var(--sans);font-weight:250;font-size:min(2vw,3.6vh)">55-64</span>
            </div>
            <div style="display:grid;grid-template-columns:auto 1fr auto;gap:1.2vw;align-items:center;padding:1.2vh 0">
              <div style="background:var(--grey-2);color:var(--text-helper);padding:.3em .8em;font-family:var(--mono);font-size:14px;font-weight:600;letter-spacing:.1em">淘汰</div>
              <span style="font-family:var(--sans),var(--sans-zh);font-size:max(16px,.94vw);opacity:.5">不进入结果统计</span>
              <span style="font-family:var(--sans);font-weight:250;font-size:min(2vw,3.6vh);color:var(--text-helper)">&lt;55</span>
            </div>
          </div>
        </div>
        <span class="vrule"></span>
        <div class="col">
          <div class="col-tag"><span class="num">02</span> ACTIONS</div>
          <h3 class="col-ttl" style="font-size:min(3.6vw,6.4vh)">操作</h3>
          <ul class="col-list">
            <li>刷新结果 — 从 JSON 重新加载</li>
            <li>导出 Excel — 生成 candidates_all.xlsx</li>
            <li>右键候选人 — 查看详情 / 单独打招呼</li>
            <li>清空候选人 — 操作前自动备份</li>
          </ul>
          <div class="frame-img r-16x9 fit-contain" style="margin-top:auto;max-height:28vh">
            <img src="images/11-results.png" alt="筛选结果界面" loading="eager">
          </div>
        </div>
      </div>
    </div>
  </div>
</section>

<!-- ═══════ P12 · 数据统计 · grid-3 × stat-card ×6 ═══════ -->
<section class="slide grey" data-animate="grid-reveal">
  <div class="canvas-card">
    <div class="chrome-min">
      <div class="l">Section · Statistics</div>
      <div class="r">12 / 15</div>
    </div>
    <div style="flex:1;padding:0;display:grid;grid-template-rows:auto 1fr;gap:3vh">
      <div data-anim="head" style="display:flex;flex-direction:column;gap:1.4vh">
        <div class="t-meta">DATA DASHBOARD</div>
        <h2 style="font-family:var(--sans),var(--sans-zh);font-weight:200;font-size:min(6.4vw,11.2vh);line-height:.96;letter-spacing:-.035em">数据统计</h2>
        <p class="lead" style="max-width:48ch;color:var(--text-secondary)">按岗位和时间范围统计。优质率极低？通常不是市场没人，而是规则过硬。</p>
      </div>
      <div data-anim="grid" class="grid-3" style="gap:2.4vh 2vw;align-content:start">
        <div class="stat-card accent-top">
          <span class="stat-label">总候选人</span>
          <p class="stat-note">分数 ≥ 55 的候选人数</p>
        </div>
        <div class="stat-card accent-top">
          <span class="stat-label">强烈推荐</span>
          <p class="stat-note">≥ 75 分，自动打招呼（激进策略）</p>
        </div>
        <div class="stat-card accent-top">
          <span class="stat-label">推荐</span>
          <p class="stat-note">65-74 分，自动打招呼（保守策略）</p>
        </div>
        <div class="stat-card accent-top">
          <span class="stat-label">待定</span>
          <p class="stat-note">55-64 分，仅保存不打招呼</p>
        </div>
        <div class="stat-card accent-top">
          <span class="stat-label">已打招呼</span>
          <p class="stat-note">已发送沟通消息的候选人数</p>
        </div>
        <div class="stat-card accent-top">
          <span class="stat-label">优质率 / 打招呼率</span>
          <p class="stat-note">强推+推荐占比 / 已打招呼占比</p>
        </div>
      </div>
    </div>
  </div>
</section>

<!-- ═══════ P13 · 推荐习惯 · duo-compare ═══════ -->
<section class="slide" data-animate="duo-mirror">
  <div class="canvas-card">
    <div class="chrome-min">
      <div class="l">Section · Best Practices</div>
      <div class="r">13 / 15</div>
    </div>
    <div style="flex:1;padding:0;display:grid;grid-template-rows:auto 1fr;gap:3vh">
      <div data-anim="head" style="display:flex;flex-direction:column;gap:1.4vh">
        <div class="t-meta">RECOMMENDED</div>
        <h2 style="font-family:var(--sans),var(--sans-zh);font-weight:200;font-size:min(6.4vw,11.2vh);line-height:.96;letter-spacing:-.035em">推荐操作习惯</h2>
      </div>
      <div class="duo-compare" data-anim="compare">
        <div class="col">
          <div class="col-tag"><span class="num">A</span> FIRST RUN</div>
          <h3 class="col-ttl">新岗位首次跑</h3>
          <ul class="col-list">
            <li>先只配置规则，不启用 AI</li>
            <li>打招呼策略选「不打招呼（仅筛选）」</li>
            <li>安全扫描开启，滚动轮次 50-200</li>
            <li>看结果里的误杀、误放</li>
            <li>调整岗位规则</li>
            <li>确认规则后开启「仅强烈推荐」</li>
          </ul>
        </div>
        <span class="vrule"></span>
        <div class="col accent">
          <div class="col-tag"><span class="num">B</span> DAILY RUN</div>
          <h3 class="col-ttl">成熟岗位日常</h3>
          <ul class="col-list">
            <li>检查浏览器连接状态</li>
            <li>确认 BOSS 当前职位正确</li>
            <li>运行筛选（安全扫描默认 100 轮）</li>
            <li>查看筛选结果</li>
            <li>导出 Excel</li>
            <li>查看统计，判断是否微调规则</li>
          </ul>
        </div>
      </div>
    </div>
  </div>
</section>

<!-- ═══════ P14 · 快速检查清单 · timeline-h ×6 ═══════ -->
<section class="slide" data-animate="timeline-walk">
  <div class="canvas-card">
    <div class="chrome-min">
      <div class="l">Section · Checklist</div>
      <div class="r">14 / 15</div>
    </div>
    <div style="flex:1;padding:0;display:grid;grid-template-rows:auto 1fr;gap:5vh">
      <div data-anim="head" style="display:flex;flex-direction:column;gap:1.4vh">
        <div class="t-meta">PRE-FLIGHT CHECK</div>
        <h2 style="font-family:var(--sans),var(--sans-zh);font-weight:200;font-size:min(6.4vw,11.2vh);line-height:.96;letter-spacing:-.035em">快速检查清单</h2>
        <p class="lead" style="max-width:48ch;color:var(--text-secondary)">运行前必检 6 项。每一项都可能成为运行失败的根因。</p>
      </div>
      <div class="timeline-h" data-anim="axis">
        <span class="tl-h-axis"></span>
        <div class="tl-row">
          <div class="th-node up accent"><span class="yr">CHECK</span><span class="dot"></span><div class="label"><span class="name">岗位规则</span><span class="desc">已保存</span></div></div>
          <div class="th-node down"><span class="yr">CHECK</span><span class="dot"></span><div class="label"><span class="name">BOSS 登录</span><span class="desc">网页端已登录</span></div></div>
          <div class="th-node up"><span class="yr">CHECK</span><span class="dot"></span><div class="label"><span class="name">Chrome</span><span class="desc">已连接</span></div></div>
          <div class="th-node down accent"><span class="yr">CHECK</span><span class="dot"></span><div class="label"><span class="name">推荐页面</span><span class="desc">目标岗位</span></div></div>
          <div class="th-node up"><span class="yr">CHECK</span><span class="dot"></span><div class="label"><span class="name">岗位一致</span><span class="desc">系统 = BOSS 页面</span></div></div>
          <div class="th-node down"><span class="yr">CHECK</span><span class="dot"></span><div class="label"><span class="name">AI 模型</span><span class="desc">已测试通过</span></div></div>
        </div>
      </div>
    </div>
  </div>
</section>

<!-- ═══════ P15 · CLOSING · 左 IKB+ASCII / 右 takeaway ═══════ -->
<section class="slide split" data-animate="split-statement">
  <div class="canvas-card">
    <div class="split-half">
      <!-- 左半 · IKB 宣言 + ASCII 呼吸场 -->
      <div class="half b-accent" style="padding:5.6vh 3.6vw 4.4vh;justify-content:space-between;position:relative;overflow:hidden">
        <canvas class="ascii-bg" aria-hidden="true"></canvas>
        <div class="chrome-min" style="margin-bottom:0;position:relative;z-index:1">
          <div class="l">15 / 15</div>
          <div class="r">CLOSING</div>
        </div>
        <div data-anim="manifesto" style="display:flex;flex-direction:column;gap:2vh;position:relative;z-index:1">
          <div class="t-meta" style="color:rgba(255,255,255,.78);letter-spacing:.22em;margin-bottom:1.6vh">MANIFESTO</div>
          <h2 style="font-family:var(--sans),var(--sans-zh);font-size:min(8vw,14vh);line-height:.94;letter-spacing:-.025em;font-weight:200;color:#fff">配置规则.<br/>运行<span style="font-style:italic;font-weight:300">筛选</span>.</h2>
          <div style="font-family:var(--sans),var(--sans-zh);font-size:max(14px,1vw);line-height:1.6;color:rgba(255,255,255,.82);font-weight:300;max-width:36ch;margin-top:1.4vh">让招聘自动化成为可重复执行的流程，而非一次性的手工操作。</div>
        </div>
        <div data-anim="signature" style="display:flex;justify-content:space-between;align-items:end;border-top:1px solid rgba(255,255,255,.22);padding-top:2vh;position:relative;z-index:1">
          <div class="t-meta" style="color:rgba(255,255,255,.62)">BOSS 招聘系统 · v2.9</div>
          <div class="t-meta" style="color:rgba(255,255,255,.62)">2026.06</div>
        </div>
      </div>
      <!-- 右半 · 三条 takeaway · 白底承载理性收束 -->
      <div class="half" style="padding:5.6vh 3.6vw 4.4vh;justify-content:space-between">
        <div class="chrome-min">
          <div class="l">TAKEAWAYS</div>
          <div class="r">03 RULES</div>
        </div>
        <div data-anim="rules" style="display:flex;flex-direction:column;gap:0">
          <div style="display:grid;grid-template-columns:auto 1fr;gap:2vw;align-items:start;padding:2.6vh 0;border-top:1px solid var(--border-subtle)">
            <div style="font-family:var(--sans);font-weight:200;font-size:min(4.4vw,7.8vh);line-height:.9;color:var(--text-primary)">01</div>
            <div>
              <h3 style="font-family:var(--sans),var(--sans-zh);font-weight:400;font-size:max(18px,1.8vw);line-height:1.2;letter-spacing:-.015em;color:var(--text-primary);margin-bottom:1vh">新岗位先试跑</h3>
              <p style="font-family:var(--sans),var(--sans-zh);font-size:max(16px,.94vw);line-height:1.6;color:var(--text-secondary);font-weight:400">先用「仅筛选」模式和安全扫描跑 50-200 轮，确认规则无误杀误放后再开打招呼。</p>
            </div>
          </div>
          <div style="display:grid;grid-template-columns:auto 1fr;gap:2vw;align-items:start;padding:2.6vh 0;border-top:1px solid var(--border-subtle)">
            <div style="font-family:var(--sans);font-weight:200;font-size:min(4.4vw,7.8vh);line-height:.9;color:var(--text-primary)">02</div>
            <div>
              <h3 style="font-family:var(--sans),var(--sans-zh);font-weight:400;font-size:max(18px,1.8vw);line-height:1.2;letter-spacing:-.015em;color:var(--text-primary);margin-bottom:1vh">权重不要虚高</h3>
              <p style="font-family:var(--sans),var(--sans-zh);font-size:max(16px,.94vw);line-height:1.6;color:var(--text-secondary);font-weight:400">技能权重 1/2/3 三档够用。全部设成高权重 = 没有权重，评分排序失去意义。</p>
            </div>
          </div>
          <div style="display:grid;grid-template-columns:auto 1fr;gap:2vw;align-items:start;padding:2.6vh 0;border-top:1px solid var(--border-subtle);border-bottom:2px solid var(--accent)">
            <div style="font-family:var(--sans);font-weight:200;font-size:min(4.4vw,7.8vh);line-height:.9;color:var(--accent)">03</div>
            <div>
              <h3 style="font-family:var(--sans),var(--sans-zh);font-weight:400;font-size:max(18px,1.8vw);line-height:1.2;letter-spacing:-.015em;color:var(--accent);margin-bottom:1vh">看数据调规则</h3>
              <p style="font-family:var(--sans),var(--sans-zh);font-size:max(16px,.94vw);line-height:1.6;color:var(--text-secondary);font-weight:400">数据统计页判断规则质量。优质率低 = 规则过硬或关键词偏窄，不是市场没人。</p>
            </div>
          </div>
        </div>
        <div data-anim="foot" class="t-meta" style="color:var(--text-helper);text-align:right">→ 完 · END OF FIELD NOTE</div>
      </div>
    </div>
  </div>
</section>
"""

# ════════════════════════════════════════════════════════════
# ASSEMBLE
# ════════════════════════════════════════════════════════════

def main():
    os.makedirs(OUT_DIR, exist_ok=True)

    # Read template
    with open(TEMPLATE, "r", encoding="utf-8") as f:
        tpl = f.read()

    # Replace title
    tpl = tpl.replace(
        "[必填] 替换为 PPT 标题 · Deck Title",
        "BOSS 招聘系统操作指南 · Swiss IKB"
    )

    # Insert slides: replace from the SLIDES_HERE comment to the closing </div> of #deck
    marker = "<!-- SLIDES_HERE"
    idx = tpl.find(marker)
    if idx < 0:
        print("[ERROR] Cannot find SLIDES_HERE marker in template")
        return

    # Find the </div> that closes #deck (after the marker)
    # The template has two example slides, then </div> for #deck
    # We need to find the first </div> at column 0 after the examples
    close_tag = "\n</div>\n\n<div id=\"nav\">"
    close_idx = tpl.find(close_tag, idx)
    if close_idx < 0:
        print("[ERROR] Cannot find closing </div> of #deck")
        return

    # Build output: everything before marker + slides + everything from close_tag onward
    output = tpl[:idx] + SLIDES.strip() + "\n" + tpl[close_idx:]

    # Add lucide icons CDN if not present
    if 'unpkg.com/lucide@latest' not in output:
        output = output.replace(
            "</body>",
            '<script src="https://unpkg.com/lucide@latest/dist/umd/lucide.min.js"></script>\n'
            '<script>lucide.createIcons();</script>\n</body>'
        )

    # Write
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        f.write(output)

    size_kb = os.path.getsize(OUT_FILE) // 1024
    print(f"Done: {OUT_FILE}")
    print(f"  Size: {size_kb} KB")
    print(f"  Slides: 15")
    print(f"  Theme: IKB (Klein Blue)")
    print(f"  Open in browser to view")


if __name__ == "__main__":
    main()
