"""宿主进程的全局单例：崩溃上报钩子、日志器、配置、共享密钥、服务注册表。这些对象在导入时创建、之后不再重新绑定。"""

from __future__ import annotations

import os
import secrets

import paths as _paths
import structlog

# 日志配置必须先于本模块的第一条日志（崩溃上报安装、配置加载）：
# 后端标准输出要交给外壳读共享密钥，未配置时 structlog 默认写标准输出（2026-10-13 修复）。
import host.logging_setup  # noqa: F401  # isort: skip
from config import load_config, resolve_config_path
from context import ServiceContext
from observability.crash_reports import install_crash_reporter

# Install the uncaught-exception hook as early as possible so import-time
# failures later in this file still land in crash_reports/.
install_crash_reporter()

# 记录器名固定为 "main"：主入口拆进 host 包之前日志里一直是这个名字；
# 不带名字时取第一次打日志的模块，拆分后会变成别的模块名（2026-10-13）。
logger = structlog.get_logger("main")

# ensure user data / cache / models directories exist before any
# subsystem tries to write into them. Also seeds <user_data>/config.toml
# from the bundle default on first run (via resolve_config_path).
_paths.ensure_user_dirs()

_CONFIG_PATH = resolve_config_path()
config = load_config(_CONFIG_PATH)
logger.info(
    "config_loaded",
    path=str(_CONFIG_PATH),
    exists=_CONFIG_PATH.is_file(),
    user_data_dir=str(_paths.user_data_dir()),
    user_models_dir=str(_paths.user_models_dir()),
    model_root=str(_paths.model_root()),
    # 2026-06-28 路径绑定可观测：portable=是否走 <install>/userdata；
    # env_pinned=Tauri 是否注入了 DESKPET_USER_DATA_DIR（单一事实源生效）。
    # 装机版路径漂移诊断就看这两个 + path 跨会话是否恒定。
    portable=_paths.is_portable_mode(),
    env_pinned=bool(os.environ.get("DESKPET_USER_DATA_DIR")),
)
PROJECT_ROOT = _CONFIG_PATH.parent
SHARED_SECRET = secrets.token_hex(16)

service_context = ServiceContext()
service_context.register("execution_uow", None)
service_context.register("run_execution_fence_acquirer", None)
service_context.register("provider_invocation_coordinator", None)
service_context.register("harness_public_read_service", None)
service_context.register("sdk_runtime_ready", None)
