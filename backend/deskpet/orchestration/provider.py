# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""编排用哪个模型：启动时读一次提供方链的首项（与聊天同一来源）成 ``ProviderSnapshot``，再换成
SDK 宿主的 ``ModelConfig``。换提供方要重启才对新任务生效。全新安装没有链：服务不可用，原因
"未配置模型"，聊天照常。密钥只进 SDK 的 ``Secret``，不进 repr、日志、回执与清单。
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent_orchestrator.host import ModelConfig
from agent_orchestrator.host.settings import DEEPSEEK_OFFICIAL_HOSTS, MODEL_ALIASES

from .settings import compatible_hosts

NO_MODEL = "未配置模型"
TOKENIZER_PATH_ENV = "DESKPET_ORCH_TOKENIZER_PATH"

logger = logging.getLogger(__name__)


class ProviderUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class ProviderSnapshot:
    provider_id: str
    base_url: str
    configured_model: str
    requested_model: str
    api_key: str = field(repr=False)
    response_model_aliases: tuple[str, ...] = ()

    def public(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "configured": self.configured_model,
            "requested": self.requested_model,
        }


def requested_model(base_url: str, configured: str) -> str:
    from urllib.parse import urlparse

    host = (urlparse(base_url).hostname or "").lower()
    if host in DEEPSEEK_OFFICIAL_HOSTS:
        return MODEL_ALIASES.get(configured, configured)
    return configured


def snapshot_from_registry(registry: Any) -> ProviderSnapshot:
    if registry is None:
        raise ProviderUnavailable(NO_MODEL)
    try:
        chain = registry.get_chain()
    except Exception as error:  # NoProviderConfiguredError on a fresh install
        raise ProviderUnavailable(NO_MODEL) from error
    if not chain:
        raise ProviderUnavailable(NO_MODEL)
    entry = dict(chain[0])
    models = list(entry.get("models") or ())
    configured = str(
        entry.get("default_model")
        or entry.get("model")
        or (models[0] if models else "")
    )
    base_url = str(entry.get("base_url") or "")
    provider_id = str(entry.get("id") or "")
    if not (configured and base_url and provider_id):
        raise ProviderUnavailable(NO_MODEL)
    key = registry.resolve_api_key(provider_id)
    if not key:
        raise ProviderUnavailable(f"{NO_MODEL}：provider {provider_id} 没有可用的密钥")
    return ProviderSnapshot(
        provider_id=provider_id,
        base_url=base_url,
        configured_model=configured,
        requested_model=requested_model(base_url, configured),
        api_key=str(key),
    )


def model_config(snapshot: ProviderSnapshot, settings: Any) -> ModelConfig:
    """SDK 宿主的模型配置：转发主机、回显别名与思考开关来自设置。"""

    return ModelConfig(
        base_url=snapshot.base_url,
        model=snapshot.requested_model,
        api_key=snapshot.api_key,
        compatible_hosts=tuple(sorted(compatible_hosts(settings))),
        response_model_aliases=tuple(snapshot.response_model_aliases),
        thinking=str(getattr(settings, "thinking", "enabled") or "enabled"),
    )


def tokenizer_path() -> Path | None:
    """钉死的 DeepSeek 分词文件：``DESKPET_ORCH_TOKENIZER_PATH``（绝对路径），否则模型目录下的
    ``deepseek-v41/tokenizer.json``。"""

    configured = os.environ.get(TOKENIZER_PATH_ENV)
    if configured:
        path = Path(configured)
        return path if path.is_absolute() else None
    try:
        # 按需导入：可选依赖
        from paths import user_models_dir
    except ImportError:
        return None
    candidate = Path(user_models_dir()) / "deepseek-v41" / "tokenizer.json"
    return candidate if candidate.is_file() else None


def models_dir() -> Path | None:
    try:
        # 按需导入：可选依赖
        from paths import user_models_dir
    except ImportError:
        return None
    try:
        return Path(user_models_dir())
    except Exception:
        logger.warning(
            "user models dir unavailable; vector lane stays closed", exc_info=True
        )
        return None


__all__ = (
    "NO_MODEL",
    "ProviderSnapshot",
    "ProviderUnavailable",
    "model_config",
    "models_dir",
    "requested_model",
    "snapshot_from_registry",
    "tokenizer_path",
)
