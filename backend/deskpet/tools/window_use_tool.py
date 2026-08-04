# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Identity-bound Windows window capture and input tools."""

from __future__ import annotations

import base64
import ctypes
import json
import os
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import psutil

from deskpet.types.task_grants import ResourceSelector

from .capabilities import ToolExecutionContext
from .computer_use_tool import (
    _CU_HIDE_ENV,
    _capture_workspace,
    _err,
    _guard,
    _load_mss,
    _load_pyautogui,
    _ok,
    _workspace_root,
)
from .registry import registry


_MAX_WINDOWS = 100
_MAX_HOLD_MS = 10_000
_MAX_SEQUENCE_STEPS = 12
_MAX_SEQUENCE_PAUSE_MS = 2_000
_MAX_SEQUENCE_DURATION_MS = 12_000
_KEYEVENTF_EXTENDEDKEY = 0x0001
_KEYEVENTF_KEYUP = 0x0002
_KEYEVENTF_SCANCODE = 0x0008
_MAPVK_VK_TO_VSC_EX = 4
_EXTENDED_VIRTUAL_KEYS = frozenset(
    {
        0x21,  # Page Up
        0x22,  # Page Down
        0x23,  # End
        0x24,  # Home
        0x25,  # Left
        0x26,  # Up
        0x27,  # Right
        0x28,  # Down
        0x2D,  # Insert
        0x2E,  # Delete
        0x5B,  # Left Windows
        0x5C,  # Right Windows
        0x6F,  # Numpad divide
        0x90,  # Num Lock
        0xA3,  # Right Control
        0xA5,  # Right Alt
    }
)
_NAMED_VIRTUAL_KEYS = {
    "backspace": 0x08,
    "tab": 0x09,
    "enter": 0x0D,
    "return": 0x0D,
    "shift": 0x10,
    "ctrl": 0x11,
    "control": 0x11,
    "alt": 0x12,
    "capslock": 0x14,
    "esc": 0x1B,
    "escape": 0x1B,
    "space": 0x20,
    "pageup": 0x21,
    "pagedown": 0x22,
    "end": 0x23,
    "home": 0x24,
    "left": 0x25,
    "up": 0x26,
    "right": 0x27,
    "down": 0x28,
    "insert": 0x2D,
    "delete": 0x2E,
    "win": 0x5B,
    "winleft": 0x5B,
    "winright": 0x5C,
    "numlock": 0x90,
    "scrolllock": 0x91,
}


