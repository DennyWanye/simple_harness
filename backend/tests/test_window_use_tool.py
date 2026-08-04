from __future__ import annotations

import ctypes
import json
import sys
import types
from pathlib import Path

import pytest

from deskpet.tools import window_use_tool as wu
from deskpet.tools.capabilities import ToolExecutionContext


class _Backend:
    def __init__(self, windows: tuple[wu.WindowIdentity, ...]) -> None:
        self.windows = windows
        self.focused: list[int] = []
        self.focus_result = True

    def list_windows(self) -> tuple[wu.WindowIdentity, ...]:
        return self.windows

    def focus(self, hwnd: int) -> bool:
        self.focused.append(hwnd)
        return self.focus_result


class _PyAutoGUI:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str | tuple[str, ...]]] = []
        self.fail_down: str | None = None
        self.fail_up: str | None = None

    def press(self, key: str) -> None:
        self.calls.append(("press", key))

    def hotkey(self, *keys: str) -> None:
        self.calls.append(("hotkey", keys))

    def keyDown(self, key: str) -> None:  # noqa: N802
        if key == self.fail_down:
            raise RuntimeError("down failed")
        self.calls.append(("down", key))

    def keyUp(self, key: str) -> None:  # noqa: N802
        self.calls.append(("up", key))
        if key == self.fail_up:
            raise RuntimeError("up failed")


class _Shot:
    size = (640, 480)
    rgb = b"\x00" * (640 * 480 * 3)


class _Mss:
    def __init__(self) -> None:
        self.regions: list[dict[str, int]] = []
        self.closed = False

    def grab(self, region):
        self.regions.append(dict(region))
        return _Shot()

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def game_window() -> wu.WindowIdentity:
    return wu.WindowIdentity(
        hwnd=1001,
        pid=4242,
        creation_time=1234.5,
        title="DeskPet Godot Demo",
        left=120,
        top=80,
        width=640,
        height=480,
        minimized=False,
    )


@pytest.fixture
def backend(monkeypatch, game_window) -> _Backend:
    value = _Backend((game_window,))
    monkeypatch.setattr(wu, "_guard", lambda: None)
    monkeypatch.setattr(wu, "_load_window_backend", lambda: value)
    return value


def _target(window: wu.WindowIdentity) -> dict[str, object]:
    return {
        "pid": window.pid,
        "creation_time": window.creation_time,
        "hwnd": window.hwnd,
    }


def test_window_list_returns_exact_identity(backend, game_window) -> None:
    out = json.loads(wu._window_list({"query": "godot"}))

    assert out["ok"] is True
    assert out["count"] == 1
    assert out["windows"][0] == game_window.to_dict()


def test_window_target_rejects_stale_pid_creation_time(
    backend, game_window
) -> None:
    out = json.loads(
        wu._window_focus(
            {
                **_target(game_window),
                "creation_time": game_window.creation_time + 1,
            }
        )
    )

    assert out["ok"] is False
    assert "exact process identity" in out["error"]
    assert backend.focused == []


def test_window_target_requires_exact_hwnd(monkeypatch, game_window) -> None:
    second = wu.WindowIdentity(
        **{
            **game_window.to_dict(),
            "hwnd": 1002,
            "title": "DeskPet Godot Debugger",
        }
    )
    backend = _Backend((game_window, second))
    monkeypatch.setattr(wu, "_guard", lambda: None)
    monkeypatch.setattr(wu, "_load_window_backend", lambda: backend)

    out = json.loads(
        wu._window_focus(
            {
                "pid": game_window.pid,
                "creation_time": game_window.creation_time,
            }
        )
    )

    assert out["ok"] is False
    assert "hwnd must be a positive integer" in out["error"]


def test_window_focus_is_verified(backend, game_window) -> None:
    out = json.loads(wu._window_focus(_target(game_window)))

    assert out["ok"] is True
    assert out["focused"] is True
    assert backend.focused == [game_window.hwnd]


