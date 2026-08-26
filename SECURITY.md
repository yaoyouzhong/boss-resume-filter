# 安全策略

## 受支持版本

安全修复仅面向当前最新公开版本。发现问题前，请先从 [GitHub Releases](https://github.com/yaoyouzhong/boss-resume-filter/releases/latest) 确认使用的是最新版本。

## 报告安全问题

请使用 GitHub 的 [私密漏洞报告](https://github.com/yaoyouzhong/boss-resume-filter/security/advisories/new) 提交安全问题，不要在公开 Issue 中披露漏洞细节。

报告建议包含：

- 受影响的程序版本、操作系统和功能模块。
- 实际影响与可重复的最小步骤。
- 已做脱敏处理的日志、截图或示例数据。
- 已知的缓解方法（如有）。

维护者会在私密报告中确认问题、说明处理状态，并在修复公开前与报告者协调披露范围。

## 敏感数据边界

无论公开还是私密报告，都不应提交真实候选人简历、身份证件、学历证书、联系方式、API Key、Chrome 配置文件、登录凭据、Cookie、本地候选人 JSON 或未加密备份。请使用完全合成的示例数据，或使用程序生成的脱敏诊断包。

## 不属于安全漏洞的问题

BOSS 平台风控、限流、页面改版、账号状态，以及第三方 AI 服务的可用性问题，通常不属于本项目安全漏洞。一般故障请使用 Bug Report 模板；平台与数据使用边界见 [使用声明](DISCLAIMER.md)。