@dataclass(frozen=True, slots=True)
class WindowIdentity:
    hwnd: int
    pid: int
    creation_time: float
    title: str
    left: int
    top: int
    width: int
    height: int
    minimized: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class WindowsWindowBackend:
    """Small ctypes adapter kept behind an injectable test seam."""

    def __init__(self) -> None:
        if os.name != "nt":
            raise OSError("window control is available only on Windows")
        from ctypes import wintypes

        self._wintypes = wintypes
        self._user32 = ctypes.windll.user32
        self._kernel32 = ctypes.windll.kernel32

        ulong_ptr = ctypes.c_size_t

        class MouseInput(ctypes.Structure):
            _fields_ = [
                ("dx", wintypes.LONG),
                ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", ulong_ptr),
            ]

        class KeyboardInput(ctypes.Structure):
            _fields_ = [
                ("wVk", wintypes.WORD),
                ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", ulong_ptr),
            ]

        class HardwareInput(ctypes.Structure):
            _fields_ = [
                ("uMsg", wintypes.DWORD),
                ("wParamL", wintypes.WORD),
                ("wParamH", wintypes.WORD),
            ]

        class InputUnion(ctypes.Union):
            _fields_ = [
                ("mi", MouseInput),
                ("ki", KeyboardInput),
                ("hi", HardwareInput),
            ]

        class Input(ctypes.Structure):
            _fields_ = [("type", wintypes.DWORD), ("value", InputUnion)]

        self._keyboard_input_type = KeyboardInput
        self._input_union_type = InputUnion
        self._input_type = Input

    def list_windows(self) -> tuple[WindowIdentity, ...]:
        wintypes = self._wintypes
        rows: list[WindowIdentity] = []

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def visit(hwnd, _lparam):
            if not self._user32.IsWindowVisible(hwnd):
                return True
            length = int(self._user32.GetWindowTextLengthW(hwnd))
            if length <= 0:
                return True
            title_buffer = ctypes.create_unicode_buffer(length + 1)
            self._user32.GetWindowTextW(hwnd, title_buffer, length + 1)
            title = title_buffer.value.strip()
            if not title:
                return True
            rect = wintypes.RECT()
            if not self._user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                return True
            width = int(rect.right - rect.left)
            height = int(rect.bottom - rect.top)
            if width <= 0 or height <= 0:
                return True
            pid_value = wintypes.DWORD()
            self._user32.GetWindowThreadProcessId(
                hwnd, ctypes.byref(pid_value)
            )
            pid = int(pid_value.value)
            try:
                created = float(psutil.Process(pid).create_time())
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                return True
            rows.append(
                WindowIdentity(
                    hwnd=int(hwnd),
                    pid=pid,
                    creation_time=created,
                    title=title,
                    left=int(rect.left),
                    top=int(rect.top),
                    width=width,
                    height=height,
                    minimized=bool(self._user32.IsIconic(hwnd)),
                )
            )
            return len(rows) < _MAX_WINDOWS

        self._user32.EnumWindows(visit, 0)
        return tuple(rows)

    def focus(self, hwnd: int) -> bool:
        if not self._user32.IsWindow(hwnd):
            return False
        foreground = int(self._user32.GetForegroundWindow() or 0)
        current_thread = int(self._kernel32.GetCurrentThreadId())
        foreground_thread = (
            int(self._user32.GetWindowThreadProcessId(foreground, None))
            if foreground
            else 0
        )
        target_thread = int(
            self._user32.GetWindowThreadProcessId(hwnd, None)
        )
        attached: list[int] = []
        detach_ok = True
        try:
            for thread_id in {foreground_thread, target_thread}:
                if (
                    thread_id
                    and thread_id != current_thread
                    and self._user32.AttachThreadInput(
                        current_thread, thread_id, True
                    )
                ):
                    attached.append(thread_id)
            self._user32.ShowWindow(hwnd, 9)  # SW_RESTORE
            self._user32.BringWindowToTop(hwnd)
            self._user32.SetForegroundWindow(hwnd)
            self._user32.SetFocus(hwnd)
        finally:
            for thread_id in reversed(attached):
                detached = False
                for _attempt in range(2):
                    if self._user32.AttachThreadInput(
                        current_thread, thread_id, False
                    ):
                        detached = True
                        break
                detach_ok = detach_ok and detached
        return (
            detach_ok
            and int(self._user32.GetForegroundWindow() or 0) == int(hwnd)
        )

    def _virtual_key(self, key: str) -> int:
        normalized = str(key or "").strip().casefold()
        if len(normalized) == 1:
            mapped = int(self._user32.VkKeyScanW(ord(normalized)))
            if mapped == -1:
                raise ValueError(f"unsupported key: {key}")
            modifiers = (mapped >> 8) & 0xFF
            if modifiers:
                raise ValueError(
                    f"key requires explicit modifiers: {key}; "
                    "pass a '+'-joined combo such as shift+1"
                )
            return mapped & 0xFF
        if normalized in _NAMED_VIRTUAL_KEYS:
            return _NAMED_VIRTUAL_KEYS[normalized]
        if (
            normalized.startswith("f")
            and normalized[1:].isdigit()
            and 1 <= int(normalized[1:]) <= 24
        ):
            return 0x6F + int(normalized[1:])
        raise ValueError(f"unsupported key: {key}")

    def _send_key(self, key: str, *, key_up: bool) -> None:
        virtual_key = self._virtual_key(key)
        scan_code = int(
            self._user32.MapVirtualKeyW(
                virtual_key,
                _MAPVK_VK_TO_VSC_EX,
            )
        )
        if scan_code <= 0:
            raise OSError(f"no hardware scan code for key: {key}")
        flags = _KEYEVENTF_SCANCODE
        if key_up:
            flags |= _KEYEVENTF_KEYUP
        if (
            virtual_key in _EXTENDED_VIRTUAL_KEYS
            or scan_code & 0xFF00 in {0xE000, 0xE100}
        ):
            flags |= _KEYEVENTF_EXTENDEDKEY
        keyboard = self._keyboard_input_type(
            0,
            scan_code & 0xFF,
            flags,
            0,
            0,
        )
        event = self._input_type(
            1,  # INPUT_KEYBOARD
            self._input_union_type(ki=keyboard),
        )
        sent = int(
            self._user32.SendInput(
                1,
                ctypes.byref(event),
                ctypes.sizeof(self._input_type),
            )
        )
        if sent != 1:
            raise ctypes.WinError()

    def key_down(self, key: str) -> None:
        self._send_key(key, key_up=False)

    def key_up(self, key: str) -> None:
        self._send_key(key, key_up=True)