def test_windows_focus_fails_closed_when_thread_detach_cannot_complete() -> None:
    class User32:
        def __init__(self) -> None:
            self.detach_calls = 0

        @staticmethod
        def IsWindow(_hwnd):
            return True

        @staticmethod
        def GetForegroundWindow():
            return 1001

        @staticmethod
        def GetWindowThreadProcessId(_hwnd, _pid):
            return 20

        def AttachThreadInput(self, _current, _target, attach):
            if attach:
                return True
            self.detach_calls += 1
            return False

        @staticmethod
        def ShowWindow(_hwnd, _mode):
            return True

        @staticmethod
        def BringWindowToTop(_hwnd):
            return True

        @staticmethod
        def SetForegroundWindow(_hwnd):
            return True

        @staticmethod
        def SetFocus(_hwnd):
            return 1001

    backend = object.__new__(wu.WindowsWindowBackend)
    backend._user32 = User32()  # type: ignore[attr-defined]
    backend._kernel32 = types.SimpleNamespace(GetCurrentThreadId=lambda: 10)  # type: ignore[attr-defined]

    assert backend.focus(1001) is False
    assert backend._user32.detach_calls == 2  # type: ignore[attr-defined]


def test_window_capture_uses_exact_window_rectangle(
    tmp_path, monkeypatch, backend, game_window
) -> None:
    capture = _Mss()
    mss_module = types.SimpleNamespace(mss=lambda: capture)
    tools_module = types.ModuleType("mss.tools")

    def to_png(_rgb, _size, output):
        Path(output).write_bytes(b"\x89PNG\r\n\x1a\nwindow")

    tools_module.to_png = to_png  # type: ignore[attr-defined]
    monkeypatch.setattr(wu, "_load_mss", lambda: mss_module)
    monkeypatch.setitem(sys.modules, "mss.tools", tools_module)

    out = json.loads(
        wu._capture_target(
            {**_target(game_window), "name": "proof/game"},
            root=tmp_path.resolve(),
        )
    )

    assert out["ok"] is True
    assert out["path"] == "proof/game.png"
    assert capture.regions == [
        {"left": 120, "top": 80, "width": 640, "height": 480}
    ]
    assert backend.focused == []
    assert capture.closed is True
    assert (tmp_path / "proof" / "game.png").is_file()


def test_window_capture_initializes_dpi_before_reading_window_rectangle(
    tmp_path, monkeypatch, game_window
) -> None:
    dpi_aware = False
    physical = wu.WindowIdentity(
        **{
            **game_window.to_dict(),
            "left": 180,
            "top": 120,
            "width": 960,
            "height": 720,
        }
    )

    class DpiBackend(_Backend):
        def list_windows(self):
            return (physical if dpi_aware else game_window,)

    backend = DpiBackend((game_window,))
    capture = _Mss()

    def open_mss():
        nonlocal dpi_aware
        dpi_aware = True
        return capture

    tools_module = types.ModuleType("mss.tools")
    tools_module.to_png = (  # type: ignore[attr-defined]
        lambda _rgb, _size, output: Path(output).write_bytes(b"png")
    )
    monkeypatch.setattr(wu, "_guard", lambda: None)
    monkeypatch.setattr(wu, "_load_window_backend", lambda: backend)
    monkeypatch.setattr(
        wu, "_load_mss", lambda: types.SimpleNamespace(mss=open_mss)
    )
    monkeypatch.setitem(sys.modules, "mss.tools", tools_module)

    out = json.loads(
        wu._capture_target(_target(game_window), root=tmp_path.resolve())
    )

    assert out["ok"] is True
    assert capture.regions == [
        {"left": 180, "top": 120, "width": 960, "height": 720}
    ]
    assert out["window"]["width"] == 960
    assert out["window"]["height"] == 720


def test_window_capture_discards_image_when_geometry_changes(
    tmp_path, monkeypatch, game_window
) -> None:
    moved = wu.WindowIdentity(
        **{
            **game_window.to_dict(),
            "left": game_window.left + 20,
        }
    )

    class MovingBackend(_Backend):
        def __init__(self):
            super().__init__((game_window,))
            self.reads = 0

        def list_windows(self):
            self.reads += 1
            return (moved if self.reads >= 2 else game_window,)

    backend = MovingBackend()
    capture = _Mss()
    tools_module = types.ModuleType("mss.tools")
    tools_module.to_png = (  # type: ignore[attr-defined]
        lambda _rgb, _size, output: Path(output).write_bytes(b"unexpected")
    )
    monkeypatch.setattr(wu, "_guard", lambda: None)
    monkeypatch.setattr(wu, "_load_window_backend", lambda: backend)
    monkeypatch.setattr(
        wu,
        "_load_mss",
        lambda: types.SimpleNamespace(mss=lambda: capture),
    )
    monkeypatch.setitem(sys.modules, "mss.tools", tools_module)

    out = json.loads(
        wu._capture_target(
            {**_target(game_window), "name": "proof/moved.png"},
            root=tmp_path.resolve(),
        )
    )

    assert out["ok"] is False
    assert "geometry changed" in out["error"]
    assert not (tmp_path / "proof" / "moved.png").exists()


