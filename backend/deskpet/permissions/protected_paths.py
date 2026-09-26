# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Open by default, protected core files on request (plan 2026-09-26-permission-open-by-default).

User decision 2026-09-26: the agent may read and write anywhere except a small
set of core files; touching one of those asks the user in the session window,
and the run is **never** held waiting for the answer.

- :func:`classify` is the one verdict: ``"allow"`` or ``"ask"``.
- An ``ask`` call is refused at once with a plain message (the model goes on
  with other work or tells the user), and a card is pushed to the app
  (``protected_path_request``).  The user's click (``protected_path_decision``)
  records a grant: *once* (the next matching call) or *session* (every call of
  that session).  The model's retry then passes.
- :func:`dispatch_clearance` marks, for one dispatch, the paths the central
  checkpoint has already cleared, so the per-tool containment checks
  (:func:`guard`) agree with it instead of refusing a second time.

Grants live in memory: a restart asks again, which is acceptable.
"""

from __future__ import annotations

import contextlib
import contextvars
import logging
import os
import re
import sys
import tempfile
import threading
import uuid
from collections.abc import Awaitable, Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

logger = logging.getLogger(__name__)

Op = Literal["read", "write"]
Verdict = Literal["allow", "ask"]

# Credentials and keys: reading them is as sensitive as writing.
_CREDENTIAL_DIRS = (".ssh", ".aws", ".gnupg", ".kube", ".grok", ".config/gcloud", "Library/Keychains")
_CREDENTIAL_FILES = (".docker/config.json", ".netrc")
_CREDENTIAL_NAME = re.compile(
    r"^(?:\.env(?:\..+)?|llm_runtime.*\.json|id_rsa.*|id_ed25519.*|id_ecdsa.*|login\.keychain.*|"
    r"cookies(?:\.sqlite)?|login data)$",
    re.IGNORECASE,
)
# Operating-system directories: reading is fine, writing asks.
_SYSTEM_ROOTS_POSIX = ("/System", "/usr", "/bin", "/sbin", "/etc", "/private/etc", "/private/var/db", "/Library")
_SYSTEM_EXCEPTIONS_POSIX = ("/usr/local",)

READ_TOOL_HINTS = ("read", "list", "glob", "grep", "search", "ocr", "extract", "view", "inspect", "stat", "find")
PATH_ARGUMENT_KEYS = (
    "path", "file_path", "dir_path", "directory", "source", "src", "destination", "dest", "target",
    "output_path", "output", "input_path", "image_path", "cwd", "save_path", "filename",
)

MESSAGE = (
    "「{path}」是受保护的核心文件（{reason}），已在会话窗口里向用户申请{op_label}权限。"
    "这次调用没有执行。用户同意后再调用一次即可；在那之前请先做不涉及它的事，"
    "或告诉用户需要在弹出的卡片上点「允许」。"
)
_REASON_LABEL = {"credential": "凭证或密钥", "self": "本应用自身的程序或数据", "system": "系统目录"}


def _canonical(path: str | os.PathLike[str], base: Path | None = None) -> Path:
    try:
        candidate = Path(path).expanduser()
    except RuntimeError:
        candidate = Path(path)
    if not candidate.is_absolute():
        # never the process cwd (the backend directory lives in the source tree)
        candidate = (base if base is not None else _default_workspace()) / candidate
    try:
        return candidate.resolve()
    except (OSError, RuntimeError):
        return Path(os.path.abspath(candidate))


def _default_workspace() -> Path:
    try:
        from agent.write_scope import resolve_workspace_root

        return resolve_workspace_root()
    except Exception:  # noqa: BLE001
        return Path(tempfile.gettempdir())


_CASE_INSENSITIVE = sys.platform in ("darwin", "win32")


def _fold(path: Path) -> str:
    text = path.as_posix()
    return text.casefold() if _CASE_INSENSITIVE else text


def _under(path: Path, root: Path) -> bool:
    """Containment on the real filesystem's terms: macOS and Windows compare
    names without case, so ``~/.SSH/key`` is ``~/.ssh/key`` (verification
    2026-09-26 found the case-sensitive compare let it through)."""

    candidate, base = _fold(path), _fold(root).rstrip("/")
    return candidate == base or candidate.startswith(base + "/")


def _home() -> Path:
    return _canonical(Path.home())


def _self_roots() -> list[Path]:
    roots: list[Path] = []
    try:
        import paths  # type: ignore[import-not-found]

        backend = Path(paths.__file__).resolve().parent
        roots.append(backend.parent)  # the Host repository / install tree
        install = paths._install_dir()
        if install is not None:
            roots.append(install)
        data = paths.user_data_dir()
        roots += [data / "config.toml", data / "data"]
    except Exception:  # noqa: BLE001 - classification never raises
        pass
    executable = Path(sys.executable).resolve()
    for parent in executable.parents:
        if parent.suffix == ".app":
            roots.append(parent)
            break
    for base in (Path("/Applications"), _home() / "Applications"):
        roots.append(base / "SimpleHarness.app")
    return [_canonical(root) for root in roots]


def allowed_roots(extra: Iterable[str | os.PathLike[str]] = ()) -> list[Path]:
    """Always-allowed roots: the agent's workspace, the app's output directory,
    the temp directory and any root the caller already holds (a task's bound
    roots).  They win over the "self" and "system" rules (in development the
    workspace sits inside the source tree) but never over credentials."""

    roots: list[Path] = []
    try:
        from agent.write_scope import resolve_workspace_root

        roots.append(resolve_workspace_root())
    except Exception:  # noqa: BLE001
        pass
    try:
        import paths  # type: ignore[import-not-found]

        roots.append(paths.output_dir())
    except Exception:  # noqa: BLE001
        pass
    roots.append(Path(tempfile.gettempdir()))
    roots += [Path(root) for root in extra if root]
    return [_canonical(root) for root in roots]


def protected_reason(path: Path, op: Op) -> str | None:
    """Which protected class ``path`` (canonical) falls in for ``op``, if any."""

    home = _home()
    if any(_under(path, home / d) for d in _CREDENTIAL_DIRS) or any(_under(path, home / f) for f in _CREDENTIAL_FILES):
        return "credential"
    if _CREDENTIAL_NAME.match(path.name):
        return "credential"
    if op == "read":
        return None
    if any(_under(path, root) for root in _self_roots()):
        return "self"
    try:
        from deskpet.tools.office_paths import is_system_path

        if is_system_path(path):  # Windows system roots (harmless elsewhere)
            return "system"
    except Exception:  # noqa: BLE001
        pass
    if os.name == "nt":
        return None
    if any(_under(path, Path(e)) for e in _SYSTEM_EXCEPTIONS_POSIX):
        return None
    if any(_under(path, Path(r)) for r in _SYSTEM_ROOTS_POSIX):
        return "system"
    return None


def classify(path: str | os.PathLike[str], op: Op, *, base: Path | None = None,
             extra_allowed: Iterable[str | os.PathLike[str]] = ()) -> Verdict:
    canonical = _canonical(path, base)
    reason = protected_reason(canonical, op)
    if reason == "credential":
        return "ask"  # credentials ask wherever they sit, the workspace included
    if reason is None or any(_under(canonical, root) for root in allowed_roots(extra_allowed)):
        return "allow"
    return "ask"


def op_for_tool(tool_name: str) -> Op:
    name = tool_name.lower()
    return "read" if any(hint in name for hint in READ_TOOL_HINTS) else "write"


def shell_tokens(command: str) -> list[str]:
    """Every word of a shell command that could name a file (for reads such as
    ``cat ~/.ssh/id_rsa`` or ``cp ~/.ssh/id_rsa /tmp/k``)."""

    import shlex

    try:
        words = shlex.split(command, posix=os.name != "nt")
    except ValueError:
        words = command.split()
    out: list[str] = []
    for word in words:
        word = os.path.expandvars(word)  # ``cat $HOME/.ssh/key`` names the same file
        for part in re.split(r"[=<>|;&]+", word):
            part = part.strip("\"'")
            if part and ("/" in part or "\\" in part or part.startswith(("~", ".")) or _CREDENTIAL_NAME.match(Path(part).name)):
                out.append(part)
    return out


def credential_reads_in_command(command: str, base: Path | None = None) -> list[Path]:
    return [p for p in (_canonical(t, base) for t in shell_tokens(command)) if protected_reason(p, "read") == "credential"]


def paths_in_arguments(arguments: Mapping[str, Any]) -> list[str]:
    found: list[str] = []
    for key in PATH_ARGUMENT_KEYS:
        value = arguments.get(key)
        if isinstance(value, str) and value.strip():
            found.append(value.strip())
        elif isinstance(value, (list, tuple)):
            found += [v.strip() for v in value if isinstance(v, str) and v.strip()]
    command = arguments.get("command")
    if isinstance(command, str) and command.strip():
        try:
            from agent.write_scope import extract_shell_write_targets

            found += extract_shell_write_targets(command)
        except Exception:  # noqa: BLE001
            pass
    return found


# ---------------------------------------------------------------- grants


@dataclass(frozen=True)
class PendingRequest:
    request_id: str
    session_id: str
    path: str
    op: Op
    tool: str
    reason: str


_lock = threading.Lock()
_session_grants: dict[str, set[tuple[str, Op]]] = {}
_once_grants: dict[str, set[tuple[str, Op]]] = {}
_pending: dict[str, PendingRequest] = {}
Notifier = Callable[[dict[str, Any]], Awaitable[None]]
_notifier: Notifier | None = None


def set_notifier(notifier: Notifier | None) -> None:
    """main.py installs the control-channel broadcast at startup."""

    global _notifier
    _notifier = notifier


def reset_for_tests() -> None:
    with _lock:
        _session_grants.clear()
        _once_grants.clear()
        _pending.clear()


def _key(path: Path, op: Op) -> tuple[str, Op]:
    return (_fold(path), op)


def granted(session_id: str, path: Path, op: Op, *, consume: bool) -> bool:
    key = _key(path, op)
    with _lock:
        if key in _session_grants.get(session_id, set()) or (op == "read" and _key(path, "write") in _session_grants.get(session_id, set())):
            return True
        once = _once_grants.get(session_id, set())
        if key in once:
            if consume:
                once.discard(key)
            return True
    return False


async def request(session_id: str, path: Path, op: Op, tool: str, reason: str) -> PendingRequest:
    """Record one pending request (deduplicated) and push the card."""

    with _lock:
        for pending in _pending.values():
            if (pending.session_id, _fold(Path(pending.path)), pending.op) == (session_id, _fold(path), op):
                existing = pending
                break
        else:
            existing = None
            pending = PendingRequest(uuid.uuid4().hex, session_id, str(path), op, tool, reason)
            _pending[pending.request_id] = pending
    target = existing or pending
    # Pushed on every refusal, not only the first: a card lost while the UI was
    # disconnected must come back on the model's retry (the UI dedupes by id).
    if _notifier is not None:
        try:
            await _notifier({"type": "protected_path_request", "payload": {
                "request_id": target.request_id, "session_id": session_id, "path": target.path,
                "op": op, "tool": tool, "reason": _REASON_LABEL.get(reason, reason)}})
        except Exception as error:  # noqa: BLE001 - the refusal already reached the model
            logger.warning("protected_path_request_push_failed: %s", error)
    return target


def decide(request_id: str, decision: str) -> PendingRequest | None:
    """Apply the user's click. ``allow_once`` | ``allow_session`` | ``deny``."""

    with _lock:
        pending = _pending.pop(request_id, None)
        if pending is None:
            return None
        key = _key(Path(pending.path), pending.op)
        if decision == "allow_session":
            _session_grants.setdefault(pending.session_id, set()).add(key)
        elif decision == "allow_once":
            _once_grants.setdefault(pending.session_id, set()).add(key)
    logger.info("protected_path_decision decision=%s op=%s", decision, pending.op)
    return pending


