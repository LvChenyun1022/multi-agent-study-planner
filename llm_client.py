# -*- coding: utf-8 -*-
"""llm_client.py —— 真实 LLM 接入层（OpenAI 兼容接口，纯标准库实现）。

提供两个公开接口：

1. ``OpenAICompatibleLLM`` —— 实现 ``LLMClient`` 协议（``chat(system, user) -> str``）。
   通过 ``POST {base_url}/chat/completions``（Bearer 认证）调用任何
   OpenAI 兼容的大模型服务（DeepSeek / Moonshot / 各类中转站均可）。
   - 仅用标准库 urllib，不新增第三方依赖；
   - 超时 60 秒，失败自动重试 1 次；
   - **永不抛出异常**：任何网络/解析错误都会打印警告并回退用
     ``MockLLM`` 回答这一次调用，保证演示流程永不崩；
   - 报错信息中不泄露 API Key。

2. ``create_llm_from_config(config_path="config.json")`` —— 工厂函数。
   读取 config.json 的 ``"llm"`` 段（base_url / api_key / model），
   任一缺失或文件不存在时返回 ``None``（调用方传 None 给 Agent，
   BaseAgent 会自动使用 MockLLM，行为与原来一致）。

配置示例（config.json，已被 .gitignore 忽略）::

    {
      "llm": {
        "base_url": "https://yuanyuaicloud.cn/v1",
        "api_key": "sk-...",
        "model": "deepseek-v4-pro"
      }
    }
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from agents.base_agent import MockLLM

# 默认超时（秒）与重试次数
_TIMEOUT = 60
_MAX_RETRIES = 1  # 首次失败后再重试 1 次，共最多 2 次请求


class OpenAICompatibleLLM:
    """OpenAI 兼容接口的 LLM 客户端（chat/completions 端点）。

    参数：
        base_url: 服务根地址，如 ``https://yuanyuaicloud.cn/v1``
        api_key:  Bearer 令牌
        model:    模型名，如 ``deepseek-v4-pro``
    """

    def __init__(self, base_url: str, api_key: str, model: str,
                 timeout: int = _TIMEOUT, max_retries: int = _MAX_RETRIES) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.max_retries = max_retries
        self._fallback = MockLLM()  # 失败兜底：用规则模板回答，演示不中断

    # ------------------------------------------------------------------
    # LLMClient 协议实现
    # ------------------------------------------------------------------
    def chat(self, system: str, user: str) -> str:
        """调用真实 API；失败（含重试）则打印警告并回退 MockLLM。"""
        try:
            return self._chat_with_retry(system, user)
        except Exception as exc:  # noqa: BLE001 —— 兜底设计：任何异常都不外抛
            print(f"⚠ [LLM] 真实 API 调用失败，本次回退 MockLLM：{_脱敏(exc)}")
            return self._fallback.chat(system, user)

    # ------------------------------------------------------------------
    # 内部实现
    # ------------------------------------------------------------------
    def _chat_with_retry(self, system: str, user: str) -> str:
        """发起请求，失败重试 ``max_retries`` 次，全部失败抛异常给上层兜底。"""
        last_exc: Exception | None = None
        for 尝试 in range(1 + self.max_retries):
            try:
                return self._request_once(system, user)
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                if 尝试 < self.max_retries:
                    print(f"⚠ [LLM] 第 {尝试 + 1} 次请求失败，重试中：{_脱敏(exc)}")
        raise last_exc  # type: ignore[misc]

    def _request_once(self, system: str, user: str) -> str:
        """单次 HTTP 请求：POST {base_url}/chat/completions。"""
        url = f"{self.base_url}/chat/completions"
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        return body["choices"][0]["message"]["content"]


# ---------------------------------------------------------------------------
# 工厂函数
# ---------------------------------------------------------------------------
def create_llm_from_config(config_path: str = "config.json"):
    """从 config.json 的 "llm" 段创建真实 LLM 客户端。

    返回：
        - 配置齐全 → ``OpenAICompatibleLLM`` 实例；
        - 文件不存在 / JSON 损坏 / base_url、api_key、model 任一缺失 → ``None``
          （调用方传 None 给 Agent，BaseAgent 会自动使用 MockLLM）。
    """
    if not os.path.exists(config_path):
        return None
    try:
        with open(config_path, encoding="utf-8") as f:
            config = json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        print(f"⚠ [LLM] 读取 {config_path} 失败（{exc}），将使用 MockLLM。")
        return None

    llm_cfg = config.get("llm") or {}
    base_url = (llm_cfg.get("base_url") or "").strip()
    api_key = (llm_cfg.get("api_key") or "").strip()
    model = (llm_cfg.get("model") or "").strip()
    if not (base_url and api_key and model):
        return None
    return OpenAICompatibleLLM(base_url=base_url, api_key=api_key, model=model)


def _脱敏(exc: Exception) -> str:
    """把异常转成不含 API Key 的简短描述，避免把凭据打进日志。"""
    文本 = f"{type(exc).__name__}: {exc}"
    # HTTPError 的 read 内容可能很长，只截前 200 字符；urlopen 不会把
    # 请求头放进异常消息，但保险起见统一截断并剔除疑似 sk- 串
    if len(文本) > 200:
        文本 = 文本[:200] + "..."
    return 文本.replace("sk-", "sk-***")