def test_window_capture_normalizes_backend_close_failure(
    tmp_path, monkeypatch, backend, game_window
) -> None:
    capture = _Mss()

    def close_failure() -> None:
        raise RuntimeError("close failed")

    capture.close = close_failure  # type: ignore[method-assign]
    tools_module = types.ModuleType("mss.tools")
    tools_module.to_png = (  # type: ignore[attr-defined]
        lambda _rgb, _size, output: Path(output).write_bytes(b"png")
    )
    monkeypatch.setattr(
        wu,
        "_load_mss",
        lambda: types.SimpleNamespace(mss=lambda: capture),
    )
    monkeypatch.setitem(sys.modules, "mss.tools", tools_module)

    out = json.loads(
        wu._capture_target(_target(game_window), root=tmp_path.resolve())
    )

    assert out["ok"] is False
    assert "backend close failed" in out["error"]


@pytest.mark.parametrize(
    "name",
    [
        "C:drive-relative.png",
        "C:\\absolute.png",
        "\\\\server\\share\\capture.png",
        "../escape.png",
    ],
)
def test_window_capture_rejects_non_workspace_relative_names(
    tmp_path, monkeypatch, backend, game_window, name
) -> None:
    monkeypatch.setattr(
        wu,
        "_load_mss",
        lambda: (_ for _ in ()).throw(
            AssertionError("capture backend must not be reached")
        ),
    )

    out = json.loads(
        wu._capture_target(
            {**_target(game_window), "name": name},
            root=tmp_path.resolve(),
        )
    )

    assert out["ok"] is False
    assert (
        "workspace-relative" in out["error"]
        or "escapes workspace" in out["error"]
    )


def test_window_key_holds_then_releases_in_reverse_order(
    monkeypatch, backend, game_window
) -> None:
    pg = _PyAutoGUI()
    slept: list[float] = []
    monkeypatch.setattr(wu, "_load_pyautogui", lambda: pg)
    monkeypatch.setattr(wu.time, "sleep", slept.append)

    out = json.loads(
        wu._window_key(
            {
                **_target(game_window),
                "keys": "Shift+Right",
                "hold_ms": 1500,
            }
        )
    )

    assert out["ok"] is True
    assert out["released"] is True
    assert slept == [1.5]
    assert pg.calls == [
        ("down", "shift"),
        ("down", "right"),
        ("up", "right"),
        ("up", "shift"),
    ]
    assert backend.focused == [game_window.hwnd]


def test_window_key_prefers_native_scan_code_backend(
    monkeypatch, game_window
) -> None:
    class NativeBackend(_Backend):
        def __init__(self) -> None:
            super().__init__((game_window,))
            self.key_calls: list[tuple[str, str]] = []

        def key_down(self, key: str) -> None:
            self.key_calls.append(("down", key))

        def key_up(self, key: str) -> None:
            self.key_calls.append(("up", key))

    native = NativeBackend()
    slept: list[float] = []
    monkeypatch.setattr(wu, "_guard", lambda: None)
    monkeypatch.setattr(wu, "_load_window_backend", lambda: native)
    monkeypatch.setattr(
        wu,
        "_load_pyautogui",
        lambda: (_ for _ in ()).throw(
            AssertionError("native keyboard path must not load pyautogui")
        ),
    )
    monkeypatch.setattr(wu.time, "sleep", slept.append)

    out = json.loads(
        wu._window_key(
            {
                **_target(game_window),
                "keys": "Shift+Right",
                "hold_ms": 1500,
            }
        )
    )

    assert out["ok"] is True
    assert slept == [1.5]
    assert native.key_calls == [
        ("down", "shift"),
        ("down", "right"),
        ("up", "right"),
        ("up", "shift"),
    ]


