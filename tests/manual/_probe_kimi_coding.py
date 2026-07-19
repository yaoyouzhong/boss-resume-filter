# -*- coding: utf-8 -*-
"""一次性诊断探针：验证 kimi coding 端点对 max_tokens/max_completion_tokens 的行为。

只读诊断，不写任何文件、不打印密钥。
用法: python tests/manual/_probe_kimi_coding.py
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
    print("NO_KEY: 钥匙串中没有 kimi + api.kimi.com/coding 的 Key")
    sys.exit(1)
print(f"KEY_OK: 已取到 Key（长度 {len(api_key)}，内容不打印）")

headers = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {api_key}",
}
messages = [{"role": "user", "content": "只回复两个字：你好"}]


def probe(label, body, timeout=(10, 90)):
    start = time.monotonic()
    try:
        resp = requests.post(
            f"{BASE_URL}/chat/completions",
            json=body, headers=headers, timeout=timeout,
        )
    except requests.exceptions.ConnectTimeout:
        print(f"{label}: CONNECT_TIMEOUT")
        return
    except requests.exceptions.ReadTimeout:
        print(f"{label}: READ_TIMEOUT after {time.monotonic()-start:.1f}s")
        return
    except requests.exceptions.RequestException as exc:
        print(f"{label}: {type(exc).__name__}: {str(exc)[:150]}")
        return
    elapsed = time.monotonic() - start
    print(f"{label}: HTTP {resp.status_code} in {elapsed:.1f}s")
    if resp.status_code != 200:
        print(f"  body: {resp.text[:300]}")
        return
    data = resp.json()
    choice = (data.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    usage = data.get("usage") or {}
    print(f"  finish_reason={choice.get('finish_reason')!r} content={str(msg.get('content'))[:60]!r}")
    reasoning = msg.get("reasoning_content")
    print(f"  reasoning_content={'<%d chars>' % len(reasoning) if reasoning else None}")
    print(f"  usage={json.dumps(usage, ensure_ascii=False)}")


# 1. job_ai_parser 的原始发法：max_tokens + temperature=0.1
probe("A max_tokens=2000 temp=0.1 (job_ai_parser 现状)", {
    "model": MODEL, "messages": messages,
    "max_tokens": 2000, "temperature": 0.1, "stream": False,
})

# 2. ai_adapter 的 Kimi Coding 发法：max_completion_tokens + temperature=1
probe("B max_completion_tokens=1024 temp=1 (ai_adapter 现状)", {
    "model": MODEL, "messages": messages,
    "max_completion_tokens": 1024, "temperature": 1, "stream": False,
})

# 3. 小预算下 k3 是否把 token 全耗在 reasoning 上（复现学历核验报错）
probe("C max_completion_tokens=300 temp=1 (模拟小预算)", {
    "model": MODEL, "messages": messages,
    "max_completion_tokens": 300, "temperature": 1, "stream": False,
})
