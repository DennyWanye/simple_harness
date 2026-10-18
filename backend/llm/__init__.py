# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""LLM 调用的公共类型与错误。

    types.py              ChatResponse / ChatChunk / ToolCall / ChatUsage
    errors.py             LLMProviderError 及 429 / 超时 / 鉴权 / 预算子类
    provider_registry.py  用户配置的提供方列表（config.toml ``[[llm.endpoints]]``，密钥在系统钥匙串）
    pricing.py            按模型的每百万 token 价格表
    model_info.py         模型窗口等元数据
    context_messages.py   提供方调用的用途与尝试上下文变量

实际发请求走 ``providers/openai_compatible.py``（OpenAI 兼容协议）。
"""

from llm.errors import (
    LLMAuthError,
    LLMBudgetExceededError,
    LLMProviderError,
    LLMRateLimitError,
    LLMTimeoutError,
)
from llm.types import ChatChunk, ChatResponse, ChatUsage, ToolCall

__all__ = [
    "ChatChunk",
    "ChatResponse",
    "ChatUsage",
    "ToolCall",
    "LLMAuthError",
    "LLMBudgetExceededError",
    "LLMProviderError",
    "LLMRateLimitError",
    "LLMTimeoutError",
]
