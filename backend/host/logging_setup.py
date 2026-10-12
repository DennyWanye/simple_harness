"""后端进程的日志配置：标准库日志写文件与控制台，structlog 输出 JSON，并做日志脱敏。导入本模块即完成配置。"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

import paths as _paths
import structlog
from observability.log_redaction import add_safe_exception_summary, redact_log_event
from observability.sdk import HostSdkObservability

# P2-2 debug (2026-04-20): Rust supervisor drains child stdout/stderr after
# the SHARED_SECRET handshake, so structlog's console output vanishes once
# the frontend is driving the backend. Mirror everything into
# logs/backend.log via the stdlib logging root so we can tail pipeline
# events (asr_result / vad / lip_sync) without bouncing through the
# supervisor. structlog defaults to using stdlib logging under the hood,
# so configuring the root handler is enough.
_log_dir = _paths.user_log_dir()
_log_dir.mkdir(parents=True, exist_ok=True)
_log_file = _log_dir / "backend.log"
_file_handler = RotatingFileHandler(
    _log_file,
    maxBytes=20 * 1024 * 1024,
    backupCount=5,
    encoding="utf-8",
)
_stream_handler = logging.StreamHandler()
_foreign_pre_chain = [
    structlog.contextvars.merge_contextvars,
    structlog.stdlib.add_logger_name,
    structlog.stdlib.add_log_level,
    structlog.stdlib.PositionalArgumentsFormatter(),
    # 编排器的进度备注（``orchestrator.progress``）只把内容放在 stdlib ``extra={"detail": …}``；
    # 不带出来，"系统发布没提交成功"这类原因在日志里只剩一个空事件名。只放行这一个字段，
    # 且在脱敏之前加入，照样经过脱敏。
    structlog.stdlib.ExtraAdder(allow=("detail",)),
    add_safe_exception_summary,
    redact_log_event,
    structlog.processors.TimeStamper(fmt="iso"),
]
_json_log_formatter = structlog.stdlib.ProcessorFormatter(
    processor=structlog.processors.JSONRenderer(sort_keys=True),
    foreign_pre_chain=_foreign_pre_chain,
)
_file_handler.setFormatter(_json_log_formatter)
_stream_handler.setFormatter(_json_log_formatter)
logging.basicConfig(
    level=logging.INFO,
    handlers=[_stream_handler, _file_handler],
    force=True,  # override anything uvicorn may have installed earlier
)
# aiosqlite DEBUG records interpolate bound parameters, which can include
# conversation and recalled-memory plaintext.  Keep the dependency below the
# application log boundary even when a developer raises the root level.
logging.getLogger("aiosqlite").setLevel(logging.WARNING)

_sdk_observability = HostSdkObservability(_log_dir)

# structlog defaults to its own PrintLogger (stdout only). Point it at
# stdlib logging so the FileHandler above actually receives events.
structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        add_safe_exception_summary,
        redact_log_event,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
    ],
    logger_factory=structlog.stdlib.LoggerFactory(),
    wrapper_class=structlog.stdlib.BoundLogger,
    cache_logger_on_first_use=True,
)