def is_credential(path: str | os.PathLike[str]) -> bool:
    """For tools that walk directories: skip a credential file unless this
    dispatch cleared it."""

    canonical = _canonical(path)
    if protected_reason(canonical, "read") != "credential":
        return False
    return _key(canonical, "read") not in _clearance.get()


def refusal_message(path: Path, op: Op, reason: str) -> str:
    return MESSAGE.format(path=path, reason=_REASON_LABEL.get(reason, reason),
                          op_label="读取" if op == "read" else "写入")


# ------------------------------------------------------------ clearance

_clearance: contextvars.ContextVar[frozenset[tuple[str, Op]]] = contextvars.ContextVar(
    "protected_path_clearance", default=frozenset())


@contextlib.contextmanager
def dispatch_clearance(cleared: Iterable[tuple[Path, Op]]) -> Iterator[None]:
    token = _clearance.set(frozenset(_key(p, o) for p, o in cleared))
    try:
        yield
    finally:
        _clearance.reset(token)


def guard(path: str | os.PathLike[str], op: Op, *, base: Path | None = None,
          extra_allowed: Iterable[str | os.PathLike[str]] = ()) -> str | None:
    """Per-tool containment check: ``None`` admits, else the refusal text.

    Paths the central checkpoint already cleared for this dispatch pass; so do
    reads of a path cleared for writing."""

    canonical = _canonical(path, base)
    if classify(canonical, op, extra_allowed=extra_allowed) == "allow":
        return None
    cleared = _clearance.get()
    if _key(canonical, op) in cleared or (op == "read" and _key(canonical, "write") in cleared):
        return None
    return refusal_message(canonical, op, protected_reason(canonical, op) or "self")