_backend_lock = threading.Lock()
_window_key_lock = threading.Lock()
_DESKTOP_INPUT_LANE = "desktop-input:primary"
_WINDOW_BACKEND: WindowsWindowBackend | None = None


def _load_window_backend() -> WindowsWindowBackend:
    global _WINDOW_BACKEND
    with _backend_lock:
        if _WINDOW_BACKEND is None:
            _WINDOW_BACKEND = WindowsWindowBackend()
    return _WINDOW_BACKEND


def _parse_target(args: dict[str, Any]) -> tuple[int, float, int, str]:
    pid = args.get("pid")
    created = args.get("creation_time")
    hwnd = args.get("hwnd")
    title_contains = str(args.get("title_contains") or "").strip()
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        raise ValueError("pid must be a positive integer")
    if (
        not isinstance(created, (int, float))
        or isinstance(created, bool)
        or float(created) <= 0
    ):
        raise ValueError("creation_time must be a positive number")
    if (
        not isinstance(hwnd, int) or isinstance(hwnd, bool) or hwnd <= 0
    ):
        raise ValueError("hwnd must be a positive integer")
    return pid, float(created), hwnd, title_contains


def _find_target(args: dict[str, Any]) -> WindowIdentity:
    pid, created, hwnd, title_contains = _parse_target(args)
    matches = [
        window
        for window in _load_window_backend().list_windows()
        if window.pid == pid
        and abs(window.creation_time - created) < 0.01
        and window.hwnd == hwnd
        and (
            not title_contains
            or title_contains.casefold() in window.title.casefold()
        )
    ]
    if not matches:
        raise LookupError(
            "no visible window matches the exact process identity"
        )
    if len(matches) > 1:
        raise LookupError(
            "multiple windows match; pass hwnd or title_contains from window_list"
        )
    return matches[0]


def _window_list(args: dict[str, Any], _task_id: str = "") -> str:
    gated = _guard()
    if gated is not None:
        return gated
    query = str(args.get("query") or "").strip().casefold()
    try:
        windows = _load_window_backend().list_windows()
    except Exception as exc:  # noqa: BLE001
        return _err(
            f"window backend unavailable: {exc}",
            "Window discovery requires an interactive Windows desktop.",
        )
    visible = [
        window.to_dict()
        for window in windows
        if not query
        or query in window.title.casefold()
        or query == str(window.pid)
    ]
    return _ok(windows=visible[:_MAX_WINDOWS], count=len(visible))


def _window_focus(args: dict[str, Any], _task_id: str = "") -> str:
    gated = _guard()
    if gated is not None:
        return gated
    try:
        window = _find_target(args)
        focused = _load_window_backend().focus(window.hwnd)
    except Exception as exc:  # noqa: BLE001 - normalized tool envelope
        return _err(str(exc), "Call window_list again and use its exact identity.")
    if not focused:
        return _err(
            "window focus was not acquired",
            "The window may be closing or Windows may be blocking foreground activation.",
            retriable=True,
        )
    return _ok(window=window.to_dict(), focused=True)