def test_windows_native_keyboard_uses_scan_codes_and_extended_flag() -> None:
    backend = wu.WindowsWindowBackend()

    class User32:
        def __init__(self) -> None:
            self.events: list[tuple[int, int, int, int]] = []

        @staticmethod
        def VkKeyScanW(key: int) -> int:
            return ord(chr(key).upper())

        @staticmethod
        def MapVirtualKeyW(virtual_key: int, _mode: int) -> int:
            return 0xE04D if virtual_key == 0x27 else 0x20

        def SendInput(self, count, pointer, size):
            event = ctypes.cast(
                pointer,
                ctypes.POINTER(backend._input_type),
            ).contents
            keyboard = event.value.ki
            self.events.append(
                (
                    int(count),
                    int(keyboard.wVk),
                    int(keyboard.wScan),
                    int(keyboard.dwFlags),
                )
            )
            assert int(size) == ctypes.sizeof(backend._input_type)
            return 1

    user32 = User32()
    backend._user32 = user32

    backend.key_down("d")
    backend.key_up("d")
    backend.key_down("right")
    backend.key_up("right")

    assert user32.events == [
        (1, 0, 0x20, wu._KEYEVENTF_SCANCODE),
        (
            1,
            0,
            0x20,
            wu._KEYEVENTF_SCANCODE | wu._KEYEVENTF_KEYUP,
        ),
        (
            1,
            0,
            0x4D,
            wu._KEYEVENTF_SCANCODE | wu._KEYEVENTF_EXTENDEDKEY,
        ),
        (
            1,
            0,
            0x4D,
            wu._KEYEVENTF_SCANCODE
            | wu._KEYEVENTF_EXTENDEDKEY
            | wu._KEYEVENTF_KEYUP,
        ),
    ]


def test_windows_native_keyboard_rejects_failed_send() -> None:
    backend = wu.WindowsWindowBackend()

    class User32:
        @staticmethod
        def VkKeyScanW(_key: str) -> int:
            return 0x44

        @staticmethod
        def MapVirtualKeyW(_virtual_key: int, _mode: int) -> int:
            return 0x20

        @staticmethod
        def SendInput(_count, _pointer, _size):
            return 0

    backend._user32 = User32()

    with pytest.raises(OSError):
        backend.key_down("d")


def test_windows_native_keyboard_rejects_implicit_modifier_keys() -> None:
    backend = wu.WindowsWindowBackend()
    backend._user32 = types.SimpleNamespace(  # type: ignore[attr-defined]
        VkKeyScanW=lambda _key: 0x0131
    )

    with pytest.raises(ValueError, match="explicit modifiers"):
        backend._virtual_key("!")


def test_windows_native_keyboard_rejects_pause_without_e1_support() -> None:
    backend = wu.WindowsWindowBackend()

    with pytest.raises(ValueError, match="unsupported key"):
        backend._virtual_key("pause")


def test_window_key_rejects_concurrent_keyboard_transaction(
    monkeypatch, backend, game_window
) -> None:
    monkeypatch.setattr(
        wu,
        "_load_pyautogui",
        lambda: (_ for _ in ()).throw(
            AssertionError("busy transaction must fail before input")
        ),
    )
    assert wu._window_key_lock.acquire(blocking=False)
    try:
        out = json.loads(
            wu._window_key(
                {
                    **_target(game_window),
                    "keys": "right",
                    "hold_ms": 500,
                }
            )
        )
    finally:
        wu._window_key_lock.release()

    assert out["ok"] is False
    assert out["retriable"] is True
    assert "keyboard input is busy" in out["error"]
    assert backend.focused == []


def test_window_key_releases_already_pressed_keys_after_failure(
    monkeypatch, backend, game_window
) -> None:
    pg = _PyAutoGUI()
    pg.fail_down = "right"
    monkeypatch.setattr(wu, "_load_pyautogui", lambda: pg)

    out = json.loads(
        wu._window_key(
            {
                **_target(game_window),
                "keys": "Shift+Right",
                "hold_ms": 500,
            }
        )
    )

    assert out["ok"] is False
    assert pg.calls == [
        ("down", "shift"),
        ("up", "right"),
        ("up", "shift"),
    ]


def test_window_key_sends_nothing_when_focus_cannot_be_verified(
    monkeypatch, backend, game_window
) -> None:
    pg = _PyAutoGUI()
    backend.focus_result = False
    monkeypatch.setattr(wu, "_load_pyautogui", lambda: pg)

    out = json.loads(
        wu._window_key(
            {
                **_target(game_window),
                "keys": "right",
                "hold_ms": 500,
            }
        )
    )

    assert out["ok"] is False
    assert "focus" in out["error"]
    assert pg.calls == []