async def check_call(session_id: str, tool: str, arguments: Mapping[str, Any], *, base: Path | None,
                     extra_allowed: Iterable[str | os.PathLike[str]] = ()) -> tuple[str | None, list[tuple[Path, Op]]]:
    """The central checkpoint for one tool call.

    Returns ``(refusal, cleared)``: a refusal text when some protected path has
    no grant (the card was pushed), else the list of protected paths cleared by
    a grant (for :func:`dispatch_clearance`)."""

    op = op_for_tool(tool)
    cleared: list[tuple[Path, Op]] = []
    extra = list(extra_allowed)
    targets: list[tuple[Path, Op]] = [(_canonical(raw, base), op) for raw in paths_in_arguments(arguments)]
    command = arguments.get("command")
    if isinstance(command, str):
        targets += [(p, "read") for p in credential_reads_in_command(command, base)]
    for canonical, op in targets:
        if classify(canonical, op, extra_allowed=extra) == "allow":
            continue
        if granted(session_id, canonical, op, consume=True):
            cleared.append((canonical, op))
            continue
        reason = protected_reason(canonical, op) or "self"
        await request(session_id, canonical, op, tool, reason)
        return refusal_message(canonical, op, reason), []
    return None, cleared


__all__ = (
    "PATH_ARGUMENT_KEYS", "PendingRequest", "allowed_roots", "check_call", "classify", "decide",
    "credential_reads_in_command", "dispatch_clearance", "granted", "guard", "is_credential", "op_for_tool",
    "paths_in_arguments", "protected_reason", "shell_tokens",
    "refusal_message", "request", "reset_for_tests", "set_notifier",
)