def _capture_target(
    args: dict[str, Any],
    *,
    root: Path,
) -> str:
    gated = _guard()
    if gated is not None:
        return gated
    name = str(args.get("name") or "").strip()
    named_target: Path | None = None
    if name:
        relative = Path(name)
        if (
            relative.is_absolute()
            or relative.drive
            or str(relative).startswith(("\\\\", "//"))
        ):
            return _err("name must be workspace-relative")
        named_target = (root / relative).resolve(strict=False)
        try:
            named_target.relative_to(root)
        except ValueError:
            return _err("name escapes workspace")
        if named_target.suffix.casefold() != ".png":
            named_target = named_target.with_suffix(".png")
    capture_error: Exception | None = None
    close_error: Exception | None = None
    raw = b""
    window: WindowIdentity | None = None
    target: Path | None = None
    sct = None
    try:
        # mss configures process DPI awareness on first initialization.  Load
        # it before reading GetWindowRect so the very first capture uses the
        # same physical coordinate space as the screenshot backend.
        sct = _load_mss().mss()
        window = _find_target(args)
        if window.minimized:
            raise RuntimeError("target window is minimized")
        region = {
            "left": window.left,
            "top": window.top,
            "width": window.width,
            "height": window.height,
        }
        shot = sct.grab(region)
        verified = _find_target(args)
        if (
            verified.minimized
            or (
                verified.left,
                verified.top,
                verified.width,
                verified.height,
            )
            != (
                window.left,
                window.top,
                window.width,
                window.height,
            )
        ):
            raise RuntimeError(
                "target window geometry changed during capture"
            )
        target = named_target or (
            root
            / "screenshots"
            / f"window-{window.pid}-{int(time.time() * 1000)}.png"
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        from mss.tools import to_png

        to_png(shot.rgb, shot.size, output=str(target))
        raw = target.read_bytes()
    except Exception as exc:  # noqa: BLE001
        capture_error = exc
    finally:
        close = getattr(sct, "close", None)
        if callable(close):
            try:
                close()
            except Exception as exc:  # noqa: BLE001
                close_error = exc
    if capture_error is not None:
        suffix = (
            f"; backend close also failed: {close_error}"
            if close_error is not None
            else ""
        )
        return _err(
            f"window capture failed: {capture_error}{suffix}",
            retriable=True,
        )
    if close_error is not None:
        return _err(
            f"window capture backend close failed: {close_error}",
            retriable=True,
        )
    assert window is not None and target is not None
    return _ok(
        window=window.to_dict(),
        path=str(target.relative_to(root)).replace("\\", "/"),
        abs_path=str(target),
        bytes=len(raw),
        image_base64=base64.b64encode(raw).decode("ascii"),
        image_mime="image/png",
    )


def _window_capture(args: dict[str, Any], _task_id: str = "") -> str:
    return _capture_target(args, root=_workspace_root())


def _window_capture_context(
    args: dict[str, Any], context: ToolExecutionContext
) -> str:
    return _capture_target(args, root=_capture_workspace(context))


def _parse_key_press(args: dict[str, Any]) -> tuple[tuple[str, ...], int]:
    keys = str(args.get("keys") or "").strip()
    parts = tuple(
        part.strip().casefold() for part in keys.split("+") if part.strip()
    )
    if not parts or len(parts) > 4:
        raise ValueError("keys must contain one to four '+'-joined keys")
    hold_ms = args.get("hold_ms", 0)
    if (
        not isinstance(hold_ms, int)
        or isinstance(hold_ms, bool)
        or not 0 <= hold_ms <= _MAX_HOLD_MS
    ):
        raise ValueError(
            f"hold_ms must be an integer from 0 to {_MAX_HOLD_MS}"
        )
    return parts, hold_ms


def _execute_key_press(
    args: dict[str, Any], parts: tuple[str, ...], hold_ms: int
) -> str:
    try:
        window = _find_target(args)
        backend = _load_window_backend()
        if not backend.focus(window.hwnd):
            return _err("window focus was not acquired", retriable=True)
        use_native_keys = all(
            callable(getattr(backend, method, None))
            for method in ("key_down", "key_up")
        )
        pg = None if use_native_keys else _load_pyautogui()
    except Exception as exc:  # noqa: BLE001 - normalized tool envelope
        return _err(
            str(exc),
            "Call window_list again and use its exact identity.",
        )
    release_candidates: list[str] = []
    input_error: Exception | None = None
    release_errors: list[str] = []
    try:
        for part in parts:
            # Add before keyDown: a backend can raise after the OS accepted
            # the key-down event. A best-effort keyUp is safer than leaving
            # an uncertain modifier or movement key stuck.
            release_candidates.append(part)
            if use_native_keys:
                backend.key_down(part)
            else:
                pg.keyDown(part)
        if hold_ms:
            time.sleep(hold_ms / 1000.0)
    except Exception as exc:  # noqa: BLE001
        input_error = exc
    finally:
        for part in reversed(release_candidates):
            try:
                if use_native_keys:
                    backend.key_up(part)
                else:
                    pg.keyUp(part)
            except Exception as exc:  # noqa: BLE001
                release_errors.append(f"{part}: {exc}")
    if input_error is not None:
        suffix = (
            f"; key release errors: {', '.join(release_errors)}"
            if release_errors
            else ""
        )
        return _err(
            f"window key input failed: {input_error}{suffix}",
            retriable=True,
        )
    if release_errors:
        return _err(
            "window key release could not be verified: "
            + ", ".join(release_errors),
            "Do not send more input until the target window state is checked.",
            retriable=True,
        )
    return _ok(
        window=window.to_dict(),
        keys=list(parts),
        hold_ms=hold_ms,
        focused=True,
        released=True,
    )


def _parse_key_sequence(
    steps: Any,
) -> tuple[tuple[tuple[str, ...], int, int], ...]:
    if not isinstance(steps, list) or not 1 <= len(steps) <= _MAX_SEQUENCE_STEPS:
        raise ValueError(
            f"steps must contain 1 to {_MAX_SEQUENCE_STEPS} key steps"
        )
    parsed: list[tuple[tuple[str, ...], int, int]] = []
    total_ms = 0
    key_step_count = 0
    for index, step in enumerate(steps):
        if not isinstance(step, dict):
            raise ValueError(f"steps[{index}] must be an object")
        pause_ms = step.get("pause_ms", 0)
        if (
            not isinstance(pause_ms, int)
            or isinstance(pause_ms, bool)
            or not 0 <= pause_ms <= _MAX_SEQUENCE_PAUSE_MS
        ):
            raise ValueError(
                f"steps[{index}].pause_ms must be an integer from 0 to "
                f"{_MAX_SEQUENCE_PAUSE_MS}"
            )
        if str(step.get("keys") or "").strip():
            parts, hold_ms = _parse_key_press(step)
            key_step_count += 1
        else:
            hold_ms = step.get("hold_ms", 0)
            if hold_ms not in (0, None):
                raise ValueError(
                    f"steps[{index}].hold_ms requires a non-empty keys value"
                )
            if pause_ms <= 0:
                raise ValueError(
                    f"steps[{index}] must contain keys or a positive pause_ms"
                )
            parts = ()
            hold_ms = 0
        total_ms += hold_ms + pause_ms
        parsed.append((parts, hold_ms, pause_ms))
    if key_step_count == 0:
        raise ValueError("steps must contain at least one key step")
    if total_ms > _MAX_SEQUENCE_DURATION_MS:
        raise ValueError(
            "sequence hold and pause duration must total no more than "
            f"{_MAX_SEQUENCE_DURATION_MS} ms"
        )
    return tuple(parsed)


def _window_key(args: dict[str, Any], _task_id: str = "") -> str:
    gated = _guard()
    if gated is not None:
        return gated
    has_keys = "keys" in args
    has_steps = "steps" in args
    if has_keys == has_steps:
        return _err("provide exactly one of keys or steps")
    try:
        if has_steps:
            sequence = _parse_key_sequence(args.get("steps"))
        else:
            parts, hold_ms = _parse_key_press(args)
            sequence = ((parts, hold_ms, 0),)
    except ValueError as exc:
        return _err(str(exc))
    if not _window_key_lock.acquire(blocking=False):
        return _err(
            "window keyboard input is busy",
            "Retry after the current keyboard transaction finishes.",
            retriable=True,
        )
    try:
        completed: list[dict[str, Any]] = []
        last_window: dict[str, Any] | None = None
        for index, (parts, hold_ms, pause_ms) in enumerate(sequence):
            if parts:
                result = json.loads(_execute_key_press(args, parts, hold_ms))
                if not result.get("ok"):
                    result.update(
                        sequence=has_steps,
                        failed_step=index,
                        completed_steps=len(completed),
                    )
                    return json.dumps(result, ensure_ascii=False)
                last_window = result["window"]
                completed.append(
                    {
                        "keys": result["keys"],
                        "hold_ms": hold_ms,
                        "pause_ms": pause_ms,
                    }
                )
            else:
                completed.append({"pause_ms": pause_ms})
            if pause_ms:
                time.sleep(pause_ms / 1000.0)
        if not has_steps:
            return json.dumps(result, ensure_ascii=False)
        return _ok(
            window=last_window,
            sequence=True,
            completed_steps=len(completed),
            steps=completed,
            focused=True,
            released=True,
        )
    finally:
        _window_key_lock.release()


def _target_properties() -> dict[str, Any]:
    return {
        "pid": {"type": "integer", "minimum": 1},
        "creation_time": {"type": "number", "exclusiveMinimum": 0},
        "hwnd": {"type": "integer", "minimum": 1},
        "title_contains": {"type": "string"},
    }


def _window_resources(
    args: dict[str, Any],
    _context: ToolExecutionContext,
) -> tuple[ResourceSelector, ...]:
    pid, created, hwnd, _title = _parse_target(args)
    return (
        ResourceSelector(
            "desktop_target",
            f"window:{pid}:{created:.6f}:{hwnd}",
            ("control", "input"),
        ),
        ResourceSelector(
            "desktop_target",
            _DESKTOP_INPUT_LANE,
            ("control", "input"),
        ),
    )


def _window_capture_resources(
    args: dict[str, Any],
    context: ToolExecutionContext,
) -> tuple[ResourceSelector, ...]:
    pid, created, hwnd, _title = _parse_target(args)
    return (
        ResourceSelector(
            "desktop_target",
            f"window:{pid}:{created:.6f}:{hwnd}",
            ("observe", "read"),
        ),
        ResourceSelector(
            "desktop_target",
            _DESKTOP_INPUT_LANE,
            ("observe", "read"),
        ),
        ResourceSelector.filesystem(_capture_workspace(context), "write"),
    )


_TOOLSET = "computer_use"
_TARGET_REQUIRED = ["pid", "creation_time", "hwnd"]

registry.register(
    "window_list",
    _TOOLSET,
    {
        "name": "window_list",
        "description": (
            "List visible top-level Windows windows with hwnd, PID, process "
            "creation time, title, and bounds. Use the returned exact identity "
            "for window_focus, window_capture, and window_key."
        ),
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": [],
        },
    },
    _window_list,
    permission_category="read_file",
    requires_env=_CU_HIDE_ENV,
)
registry.register(
    "window_focus",
    _TOOLSET,
    {
        "name": "window_focus",
        "description": "Focus one exact visible window after PID-reuse validation.",
        "parameters": {
            "type": "object",
            "properties": _target_properties(),
            "required": _TARGET_REQUIRED,
        },
    },
    _window_focus,
    permission_category="shell",
    dangerous=True,
    requires_env=_CU_HIDE_ENV,
    concurrency_safe=False,
    resource_scope_resolver=_window_resources,
    resource_scope_resolver_id="builtin:window_focus:exact-window",
)
registry.register(
    "window_capture",
    _TOOLSET,
    {
        "name": "window_capture",
        "description": (
            "Capture only one exact window rectangle, not the full desktop. "
            "Returns a PNG path and base64 image for visual verification."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                **_target_properties(),
                "name": {"type": "string"},
            },
            "required": _TARGET_REQUIRED,
        },
    },
    _window_capture,
    context_handler=_window_capture_context,
    permission_category="read_file",
    requires_env=_CU_HIDE_ENV,
    concurrency_safe=False,
    resource_scope_resolver=_window_capture_resources,
    resource_scope_resolver_id="builtin:window_capture:exact-window",
)
registry.register(
    "window_key",
    _TOOLSET,
    {
        "name": "window_key",
        "description": (
            "Focus one exact window, then press either one key/combo or a short "
            "ordered steps sequence. Prefer steps for a bounded movement burst, "
            "then call window_capture to inspect the result; do not submit "
            "parallel window_key calls. All pressed keys are released even "
            "after errors."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                **_target_properties(),
                "keys": {"type": "string"},
                "hold_ms": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": _MAX_HOLD_MS,
                    "default": 0,
                },
                "steps": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": _MAX_SEQUENCE_STEPS,
                    "items": {
                        "type": "object",
                        "description": (
                            "A key step (non-empty keys, optional hold_ms and "
                            "post-key pause_ms) or a pause-only step (positive "
                            "pause_ms with keys omitted or empty)."
                        ),
                        "properties": {
                            "keys": {"type": "string"},
                            "hold_ms": {
                                "type": "integer",
                                "minimum": 0,
                                "maximum": _MAX_HOLD_MS,
                                "default": 0,
                            },
                            "pause_ms": {
                                "type": "integer",
                                "minimum": 0,
                                "maximum": _MAX_SEQUENCE_PAUSE_MS,
                                "default": 0,
                            },
                        },
                    },
                },
            },
            "required": _TARGET_REQUIRED,
        },
    },
    _window_key,
    permission_category="shell",
    dangerous=True,
    requires_env=_CU_HIDE_ENV,
    timeout_seconds=15.0,
    concurrency_safe=False,
    resource_scope_resolver=_window_resources,
    resource_scope_resolver_id="builtin:window_key:exact-window",
)


__all__ = [
    "WindowIdentity",
    "WindowsWindowBackend",
    "_find_target",
    "_window_capture",
    "_window_focus",
    "_window_key",
    "_window_list",
]
