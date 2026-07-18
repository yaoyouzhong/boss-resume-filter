# -*- coding: utf-8 -*-
"""一次性实测：LLM 二次评分走 k3 关闭 thinking 后的端到端耗时。只读，不打印密钥。"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from security import get_api_key
from llm_eval import _call_llm_api

base_url = "https://api.kimi.com/coding/v1"
api_key = get_api_key("kimi", base_url)
config = {
    "api_provider": "kimi",
    "base_url": base_url,
    "model": "k3",
    "_ignore_capability_cache": True,
}
messages = [
    {
        "role": "system",
        "content": '你是简历评估助手。返回严格 JSON：{"adjustment": -15到15的整数, "reason": "一句话理由"}',
    },
    {
        "role": "user",
        "content": (
            "候选人：5年Java经验，熟悉Spring Cloud、MySQL，本科，证券行业背景。"
            "岗位：高级Java工程师，要求5年经验、本科、Spring Cloud。请评估匹配度调整分。"
        ),
    },
]

start = time.monotonic()
result = _call_llm_api(messages, config, api_key)
elapsed = time.monotonic() - start
print(f"elapsed: {elapsed:.1f}s")
print("success:", result.success)
print("adjustment:", result.adjustment)
print("reason:", result.reason)
