# -*- coding: utf-8 -*-
"""一次性诊断探针：实测 k3 解析真实长度 JD 的耗时 + 探测推理开关。

只读诊断，不写文件、不打印密钥。
用法: python tests/manual/_probe_kimi_latency.py
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import requests

from security import get_api_key

BASE_URL = "https://api.kimi.com/coding/v1"
MODEL = "k3"

api_key = get_api_key("kimi", BASE_URL)
if not api_key:
    print("NO_KEY")
    sys.exit(1)

headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}

# 接近真实长度的 JD（金融 IT 岗）
JD = """高级Java开发工程师（证券核心交易系统方向）
岗位职责：
1、负责证券核心交易系统（集中交易、两融、期权）后端服务的设计与开发；
2、参与分布式交易系统架构演进，负责高并发、低延迟交易链路的性能优化；
3、负责与恒生、金证、顶点等柜台风控系统的对接开发；
4、编写技术文档，参与代码评审，指导初中级工程师。
任职要求：
1、统招本科及以上学历，计算机相关专业，5年以上Java开发经验；
2、精通Spring Cloud、Spring Boot、MyBatis，熟悉Dubbo、gRPC等RPC框架；
3、熟悉MySQL、Oracle，具备SQL调优能力，熟悉Redis、RocketMQ、Kafka；
4、有证券、基金、期货等金融行业交易系统开发经验者优先；
5、熟悉FIX协议、有撮合引擎或风控引擎开发经验者优先；
6、具备良好的沟通能力和团队协作精神，能承受一定的工作压力。
薪资范围：25-40K·14薪，工作地点：上海浦东。"""

SYSTEM = (
    "你是招聘需求结构化解析助手。你只能基于原文和正则初稿做补充、纠错、归一化。"
    "不要虚构原文没有的信息。返回严格 JSON 对象，不要 Markdown，不要解释。"
)
USER_TMPL = (
    "目标：在正则解析初稿基础上增强岗位配置。\n"
    "返回 JSON schema：{\"keywords_add\": [{\"name\":\"技能\", \"weight\":1-3}], "
    "\"warnings\": [\"需要人工确认的点\"]}\n\n"
    "原始招聘需求：\n" + JD + "\n\n正则初稿 JSON：\n{}"
)


def probe(label, extra_body, timeout=(10, 240)):
    body = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": USER_TMPL},
        ],
        "temperature": 1,
        "stream": False,
        **extra_body,
    }
    start = time.monotonic()
    try:
        resp = requests.post(
            f"{BASE_URL}/chat/completions", json=body, headers=headers, timeout=timeout,
        )
    except requests.exceptions.ReadTimeout:
        print(f"{label}: READ_TIMEOUT after {time.monotonic()-start:.1f}s")
        return
    except requests.exceptions.RequestException as exc:
        print(f"{label}: {type(exc).__name__}: {str(exc)[:120]}")
        return
    elapsed = time.monotonic() - start
    if resp.status_code != 200:
        print(f"{label}: HTTP {resp.status_code} in {elapsed:.1f}s  body: {resp.text[:200]}")
        return
    data = resp.json()
    choice = (data.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    usage = data.get("usage") or {}
    reasoning = msg.get("reasoning_content") or ""
    content = str(msg.get("content") or "")
    print(
        f"{label}: HTTP 200 in {elapsed:.1f}s  finish={choice.get('finish_reason')!r} "
        f"reasoning={len(reasoning)}chars content={len(content)}chars "
        f"completion_tokens={usage.get('completion_tokens')} "
        f"reasoning_tokens={(usage.get('completion_tokens_details') or {}).get('reasoning_tokens')}"
    )


# A. 当前代码路径：reasoning 开启，预算 2000（模拟真实 JD 解析耗时）
probe("A reasoning ON, budget=2000", {"max_completion_tokens": 2000})

# B. 尝试 thinking.type=disabled（xiaomi 风格开关）
probe("B thinking disabled, budget=2000", {
    "max_completion_tokens": 2000, "thinking": {"type": "disabled"},
})

# B2. thinking disabled + temperature=0.6（按端点报错提示的组合）
probe("B2 thinking disabled, temp=0.6, budget=2000", {
    "max_completion_tokens": 2000, "temperature": 0.6,
    "thinking": {"type": "disabled"},
})

# C. 尝试 enable_thinking=false（qwen 风格开关）
probe("C enable_thinking=false, budget=2000", {
    "max_completion_tokens": 2000, "enable_thinking": False,
})