def test_window_key_attempts_every_release_and_reports_release_failure(
    monkeypatch, backend, game_window
) -> None:
    pg = _PyAutoGUI()
    pg.fail_up = "right"
    monkeypatch.setattr(wu, "_load_pyautogui", lambda: pg)
    monkeypatch.setattr(wu.time, "sleep", lambda _seconds: None)

    out = json.loads(
        wu._window_key(
            {
                **_target(game_window),
                "keys": "Shift+Right",
                "hold_ms": 500,
            }
        )
    )

    assert out["ok"] is False
    assert "release could not be verified" in out["error"]
    assert pg.calls[-2:] == [("up", "right"), ("up", "shift")]


def test_window_key_releases_all_keys_when_hold_sleep_fails(
    monkeypatch, backend, game_window
) -> None:
    pg = _PyAutoGUI()
    monkeypatch.setattr(wu, "_load_pyautogui", lambda: pg)
    monkeypatch.setattr(
        wu.time,
        "sleep",
        lambda _seconds: (_ for _ in ()).throw(RuntimeError("sleep failed")),
    )

    out = json.loads(
        wu._window_key(
            {
                **_target(game_window),
                "keys": "Shift+Right",
                "hold_ms": 500,
            }
        )
    )

    assert out["ok"] is False
    assert pg.calls[-2:] == [("up", "right"), ("up", "shift")]


@pytest.mark.parametrize("hold_ms", [-1, 10_001, 1.5, True])
def test_window_key_rejects_invalid_hold_duration(
    backend, game_window, hold_ms
) -> None:
    out = json.loads(
        wu._window_key(
            {
                **_target(game_window),
                "keys": "right",
                "hold_ms": hold_ms,
            }
        )
    )

    assert out["ok"] is False
    assert "hold_ms" in out["error"]
    assert backend.focused == []


def test_window_key_zero_duration_uses_explicit_down_up_semantics(
    monkeypatch, backend, game_window
) -> None:
    pg = _PyAutoGUI()
    monkeypatch.setattr(wu, "_load_pyautogui", lambda: pg)

    single = json.loads(
        wu._window_key({**_target(game_window), "keys": "enter"})
    )
    combo = json.loads(
        wu._window_key({**_target(game_window), "keys": "Ctrl+S"})
    )

    assert single["ok"] is True and combo["ok"] is True
    assert pg.calls == [
        ("down", "enter"),
        ("up", "enter"),
        ("down", "ctrl"),
        ("down", "s"),
        ("up", "s"),
        ("up", "ctrl"),
    ]


def test_window_key_sequence_executes_in_order_with_one_transaction(
    monkeypatch, backend, game_window
) -> None:
    pg = _PyAutoGUI()
    slept: list[float] = []
    monkeypatch.setattr(wu, "_load_pyautogui", lambda: pg)
    monkeypatch.setattr(wu.time, "sleep", slept.append)

    out = json.loads(
        wu._window_key(
            {
                **_target(game_window),
                "steps": [
                    {"keys": "right", "hold_ms": 400, "pause_ms": 100},
                    {"keys": "up+right", "hold_ms": 250},
                    {"keys": "space", "pause_ms": 50},
                ],
            }
        )
    )

    assert out["ok"] is True
    assert out["sequence"] is True
    assert out["completed_steps"] == 3
    assert slept == [0.4, 0.1, 0.25, 0.05]
    assert pg.calls == [
        ("down", "right"),
        ("up", "right"),
        ("down", "up"),
        ("down", "right"),
        ("up", "right"),
        ("up", "up"),
        ("down", "space"),
        ("up", "space"),
    ]
    assert backend.focused == [game_window.hwnd] * 3


def test_window_key_sequence_accepts_pause_only_step(
    monkeypatch, backend, game_window
) -> None:
    pg = _PyAutoGUI()
    slept: list[float] = []
    monkeypatch.setattr(wu, "_load_pyautogui", lambda: pg)
    monkeypatch.setattr(wu.time, "sleep", slept.append)

    out = json.loads(
        wu._window_key(
            {
                **_target(game_window),
                "steps": [
                    {"keys": "A", "hold_ms": 250},
                    {"keys": "", "pause_ms": 100},
                    {"keys": "S", "hold_ms": 250},
                ],
            }
        )
    )

    assert out["ok"] is True
    assert out["completed_steps"] == 3
    assert out["steps"] == [
        {"keys": ["a"], "hold_ms": 250, "pause_ms": 0},
        {"pause_ms": 100},
        {"keys": ["s"], "hold_ms": 250, "pause_ms": 0},
    ]
    assert slept == [0.25, 0.1, 0.25]
    assert pg.calls == [
        ("down", "a"),
        ("up", "a"),
        ("down", "s"),
        ("up", "s"),
    ]
    assert backend.focused == [game_window.hwnd] * 2


