"""Shared constants for BOSS resume screening."""

# ========== 评分模型参数 ==========
SCORE_BASE = 25                    # 基础分
SCORE_SKILL_MAX = 50               # 技能匹配上限
SCORE_EXP_MAX = 15                 # 经验超额上限
SCORE_EXP_MULTIPLIER = 3           # 每超出一年经验的加分
SCORE_EDU_MAX = 10                 # 学历加分上限

# 学历加分档位（博士=10, 985硕士=9, 普通硕士=7, 985本科=6, 普通本科=3）
SCORE_EDU_DOCTOR = 10
SCORE_EDU_MASTER_985 = 9
SCORE_EDU_MASTER = 7
SCORE_EDU_BACHELOR_985 = 6
SCORE_EDU_BACHELOR = 3

# ========== 评分阈值 ==========
SCORE_THRESHOLD_PASS = 55          # 通过筛选（待定及以上）
SCORE_THRESHOLD_RECOMMEND = 65     # 推荐
SCORE_THRESHOLD_STRONG = 75        # 强烈推荐

# ========== 非统招学历关键词 ==========
NON_REGULAR_EDU = [
    "自考", "成教", "函授", "夜大", "网络教育", "继续教育", "非统招",
    "专升本", "电大", "远程教育", "成人高考", "成人教育", "脱产", "业余",
]

# ========== 中文数字映射 ==========
CHINESE_NUMERALS = {
    '零': 0, '一': 1, '二': 2, '两': 2, '三': 3, '四': 4,
    '五': 5, '六': 6, '七': 7, '八': 8, '九': 9, '十': 10,
}

# ========== 滚动与扫描参数 ==========
SCROLL_PX = 800                    # 每次滚动像素
MAX_SCROLL_SEARCH = 40             # 最大滚动搜索次数
MAX_ROUNDS_DEFAULT = 30            # 默认最大扫描轮数
EMPTY_ROUNDS_LIMIT = 5             # 连续无新候选人轮数上限
GREET_FAIL_LIMIT = 3               # 连续打招呼失败次数上限
GREET_UNCERTAIN_LIMIT = 2          # 连续发送结果待核实次数上限
CAPTCHA_MAX_WAIT = 300             # 验证码最大等待秒数（5 分钟）
CAPTCHA_CHECK_INTERVAL = 3         # 验证码检查间隔秒数
API_PAGE_DELAY_CENTER = 3.0        # API 分页直调间隔中心值（秒），默认约 2-4 秒
API_PAGE_DELAY_SPREAD = 2.0        # API 分页直调随机抖动范围（秒）
API_CANDIDATE_LIMIT_DEFAULT = 400  # API 结构化补全预算，默认最多补全 20 页
AUTO_GREET_RUN_LIMIT = 50          # 单次自动打招呼人数上限
GREET_DELAY_CENTER = 5.5           # 自动打招呼单人间隔中心值（秒）
GREET_DELAY_SPREAD = 3.0           # 自动打招呼单人间隔随机抖动范围（秒）
GREET_BATCH_SIZE = 10              # 自动打招呼批次大小
GREET_BATCH_PAUSE_CENTER = 5.5     # 自动打招呼批次暂停中心值（秒），默认约 3-8 秒
GREET_BATCH_PAUSE_SPREAD = 5.0     # 自动打招呼批次暂停随机抖动范围（秒）

# ========== HTTP ==========
USER_AGENT = "BossResumeFilter/1.0"

# ========== LLM API 参数 ==========
LLM_MAX_TOKENS = 1024              # 最大返回 token 数，需容纳完整评估参数
LLM_TEMPERATURE = 0.3              # 采样温度（低 = 更确定）
LLM_CONNECT_TIMEOUT = 10              # AI 评估连接超时（秒，固定值）
LLM_READ_TIMEOUT_DEFAULT = 60         # AI 评估读取超时默认值（秒，非中转）
LLM_RELAY_READ_TIMEOUT_DEFAULT = 120  # 中转服务读取超时默认值（秒）
LLM_MAX_RETRIES = 3                # API 调用最大重试次数
LLM_MAX_WORKERS = 5                # AI 评估默认并发数
LLM_RELAY_MAX_WORKERS = 3          # 中转服务并发数，避免网关拥塞

# ========== 自动更新超时（秒） ==========
UPDATE_TIMEOUT_GITEE = 8           # Gitee latest.json 请求
UPDATE_TIMEOUT_GITHUB = 10         # GitHub Releases API 请求
UPDATE_TIMEOUT_DOWNLOAD = 30       # 文件下载（含国内镜像）
UPDATE_TIMEOUT_CHANGELOG = 8       # 远端 CHANGELOG.md 获取
UPDATE_TIMEOUT_RELEASE_NOTES_GITEE = 3.0   # 更新日志弹窗远端说明：Gitee 首次短超时
UPDATE_TIMEOUT_RELEASE_NOTES_GITEE_RETRY = 5.0  # 更新日志弹窗远端说明：Gitee 重试超时
UPDATE_TIMEOUT_RELEASE_NOTES_GITHUB = 3.5  # 更新日志弹窗远端说明：GitHub 短超时
UPDATE_TIMEOUT_GIT_PULL = 30       # git pull subprocess 超时

# ========== BOSS 推荐页空状态标记 ==========
# 用于检测推荐牛人页面无候选人的情况（bossmaster + gui_main 共用）
EMPTY_RECOMMEND_MARKS = (
    "暂无推荐",
    "暂无合适",
    "暂时没有",
    "没有更多",
)
# 更严格的空状态：未发布职位（仅 bossmaster 选择器健康检查使用）
EMPTY_RECOMMEND_NO_JOB_MARK = "您需要先发布职位，才能查看推荐牛人"

# ========== 城市列表 ==========
# 中国主要城市列表（按长度降序，优先匹配长名如"哈尔滨"）
MAJOR_CITIES = sorted([
    '北京', '上海', '广州', '深圳', '杭州', '南京', '成都', '武汉',
    '西安', '苏州', '重庆', '长沙', '合肥', '郑州', '天津', '济南',
    '青岛', '厦门', '福州', '珠海', '东莞', '无锡', '宁波', '大连',
    '沈阳', '昆明', '贵阳', '南宁', '海口', '南昌', '太原', '长春',
    '哈尔滨', '石家庄',
], key=len, reverse=True)