def test_window_key_sequence_stops_after_failed_step_and_releases_keys(
    monkeypatch, backend, game_window
) -> None:
    pg = _PyAutoGUI()
    pg.fail_down = "left"
    monkeypatch.setattr(wu, "_load_pyautogui", lambda: pg)

    out = json.loads(
        wu._window_key(
            {
                **_target(game_window),
                "steps": [
                    {"keys": "right"},
                    {"keys": "shift+left"},
                    {"keys": "enter"},
                ],
            }
        )
    )

    assert out["ok"] is False
    assert out["sequence"] is True
    assert out["failed_step"] == 1
    assert out["completed_steps"] == 1
    assert pg.calls == [
        ("down", "right"),
        ("up", "right"),
        ("down", "shift"),
        ("up", "left"),
        ("up", "shift"),
    ]
    assert backend.focused == [game_window.hwnd] * 2


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({}, "exactly one"),
        ({"keys": "right", "steps": [{"keys": "left"}]}, "exactly one"),
        ({"steps": []}, "1 to"),
        ({"steps": [{"pause_ms": 100}]}, "at least one key step"),
        ({"steps": [{"keys": ""}]}, "keys or a positive pause_ms"),
        (
            {"steps": [{"keys": "right"}, {"pause_ms": 100, "hold_ms": 1}]},
            "hold_ms requires",
        ),
        (
            {"steps": [{"keys": "right", "pause_ms": 2001}]},
            "pause_ms",
        ),
        (
            {
                "steps": [
                    {"keys": "right", "hold_ms": 10_000},
                    {"keys": "left", "hold_ms": 2_001},
                ]
            },
            "total",
        ),
    ],
)
def test_window_key_rejects_invalid_sequence_before_input(
    backend, game_window, payload, message
) -> None:
    out = json.loads(wu._window_key({**_target(game_window), **payload}))

    assert out["ok"] is False
    assert message in out["error"]
    assert backend.focused == []


def test_window_tools_are_registered_with_identity_bound_schemas() -> None:
    for name in ("window_list", "window_focus", "window_capture", "window_key"):
        spec = wu.registry.get(name)
        assert spec is not None
        assert spec.toolset == "computer_use"
        assert spec.requires_env == []
    key_schema = wu.registry.get("window_key").schema
    assert key_schema["parameters"]["required"] == [
        "pid",
        "creation_time",
        "hwnd",
    ]
    assert (
        key_schema["parameters"]["properties"]["hold_ms"]["maximum"]
        == wu._MAX_HOLD_MS
    )
    assert (
        key_schema["parameters"]["properties"]["steps"]["maxItems"]
        == wu._MAX_SEQUENCE_STEPS
    )
    assert (
        "required"
        not in key_schema["parameters"]["properties"]["steps"]["items"]
    )


def test_window_control_tools_are_serial_execution_boundaries() -> None:
    assert wu.registry.get("window_list").concurrency_safe is True
    for name in ("window_focus", "window_capture", "window_key"):
        assert wu.registry.get(name).concurrency_safe is False


def test_window_resource_scope_binds_pid_creation_time_and_hwnd(
    game_window, tmp_path
) -> None:
    context = ToolExecutionContext(
        scope_id="scope",
        session_id="session",
        request_id="request",
        workspace=str(tmp_path),
        write_scope_root=str(tmp_path),
    )
    selector = wu._window_resources(_target(game_window), context)[0]

    assert selector.to_dict() == {
        "kind": "desktop_target",
        "canonical_value": "window:4242:1234.500000:1001",
        "access": ["control", "input"],
    }
    lane = wu._window_resources(_target(game_window), context)[1]
    assert lane.to_dict() == {
        "kind": "desktop_target",
        "canonical_value": "desktop-input:primary",
        "access": ["control", "input"],
    }
