import ctypes
import ctypes.wintypes as wt
import json
import math
import os
import queue
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk


APP_NAME = "KeyMouse Marco Weaver"
VERSION = "2.1.0"
SCHEMA_VERSION = 2
UNDO_LIMIT = 20
SPEED_MIN = 0.01
SPEED_MAX = 20.0
REPEAT_MODE_COUNT = "count"
REPEAT_MODE_LOOP = "loop"
TICK32_MODULUS = 1 << 32
TICK32_HALF_RANGE = 1 << 31
INPUT_RELEASE_ATTEMPTS = 3
INPUT_RELEASE_RETRY_DELAY = 0.02

WH_KEYBOARD_LL = 13
WH_MOUSE_LL = 14
HC_ACTION = 0

WM_QUIT = 0x0012
WM_HOTKEY = 0x0312
PM_NOREMOVE = 0x0000
WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_RBUTTONDOWN = 0x0204
WM_RBUTTONUP = 0x0205
WM_MBUTTONDOWN = 0x0207
WM_MBUTTONUP = 0x0208
WM_MOUSEWHEEL = 0x020A
WM_XBUTTONDOWN = 0x020B
WM_XBUTTONUP = 0x020C
WM_MOUSEHWHEEL = 0x020E

MOUSE_MESSAGE_NAMES = {
    WM_MOUSEMOVE: "移动",
    WM_LBUTTONDOWN: "左键按下",
    WM_LBUTTONUP: "左键释放",
    WM_RBUTTONDOWN: "右键按下",
    WM_RBUTTONUP: "右键释放",
    WM_MBUTTONDOWN: "中键按下",
    WM_MBUTTONUP: "中键释放",
    WM_MOUSEWHEEL: "垂直滚轮",
    WM_MOUSEHWHEEL: "水平滚轮",
    WM_XBUTTONDOWN: "侧键按下",
    WM_XBUTTONUP: "侧键释放",
}
MOUSE_MESSAGE_VALUES = tuple(MOUSE_MESSAGE_NAMES.values())
MOUSE_MESSAGE_CODES = {label: code for code, label in MOUSE_MESSAGE_NAMES.items()}
SUPPORTED_MOUSE_MESSAGES = frozenset(MOUSE_MESSAGE_NAMES)
INT32_MIN = -(1 << 31)
INT32_MAX = (1 << 31) - 1
UINT32_MAX = (1 << 32) - 1
SCAN_CODE_MAX = (1 << 16) - 1

# This catalog is the single source of truth for both insertion and editing.
# Every entry describes one physical input message. Composite click actions are
# intentionally absent so users never create an implicit second event.
ACTION_CATALOG = (
    {"label": "键盘按键", "kind": "key", "uses_key": True},
    {"label": "移动", "kind": "mouse", "message": WM_MOUSEMOVE, "uses_xy": True},
    {"label": "左键按下", "kind": "mouse", "message": WM_LBUTTONDOWN, "uses_xy": True},
    {"label": "左键释放", "kind": "mouse", "message": WM_LBUTTONUP, "uses_xy": True},
    {"label": "右键按下", "kind": "mouse", "message": WM_RBUTTONDOWN, "uses_xy": True},
    {"label": "右键释放", "kind": "mouse", "message": WM_RBUTTONUP, "uses_xy": True},
    {"label": "中键按下", "kind": "mouse", "message": WM_MBUTTONDOWN, "uses_xy": True},
    {"label": "中键释放", "kind": "mouse", "message": WM_MBUTTONUP, "uses_xy": True},
    {"label": "侧键1按下", "kind": "mouse", "message": WM_XBUTTONDOWN, "modifier": 1, "uses_xy": True},
    {"label": "侧键1释放", "kind": "mouse", "message": WM_XBUTTONUP, "modifier": 1, "uses_xy": True},
    {"label": "侧键2按下", "kind": "mouse", "message": WM_XBUTTONDOWN, "modifier": 2, "uses_xy": True},
    {"label": "侧键2释放", "kind": "mouse", "message": WM_XBUTTONUP, "modifier": 2, "uses_xy": True},
    {"label": "垂直滚轮向上", "kind": "mouse", "message": WM_MOUSEWHEEL, "modifier": 1, "uses_xy": True, "uses_delta": True},
    {"label": "垂直滚轮向下", "kind": "mouse", "message": WM_MOUSEWHEEL, "modifier": -1, "uses_xy": True, "uses_delta": True},
    {"label": "水平滚轮向左", "kind": "mouse", "message": WM_MOUSEHWHEEL, "modifier": -1, "uses_xy": True, "uses_delta": True},
    {"label": "水平滚轮向右", "kind": "mouse", "message": WM_MOUSEHWHEEL, "modifier": 1, "uses_xy": True, "uses_delta": True},
)
ACTION_DEFINITIONS = {entry["label"]: entry for entry in ACTION_CATALOG}
EVENT_CATEGORY_VALUES = ("鼠标", "键盘")
EVENT_TYPE_VALUES = EVENT_CATEGORY_VALUES
ACTION_TYPE_VALUES = {
    "鼠标": tuple(entry["label"] for entry in ACTION_CATALOG if entry["kind"] == "mouse"),
    "键盘": ("按下", "释放"),
}
MOUSE_ACTION_VALUES = tuple(entry["label"] for entry in ACTION_CATALOG if entry["kind"] == "mouse")


def validate_editor_choice(category, action):
    """Normalize and validate the two-level editor selection."""
    category = str(category or "").strip()
    if category not in EVENT_CATEGORY_VALUES:
        raise ValueError("错误的事件类型")
    action = str(action or "").strip()
    if action not in ACTION_TYPE_VALUES[category]:
        raise ValueError("错误的动作")
    return category, action


def parse_repeat_settings(mode, text):
    """Return a positive repeat count, or None for playback until stopped."""
    if mode == REPEAT_MODE_LOOP:
        return None
    if mode != REPEAT_MODE_COUNT:
        raise ValueError("错误的重复模式")
    value = str(text).strip()
    if not value or not value.isdigit() or int(value) < 1:
        raise ValueError("重复次数必须是大于等于 1 的整数")
    return int(value)


def parse_composition_speed(value):
    """Return a valid per-file composition speed multiplier."""
    if isinstance(value, bool):
        raise ValueError("编排速度必须是数字")
    try:
        speed = float(str(value).strip())
    except (TypeError, ValueError, OverflowError):
        raise ValueError("编排速度必须是数字") from None
    if not math.isfinite(speed) or not SPEED_MIN <= speed <= SPEED_MAX:
        raise ValueError(f"编排速度必须在 {SPEED_MIN:g} 到 {SPEED_MAX:g} 之间")
    return speed


def move_list_item(items, source_index, target_index):
    """Return a copy with one item moved to another list position."""
    reordered = list(items)
    if (
        isinstance(source_index, bool)
        or isinstance(target_index, bool)
        or not isinstance(source_index, int)
        or not isinstance(target_index, int)
    ):
        raise ValueError("无效的拖动位置")
    if not (0 <= source_index < len(reordered)) or not (0 <= target_index < len(reordered)):
        return reordered
    if source_index != target_index:
        reordered.insert(target_index, reordered.pop(source_index))
    return reordered


def compose_macro_events(sequence, target_screen=None):
    """Concatenate macro timelines with per-item speed and repeat settings.

    Each sequence item is a mapping with ``events`` and optional ``repeat``
    and ``speed`` fields.  A macro's own timestamps are relative to the
    beginning of that macro; speed controls its playback timing (values above
    1.0 play faster and values below 1.0 play slower), and the next item starts
    immediately after the previous item's final event timestamp while
    preserving each macro's initial gap.
    """
    if not sequence:
        raise ValueError("编排至少需要一个宏")
    composed = []
    cursor = 0.0
    for item_index, item in enumerate(sequence):
        if not isinstance(item, dict):
            raise ValueError(f"第 {item_index + 1} 个编排项无效")
        try:
            events = validate_events(item.get("events"))
        except ValueError as exc:
            raise ValueError(f"第 {item_index + 1} 个宏事件无效：{exc}") from None
        if not events:
            raise ValueError(f"第 {item_index + 1} 个宏没有可编排事件")
        repeat = item.get("repeat", 1)
        if isinstance(repeat, bool):
            raise ValueError("编排次数必须是大于等于 1 的整数")
        repeat_text = str(repeat).strip()
        if not repeat_text.isdigit():
            raise ValueError("编排次数必须是大于等于 1 的整数") from None
        try:
            repeat = int(repeat_text)
        except (TypeError, ValueError, OverflowError):
            raise ValueError("编排次数必须是大于等于 1 的整数") from None
        if repeat < 1:
            raise ValueError("编排次数必须是大于等于 1 的整数")
        try:
            speed = parse_composition_speed(item.get("speed", 1.0))
        except ValueError as exc:
            raise ValueError(f"第 {item_index + 1} 个宏{exc}") from None
        duration = event_end_time(events[-1]) / speed
        source_screen = item.get("screen")
        for _ in range(repeat):
            for event in events:
                copied = dict(event)
                copied["t"] = cursor + float(event["t"]) / speed
                if copied.get("kind") == "mouse" and source_screen and target_screen:
                    copied["x"], copied["y"] = map_screen_point_between(
                        copied.get("x", 0), copied.get("y", 0), source_screen, target_screen
                    )
                composed.append(copied)
            cursor += duration
    return validate_events(composed)


def event_category_for_event(event):
    """Return the first-level event category shown by both editors."""
    return {"mouse": "鼠标", "key": "键盘"}.get(event.get("kind"), "")


def action_type_for_event(event):
    """Return the second-level action label shown by both editors."""
    kind = event.get("kind")
    if kind == "key":
        return "释放" if event.get("action") == "up" else "按下"
    if kind == "mouse":
        return mouse_action_label(event.get("message", WM_MOUSEMOVE), event.get("data", 0))
    return ""


def _finite_float(value, label):
    """Parse a finite float so malformed JSON cannot create endless waits."""
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError(f"{label}必须是数字")
    if not math.isfinite(result):
        raise ValueError(f"{label}必须是有限数字")
    return result


def _coerce_bool(value, label, default=False):
    """Normalize JSON/UI boolean values without treating 'false' as true."""
    if value is None:
        return bool(default)
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    raise ValueError(f"{label}必须是布尔值")


def _parse_int(value, error):
    """Parse an integer and expose one consistent user-facing error."""
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError(error) from None


def _is_mouse_move(event):
    """Return whether an event is a recorded pointer-move message."""
    return event.get("kind") == "mouse" and int(event.get("message", WM_MOUSEMOVE)) == WM_MOUSEMOVE


KEYSYM_NAMES = {
    "BackSpace": "Backspace",
    "Return": "Enter",
    "Escape": "Esc",
    "space": "Space",
    "Tab": "Tab",
    "Delete": "Delete",
    "Insert": "Insert",
    "Home": "Home",
    "End": "End",
    "Prior": "Page Up",
    "Next": "Page Down",
    "Left": "Left",
    "Right": "Right",
    "Up": "Up",
    "Down": "Down",
    "Control_L": "Ctrl",
    "Control_R": "Ctrl (右)",
    "Alt_L": "Alt",
    "Alt_R": "Alt (右)",
    "Shift_L": "Shift",
    "Shift_R": "Shift (右)",
    "Caps_Lock": "Caps Lock",
    "Num_Lock": "Num Lock",
    "Scroll_Lock": "Scroll Lock",
    "Print": "Print Screen",
    "Pause": "Pause",
}

VK_KEY_NAMES = {
    8: "Backspace",
    9: "Tab",
    13: "Enter",
    16: "Shift",
    17: "Ctrl",
    18: "Alt",
    19: "Pause",
    20: "Caps Lock",
    27: "Esc",
    32: "Space",
    33: "Page Up",
    34: "Page Down",
    35: "End",
    36: "Home",
    37: "Left",
    38: "Up",
    39: "Right",
    40: "Down",
    45: "Insert",
    46: "Delete",
    91: "Win",
    92: "Win (右)",
    93: "菜单",
    144: "Num Lock",
    145: "Scroll Lock",
}


def key_display_name(vk, keysym=""):
    """Return a readable key name while retaining numeric codes in the event."""
    if keysym and keysym not in {"??", "#??", "unknown"}:
        if len(keysym) == 1 and keysym.isprintable():
            return keysym.upper()
        if keysym in KEYSYM_NAMES:
            return KEYSYM_NAMES[keysym]
        return keysym
    vk = int(vk)
    if 0x30 <= vk <= 0x39 or 0x41 <= vk <= 0x5A:
        return chr(vk)
    if 0x70 <= vk <= 0x87:
        return f"F{vk - 0x6F}"
    return VK_KEY_NAMES.get(vk, f"按键 ({vk})")


def tk_event_vk(event):
    """Extract a VK from Tk, recovering printable keys from IME PROCESSKEY."""
    try:
        vk = int(getattr(event, "keycode", 0) or 0)
    except (TypeError, ValueError):
        vk = 0
    char = getattr(event, "char", "") or ""
    if vk == 229 and len(char) == 1 and char.isprintable():
        if char.isalpha():
            return ord(char.upper())
        if char.isdigit():
            return ord(char)
    if not vk:
        try:
            vk = int(getattr(event, "keysym_num", 0) or 0)
        except (TypeError, ValueError):
            vk = 0
    return vk

VK_F8 = 0x77
VK_F9 = 0x78
VK_F10 = 0x79
VK_SNAPSHOT = 0x2C
IGNORED_HOTKEYS = {VK_F8, VK_F9, VK_F10}

LLKHF_EXTENDED = 0x01
LLKHF_INJECTED = 0x10
LLKHF_UP = 0x80
LLMHF_INJECTED = 0x01

MOD_NOREPEAT = 0x4000
HOTKEY_TOGGLE_RECORD = 1
HOTKEY_PLAY = 2
HOTKEY_STOP = 3

INPUT_MOUSE = 0
INPUT_KEYBOARD = 1
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_SCANCODE = 0x0008
MAPVK_VK_TO_VSC = 0
MAPVK_VSC_TO_VK_EX = 3
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_XDOWN = 0x0080
MOUSEEVENTF_XUP = 0x0100
MOUSEEVENTF_HWHEEL = 0x1000
MOUSE_BUTTON_INPUTS = {
    WM_LBUTTONDOWN: (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP),
    WM_LBUTTONUP: (MOUSEEVENTF_LEFTUP, MOUSEEVENTF_LEFTUP),
    WM_RBUTTONDOWN: (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP),
    WM_RBUTTONUP: (MOUSEEVENTF_RIGHTUP, MOUSEEVENTF_RIGHTUP),
    WM_MBUTTONDOWN: (MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP),
    WM_MBUTTONUP: (MOUSEEVENTF_MIDDLEUP, MOUSEEVENTF_MIDDLEUP),
    WM_XBUTTONDOWN: (MOUSEEVENTF_XDOWN, MOUSEEVENTF_XUP),
    WM_XBUTTONUP: (MOUSEEVENTF_XUP, MOUSEEVENTF_XUP),
}
MOUSE_BUTTON_DOWN_MESSAGES = frozenset(
    (WM_LBUTTONDOWN, WM_RBUTTONDOWN, WM_MBUTTONDOWN, WM_XBUTTONDOWN)
)
X_BUTTON_MESSAGES = frozenset((WM_XBUTTONDOWN, WM_XBUTTONUP))
MOUSE_WHEEL_INPUTS = {
    WM_MOUSEWHEEL: MOUSEEVENTF_WHEEL,
    WM_MOUSEHWHEEL: MOUSEEVENTF_HWHEEL,
}
MOUSE_RELEASE_FLAG_NAMES = {
    MOUSEEVENTF_LEFTUP: "鼠标左键",
    MOUSEEVENTF_RIGHTUP: "鼠标右键",
    MOUSEEVENTF_MIDDLEUP: "鼠标中键",
}
SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79


user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
ULONG_PTR = wt.WPARAM


class POINT(ctypes.Structure):
    _fields_ = [("x", wt.LONG), ("y", wt.LONG)]


class MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", wt.HWND),
        ("message", wt.UINT),
        ("wParam", wt.WPARAM),
        ("lParam", wt.LPARAM),
        ("time", wt.DWORD),
        ("pt", POINT),
    ]


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wt.DWORD),
        ("scanCode", wt.DWORD),
        ("flags", wt.DWORD),
        ("time", wt.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("pt", POINT),
        ("mouseData", wt.DWORD),
        ("flags", wt.DWORD),
        ("time", wt.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wt.WORD),
        ("wScan", wt.WORD),
        ("dwFlags", wt.DWORD),
        ("time", wt.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wt.LONG),
        ("dy", wt.LONG),
        ("mouseData", wt.DWORD),
        ("dwFlags", wt.DWORD),
        ("time", wt.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wt.DWORD),
        ("wParamL", wt.WORD),
        ("wParamH", wt.WORD),
    ]


class INPUTUNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wt.DWORD), ("union", INPUTUNION)]


LowLevelProc = ctypes.WINFUNCTYPE(wt.LPARAM, ctypes.c_int, wt.WPARAM, wt.LPARAM)

user32.SetWindowsHookExW.argtypes = [ctypes.c_int, LowLevelProc, wt.HINSTANCE, wt.DWORD]
user32.SetWindowsHookExW.restype = wt.HHOOK
user32.CallNextHookEx.argtypes = [wt.HHOOK, ctypes.c_int, wt.WPARAM, wt.LPARAM]
user32.CallNextHookEx.restype = wt.LPARAM
user32.UnhookWindowsHookEx.argtypes = [wt.HHOOK]
user32.UnhookWindowsHookEx.restype = wt.BOOL
user32.GetMessageW.argtypes = [ctypes.POINTER(MSG), wt.HWND, wt.UINT, wt.UINT]
user32.GetMessageW.restype = wt.BOOL
user32.PeekMessageW.argtypes = [ctypes.POINTER(MSG), wt.HWND, wt.UINT, wt.UINT, wt.UINT]
user32.PeekMessageW.restype = wt.BOOL
user32.TranslateMessage.argtypes = [ctypes.POINTER(MSG)]
user32.TranslateMessage.restype = wt.BOOL
user32.DispatchMessageW.argtypes = [ctypes.POINTER(MSG)]
user32.DispatchMessageW.restype = wt.LPARAM
user32.PostThreadMessageW.argtypes = [wt.DWORD, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.PostThreadMessageW.restype = wt.BOOL
user32.SendInput.argtypes = [wt.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
user32.SendInput.restype = wt.UINT
user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
user32.SetCursorPos.restype = wt.BOOL
user32.GetCursorPos.argtypes = [ctypes.POINTER(POINT)]
user32.GetCursorPos.restype = wt.BOOL
user32.GetSystemMetrics.argtypes = [ctypes.c_int]
user32.GetSystemMetrics.restype = ctypes.c_int
user32.MapVirtualKeyW.argtypes = [wt.UINT, wt.UINT]
user32.MapVirtualKeyW.restype = wt.UINT
user32.keybd_event.argtypes = [ctypes.c_ubyte, ctypes.c_ubyte, wt.DWORD, ULONG_PTR]
user32.keybd_event.restype = None
user32.RegisterHotKey.argtypes = [wt.HWND, ctypes.c_int, wt.UINT, wt.UINT]
user32.RegisterHotKey.restype = wt.BOOL
user32.UnregisterHotKey.argtypes = [wt.HWND, ctypes.c_int]
user32.UnregisterHotKey.restype = wt.BOOL
kernel32.GetCurrentThreadId.argtypes = []
kernel32.GetCurrentThreadId.restype = wt.DWORD
kernel32.GetModuleHandleW.argtypes = [wt.LPCWSTR]
kernel32.GetModuleHandleW.restype = wt.HMODULE
kernel32.GetTickCount64.argtypes = []
kernel32.GetTickCount64.restype = ctypes.c_ulonglong


def last_error_message(prefix):
    return f"{prefix}: Windows error {ctypes.get_last_error()}"


def expand_hook_tick(source_tick, reference_tick):
    """Expand a hook's 32-bit system tick near a 64-bit uptime reference."""
    source_tick = int(source_tick) & UINT32_MAX
    reference_tick = int(reference_tick)
    candidate = (reference_tick & ~UINT32_MAX) | source_tick
    if candidate - reference_tick > TICK32_HALF_RANGE:
        candidate -= TICK32_MODULUS
    elif reference_tick - candidate > TICK32_HALF_RANGE:
        candidate += TICK32_MODULUS
    return candidate


def hook_elapsed_seconds(source_tick, start_tick, reference_tick):
    """Convert a Windows hook source timestamp to recording-relative seconds."""
    expanded_tick = expand_hook_tick(source_tick, reference_tick)
    return max(0.0, (expanded_tick - int(start_tick)) / 1000.0)


def _normalize_key_event(event, index):
    vk = _parse_int(event.get("vk", -1), f"event {index} has invalid key code")
    scan = _parse_int(event.get("scan", 0), f"event {index} has invalid key code")
    if not 0 <= vk <= 255:
        raise ValueError(f"event {index} has invalid virtual key")
    if not 0 <= scan <= SCAN_CODE_MAX:
        raise ValueError(f"event {index} has invalid scan code")

    action = event.get("action", "down")
    if not isinstance(action, str) or action not in {"down", "up"}:
        raise ValueError(f"event {index} has invalid key action")
    try:
        extended = _coerce_bool(event.get("extended", False), f"event {index} extended")
    except ValueError:
        raise ValueError(f"event {index} has invalid extended flag") from None

    event.pop("duration", None)
    event.update(vk=vk, scan=scan, action=action, extended=extended)


def _normalize_mouse_event(event, index):
    message = _parse_int(event.get("message", WM_MOUSEMOVE), f"event {index} has invalid mouse fields")
    x = _parse_int(event.get("x", 0), f"event {index} has invalid mouse fields")
    y = _parse_int(event.get("y", 0), f"event {index} has invalid mouse fields")
    data = _parse_int(event.get("data", 0), f"event {index} has invalid mouse fields")
    if message not in SUPPORTED_MOUSE_MESSAGES:
        raise ValueError(f"event {index} has unknown mouse message")
    if not INT32_MIN <= x <= INT32_MAX or not INT32_MIN <= y <= INT32_MAX:
        raise ValueError(f"event {index} has invalid mouse coordinate")
    if not 0 <= data <= UINT32_MAX:
        raise ValueError(f"event {index} has invalid mouse data")
    event.pop("duration", None)
    event.update(message=message, x=x, y=y, data=data)


def validate_events(events):
    """Return a normalized copy of a macro event list or raise ValueError."""
    if not isinstance(events, list):
        raise ValueError("events must be a list")
    normalized = []
    previous_t = 0.0
    for index, raw in enumerate(events):
        if not isinstance(raw, dict):
            raise ValueError(f"event {index} must be an object")
        event = dict(raw)
        try:
            timestamp = _finite_float(event.get("t", previous_t), f"event {index} timestamp")
            event["t"] = max(previous_t, timestamp)
        except ValueError:
            raise ValueError(f"event {index} has invalid timestamp") from None
        kind = event.get("kind")
        if kind == "key":
            _normalize_key_event(event, index)
        elif kind == "mouse":
            _normalize_mouse_event(event, index)
        else:
            raise ValueError(f"event {index} has unknown kind")
        normalized.append(event)
        previous_t = event["t"]
    return normalized


def event_end_time(event):
    """Return the end of an event; waits are represented by gaps between t values."""
    return float(event.get("t", 0.0))


def delete_mouse_moves(events):
    """Remove every standalone mouse-move event while preserving all others."""
    normalized = validate_events(events)
    return validate_events([event for event in normalized if not _is_mouse_move(event)])


def move_event(events, index, direction):
    """Move one event by one slot while keeping timeline starts monotonic."""
    if not isinstance(index, int) or direction not in (-1, 1):
        raise ValueError("无效的事件位置")
    return move_event_to(events, index, index + direction)


def move_event_to(events, source_index, target_index):
    """Move an event to any slot while preserving the timeline's time slots."""
    normalized = validate_events(events)
    if (
        isinstance(source_index, bool)
        or isinstance(target_index, bool)
        or not isinstance(source_index, int)
        or not isinstance(target_index, int)
    ):
        raise ValueError("无效的事件位置")
    if not (0 <= source_index < len(normalized)) or not (0 <= target_index < len(normalized)):
        return normalized

    # Ordering is edited independently from timing: each visual row keeps its
    # original timestamp, so dragging does not erase or invent playback gaps.
    slot_times = [event["t"] for event in normalized]
    reordered = move_list_item(normalized, source_index, target_index)
    for event, timestamp in zip(reordered, slot_times):
        event["t"] = timestamp
    return validate_events(reordered)


def bind_treeview_drag_reorder(tree, item_count, on_reorder, enabled=None):
    """Add row drag-and-drop reordering to a numeric-iid Treeview."""
    enabled = enabled or (lambda: True)
    state = {"source": None, "target": None}
    drag_tag = "drag_target"

    def row_index_at(y):
        children = tree.get_children()
        if not children:
            return None
        row = tree.identify_row(y)
        if row in children:
            return children.index(row)
        visible = []
        for index, child in enumerate(children):
            bounds = tree.bbox(child)
            if bounds:
                visible.append((index, bounds))
        if not visible:
            return None
        first_index, first_bounds = visible[0]
        last_index, last_bounds = visible[-1]
        if y < first_bounds[1]:
            return first_index
        if y >= last_bounds[1] + last_bounds[3]:
            return last_index
        return None

    def set_target(index):
        previous = state["target"]
        if previous == index:
            return
        for position in (previous, index):
            if position is None:
                continue
            iid = str(position)
            if not tree.exists(iid):
                continue
            tags = tuple(tag for tag in tree.item(iid, "tags") if tag != drag_tag)
            if position == index:
                tags += (drag_tag,)
            tree.item(iid, tags=tags)
        state["target"] = index

    def clear_drag():
        set_target(None)
        state["source"] = None
        state["target"] = None
        try:
            tree.configure(cursor="")
        except tk.TclError:
            pass

    def on_press(event):
        clear_drag()
        if not enabled() or tree.identify_region(event.x, event.y) != "cell":
            return None
        source = row_index_at(event.y)
        if source is None or source >= item_count():
            return None
        iid = str(source)
        tree.selection_set(iid)
        tree.focus(iid)
        tree.focus_set()
        state["source"] = source
        state["target"] = source
        tree.configure(cursor="fleur")
        return "break"

    def on_motion(event):
        if state["source"] is None:
            return None
        height = max(1, tree.winfo_height())
        threshold = min(42, max(18, height // 12))
        if event.y < threshold:
            tree.yview_scroll(-1, "units")
        elif event.y > height - threshold:
            tree.yview_scroll(1, "units")
        target = row_index_at(event.y)
        if target is not None and target < item_count():
            set_target(target)
        return "break"

    def on_release(_event):
        source = state["source"]
        target = state["target"]
        clear_drag()
        if source is not None and target is not None and source != target and enabled():
            on_reorder(source, target)
        return "break" if source is not None else None

    tree.bind("<ButtonPress-1>", on_press, add="+")
    tree.bind("<B1-Motion>", on_motion, add="+")
    tree.bind("<ButtonRelease-1>", on_release, add="+")
    return state


def load_macro_payload(path):
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("宏文件根对象必须是 JSON 对象")
    schema_version = payload.get("schema_version")
    if schema_version != SCHEMA_VERSION:
        raise ValueError(f"不支持的宏格式版本：{schema_version!r}，当前仅支持 {SCHEMA_VERSION}")
    if "events" not in payload:
        raise ValueError("宏文件缺少 events 字段")
    events = validate_events(payload["events"])
    return payload, events


def save_macro_payload(path, events, record_mouse_moves=True, screen_bounds=None):
    payload = {
        "app": APP_NAME,
        "version": VERSION,
        "schema_version": SCHEMA_VERSION,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "record_mouse_moves": bool(record_mouse_moves),
        "screen": screen_bounds or virtual_screen_bounds(),
        "events": validate_events(events),
    }
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(payload, ensure_ascii=False, indent=2)
    temporary = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent)
        )
        temporary = Path(temporary_name)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
        temporary = None
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except OSError:
                pass
    return payload


def enable_dpi_awareness():
    """Keep recorded screen coordinates stable on mixed-DPI Windows displays."""
    try:
        shcore = ctypes.WinDLL("shcore", use_last_error=True)
        shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
    except (AttributeError, OSError):
        try:
            user32.SetProcessDPIAware()
        except AttributeError:
            pass


def enable_dark_title_bar(root):
    """Ask DWM to match the native title bar to the dark application theme."""
    try:
        root.update_idletasks()
        hwnd = user32.GetParent(root.winfo_id()) or root.winfo_id()
        enabled = ctypes.c_int(1)
        dwmapi = ctypes.WinDLL("dwmapi", use_last_error=True)
        for attribute in (20, 19):  # Windows 10 20H1+, then the older fallback.
            if dwmapi.DwmSetWindowAttribute(hwnd, attribute, ctypes.byref(enabled), ctypes.sizeof(enabled)) == 0:
                break
    except (AttributeError, OSError, tk.TclError):
        pass


def virtual_screen_bounds():
    return {
        "left": int(user32.GetSystemMetrics(SM_XVIRTUALSCREEN)),
        "top": int(user32.GetSystemMetrics(SM_YVIRTUALSCREEN)),
        "width": max(1, int(user32.GetSystemMetrics(SM_CXVIRTUALSCREEN))),
        "height": max(1, int(user32.GetSystemMetrics(SM_CYVIRTUALSCREEN))),
    }


def move_cursor_precisely(x, y):
    """Move to a physical screen coordinate and verify Windows accepted it."""
    target = (int(x), int(y))
    point = POINT()
    for _attempt in range(3):
        if not user32.SetCursorPos(*target):
            raise OSError(last_error_message("SetCursorPos failed"))
        if user32.GetCursorPos(ctypes.byref(point)) and (int(point.x), int(point.y)) == target:
            return
        time.sleep(0.001)
    raise OSError(f"cursor did not reach ({target[0]}, {target[1]})")


def map_screen_point(x, y, source_bounds):
    """Map a recorded virtual desktop point when the monitor layout changed."""
    if not source_bounds:
        return int(x), int(y)
    return map_screen_point_between(x, y, source_bounds, virtual_screen_bounds())


def map_screen_point_between(x, y, source_bounds, target_bounds):
    """Map a virtual desktop point from one monitor layout into another."""
    try:
        source = {key: int(source_bounds[key]) for key in ("left", "top", "width", "height")}
        target = {key: int(target_bounds[key]) for key in ("left", "top", "width", "height")}
    except (KeyError, TypeError, ValueError):
        return int(x), int(y)
    if source == target:
        return int(x), int(y)
    source_x = (int(x) - source["left"]) / max(1, source["width"] - 1)
    source_y = (int(y) - source["top"]) / max(1, source["height"] - 1)
    return (
        round(target["left"] + source_x * (target["width"] - 1)),
        round(target["top"] + source_y * (target["height"] - 1)),
    )


def clamp_screen_point(x, y, bounds=None):
    """Keep a cursor target inside the current virtual desktop's valid pixels."""
    current = bounds or virtual_screen_bounds()
    try:
        left = int(current["left"])
        top = int(current["top"])
        right = left + max(1, int(current["width"])) - 1
        bottom = top + max(1, int(current["height"])) - 1
    except (KeyError, TypeError, ValueError):
        return int(x), int(y)
    return min(max(int(x), left), right), min(max(int(y), top), bottom)


def high_word(value):
    return (value >> 16) & 0xFFFF


def signed_word(value):
    value &= 0xFFFF
    return value - 0x10000 if value & 0x8000 else value


def mouse_action_label(message, data=0):
    """Return a user-facing mouse action including wheel direction and side-button id."""
    message = int(message)
    data = int(data)
    if message == WM_MOUSEWHEEL:
        return "垂直滚轮向上" if signed_word(high_word(data)) >= 0 else "垂直滚轮向下"
    if message == WM_MOUSEHWHEEL:
        return "水平滚轮向右" if signed_word(high_word(data)) >= 0 else "水平滚轮向左"
    if message in (WM_XBUTTONDOWN, WM_XBUTTONUP):
        button = high_word(data)
        action = "按下" if message == WM_XBUTTONDOWN else "释放"
        if button in (1, 2):
            return f"侧键{button}{action}"
        # Older recordings did not persist the X-button id; expose them as
        # side-button 1 so the inspector still has a valid catalog option.
        return f"侧键1{action}"
    return MOUSE_MESSAGE_NAMES.get(message, str(message))


def wheel_amount(data):
    """Return the absolute wheel amount stored in a mouse event's high word."""
    return abs(signed_word(high_word(int(data))))


def event_type_label(event):
    """Return the editable event-type label used by the inspector."""
    return event_category_for_event(event)


def build_action_events(action, base_time=0.0, x=0, y=0, delta=120, vk=None, scan=0, key_action="按下", extended=False):
    """Build normalized event dictionaries for both insertion and property editing."""
    base_time = _finite_float(base_time, "时间")
    if base_time < 0:
        raise ValueError("时间不能为负数")
    x = _parse_int(x, "坐标必须是整数")
    y = _parse_int(y, "坐标必须是整数")
    if not INT32_MIN <= x <= INT32_MAX or not INT32_MIN <= y <= INT32_MAX:
        raise ValueError("坐标超出有效范围")
    if action == "键盘按键":
        vk = _parse_int(vk, "按键代码必须为 0-255")
        if not 0 <= vk <= 255:
            raise ValueError("按键代码必须为 0-255")
        scan = _parse_int(scan, "扫描码必须为 0-65535")
        if not 0 <= scan <= SCAN_CODE_MAX:
            raise ValueError("扫描码必须为 0-65535")
        if key_action not in ("按下", "释放", "down", "up"):
            raise ValueError("错误的键盘动作")
        extended = _coerce_bool(extended, "扩展键标记")
        return [{"kind": "key", "t": base_time, "vk": vk, "scan": scan, "action": "up" if key_action in ("释放", "up") else "down", "extended": extended}]

    definition = ACTION_DEFINITIONS.get(action)
    if not definition or definition["kind"] != "mouse":
        raise ValueError(f"未知动作类型：{action}")
    message, modifier = definition["message"], definition.get("modifier")
    data = 0
    if message in (WM_MOUSEWHEEL, WM_MOUSEHWHEEL):
        amount = abs(_parse_int(delta, "滚轮量必须是整数")) or 120
        if amount > 0x7FFF:
            raise ValueError("滚轮量必须不超过 32767")
        direction = modifier if modifier is not None else 1
        data = ((amount * direction) & 0xFFFF) << 16
    elif message in (WM_XBUTTONDOWN, WM_XBUTTONUP) and modifier in (1, 2):
        data = int(modifier) << 16
    return [{"kind": "mouse", "t": base_time, "message": message, "x": x, "y": y, "data": data}]


def send_keyboard(vk, scan=0, is_up=False, extended=False):
    if not scan:
        scan = int(user32.MapVirtualKeyW(vk, MAPVK_VK_TO_VSC))
    if vk == VK_SNAPSHOT and not scan:
        scan = 0x37
    if vk == VK_SNAPSHOT:
        flags = KEYEVENTF_EXTENDEDKEY | (KEYEVENTF_KEYUP if is_up else 0)
        user32.keybd_event(vk, scan, flags, 0)
        return
    flags = KEYEVENTF_KEYUP if is_up else 0
    if extended:
        flags |= KEYEVENTF_EXTENDEDKEY
    if scan:
        flags |= KEYEVENTF_SCANCODE
    item = INPUT(type=INPUT_KEYBOARD)
    item.union.ki = KEYBDINPUT(wVk=0 if scan else vk, wScan=scan, dwFlags=flags, time=0, dwExtraInfo=0)
    sent = user32.SendInput(1, ctypes.byref(item), ctypes.sizeof(INPUT))
    if sent != 1:
        raise OSError(last_error_message("SendInput keyboard failed"))


def send_mouse(flags, data=0):
    item = INPUT(type=INPUT_MOUSE)
    item.union.mi = MOUSEINPUT(dx=0, dy=0, mouseData=data, dwFlags=flags, time=0, dwExtraInfo=0)
    sent = user32.SendInput(1, ctypes.byref(item), ctypes.sizeof(INPUT))
    if sent != 1:
        raise OSError(last_error_message("SendInput mouse failed"))


def retry_input_release(callback, *args):
    """Retry one release action and return its final exception, if any."""
    last_error = None
    for attempt in range(INPUT_RELEASE_ATTEMPTS):
        try:
            callback(*args)
            return None
        except Exception as exc:
            last_error = exc
            if attempt + 1 < INPUT_RELEASE_ATTEMPTS:
                time.sleep(INPUT_RELEASE_RETRY_DELAY)
    return last_error


def mouse_release_name(release_flag, data=0):
    if release_flag == MOUSEEVENTF_XUP:
        return f"鼠标侧键 {int(data)}"
    return MOUSE_RELEASE_FLAG_NAMES.get(release_flag, f"鼠标按钮 ({release_flag})")


class HookThread:
    def __init__(self, hook_id, handler, on_error):
        self.hook_id = hook_id
        self.handler = handler
        self.on_error = on_error
        self.hook = None
        self.thread_id = None
        self.ready = threading.Event()
        self.finished = threading.Event()
        self.proc = LowLevelProc(self._dispatch)
        self.thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self.thread.start()
        if not self.ready.wait(timeout=1.0):
            self.on_error("输入 hook 线程启动超时。")
            return False
        return self.hook is not None

    def stop(self):
        if self.thread_id and not self.finished.is_set():
            user32.PostThreadMessageW(self.thread_id, WM_QUIT, 0, 0)
        self.finished.wait(timeout=2.0)
        self.thread.join(timeout=0.2)

    def _dispatch(self, n_code, w_param, l_param):
        if n_code == HC_ACTION:
            try:
                self.handler(w_param, l_param)
            except Exception as exc:
                self.on_error(f"Hook callback error: {exc}")
        return user32.CallNextHookEx(self.hook, n_code, w_param, l_param)

    def _run(self):
        self.thread_id = kernel32.GetCurrentThreadId()
        # Create the thread message queue before publishing readiness so a
        # concurrent stop() can always deliver WM_QUIT.
        user32.PeekMessageW(ctypes.byref(MSG()), None, 0, 0, PM_NOREMOVE)
        hmod = kernel32.GetModuleHandleW(None)
        self.hook = user32.SetWindowsHookExW(self.hook_id, self.proc, hmod, 0)
        if not self.hook:
            self.on_error(last_error_message("SetWindowsHookEx failed"))
            self.ready.set()
            self.finished.set()
            return
        self.ready.set()
        msg = MSG()
        try:
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        finally:
            if self.hook:
                user32.UnhookWindowsHookEx(self.hook)
                self.hook = None
            self.finished.set()


class RecordingHookThread:
    """Run keyboard and mouse low-level hooks on one ordered message thread."""

    def __init__(self, keyboard_handler, mouse_handler, on_error):
        self.handlers = {
            WH_KEYBOARD_LL: keyboard_handler,
            WH_MOUSE_LL: mouse_handler,
        }
        self.on_error = on_error
        self.hooks = {}
        self.thread_id = None
        self.ready = threading.Event()
        self.finished = threading.Event()
        self.procs = {
            hook_id: LowLevelProc(
                lambda n_code, w_param, l_param, current_id=hook_id: self._dispatch(
                    current_id, n_code, w_param, l_param
                )
            )
            for hook_id in self.handlers
        }
        self.thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self.thread.start()
        if not self.ready.wait(timeout=1.0):
            self.on_error("输入 hook 线程启动超时。")
            return False
        return len(self.hooks) == len(self.handlers)

    def stop(self):
        if self.thread_id and not self.finished.is_set():
            user32.PostThreadMessageW(self.thread_id, WM_QUIT, 0, 0)
        self.finished.wait(timeout=2.0)
        self.thread.join(timeout=0.2)

    def _dispatch(self, hook_id, n_code, w_param, l_param):
        if n_code == HC_ACTION:
            try:
                self.handlers[hook_id](w_param, l_param)
            except Exception as exc:
                self.on_error(f"Hook callback error: {exc}")
        return user32.CallNextHookEx(self.hooks.get(hook_id), n_code, w_param, l_param)

    def _run(self):
        self.thread_id = kernel32.GetCurrentThreadId()
        user32.PeekMessageW(ctypes.byref(MSG()), None, 0, 0, PM_NOREMOVE)
        hmod = kernel32.GetModuleHandleW(None)
        hook_names = {WH_KEYBOARD_LL: "键盘", WH_MOUSE_LL: "鼠标"}
        try:
            for hook_id, proc in self.procs.items():
                hook = user32.SetWindowsHookExW(hook_id, proc, hmod, 0)
                if not hook:
                    self.on_error(last_error_message(f"{hook_names[hook_id]} SetWindowsHookEx failed"))
                    return
                self.hooks[hook_id] = hook
            self.ready.set()
            msg = MSG()
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        finally:
            self.ready.set()
            for hook in reversed(list(self.hooks.values())):
                user32.UnhookWindowsHookEx(hook)
            self.hooks.clear()
            self.finished.set()


class KeyCaptureHook:
    """Capture one physical key from the low-level hook, independent of IME state."""

    def __init__(self, root, on_key, on_error):
        self.root = root
        self.on_key = on_key
        self.on_error = on_error
        self.active = True
        self.hook = HookThread(WH_KEYBOARD_LL, self._keyboard_event, on_error)

    def start(self):
        self.hook.start()

    def stop(self):
        self.active = False
        self.hook.stop()

    def _keyboard_event(self, _w_param, l_param):
        if not self.active:
            return
        data = ctypes.cast(l_param, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
        if data.flags & (LLKHF_INJECTED | LLKHF_UP):
            return
        vk = int(data.vkCode)
        if vk in IGNORED_HOTKEYS:
            return
        self.active = False
        scan = int(data.scanCode)
        extended = bool(data.flags & LLKHF_EXTENDED)
        try:
            self.root.after(0, self._deliver, vk, scan, extended)
        except Exception:
            self.on_error("按键捕捉窗口已关闭。")

    def _deliver(self, vk, scan, extended):
        self.stop()
        self.on_key(vk, scan, extended)


class HotkeyThread:
    def __init__(self, callback, on_error):
        self.callback = callback
        self.on_error = on_error
        self.thread_id = None
        self.ready = threading.Event()
        self.finished = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self.thread.start()
        if not self.ready.wait(timeout=1.0):
            self.on_error("全局热键线程启动超时。")
            return False
        return True

    def stop(self):
        self.ready.wait(timeout=1.0)
        if self.thread_id and not self.finished.is_set():
            user32.PostThreadMessageW(self.thread_id, WM_QUIT, 0, 0)
        self.finished.wait(timeout=2.0)
        self.thread.join(timeout=2.0)

    def _run(self):
        self.thread_id = kernel32.GetCurrentThreadId()
        user32.PeekMessageW(ctypes.byref(MSG()), None, 0, 0, PM_NOREMOVE)
        hotkeys = [
            (HOTKEY_TOGGLE_RECORD, VK_F8),
            (HOTKEY_PLAY, VK_F9),
            (HOTKEY_STOP, VK_F10),
        ]
        registered = []
        for hotkey_id, vk in hotkeys:
            if user32.RegisterHotKey(None, hotkey_id, MOD_NOREPEAT, vk):
                registered.append(hotkey_id)
            else:
                self.on_error(f"全局热键 F{vk - 0x6F} 注册失败，可能已被其它程序占用。")
        self.ready.set()
        msg = MSG()
        try:
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == WM_HOTKEY:
                    self.callback(int(msg.wParam))
        finally:
            for hotkey_id in registered:
                user32.UnregisterHotKey(None, hotkey_id)
            self.finished.set()


class MacroApp:
    def __init__(self, root):
        self.root = root
        self.root.title(APP_NAME)
        self.root.minsize(1080, 700)
        self.events = []
        self.events_lock = threading.Lock()
        self.start_time = 0.0
        self.recording_start_tick = 0
        self.last_move_time = 0.0
        self.last_move_x = None
        self.last_move_y = None
        self.recording = False
        self.playing = False
        self.stop_playback = threading.Event()
        self.pause_playback = threading.Event()
        self.recording_hook = None
        self.key_capture_hook = None
        self.ui_queue = queue.Queue()
        self.current_file = None
        self.dirty = False
        self.recording_screen = None
        self.loaded_screen = None
        self.record_moves_enabled = True
        self.selected_index = None
        self.selected_indices = []
        self.undo_stack = []
        self.redo_stack = []
        self._pending_key_undo = None
        self.record_moves_var = tk.BooleanVar(value=True)
        self.speed_var = tk.StringVar(value="1.0")
        self.repeat_mode_var = tk.StringVar(value=REPEAT_MODE_COUNT)
        self.repeat_var = tk.StringVar(value="1")
        self.status_var = tk.StringVar(value="就绪")
        self.count_var = tk.StringVar(value="0 个 / 0.00 秒")
        self.file_var = tk.StringVar(value="未保存")
        self.progress_var = tk.StringVar(value="准备就绪")
        self._ui_style = None
        self._ui_font = "Microsoft YaHei UI"
        self._ui_scale = None
        self._font_resize_after = None
        self._scaled_text_widgets = []
        self._build_ui()
        self._center_window(1640, 1000)
        self.root.bind("<Configure>", self._on_window_resize, add="+")
        self.root.after_idle(self._apply_ui_scale)
        enable_dark_title_bar(self.root)
        self.root.after(120, self._balance_editor_panes)
        self.hotkey_thread = HotkeyThread(self._queue_hotkey, self._queue_log)
        self.hotkey_thread.start()
        self.root.after(60, self._drain_queue)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.bind("<Control-s>", lambda _event: self.save_macro())
        self.root.bind_all("<Control-z>", self._undo_hotkey)
        self.root.bind_all("<Control-y>", self._redo_hotkey)
        self.root.bind("<Delete>", lambda _event: self.delete_selected())

    def _build_ui(self):
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        colors = {
            "canvas": "#0b1016",
            "topbar": "#10161e",
            "surface": "#151c24",
            "raised": "#1d2732",
            "border": "#2b3846",
            "border_strong": "#3b4b5e",
            "text": "#edf3f8",
            "muted": "#9aa9b8",
            "faint": "#718092",
            # Muted accents keep primary actions visible without dominating the
            # dark workbench, especially in the large high-readability layout.
            "blue": "#3d668a",
            "blue_hover": "#527da0",
            "blue_pressed": "#2e4f6d",
            "green": "#45d483",
            "green_surface": "#163526",
            "red": "#a0575d",
            "red_hover": "#b86d72",
            "red_pressed": "#773d44",
            "warning": "#c1a05b",
        }
        ui_font = self._ui_font
        self._ui_style = style
        self.root.configure(background=colors["canvas"])
        self.root.option_add("*TCombobox*Listbox.background", colors["surface"])
        self.root.option_add("*TCombobox*Listbox.foreground", colors["text"])
        self.root.option_add("*TCombobox*Listbox.selectBackground", colors["blue"])
        self.root.option_add("*TCombobox*Listbox.selectForeground", "#ffffff")
        self.root.option_add("*TCombobox*Listbox.font", (ui_font, 18))

        style.configure("TFrame", background=colors["surface"])
        style.configure("TLabel", background=colors["surface"], foreground=colors["text"], font=(ui_font, 15))
        style.configure("App.TFrame", background=colors["canvas"])
        style.configure("Topbar.TFrame", background=colors["topbar"], bordercolor=colors["border_strong"], relief="solid", borderwidth=1)
        style.configure("TopbarInner.TFrame", background=colors["topbar"])
        style.configure("Surface.TFrame", background=colors["surface"], bordercolor=colors["border"], relief="solid", borderwidth=1)
        style.configure("Panel.TFrame", background=colors["surface"], bordercolor=colors["border"], relief="solid", borderwidth=1)
        style.configure("AccentLine.TFrame", background=colors["blue"])
        style.configure("Toolbar.TFrame", background=colors["raised"], bordercolor=colors["border_strong"], relief="solid", borderwidth=1)
        style.configure("ToolbarInner.TFrame", background=colors["raised"])
        style.configure("HeaderTitle.TLabel", background=colors["topbar"], foreground=colors["text"], font=(ui_font, 26, "bold"))
        style.configure("HeaderSubtle.TLabel", background=colors["topbar"], foreground=colors["muted"], font=(ui_font, 13))
        style.configure("Status.TLabel", background=colors["green_surface"], foreground=colors["green"], padding=(13, 7), font=(ui_font, 13, "bold"))
        style.configure("Title.TLabel", background=colors["surface"], foreground=colors["text"], font=(ui_font, 20, "bold"))
        style.configure("Subtle.TLabel", background=colors["surface"], foreground=colors["muted"], font=(ui_font, 14))
        style.configure("Toolbar.TLabel", background=colors["raised"], foreground=colors["muted"], font=(ui_font, 13))
        style.configure("Safety.TLabel", background=colors["surface"], foreground=colors["warning"], font=(ui_font, 15), wraplength=1000)
        style.configure("Card.TLabelframe", background=colors["surface"], bordercolor=colors["border"], relief="solid", borderwidth=1)
        style.configure("Card.TLabelframe.Label", background=colors["surface"], foreground=colors["muted"], font=(ui_font, 13, "bold"))
        style.configure("Empty.TLabel", background=colors["surface"], foreground=colors["muted"], font=(ui_font, 16), justify="center")
        style.configure("TPanedwindow", background=colors["canvas"], sashwidth=6)
        style.configure("TSeparator", background=colors["border"])

        style.configure("Vertical.TScrollbar", background=colors["raised"], troughcolor=colors["surface"], bordercolor=colors["border"], arrowcolor=colors["muted"], relief="flat")
        style.map("Vertical.TScrollbar", background=[("active", colors["border"])])

        style.configure("TButton", foreground=colors["text"], background=colors["raised"], bordercolor=colors["border_strong"], lightcolor=colors["raised"], darkcolor=colors["raised"], font=(ui_font, 13, "bold"), padding=(14, 9), relief="solid", borderwidth=1)
        style.map("TButton", foreground=[("disabled", colors["faint"])], background=[("active", colors["border_strong"]), ("pressed", colors["surface"]), ("disabled", colors["surface"])], bordercolor=[("active", colors["blue"]), ("disabled", colors["border"])])
        style.configure("Compact.TButton", padding=(11, 7), font=(ui_font, 12))
        style.configure("Accent.TButton", foreground="#ffffff", background=colors["blue"], bordercolor=colors["blue"], lightcolor=colors["blue"], darkcolor=colors["blue"], padding=(16, 9), font=(ui_font, 13, "bold"))
        style.map("Accent.TButton", background=[("active", colors["blue_hover"]), ("pressed", colors["blue_pressed"]), ("disabled", colors["raised"])], bordercolor=[("active", colors["blue_hover"]), ("disabled", colors["border"])])
        style.configure("Timeline.TButton", foreground=colors["text"], background=colors["raised"], bordercolor=colors["border_strong"], lightcolor=colors["raised"], darkcolor=colors["raised"], padding=(5, 3), font=(ui_font, 12))
        style.map("Timeline.TButton", foreground=[("disabled", colors["faint"])], background=[("active", colors["border_strong"]), ("pressed", colors["surface"]), ("disabled", colors["surface"])], bordercolor=[("active", colors["blue"]), ("disabled", colors["border"])])
        style.configure("TimelineAccent.TButton", foreground="#ffffff", background=colors["blue"], bordercolor=colors["blue"], lightcolor=colors["blue"], darkcolor=colors["blue"], padding=(8, 4), font=(ui_font, 13, "bold"))
        style.map("TimelineAccent.TButton", background=[("active", colors["blue_hover"]), ("pressed", colors["blue_pressed"]), ("disabled", colors["raised"])], bordercolor=[("active", colors["blue_hover"]), ("disabled", colors["border"])])
        style.configure("Record.TButton", foreground="#ffffff", background=colors["red"], bordercolor=colors["red"], lightcolor=colors["red"], darkcolor=colors["red"], padding=(17, 9), font=(ui_font, 13, "bold"))
        style.map("Record.TButton", background=[("active", colors["red_hover"]), ("pressed", colors["red_pressed"])])
        style.configure("Danger.TButton", foreground=colors["red_hover"], background=colors["raised"], bordercolor=colors["border_strong"], lightcolor=colors["raised"], darkcolor=colors["raised"], padding=(14, 9), font=(ui_font, 13, "bold"))
        style.map("Danger.TButton", foreground=[("active", "#ffffff"), ("disabled", colors["faint"])], background=[("active", colors["red_pressed"]), ("disabled", colors["surface"])])

        style.configure("TCheckbutton", background=colors["raised"], foreground=colors["text"], font=(ui_font, 13), indicatorcolor=colors["surface"], padding=(0, 4))
        style.map("TCheckbutton", background=[("active", colors["raised"])], indicatorcolor=[("selected", colors["blue"]), ("active", colors["border_strong"])])

        for control in ("TCombobox", "TSpinbox", "TEntry"):
            style.configure(control, fieldbackground=colors["canvas"], background=colors["raised"], foreground=colors["text"], bordercolor=colors["border"], insertcolor=colors["text"], padding=6, font=(ui_font, 14), arrowcolor=colors["muted"])
            style.map(control, fieldbackground=[("readonly", colors["canvas"]), ("disabled", colors["surface"])], foreground=[("readonly", colors["text"]), ("disabled", colors["faint"])], bordercolor=[("focus", colors["blue"])])
        style.configure("Option.TLabel", background=colors["raised"], foreground=colors["muted"], font=(ui_font, 16))
        for control in ("Option.TEntry",):
            style.configure(control, fieldbackground=colors["canvas"], background=colors["raised"], foreground=colors["text"], bordercolor=colors["border"], insertcolor=colors["text"], padding=7, font=(ui_font, 16), arrowcolor=colors["muted"])
            style.map(control, fieldbackground=[("disabled", colors["surface"])], foreground=[("disabled", colors["faint"])], bordercolor=[("focus", colors["blue"])])
        style.configure("Option.TCombobox", fieldbackground=colors["canvas"], background=colors["raised"], foreground=colors["text"], bordercolor=colors["border"], insertcolor=colors["text"], padding=7, font=(ui_font, 16), arrowcolor=colors["muted"])
        style.map("Option.TCombobox", fieldbackground=[("readonly", colors["canvas"]), ("disabled", colors["surface"])], foreground=[("readonly", colors["text"]), ("disabled", colors["faint"])], bordercolor=[("focus", colors["blue"])])
        style.configure("Option.TSpinbox", fieldbackground=colors["canvas"], background=colors["raised"], foreground=colors["text"], bordercolor=colors["border"], insertcolor=colors["text"], padding=7, font=(ui_font, 16), arrowcolor=colors["muted"], arrowsize=22)
        style.map("Option.TSpinbox", fieldbackground=[("disabled", colors["surface"])], foreground=[("disabled", colors["faint"])], bordercolor=[("focus", colors["blue"])])
        for control in ("Property.TCombobox", "Property.TEntry"):
            style.configure(control, fieldbackground=colors["canvas"], background=colors["raised"], foreground=colors["text"], bordercolor=colors["border"], insertcolor=colors["text"], padding=7, font=(ui_font, 16), arrowcolor=colors["muted"])
            style.map(control, fieldbackground=[("readonly", colors["canvas"]), ("disabled", colors["surface"])], foreground=[("readonly", colors["text"]), ("disabled", colors["faint"])], bordercolor=[("focus", colors["blue"])])

        style.configure("Treeview", rowheight=52, font=(ui_font, 14), background=colors["surface"], fieldbackground=colors["surface"], foreground=colors["text"], bordercolor=colors["border"], borderwidth=1)
        style.map("Treeview", background=[("selected", "#245a9e")], foreground=[("selected", "#ffffff")])
        style.configure("Treeview.Heading", font=(ui_font, 13, "bold"), background=colors["raised"], foreground=colors["muted"], bordercolor=colors["border_strong"], padding=(10, 9), relief="flat")
        style.map("Treeview.Heading", background=[("active", colors["border_strong"])], foreground=[("active", colors["text"])])

        main = ttk.Frame(self.root, style="App.TFrame", padding=(18, 10, 18, 10))
        main.pack(fill="both", expand=True)
        ttk.Frame(main, style="AccentLine.TFrame", height=3).pack(fill="x", pady=(0, 1))
        header = ttk.Frame(main, style="Topbar.TFrame", padding=(16, 10))
        header.pack(fill="x", pady=(0, 10))
        title_group = ttk.Frame(header, style="TopbarInner.TFrame")
        title_group.pack(side="left", fill="y")
        ttk.Label(title_group, text=APP_NAME, style="HeaderTitle.TLabel").pack(anchor="w")
        ttk.Label(title_group, text="本地宏录制、编写与回放", style="HeaderSubtle.TLabel").pack(anchor="w", pady=(1, 0))
        ttk.Label(header, textvariable=self.status_var, style="Status.TLabel").pack(side="right", padx=(12, 0))
        ttk.Label(header, textvariable=self.file_var, style="HeaderSubtle.TLabel").pack(side="right")

        controls = ttk.Frame(main, style="Toolbar.TFrame", padding=(12, 8))
        controls.pack(fill="x", pady=(0, 10))
        action_row = ttk.Frame(controls, style="ToolbarInner.TFrame")
        action_row.pack(fill="x")
        self.record_button = ttk.Button(action_row, text="●  开始录制   F8", style="Record.TButton", command=self.toggle_recording)
        self.record_button.pack(side="left", padx=(0, 8))
        self.play_button = ttk.Button(action_row, text="▶  播放   F9", style="Accent.TButton", command=self.play)
        self.play_button.pack(side="left", padx=4)
        self.stop_button = ttk.Button(action_row, text="■  停止   F10", style="Danger.TButton", command=self.stop_all)
        self.stop_button.pack(side="left", padx=4)
        ttk.Separator(action_row, orient="vertical").pack(side="left", fill="y", padx=12)
        ttk.Button(action_row, text="载入", width=4, style="Compact.TButton", command=self.load_macro).pack(side="left", padx=3)
        ttk.Button(action_row, text="保存", width=4, style="Compact.TButton", command=self.save_macro).pack(side="left", padx=3)
        # Let ttk derive the width from the label so enlarged fonts cannot
        # clip the final Chinese glyph in the fullscreen layout.
        self.compose_button = ttk.Button(action_row, text="多文件编排", style="Compact.TButton", command=self.open_composer)
        self.compose_button.pack(side="left", padx=3)
        options_content = ttk.Frame(action_row, style="ToolbarInner.TFrame")
        options_content.pack(side="right", padx=(12, 0))
        ttk.Separator(action_row, orient="vertical").pack(side="right", fill="y", padx=(12, 0))
        ttk.Label(options_content, text="速度", style="Option.TLabel").pack(side="left", padx=(0, 7))
        validate_speed = self.root.register(self._validate_speed_input)
        speed_box = ttk.Entry(options_content, textvariable=self.speed_var, width=6, style="Option.TEntry", font=(ui_font, 18), validate="key", validatecommand=(validate_speed, "%P"))
        speed_box.pack(side="left")
        speed_box.bind("<FocusOut>", self._normalize_speed)
        speed_box.bind("<Return>", self._normalize_speed)
        self._scaled_text_widgets.append(speed_box)
        ttk.Label(options_content, text="重复", style="Option.TLabel").pack(side="left", padx=(14, 7))
        repeat_modes = ttk.Frame(options_content, style="ToolbarInner.TFrame")
        repeat_modes.pack(side="left", padx=(0, 7))
        self.repeat_mode_buttons = []
        count_mode_button = tk.Radiobutton(
            repeat_modes,
            text="指定次数",
            variable=self.repeat_mode_var,
            value=REPEAT_MODE_COUNT,
            indicatoron=False,
            background=colors["raised"],
            foreground=colors["text"],
            activebackground=colors["border_strong"],
            activeforeground="#ffffff",
            selectcolor=colors["blue"],
            font=(ui_font, 14, "bold"),
            padx=11,
            pady=7,
            borderwidth=1,
            relief="solid",
            highlightthickness=0,
            command=self._update_repeat_mode,
        )
        count_mode_button.pack(side="left")
        self.repeat_mode_buttons.append(count_mode_button)
        loop_mode_button = tk.Radiobutton(
            repeat_modes,
            text="循环",
            variable=self.repeat_mode_var,
            value=REPEAT_MODE_LOOP,
            indicatoron=False,
            background=colors["raised"],
            foreground=colors["text"],
            activebackground=colors["border_strong"],
            activeforeground="#ffffff",
            selectcolor=colors["blue"],
            font=(ui_font, 14, "bold"),
            padx=11,
            pady=7,
            borderwidth=1,
            relief="solid",
            highlightthickness=0,
            command=self._update_repeat_mode,
        )
        loop_mode_button.pack(side="left", padx=(2, 0))
        self.repeat_mode_buttons.append(loop_mode_button)
        ttk.Label(options_content, text="次数", style="Option.TLabel").pack(side="left", padx=(0, 5))
        validate_repeat = self.root.register(self._validate_repeat_input)
        repeat_box = ttk.Entry(options_content, textvariable=self.repeat_var, width=7, style="Option.TEntry", font=(ui_font, 18), validate="key", validatecommand=(validate_repeat, "%P"))
        repeat_box.pack(side="left")
        repeat_box.bind("<FocusOut>", self._normalize_repeat)
        repeat_box.bind("<Return>", self._normalize_repeat)
        self.repeat_box = repeat_box
        self._scaled_text_widgets.append(repeat_box)
        self._update_repeat_mode()

        content = ttk.Panedwindow(main, orient="horizontal")
        self.editor_panes = content
        content.pack(fill="both", expand=True)

        editor = ttk.Frame(content, style="Panel.TFrame", padding=(14, 12))
        inspector = ttk.Frame(content, style="Panel.TFrame", padding=(16, 12))
        self.inspector_panel = inspector
        inspector.configure(width=360)
        content.add(editor, weight=4)
        content.add(inspector, weight=3)

        list_header = ttk.Frame(editor)
        list_header.pack(fill="x", pady=(0, 7))
        ttk.Label(list_header, text="时间轴", style="Title.TLabel").pack(side="left")
        ttk.Label(list_header, textvariable=self.count_var, style="Subtle.TLabel").pack(side="left", padx=(10, 0))
        list_tools = ttk.Frame(list_header)
        list_tools.pack(side="right")
        self.timeline_tools = list_tools
        ttk.Button(list_tools, text="＋  插入事件", style="TimelineAccent.TButton", width=10, command=self.insert_event).pack(side="left", padx=(0, 8))
        ttk.Button(list_tools, text="删除鼠标移动", style="Compact.TButton", command=self.delete_all_mouse_moves).pack(side="left", padx=2)
        for label, command in (
            ("复制", self.duplicate_selected),
            ("上移", self.move_selected_up),
            ("下移", self.move_selected_down),
            ("删除", self.delete_selected),
            ("清空", self.clear_events),
        ):
            ttk.Button(list_tools, text=label, style="Timeline.TButton", width=6, command=command).pack(side="left", padx=2)
        self.insert_event_button = list_tools.winfo_children()[0]
        self.timeline_edit_buttons = list_tools.winfo_children()
        tree_frame = ttk.Frame(editor)
        tree_frame.pack(fill="both", expand=True)

        # 列定义
        columns = ("index", "time", "action", "details")
        self.tree = ttk.Treeview(tree_frame, columns=columns, show="headings", selectmode="browse")

        headings = {"index": "#", "time": "时间", "action": "类型", "details": "详情"}
        widths = {"index": 48, "time": 105, "action": 105, "details": 420}

        for column in columns:
            self.tree.heading(column, text=headings[column])
            self.tree.column(column, width=widths[column], anchor="w" if column == "details" else "center", stretch=column == "details")

        self.tree.pack(side="left", fill="both", expand=True)

        self.tree.tag_configure("even", background=colors["surface"])
        self.tree.tag_configure("odd", background="#1b2028")
        self.tree.tag_configure("drag_target", background=colors["blue"], foreground="#ffffff")
        scrollbar = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        scrollbar.pack(side="right", fill="y")
        self.tree.configure(yscrollcommand=scrollbar.set)

        self.empty_hint = ttk.Label(tree_frame, text="还没有事件\n\n按 F8 开始录制，或点击“载入”打开一个宏", style="Empty.TLabel", justify="center")
        self.empty_hint.place(relx=0.5, rely=0.5, anchor="center")

        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)
        self.tree.bind("<Double-1>", lambda _event: self.apply_selected())
        self._timeline_drag = bind_treeview_drag_reorder(
            self.tree,
            lambda: len(self.events),
            self._move_event_to,
            enabled=lambda: not self.recording and not self.playing,
        )

        ttk.Label(inspector, text="事件属性", style="Title.TLabel").pack(anchor="w")
        self.inspector_hint = ttk.Label(inspector, text="在时间轴中选择一个事件", style="Subtle.TLabel")
        self.inspector_hint.pack(anchor="w", pady=(3, 10))

        self.inspector_help = ttk.Label(
            inspector,
            text="时间属性是在轴上的绝对时间，支持修改坐标、动作、时间",
            style="Subtle.TLabel",
            wraplength=330,
            justify="left",
        )
        self.inspector_help.pack(anchor="w", pady=(0, 10))

        # 属性表单
        form = ttk.Frame(inspector)
        form.pack(fill="x")

        self.property_vars = {name: tk.StringVar() for name in ("event_type", "time", "action_type", "x", "y", "vk", "scan", "delta", "key")}
        self.property_extended = tk.BooleanVar(value=False)
        self.advanced_key_var = tk.BooleanVar(value=False)
        self._key_capture_armed = False
        self.property_entries = {}
        self.property_widgets = {}
        self.property_labels = {}
        self.property_rows = {}

        fields = (
            ("event_type", "事件类型"),
            ("time", "时间（秒）"),
            ("action_type", "动作"),
            ("key", "按键"),
            ("vk", "按键代码"),
            ("scan", "扫描码"),
            ("x", " X 坐标"),
            ("y", " Y 坐标"),
            ("delta", "滚轮量（绝对值）"),
        )

        for row, (name, label) in enumerate(fields):
            # Keep one dedicated row below the key field for the advanced toggle.
            grid_row = row + (1 if row >= 4 else 0)
            label_widget = ttk.Label(form, text=label, width=16, anchor="w", style="Subtle.TLabel")
            label_widget.grid(row=grid_row, column=0, sticky="w", pady=3)

            if name == "event_type":
                widget = ttk.Combobox(form, textvariable=self.property_vars[name], values=EVENT_TYPE_VALUES, state="normal", style="Property.TCombobox", font=(ui_font, 18))
                widget.bind("<<ComboboxSelected>>", self._on_event_type_changed)
                widget.bind("<FocusOut>", self._on_event_type_changed, add="+")
                widget.bind("<Return>", self._on_event_type_changed, add="+")
            elif name == "action_type":
                widget = ttk.Combobox(form, textvariable=self.property_vars[name], values=ACTION_TYPE_VALUES["鼠标"], state="normal", style="Property.TCombobox", font=(ui_font, 18))
                widget.bind("<<ComboboxSelected>>", self._on_action_type_changed)
                widget.bind("<FocusOut>", self._on_action_type_changed, add="+")
                widget.bind("<Return>", self._on_action_type_changed, add="+")
            elif name == "key":
                widget = ttk.Entry(form, textvariable=self.property_vars[name], state="readonly", style="Property.TEntry", font=(ui_font, 18))
                widget.bind("<Button-1>", self._arm_key_capture)
                widget.bind("<KeyPress>", self._capture_property_key)
                label_widget.bind("<Button-1>", self._arm_key_capture)
            else:
                widget = ttk.Entry(form, textvariable=self.property_vars[name], style="Property.TEntry", font=(ui_font, 18))

            widget.grid(row=grid_row, column=1, sticky="ew", pady=3)
            self.property_entries[name] = widget
            self.property_widgets[name] = widget
            self.property_labels[name] = label_widget
            self.property_rows[name] = grid_row
            self._scaled_text_widgets.append(widget)

        self.advanced_key_toggle = ttk.Checkbutton(
            form,
            text="高级",
            variable=self.advanced_key_var,
            command=self._refresh_advanced_key_fields,
        )
        self.advanced_key_toggle.grid(row=self.property_rows["key"] + 1, column=1, sticky="w", pady=(0, 4))
        self.advanced_key_var.trace_add("write", lambda *_args: self._refresh_advanced_key_fields())
        form.columnconfigure(1, weight=1)

        ttk.Button(inspector, text="应用修改", style="Accent.TButton", command=self.apply_selected).pack(fill="x", pady=(11, 6))
        self.apply_button = inspector.winfo_children()[-1]
        ttk.Button(inspector, text="▶  播放当前步骤", command=self.play_selected_step).pack(fill="x")
        ttk.Separator(inspector).pack(fill="x", pady=12)
        self.safety_label = ttk.Label(inspector, text="回放会接管当前键盘和鼠标。请先切换到目标窗口，并避免录制密码或密钥。\n支持Ctrl+z撤销与Ctrl+y重做。", style="Safety.TLabel", wraplength=1000)
        safety = self.safety_label
        safety.pack(anchor="w", pady=(12, 0))
        inspector.bind("<Configure>", self._on_inspector_resize, add="+")

        log_frame = ttk.LabelFrame(main, text="活动日志", style="Card.TLabelframe", padding=7)
        log_frame.pack(fill="x", pady=(12, 0))
        self.log_text = tk.Text(log_frame, height=3, wrap="word", state="disabled", font=(ui_font, 14), background=colors["surface"], foreground=colors["muted"], selectbackground=colors["blue"], selectforeground="#ffffff", insertbackground=colors["text"], relief="flat", padx=10, pady=7)
        self.log_text.pack(fill="x")
        self._set_inspector_enabled(False)

    def _center_window(self, width, height):
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        width = min(width, max(self.root.winfo_reqwidth(), screen_width - 80))
        height = min(height, max(self.root.winfo_reqheight(), screen_height - 80))
        x = max(0, (screen_width - width) // 2)
        y = max(0, (screen_height - height) // 2)
        self.root.geometry(f"{width}x{height}+{x}+{y}")

    def _on_inspector_resize(self, event=None):
        if not hasattr(self, "safety_label"):
            return
        panel_width = int(getattr(event, "width", 0) or self.inspector_panel.winfo_width())
        if panel_width > 0:
            self.safety_label.configure(wraplength=max(280, panel_width - 28))

    def _validate_speed_input(self, proposed):
        """Allow editable speed text while rejecting negative values and values above the cap."""
        if proposed in ("", "."):
            return True
        if proposed.count(".") > 1 or any(char not in "0123456789." for char in proposed):
            return False
        try:
            value = float(proposed)
        except ValueError:
            return False
        return math.isfinite(value) and 0.0 <= value <= SPEED_MAX

    def _normalize_speed(self, _event=None):
        try:
            value = float(self.speed_var.get().strip())
        except (TypeError, ValueError):
            value = 1.0
        value = min(SPEED_MAX, max(SPEED_MIN, value))
        text = f"{value:.2f}".rstrip("0").rstrip(".")
        if "." not in text:
            text += ".0"
        self.speed_var.set(text)
        return "break" if _event is not None and getattr(_event, "keysym", "") == "Return" else None

    @staticmethod
    def _validate_repeat_input(proposed):
        return proposed == "" or proposed.isdigit()

    def _normalize_repeat(self, _event=None):
        value = str(self.repeat_var.get()).strip()
        if not value or not value.isdigit() or int(value) < 1:
            self.repeat_var.set("1")
        else:
            self.repeat_var.set(str(int(value)))
        return "break" if _event is not None and getattr(_event, "keysym", "") == "Return" else None

    def _update_repeat_mode(self):
        if not hasattr(self, "repeat_box"):
            return
        if self.repeat_mode_var.get() == REPEAT_MODE_LOOP:
            self.repeat_box.state(["disabled"])
        else:
            self.repeat_box.state(["!disabled"])

    def _on_window_resize(self, _event=None):
        """Debounce font changes while the native window is being resized."""
        if self._font_resize_after is not None:
            self.root.after_cancel(self._font_resize_after)
        self._font_resize_after = self.root.after(80, self._apply_ui_scale)

    def _apply_ui_scale(self):
        """Scale the readable UI typography with the current window size."""
        self._font_resize_after = None
        if self._ui_style is None:
            return
        width = max(1, self.root.winfo_width())
        height = max(1, self.root.winfo_height())
        scale = min(width / 1640.0, height / 1000.0) * 1.15
        scale = max(1.0, min(1.45, scale))
        if self._ui_scale is not None and abs(scale - self._ui_scale) < 0.015:
            return
        self._ui_scale = scale
        style = self._ui_style
        font = self._ui_font

        def size(base):
            return max(10, round(base * scale))

        self.root.option_add("*TCombobox*Listbox.font", (font, size(18)))

        style.configure("TLabel", font=(font, size(15)))
        style.configure("HeaderTitle.TLabel", font=(font, size(26), "bold"))
        style.configure("HeaderSubtle.TLabel", font=(font, size(13)))
        style.configure("Status.TLabel", font=(font, size(13), "bold"))
        style.configure("Title.TLabel", font=(font, size(20), "bold"))
        style.configure("Subtle.TLabel", font=(font, size(14)))
        style.configure("Toolbar.TLabel", font=(font, size(13)))
        style.configure("Safety.TLabel", font=(font, size(15)))
        style.configure("Card.TLabelframe.Label", font=(font, size(13), "bold"))
        style.configure("Empty.TLabel", font=(font, size(16)))
        style.configure("TButton", font=(font, size(13), "bold"), padding=(round(14 * scale), round(9 * scale)))
        style.configure("Compact.TButton", font=(font, size(12)), padding=(round(11 * scale), round(7 * scale)))
        style.configure("Accent.TButton", font=(font, size(13), "bold"), padding=(round(16 * scale), round(9 * scale)))
        style.configure("Timeline.TButton", font=(font, size(12)), padding=(max(3, round(5 * scale)), max(2, round(3 * scale))))
        style.configure("TimelineAccent.TButton", font=(font, size(13), "bold"), padding=(max(5, round(8 * scale)), max(3, round(4 * scale))))
        style.configure("Record.TButton", font=(font, size(13), "bold"), padding=(round(17 * scale), round(9 * scale)))
        style.configure("Danger.TButton", font=(font, size(13), "bold"), padding=(round(14 * scale), round(9 * scale)))
        style.configure("TCheckbutton", font=(font, size(13)), padding=(0, round(4 * scale)))
        for control in ("TCombobox", "TSpinbox", "TEntry"):
            style.configure(control, font=(font, size(14)), padding=round(6 * scale))
        style.configure("Option.TLabel", font=(font, size(16)))
        style.configure("Option.TEntry", font=(font, size(16)), padding=round(7 * scale))
        style.configure("Option.TCombobox", font=(font, size(16)), padding=round(7 * scale))
        style.configure("Option.TSpinbox", font=(font, size(16)), padding=round(7 * scale), arrowsize=max(18, round(22 * scale)))
        for control in ("Property.TCombobox", "Property.TEntry"):
            style.configure(control, font=(font, size(16)), padding=round(7 * scale))
        for widget in self._scaled_text_widgets:
            try:
                widget.configure(font=(font, size(18)))
            except tk.TclError:
                pass
        for widget in getattr(self, "repeat_mode_buttons", ()):
            widget.configure(font=(font, size(14)), padx=round(11 * scale), pady=round(7 * scale))
        self._on_inspector_resize()
        style.configure("Treeview", font=(font, size(14)), rowheight=round(52 * scale))
        style.configure("Treeview.Heading", font=(font, size(13), "bold"), padding=(round(10 * scale), round(9 * scale)))
        if hasattr(self, "log_text"):
            self.log_text.configure(font=(font, size(14)))

    def _balance_editor_panes(self):
        if not hasattr(self, "editor_panes"):
            return
        width = self.editor_panes.winfo_width()
        if width > 0:
            self.editor_panes.sashpos(0, max(650, int(width * 0.70)))

    def _queue_log(self, message):
        self.ui_queue.put(("log", message))

    def _queue_hotkey(self, hotkey_id):
        self.ui_queue.put(("hotkey", hotkey_id))

    def _drain_queue(self):
        while True:
            try:
                kind, value = self.ui_queue.get_nowait()
            except queue.Empty:
                break
            if kind == "log":
                self.log(value)
            elif kind == "hotkey":
                self.handle_hotkey(value)
            elif kind == "playback_done":
                self.playing = False
                self.pause_playback.clear()
                self._set_playback_editing_locked(False)
                self.play_button.config(text="▶  播放   F9")
                self.status_var.set(value)
                self.progress_var.set(value)
            elif kind == "progress":
                self.progress_var.set(value)
                prefix = "已暂停" if self.pause_playback.is_set() else "回放中"
                self.status_var.set(f"{prefix} · {value}")
            elif kind == "release_warning":
                messagebox.showwarning(APP_NAME, value, parent=self.root)
            if kind != "progress":
                self.update_count()
        self.root.after(40, self._drain_queue)

    def log(self, message):
        stamp = datetime.now().strftime("%H:%M:%S")
        self.log_text.configure(state="normal")
        self.log_text.insert("end", f"[{stamp}] {message}\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def handle_hotkey(self, hotkey_id):
        if hotkey_id == HOTKEY_TOGGLE_RECORD:
            self.toggle_recording()
        elif hotkey_id == HOTKEY_PLAY:
            self.play()
        elif hotkey_id == HOTKEY_STOP:
            self.stop_all()

    def toggle_recording(self):
        if self.recording:
            self.stop_recording()
        else:
            self.start_recording()

    def _stop_recording_hooks(self):
        """Stop the shared low-level input hook thread and clear it."""
        hook = getattr(self, "recording_hook", None)
        if hook is not None:
            hook.stop()
            self.recording_hook = None

    def start_recording(self):
        if self.playing:
            messagebox.showwarning(APP_NAME, "回放时不能开始录制。")
            return
        if self.events and not messagebox.askyesno(APP_NAME, "当前已有事件，开始新录制会清空它们。继续吗？"):
            return
        if self.events:
            self._push_undo()
        with self.events_lock:
            self.events = []
        self.selected_index = None
        self.selected_indices = []
        self.current_file = None
        self.dirty = True
        self.file_var.set("未保存")
        self.start_time = time.perf_counter()
        self.recording_start_tick = int(kernel32.GetTickCount64())
        self.recording_screen = virtual_screen_bounds()
        self.last_move_time = 0.0
        self.last_move_x = None
        self.last_move_y = None
        self.record_moves_enabled = self.record_moves_var.get()
        self.recording = True
        self.recording_hook = RecordingHookThread(
            self._keyboard_event,
            self._mouse_event,
            self._queue_log,
        )
        if not self.recording_hook.start():
            self._stop_recording_hooks()
            self.recording = False
            self.record_button.config(text="开始录制  F8")
            self.status_var.set("录制失败")
            self.log("录制启动失败，未捕获输入。")
            return
        self.record_button.config(text="停止录制  F8")
        self.status_var.set("录制中")
        self.log("开始录制。")

    def stop_recording(self):
        if not self.recording:
            return
        # Keep capture enabled while hooks leave their message loops so the final
        # key-up or click is not lost during shutdown.
        self._stop_recording_hooks()
        self.recording = False
        with self.events_lock:
            # Source timestamps are the primary order. Python's stable sort
            # preserves the shared hook thread's callback order within one ms.
            self.events.sort(key=lambda event: float(event.get("t", 0.0)))
        self.record_button.config(text="开始录制  F8")
        self.status_var.set("录制完成")
        self.log("录制已停止。")
        self.update_count()

    def _hook_event_time(self, source_tick):
        return hook_elapsed_seconds(
            source_tick,
            self.recording_start_tick,
            int(kernel32.GetTickCount64()),
        )

    def _record(self, event, event_time=None):
        if not self.recording:
            return
        if event_time is None:
            event_time = time.perf_counter() - self.start_time
        event["t"] = max(0.0, float(event_time))
        with self.events_lock:
            self.events.append(event)

    def _keyboard_event(self, w_param, l_param):
        data = ctypes.cast(l_param, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
        if data.flags & LLKHF_INJECTED:
            return
        vk = int(data.vkCode)
        if vk in IGNORED_HOTKEYS:
            return
        event_time = self._hook_event_time(data.time)
        self._record(
            {
                "kind": "key",
                "vk": vk,
                "scan": int(data.scanCode),
                "action": "up" if data.flags & LLKHF_UP else "down",
                "extended": bool(data.flags & LLKHF_EXTENDED),
            },
            event_time,
        )

    def _mouse_event(self, w_param, l_param):
        data = ctypes.cast(l_param, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
        if data.flags & LLMHF_INJECTED:
            return
        message = int(w_param)
        event_time = self._hook_event_time(data.time)
        if message == WM_MOUSEMOVE:
            if not self.record_moves_enabled:
                return
            if self.last_move_x is not None:
                distance = abs(int(data.pt.x) - self.last_move_x) + abs(int(data.pt.y) - self.last_move_y)
                if event_time - self.last_move_time < 0.008 and distance < 2:
                    return
            self.last_move_time = event_time
            self.last_move_x, self.last_move_y = int(data.pt.x), int(data.pt.y)
        self._record(
            {
                "kind": "mouse",
                "message": message,
                "x": int(data.pt.x),
                "y": int(data.pt.y),
                "data": int(data.mouseData),
            },
            event_time,
        )

    def update_count(self):
        with self.events_lock:
            events = list(self.events)
        count = len(events)
        total_time = max((event_end_time(event) for event in events), default=0.0)
        self.count_var.set(f"{count} 个 / {total_time:.2f} 秒")
        if hasattr(self, "tree"):
            self._refresh_tree()

    def _event_snapshot(self):
        with self.events_lock:
            events = [dict(event) for event in self.events]
        return (
            events,
            self.selected_index,
            list(self.selected_indices),
            self.current_file,
            self.loaded_screen,
            self._property_snapshot(),
        )

    def _property_snapshot(self):
        """Capture the inspector state so undo also restores an in-progress edit."""
        property_vars = getattr(self, "property_vars", {})
        values = {}
        for name, variable in property_vars.items():
            try:
                values[name] = variable.get()
            except (AttributeError, tk.TclError):
                values[name] = ""
        extended = False
        advanced = False
        try:
            extended = bool(self.property_extended.get())
        except (AttributeError, tk.TclError):
            pass
        try:
            advanced = bool(self.advanced_key_var.get())
        except (AttributeError, tk.TclError):
            pass
        kind = None
        if self.selected_index is not None:
            try:
                with self.events_lock:
                    kind = self.events[self.selected_index].get("kind")
            except (IndexError, TypeError):
                pass
        return {"values": values, "extended": extended, "advanced": advanced, "kind": kind}

    def _restore_property_snapshot(self, snapshot):
        if not snapshot or not hasattr(self, "property_vars"):
            return
        values = snapshot.get("values", {})
        syncing = getattr(self, "_restoring_property_snapshot", False)
        self._restoring_property_snapshot = True
        try:
            for name, variable in self.property_vars.items():
                variable.set(str(values.get(name, "")))
            if hasattr(self, "property_extended"):
                self.property_extended.set(bool(snapshot.get("extended", False)))
            if hasattr(self, "advanced_key_var"):
                self.advanced_key_var.set(bool(snapshot.get("advanced", False)))
        finally:
            self._restoring_property_snapshot = syncing
        if hasattr(self, "_set_action_type_options"):
            category = self.property_vars.get("event_type")
            action_type = self.property_vars.get("action_type")
            if category is not None:
                self._set_action_type_options(category.get(), action_type.get() if action_type is not None else None)
        if hasattr(self, "_set_inspector_enabled") and hasattr(self, "property_rows") and hasattr(self, "property_entries"):
            kind = snapshot.get("kind")
            self._set_inspector_enabled(kind is not None, kind)

    def _push_undo(self, snapshot=None):
        if self.recording or self.playing:
            return
        if not hasattr(self, "redo_stack"):
            self.redo_stack = []
        self.undo_stack.append(snapshot if snapshot is not None else self._event_snapshot())
        self.redo_stack.clear()
        if len(self.undo_stack) > UNDO_LIMIT:
            del self.undo_stack[:-UNDO_LIMIT]

    def _undo_hotkey(self, _event=None):
        if self.recording or self.playing:
            return None
        self.undo()
        return "break"

    def _redo_hotkey(self, _event=None):
        if self.recording or self.playing:
            return None
        self.redo()
        return "break"

    def _restore_editor_snapshot(self, snapshot, status_text, log_text):
        events, selected_index, selected_indices, current_file, loaded_screen, property_snapshot = snapshot
        with self.events_lock:
            self.events = [dict(event) for event in events]
        self.selected_index = selected_index
        self.selected_indices = list(selected_indices)
        self.current_file = current_file
        self.loaded_screen = loaded_screen
        self._pending_key_undo = None
        self.dirty = True
        self.file_var.set(status_text)
        self.status_var.set(status_text.split(" · ")[-1])
        self._refresh_tree()
        self.update_count()
        if hasattr(self, "tree") and hasattr(self, "_on_tree_select"):
            self._on_tree_select()
        self._restore_property_snapshot(property_snapshot)
        self.log(log_text)

    def undo(self):
        if self.recording or self.playing:
            return
        self._stop_key_capture()
        self._pending_key_undo = None
        if not self.undo_stack:
            self.status_var.set("没有可撤销的操作")
            return
        if not hasattr(self, "redo_stack"):
            self.redo_stack = []
        self.redo_stack.append(self._event_snapshot())
        if len(self.redo_stack) > UNDO_LIMIT:
            del self.redo_stack[:-UNDO_LIMIT]
        snapshot = self.undo_stack.pop()
        self._restore_editor_snapshot(snapshot, "未保存 · 已撤销", "已撤销上一步编辑。")

    def redo(self):
        if self.recording or self.playing:
            return
        self._stop_key_capture()
        self._pending_key_undo = None
        if not getattr(self, "redo_stack", None):
            self.status_var.set("没有可重做的操作")
            return
        current_snapshot = self._event_snapshot()
        snapshot = self.redo_stack.pop()
        self.undo_stack.append(current_snapshot)
        if len(self.undo_stack) > UNDO_LIMIT:
            del self.undo_stack[:-UNDO_LIMIT]
        self._restore_editor_snapshot(snapshot, "未保存 · 已重做", "已重做上一步编辑。")

    def _arm_key_capture(self, _event=None):
        """Clear the visible key name and wait for the next physical key press."""
        self._pending_key_undo = self._event_snapshot()
        self.property_vars["key"].set("")
        self.property_vars["vk"].set("")
        self.property_vars["scan"].set("")
        self._key_capture_armed = True
        self.property_entries["key"].focus_set()
        self._stop_key_capture()
        capture = KeyCaptureHook(self.root, self._finish_property_key_capture, self._queue_log)
        capture.start()
        if capture.hook.hook:
            self.key_capture_hook = capture
        return "break"

    def _stop_key_capture(self):
        capture = getattr(self, "key_capture_hook", None)
        if capture:
            capture.stop()
        self.key_capture_hook = None

    def _finish_property_key_capture(self, vk, scan, extended):
        pending_undo = getattr(self, "_pending_key_undo", None)
        if pending_undo is not None:
            self._push_undo(pending_undo)
            self._pending_key_undo = None
        self.property_vars["vk"].set(str(vk))
        self.property_vars["scan"].set(str(scan) if scan else "0")
        self.property_vars["key"].set(key_display_name(vk))
        self.property_extended.set(bool(extended))
        self._key_capture_armed = False
        self.key_capture_hook = None

    def _capture_property_key(self, event):
        if not self._key_capture_armed:
            return "break"
        if self.key_capture_hook:
            return "break"
        vk = tk_event_vk(event)
        if not 0 <= vk <= 255:
            return "break"
        keysym = getattr(event, "keysym", "")
        scan = int(user32.MapVirtualKeyW(vk, MAPVK_VK_TO_VSC))
        if getattr(self, "_pending_key_undo", None) is None:
            self._pending_key_undo = self._event_snapshot()
        self._push_undo(self._pending_key_undo)
        self._pending_key_undo = None
        self.property_vars["vk"].set(str(vk))
        self.property_vars["scan"].set(str(scan) if scan else "0")
        self.property_vars["key"].set(key_display_name(vk, keysym))
        self.property_extended.set(keysym in {"Control_R", "Alt_R", "Win_R", "Super_R"})
        self._key_capture_armed = False
        return "break"

    def _refresh_advanced_key_fields(self, *_args):
        key_enabled = bool(getattr(self, "_inspector_key_enabled", False))
        if key_enabled:
            toggle_row = self.property_rows["key"] + 1
            self.advanced_key_toggle.grid(row=toggle_row, column=1, sticky="w", pady=(0, 3))
        else:
            self.advanced_key_toggle.grid_remove()
        show_codes = key_enabled and self.advanced_key_var.get()
        for name in ("vk", "scan"):
            label = self.property_labels[name]
            entry = self.property_entries[name]
            if show_codes:
                row = self.property_rows[name]
                label.grid(row=row, column=0, sticky="w", pady=3)
                entry.grid(row=row, column=1, sticky="ew", pady=3)
                entry.configure(state="normal")
            else:
                label.grid_remove()
                entry.grid_remove()
                entry.configure(state="disabled")

    def _event_label(self, event):
        if event.get("kind") == "key":
            vk = int(event.get("vk", 0))
            scan = int(event.get("scan", 0))
            name = key_display_name(vk)
            return f"{name} (VK {vk} / scan {scan}) {event.get('action')}"
        message = int(event.get("message", WM_MOUSEMOVE))
        action = mouse_action_label(message, event.get("data", 0))
        amount = f" · {wheel_amount(event.get('data', 0))}" if message in (WM_MOUSEWHEEL, WM_MOUSEHWHEEL) else ""
        return f"{action}{amount}  ({event.get('x', 0)}, {event.get('y', 0)})"

    def _refresh_tree(self):
        if not hasattr(self, "tree"):
            return
        selected = list(self.selected_indices)
        if not selected and self.selected_index is not None:
            selected = [self.selected_index]
        self.tree.delete(*self.tree.get_children())
        with self.events_lock:
            events = list(self.events)
        if events:
            self.empty_hint.place_forget()
        else:
            self.empty_hint.place(relx=0.5, rely=0.5, anchor="center")
        for index, event in enumerate(events):
            kind_names = {"key": "键盘", "mouse": "鼠标"}
            kind = kind_names.get(event.get("kind"), "动作")
            self.tree.insert(
                "",
                "end",
                iid=str(index),
                values=(index + 1, f"{float(event.get('t', 0)):.4f}s", kind, self._event_label(event)),
                tags=("even" if index % 2 == 0 else "odd",),
            )
        valid_selected = [index for index in selected if 0 <= index < len(events)]
        if valid_selected:
            self.tree.selection_set(*[str(index) for index in valid_selected])
            self.tree.see(str(valid_selected[0]))

    def _on_tree_select(self, _event=None):
        self._stop_key_capture()
        self._pending_key_undo = None
        selection = self.tree.selection()
        self.selected_indices = sorted(int(item) for item in selection)
        self.selected_index = self.selected_indices[0] if self.selected_indices else None
        if self.selected_index is None:
            self.inspector_hint.configure(text="先在左侧选择一个事件")
            self.inspector_help.configure(text="普通编辑只需调整时间、坐标或动作。点击“删除鼠标移动”后，确认即可移除整段宏中的移动事件。")
            self._set_inspector_enabled(False)
            return
        if len(self.selected_indices) > 1:
            self.inspector_hint.configure(text=f"已选择 {len(self.selected_indices)} 个事件 · 属性显示第一个事件")
        with self.events_lock:
            if self.selected_index >= len(self.events):
                return
            event = dict(self.events[self.selected_index])
        self._key_capture_armed = False
        self.advanced_key_var.set(False)
        for name, value in self.property_vars.items():
            self.property_vars[name].set(str(event.get(name, "")))
        self.property_vars["time"].set(str(event.get("t", "")))
        category = event_category_for_event(event)
        self.property_vars["event_type"].set(category)
        self._set_action_type_options(category, action_type_for_event(event))
        if event.get("kind") == "key":
            self.property_vars["key"].set(key_display_name(event.get("vk", 0)))
            self.property_extended.set(bool(event.get("extended", False)))
            self.inspector_hint.configure(text="键盘事件 · 修改按键和动作")
            self.inspector_help.configure(text="需要手工填写按键编码时，请展开“高级”。")
        elif event.get("kind") == "mouse":
            message_code = int(event.get("message", WM_MOUSEMOVE))
            self.property_vars["delta"].set(str(wheel_amount(event.get("data", 0))))
            self.inspector_hint.configure(text="鼠标事件 · 修改屏幕坐标和动作")
            if message_code in (WM_MOUSEWHEEL, WM_MOUSEHWHEEL):
                self.inspector_help.configure(text="当前采用虚拟桌面绝对坐标")
            elif message_code in (WM_XBUTTONDOWN, WM_XBUTTONUP):
                self.inspector_help.configure(text="当前采用虚拟桌面绝对坐标")
            else:
                self.inspector_help.configure(text="当前采用虚拟桌面绝对坐标")
        if len(self.selected_indices) > 1:
            self.inspector_hint.configure(text=f"已选择 {len(self.selected_indices)} 个事件 · 属性显示第一个事件")
        self._set_inspector_enabled(True, event.get("kind"))

    def _set_action_type_options(self, category, selected=None):
        category = str(category or "").strip()
        values = ACTION_TYPE_VALUES.get(category, ())
        widget = self.property_entries.get("action_type")
        if widget is not None:
            widget.configure(values=values)
        if not values:
            if selected is not None:
                self.property_vars["action_type"].set(str(selected))
            return self.property_vars["action_type"].get()
        selected = str(selected or "").strip()
        known_action = any(selected in options for options in ACTION_TYPE_VALUES.values())
        action_type = selected if selected in values or (selected and not known_action) else values[0]
        self.property_vars["action_type"].set(action_type)
        return action_type

    def _on_event_type_changed(self, _event=None):
        if self.selected_index is None:
            return
        category = self.property_vars["event_type"].get().strip()
        if category not in EVENT_CATEGORY_VALUES:
            if _event is not None:
                messagebox.showerror(APP_NAME, "错误的事件类型")
            self._set_action_type_options(category, self.property_vars["action_type"].get())
            self._set_inspector_enabled(False)
            for name in ("event_type", "action_type"):
                row = self.property_rows[name]
                self.property_labels[name].grid(row=row, column=0, sticky="w", pady=3)
                self.property_entries[name].grid(row=row, column=1, sticky="ew", pady=3)
                self.property_entries[name].configure(state="normal")
            return "break" if _event is not None else None
        if category == "键盘":
            self._set_action_type_options(category, self.property_vars["action_type"].get() or "按下")
            self.inspector_help.configure(text="键盘动作需要一个按键；点击“按键”输入框后直接按目标键。需要精确控制时可展开高级选项。")
            self._set_inspector_enabled(True, "key")
        else:
            action_type = self._set_action_type_options(category, self.property_vars["action_type"].get() or "移动")
            if not self.property_vars["x"].get():
                self.property_vars["x"].set("0")
            if not self.property_vars["y"].get():
                self.property_vars["y"].set("0")
            if ACTION_DEFINITIONS.get(action_type, {}).get("uses_delta") and not self.property_vars["delta"].get():
                self.property_vars["delta"].set("120")
            self._on_action_type_changed()

    def _on_action_type_changed(self, _event=None):
        if self.selected_index is None:
            return
        category = self.property_vars["event_type"].get().strip()
        action_name = self.property_vars["action_type"].get().strip()
        if category in EVENT_CATEGORY_VALUES and action_name not in ACTION_TYPE_VALUES[category]:
            if _event is not None:
                messagebox.showerror(APP_NAME, "错误的动作")
            self._set_inspector_enabled(False)
            for name in ("event_type", "action_type"):
                row = self.property_rows[name]
                self.property_labels[name].grid(row=row, column=0, sticky="w", pady=3)
                self.property_entries[name].grid(row=row, column=1, sticky="ew", pady=3)
                self.property_entries[name].configure(state="normal")
            return "break" if _event is not None else None
        definition = ACTION_DEFINITIONS.get(action_name, {})
        if category == "鼠标" and definition.get("uses_delta"):
            try:
                current_amount = int(self.property_vars["delta"].get() or 0)
            except ValueError:
                current_amount = 0
            if current_amount <= 0:
                self.property_vars["delta"].set("120")
            self.inspector_help.configure(text="当前采用虚拟桌面绝对坐标")
        elif category == "鼠标" and definition.get("message") in (WM_XBUTTONDOWN, WM_XBUTTONUP):
            self.inspector_help.configure(text="当前采用虚拟桌面绝对坐标")
        elif category == "鼠标":
            self.inspector_help.configure(text="当前采用虚拟桌面绝对坐标")
        self._set_inspector_enabled(True, {"键盘": "key", "鼠标": "mouse"}.get(category))

    def _set_inspector_enabled(self, enabled, kind=None):
        editable_fields = {
            "key": {"event_type", "time", "action_type", "key"},
            "mouse": {"event_type", "time", "action_type", "x", "y"},
        }.get(kind, set()) if enabled else set()
        if enabled and kind == "mouse" and not self.playing:
            action = self.property_vars.get("action_type")
            action_name = action.get() if action is not None else ""
            if ACTION_DEFINITIONS.get(action_name, {}).get("uses_delta"):
                editable_fields.add("delta")
        if self.playing:
            editable_fields = set()
        self._inspector_key_enabled = "key" in editable_fields
        for name, entry in getattr(self, "property_entries", {}).items():
            visible = name in editable_fields
            if visible:
                row = self.property_rows[name]
                self.property_labels[name].grid(row=row, column=0, sticky="w", pady=3)
                entry.grid(row=row, column=1, sticky="ew", pady=3)
            else:
                self.property_labels[name].grid_remove()
                entry.grid_remove()
            if name == "key" and visible:
                entry.configure(state="readonly")
            else:
                entry.configure(state="normal" if visible else "disabled")
        self._refresh_advanced_key_fields()

    def _set_playback_editing_locked(self, locked):
        """Disable inspector editing while the playback worker owns the timeline."""
        state = ["disabled"] if locked else ["!disabled"]
        if locked:
            self._stop_key_capture()
        for name in ("apply_button", "insert_event_button", "compose_button"):
            button = getattr(self, name, None)
            if button is not None:
                button.state(state)
        for button in getattr(self, "timeline_edit_buttons", ()):
            button.state(state)
        for entry in getattr(self, "property_entries", {}).values():
            entry.state(state)
        toggle = getattr(self, "advanced_key_toggle", None)
        if toggle is not None:
            toggle.state(state)
        if not locked and hasattr(self, "tree"):
            self._on_tree_select()

    def apply_selected(self):
        if self.recording or self.playing:
            messagebox.showwarning(APP_NAME, "录制或回放中不能修改事件。")
            return
        if self.selected_index is None:
            return
        undo_snapshot = self._event_snapshot()
        with self.events_lock:
            if self.selected_index >= len(self.events):
                return
            event = dict(self.events[self.selected_index])
            try:
                category, action_type = validate_editor_choice(
                    self.property_vars["event_type"].get(),
                    self.property_vars["action_type"].get(),
                )
                self.property_vars["event_type"].set(category)
                self.property_vars["action_type"].set(action_type)
                event_time = _finite_float(self.property_vars["time"].get(), "时间")
                if event_time < 0:
                    raise ValueError("时间不能为负数")
                if self.selected_index > 0 and event_time < float(self.events[self.selected_index - 1].get("t", 0.0)):
                    raise ValueError("时间不能早于前一个事件")
                if category == "键盘":
                    vk_text = self.property_vars["vk"].get().strip()
                    scan_text = self.property_vars["scan"].get().strip()
                    if not vk_text and not scan_text:
                        raise ValueError("请先点击“按键”并按一次键，或在高级中输入按键代码/扫描码。")
                    vk = int(vk_text) if vk_text else int(user32.MapVirtualKeyW(int(scan_text), MAPVK_VSC_TO_VK_EX))
                    scan = int(scan_text) if scan_text else int(user32.MapVirtualKeyW(vk, MAPVK_VK_TO_VSC))
                    created = build_action_events("键盘按键", event_time, vk=vk, scan=scan, key_action=action_type, extended=self.property_extended.get())
                else:
                    created = build_action_events(action_type, event_time, x=self.property_vars["x"].get(), y=self.property_vars["y"].get(), delta=self.property_vars["delta"].get())
            except ValueError as exc:
                if str(exc) in {"错误的事件类型", "错误的动作"}:
                    messagebox.showerror(APP_NAME, str(exc))
                else:
                    messagebox.showerror(APP_NAME, f"属性值格式不正确：{exc}")
                return
            if len(created) == 1 and created[0] == self.events[self.selected_index]:
                return
            old_end = event_end_time(event)
            new_end = event_end_time(created[-1])
            shift = new_end - old_end
            for following in self.events[self.selected_index + 1:]:
                following["t"] = float(following.get("t", 0.0)) + shift
            self.events[self.selected_index:self.selected_index + 1] = created
            self.events = validate_events(self.events)
        self._push_undo(undo_snapshot)
        self.dirty = True
        self.status_var.set("已修改")
        self.file_var.set("未保存 · 有修改")
        self.update_count()
        self.log("已应用事件修改。")

    def delete_all_mouse_moves(self):
        if self.recording or self.playing:
            messagebox.showwarning(APP_NAME, "录制或回放中不能删除鼠标移动。")
            return
        with self.events_lock:
            original = list(self.events)
        move_count = sum(
            1 for event in original if _is_mouse_move(event)
        )
        if move_count < 1:
            messagebox.showinfo(APP_NAME, "当前宏中没有鼠标移动事件。")
            return
        if not messagebox.askyesno(
            APP_NAME,
            "将删除整段宏中的全部鼠标移动事件。拖动、悬停和绘图等依赖连续移动的操作可能失效。\n\n是否继续？",
        ):
            return
        filtered = delete_mouse_moves(original)
        removed = len(original) - len(filtered)
        self._push_undo()
        with self.events_lock:
            self.events = filtered
        self.selected_indices = []
        self.selected_index = None
        self.dirty = True
        self.file_var.set("未保存 · 有修改")
        self.status_var.set("已删除鼠标移动")
        self._refresh_tree()
        self.update_count()
        self.log(f"已删除整段宏中的鼠标移动，共移除 {removed} 个步骤。")

    def duplicate_selected(self):
        if self.selected_index is None or self.recording or self.playing:
            return
        self._push_undo()
        with self.events_lock:
            self.events.insert(self.selected_index + 1, dict(self.events[self.selected_index]))
        self.selected_index += 1
        self.selected_indices = [self.selected_index]
        self.dirty = True
        self.file_var.set("未保存 · 有修改")
        self.status_var.set("已复制步骤")
        self._refresh_tree()
        self.update_count()

    def delete_selected(self):
        if self.selected_index is None or self.recording or self.playing:
            return
        self._push_undo()
        with self.events_lock:
            if self.selected_index < len(self.events):
                self.events.pop(self.selected_index)
        self.selected_index = min(self.selected_index, len(self.events) - 1) if self.events else None
        self.selected_indices = [self.selected_index] if self.selected_index is not None else []
        self.dirty = True
        self.file_var.set("未保存 · 有修改")
        self.status_var.set("已删除步骤")
        self._refresh_tree()
        self.update_count()

    def move_selected_up(self):
        self._move_selected(-1)

    def move_selected_down(self):
        self._move_selected(1)

    def _move_selected(self, direction):
        if self.selected_index is None or self.recording or self.playing:
            return
        self._move_event_to(self.selected_index, self.selected_index + direction)

    def _move_event_to(self, source, target):
        if self.recording or self.playing:
            return
        with self.events_lock:
            if source == target or not (0 <= source < len(self.events)) or not (0 <= target < len(self.events)):
                return
        self._push_undo()
        with self.events_lock:
            self.events = move_event_to(self.events, source, target)
        self.selected_index = target
        self.selected_indices = [target]
        self.dirty = True
        self.file_var.set("未保存 · 有修改")
        self.status_var.set("已移动步骤")
        self._refresh_tree()
        self.update_count()

    def insert_event(self):
        if self.recording or self.playing:
            messagebox.showwarning(APP_NAME, "录制或回放中不能插入事件。")
            return
        dialog = tk.Toplevel(self.root)
        dialog.title("插入事件")
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.resizable(False, False)
        body = ttk.Frame(dialog, padding=18)
        body.pack(fill="both", expand=True)
        dialog_scale = float(self._ui_scale or 1.0)
        dialog_title_font = (self._ui_font, max(20, round(22 * dialog_scale)), "bold")
        dialog_text_font = (self._ui_font, max(16, round(17 * dialog_scale)))
        dialog_hint_font = (self._ui_font, max(15, round(15 * dialog_scale)))
        intro = ttk.Frame(body)
        intro.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 10))
        ttk.Label(intro, text="插入一个事件", style="Title.TLabel", font=dialog_title_font).pack(anchor="w")
        ttk.Label(intro, text="它会插入到当前选中步骤之后；时间可以直接填写", style="Subtle.TLabel", font=dialog_hint_font).pack(anchor="w", pady=(3, 8))
        ttk.Label(intro, text="键盘动作默认直接按键选择；需要精确控制时，可展开高级选项输入按键代码或扫描码。", style="Subtle.TLabel", font=dialog_hint_font, wraplength=520).pack(anchor="w")
        with self.events_lock:
            default_base_index = self.selected_index if self.selected_index is not None else len(self.events) - 1
            default_time = event_end_time(self.events[default_base_index]) if default_base_index >= 0 else 0.0
        event_type_var = tk.StringVar(value="鼠标")
        action_type_var = tk.StringVar(value="移动")
        time_var = tk.StringVar(value=f"{default_time:.4f}")
        key_display_var = tk.StringVar()
        key_vk_var = tk.StringVar()
        key_scan_var = tk.StringVar()
        key_extended = tk.BooleanVar(value=False)
        advanced_key_var = tk.BooleanVar(value=False)
        x_var = tk.StringVar()
        y_var = tk.StringVar()
        delta_var = tk.StringVar(value="120")
        ttk.Label(body, text="时间（秒）", style="Subtle.TLabel", font=dialog_text_font).grid(row=1, column=0, sticky="w", pady=5)
        time_entry = ttk.Entry(body, textvariable=time_var, width=26, style="Property.TEntry", font=dialog_text_font)
        time_entry.grid(row=1, column=1, sticky="ew", pady=5)
        self._scaled_text_widgets.append(time_entry)
        ttk.Label(body, text="事件类型", style="Subtle.TLabel", font=dialog_text_font).grid(row=2, column=0, sticky="w", pady=5)
        event_type_box = ttk.Combobox(body, textvariable=event_type_var, state="normal", width=24, style="Property.TCombobox", font=(self._ui_font, 18), values=EVENT_CATEGORY_VALUES)
        event_type_box.grid(row=2, column=1, sticky="ew", pady=5)
        self._scaled_text_widgets.append(event_type_box)
        ttk.Label(body, text="动作", style="Subtle.TLabel", font=dialog_text_font).grid(row=3, column=0, sticky="w", pady=5)
        action_type_box = ttk.Combobox(body, textvariable=action_type_var, state="normal", width=24, style="Property.TCombobox", font=(self._ui_font, 18), values=ACTION_TYPE_VALUES["鼠标"])
        action_type_box.grid(row=3, column=1, sticky="ew", pady=5)
        self._scaled_text_widgets.append(action_type_box)
        fields = (("x", "X 坐标", x_var), ("y", "Y 坐标", y_var), ("delta", "滚轮量", delta_var))
        entries = {}
        field_labels = {}
        for row, (name, label, variable) in enumerate(fields, start=4):
            label_widget = ttk.Label(body, text=label, style="Subtle.TLabel", font=dialog_text_font)
            label_widget.grid(row=row, column=0, sticky="w", pady=5)
            entry = ttk.Entry(body, textvariable=variable, width=26, style="Property.TEntry", font=dialog_text_font)
            entry.grid(row=row, column=1, sticky="ew", pady=5)
            field_labels[name] = label_widget
            entries[name] = entry
            self._scaled_text_widgets.append(entry)
        body.columnconfigure(1, weight=1)

        key_frame_row = 4 + len(fields)
        key_frame = ttk.Frame(body)
        key_frame.grid(row=key_frame_row, column=0, columnspan=2, sticky="ew", pady=(2, 5))
        key_frame.columnconfigure(1, weight=1)
        ttk.Label(key_frame, text="按键", style="Subtle.TLabel", font=dialog_text_font).grid(row=0, column=0, sticky="w", pady=4)
        key_entry = ttk.Entry(key_frame, textvariable=key_display_var, state="readonly", width=35, style="Property.TEntry", font=dialog_text_font)
        key_entry.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(2, 4))
        self._scaled_text_widgets.append(key_entry)
        ttk.Label(key_frame, text="点击按键栏后直接按一次目标键。", style="Subtle.TLabel", font=dialog_hint_font).grid(row=2, column=0, columnspan=2, sticky="w")

        advanced_frame = ttk.Frame(key_frame)
        advanced_frame.columnconfigure(1, weight=1)
        ttk.Checkbutton(key_frame, text="高级", variable=advanced_key_var, style="TCheckbutton").grid(row=3, column=0, columnspan=2, sticky="w", pady=(6, 2))
        ttk.Label(advanced_frame, text="按键代码", style="Subtle.TLabel", font=dialog_text_font).grid(row=0, column=0, sticky="w", pady=3)
        key_vk_entry = ttk.Entry(advanced_frame, textvariable=key_vk_var, width=16, style="Property.TEntry", font=dialog_text_font)
        key_vk_entry.grid(row=0, column=1, sticky="w", pady=3)
        ttk.Label(advanced_frame, text="扫描码", style="Subtle.TLabel", font=dialog_text_font).grid(row=1, column=0, sticky="w", pady=3)
        key_scan_entry = ttk.Entry(advanced_frame, textvariable=key_scan_var, width=16, style="Property.TEntry", font=dialog_text_font)
        key_scan_entry.grid(row=1, column=1, sticky="w", pady=3)
        self._scaled_text_widgets.extend((key_vk_entry, key_scan_entry))

        capture_armed = [False]
        capture_hook = [None]

        def stop_capture_hook():
            if capture_hook[0]:
                capture_hook[0].stop()
                capture_hook[0] = None

        def finish_capture(vk, scan, extended):
            stop_capture_hook()
            key_vk_var.set(str(vk))
            key_scan_var.set(str(scan) if scan else "0")
            key_extended.set(bool(extended))
            key_display_var.set(key_display_name(vk))
            capture_armed[0] = False

        def arm_key_capture(_event=None):
            key_display_var.set("")
            key_vk_var.set("")
            key_scan_var.set("")
            capture_armed[0] = True
            key_entry.focus_set()
            stop_capture_hook()
            capture = KeyCaptureHook(dialog, finish_capture, self._queue_log)
            capture.start()
            if capture.hook.hook:
                capture_hook[0] = capture
            return "break"

        def capture_key(event):
            if not capture_armed[0]:
                return "break"
            if capture_hook[0]:
                return "break"
            vk = tk_event_vk(event)
            if not 0 <= vk <= 255:
                return "break"
            scan = int(user32.MapVirtualKeyW(vk, MAPVK_VK_TO_VSC))
            key_vk_var.set(str(vk))
            key_scan_var.set(str(scan) if scan else "0")
            keysym = getattr(event, "keysym", "")
            key_extended.set(keysym in {"Control_R", "Alt_R", "Win_R", "Super_R"})
            key_display_var.set(key_display_name(vk, keysym))
            capture_armed[0] = False
            return "break"

        key_entry.bind("<Button-1>", arm_key_capture)
        key_entry.bind("<KeyPress>", capture_key)

        def refresh_key_fields(*_args):
            if advanced_key_var.get():
                advanced_frame.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(2, 5))
            else:
                advanced_frame.grid_remove()

        advanced_key_var.trace_add("write", refresh_key_fields)
        refresh_key_fields()
        cursor = POINT()
        if user32.GetCursorPos(ctypes.byref(cursor)):
            x_var.set(str(cursor.x))
            y_var.set(str(cursor.y))

        def refresh_fields(*_args):
            category = event_type_var.get().strip()
            action_type = action_type_var.get().strip()
            definition = ACTION_DEFINITIONS.get(action_type, {})
            show = {
                "x": category == "鼠标",
                "y": category == "鼠标",
                "delta": category == "鼠标" and bool(definition.get("uses_delta")),
            }
            for name, entry in entries.items():
                if show[name]:
                    field_labels[name].grid()
                    entry.grid()
                    entry.configure(state="normal")
                else:
                    field_labels[name].grid_remove()
                    entry.grid_remove()
                    entry.configure(state="disabled")
            if category == "键盘":
                key_frame.grid()
                dialog.after_idle(arm_key_capture)
            else:
                key_frame.grid_remove()
                capture_armed[0] = False
                stop_capture_hook()
        def refresh_action_types(*_args):
            category = event_type_var.get().strip()
            values = ACTION_TYPE_VALUES.get(category, ())
            action_type_box.configure(values=values)
            current_action = action_type_var.get().strip()
            known_action = any(current_action in options for options in ACTION_TYPE_VALUES.values())
            if values and current_action not in values and (not current_action or known_action):
                action_type_var.set(values[0])
            refresh_fields()

        def validate_event_type_input(_event=None):
            if event_type_var.get().strip() not in EVENT_CATEGORY_VALUES:
                messagebox.showerror(APP_NAME, "错误的事件类型", parent=dialog)
                return "break"

        def validate_action_type_input(_event=None):
            category = event_type_var.get().strip()
            action = action_type_var.get().strip()
            if category in EVENT_CATEGORY_VALUES and action not in ACTION_TYPE_VALUES[category]:
                messagebox.showerror(APP_NAME, "错误的动作", parent=dialog)
                return "break"

        event_type_var.trace_add("write", refresh_action_types)
        action_type_var.trace_add("write", refresh_fields)
        action_type_box.bind("<<ComboboxSelected>>", refresh_fields)
        action_type_box.bind("<FocusOut>", refresh_fields, add="+")
        action_type_box.bind("<Return>", refresh_fields, add="+")
        event_type_box.bind("<<ComboboxSelected>>", refresh_action_types)
        event_type_box.bind("<FocusOut>", refresh_action_types, add="+")
        event_type_box.bind("<Return>", refresh_action_types, add="+")
        event_type_box.bind("<FocusOut>", validate_event_type_input, add="+")
        event_type_box.bind("<Return>", validate_event_type_input, add="+")
        action_type_box.bind("<FocusOut>", validate_action_type_input, add="+")
        action_type_box.bind("<Return>", validate_action_type_input, add="+")
        refresh_action_types()

        def confirm():
            if self.recording or self.playing:
                messagebox.showwarning(APP_NAME, "录制或回放中不能插入事件。", parent=dialog)
                return
            try:
                category, action_type = validate_editor_choice(event_type_var.get(), action_type_var.get())
                event_type_var.set(category)
                action_type_var.set(action_type)
                undo_snapshot = self._event_snapshot()
                with self.events_lock:
                    base_index = self.selected_index if self.selected_index is not None else len(self.events) - 1
                    insert_at = base_index + 1
                    event_time = _finite_float(time_var.get(), "时间")
                    if event_time < 0:
                        raise ValueError("时间不能为负数")
                    previous_time = float(self.events[insert_at - 1].get("t", 0.0)) if insert_at > 0 else 0.0
                    if event_time < previous_time:
                        raise ValueError("时间不能早于前一个事件")
                    if category == "键盘":
                        if advanced_key_var.get():
                            vk_text = key_vk_var.get().strip()
                            scan_text = key_scan_var.get().strip()
                            if not vk_text and not scan_text:
                                raise ValueError("请输入按键代码或扫描码")
                            vk = int(vk_text) if vk_text else int(user32.MapVirtualKeyW(int(scan_text), MAPVK_VSC_TO_VK_EX))
                            scan = int(scan_text) if scan_text else int(user32.MapVirtualKeyW(vk, MAPVK_VK_TO_VSC))
                        else:
                            if not key_vk_var.get().strip():
                                raise ValueError("请先在输入框中按一次目标键")
                            vk = int(key_vk_var.get())
                            scan = int(key_scan_var.get() or 0)
                        if not 0 <= vk <= 255:
                            raise ValueError("按键代码必须为 0-255")
                        if scan < 0:
                            raise ValueError("扫描码不能为负数")
                        created = build_action_events("键盘按键", event_time, vk=vk, scan=scan, key_action=action_type, extended=key_extended.get())
                    else:
                        created = build_action_events(action_type, event_time, x=x_var.get(), y=y_var.get(), delta=delta_var.get())
                    self._push_undo(undo_snapshot)
                    if insert_at < len(self.events):
                        next_time = float(self.events[insert_at].get("t", 0.0))
                        shift = max(0.0, event_time - next_time)
                        for event in self.events[insert_at:]:
                            event["t"] = float(event.get("t", 0.0)) + shift
                    self.events[insert_at:insert_at] = created
                    self.events = validate_events(self.events)
                self.selected_index = insert_at
                self.selected_indices = [insert_at]
                self.dirty = True
                self.file_var.set("未保存 · 有修改")
                self.status_var.set("已插入事件")
                self._refresh_tree()
                self.update_count()
                self.log(f"已插入事件：{category} / {action_type}。")
                dialog.destroy()
            except (TypeError, ValueError) as exc:
                if str(exc) in {"错误的事件类型", "错误的动作"}:
                    messagebox.showerror(APP_NAME, str(exc), parent=dialog)
                else:
                    messagebox.showerror(APP_NAME, f"事件参数格式不正确：{exc}", parent=dialog)

        buttons = ttk.Frame(body)
        buttons.grid(row=key_frame_row + 1, column=0, columnspan=2, sticky="e", pady=(15, 0))
        ttk.Button(buttons, text="取消", command=lambda: (stop_capture_hook(), dialog.destroy())).pack(side="right", padx=(8, 0))
        ttk.Button(buttons, text="插入", style="Accent.TButton", command=confirm).pack(side="right")
        dialog.bind("<Return>", lambda _event: confirm())
        dialog.bind("<Escape>", lambda _event: (stop_capture_hook(), dialog.destroy()))
        dialog.protocol("WM_DELETE_WINDOW", lambda: (stop_capture_hook(), dialog.destroy()))
        dialog.update_idletasks()
        dialog.geometry(f"+{self.root.winfo_x() + 180}+{self.root.winfo_y() + 120}")

    def open_composer(self):
        if self.recording:
            messagebox.showwarning(APP_NAME, "录制中不能编排宏。")
            return
        if self.playing:
            messagebox.showwarning(APP_NAME, "回放中不能编排宏。")
            return

        dialog = tk.Toplevel(self.root)
        dialog.title("编排宏")
        dialog.transient(self.root)
        dialog.minsize(900, 560)
        dialog.grab_set()
        dialog_scale = self._ui_scale or 1.0
        title_font = (self._ui_font, max(18, round(20 * dialog_scale)), "bold")
        text_font = (self._ui_font, max(14, round(15 * dialog_scale)))

        body = ttk.Frame(dialog, style="Surface.TFrame", padding=(18, 16))
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(3, weight=1)
        ttk.Label(body, text="编排宏", style="Title.TLabel", font=title_font).grid(row=0, column=0, sticky="w")
        ttk.Label(
            body,
            text="按播放顺序加入宏文件，为每一项设置倍速和次数；可按住列表项拖动排序。",
            style="Subtle.TLabel",
            font=text_font,
        ).grid(row=1, column=0, sticky="w", pady=(3, 12))

        items = []
        summary_var = tk.StringVar(value="尚未加入宏文件")
        path_var = tk.StringVar(value="选择一项可查看完整路径")
        speed_var = tk.StringVar(value="1.0")
        repeat_var = tk.StringVar(value="1")
        current_index = [None]
        syncing_fields = [False]

        tools = ttk.Frame(body)
        tools.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        list_frame = ttk.Frame(body)
        list_frame.grid(row=3, column=0, sticky="nsew")
        columns = ("order", "file", "speed", "repeat", "events", "duration")
        tree = ttk.Treeview(list_frame, columns=columns, show="headings", selectmode="browse", height=9)
        headings = {"order": "顺序", "file": "宏文件", "speed": "速度", "repeat": "次数", "events": "事件数", "duration": "单次时长"}
        widths = {"order": 70, "file": 330, "speed": 90, "repeat": 90, "events": 110, "duration": 130}
        for column in columns:
            tree.heading(column, text=headings[column])
            tree.column(
                column,
                width=widths[column],
                anchor="w" if column == "file" else "center",
                stretch=column == "file",
            )
        tree.pack(side="left", fill="both", expand=True)
        scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=tree.yview)
        scrollbar.pack(side="right", fill="y")
        tree.configure(yscrollcommand=scrollbar.set)

        def selected_index():
            selection = tree.selection()
            if not selection:
                return None
            try:
                index = int(selection[0])
            except (TypeError, ValueError):
                return None
            return index if 0 <= index < len(items) else None

        def update_summary():
            total_events = sum(len(item["events"]) * item["repeat"] for item in items)
            total_time = sum(event_end_time(item["events"][-1]) / item["speed"] * item["repeat"] for item in items)
            if items:
                summary_var.set(f"{len(items)} 个编排项 / {total_events} 个事件 / 约 {total_time:.2f} 秒")
            else:
                summary_var.set("尚未加入宏文件")

        def refresh(select=None):
            tree.delete(*tree.get_children())
            for index, item in enumerate(items):
                tree.insert(
                    "",
                    "end",
                    iid=str(index),
                    values=(
                        index + 1,
                        item["path"].name,
                        f"{item['speed']:g}x",
                        item["repeat"],
                        len(item["events"]),
                        f"{event_end_time(item['events'][-1]) / item['speed']:.2f} 秒",
                    ),
                    tags=("even" if index % 2 == 0 else "odd",),
                )
            update_summary()
            if select is not None and 0 <= select < len(items):
                tree.selection_set(str(select))
                tree.focus(str(select))
                tree.see(str(select))

        def on_select(_event=None):
            index = selected_index()
            current_index[0] = index
            syncing_fields[0] = True
            try:
                if index is None:
                    speed_var.set("1.0")
                    repeat_var.set("1")
                    path_var.set("选择一项可查看完整路径")
                else:
                    speed_var.set(f"{items[index]['speed']:g}")
                    repeat_var.set(str(items[index]["repeat"]))
                    path_var.set(str(items[index]["path"]))
            finally:
                syncing_fields[0] = False

        def on_speed_changed(*_args):
            if syncing_fields[0]:
                return
            index = current_index[0]
            if index is None:
                return
            try:
                value = parse_composition_speed(speed_var.get())
            except ValueError:
                return
            items[index]["speed"] = value
            if tree.exists(str(index)):
                tree.set(str(index), "speed", f"{value:g}x")
            update_summary()

        def on_repeat_changed(*_args):
            if syncing_fields[0]:
                return
            index = current_index[0]
            value = repeat_var.get().strip()
            if index is None or not value.isdigit() or int(value) < 1:
                return
            items[index]["repeat"] = int(value)
            if tree.exists(str(index)):
                tree.set(str(index), "repeat", value)
            update_summary()

        def add_files():
            paths = filedialog.askopenfilenames(
                parent=dialog,
                title="加入宏文件",
                initialdir=Path(__file__).with_name("macros"),
                filetypes=[("Macro JSON", "*.json"), ("All files", "*.*")],
            )
            if not paths:
                return
            errors = []
            first_added = len(items)
            for raw_path in paths:
                path = Path(raw_path)
                try:
                    payload, events = load_macro_payload(path)
                    if not events:
                        raise ValueError("宏中没有事件")
                except Exception as exc:
                    errors.append(f"{path.name}：{exc}")
                    continue
                items.append({"path": path, "payload": payload, "events": events, "speed": 1.0, "repeat": 1})
            refresh(first_added if len(items) > first_added else None)
            if errors:
                messagebox.showerror(APP_NAME, "以下文件无法加入：\n\n" + "\n".join(errors), parent=dialog)

        def remove_selected():
            index = selected_index()
            if index is None:
                return
            items.pop(index)
            current_index[0] = None
            refresh(min(index, len(items) - 1) if items else None)
            if not items:
                on_select()

        def move_composer_item(source, target):
            if self.recording or self.playing:
                return
            if source == target or not (0 <= source < len(items)) or not (0 <= target < len(items)):
                return
            items[:] = move_list_item(items, source, target)
            current_index[0] = None
            refresh(target)
            on_select()

        def move_selected(direction):
            index = selected_index()
            if index is not None:
                move_composer_item(index, index + direction)

        ttk.Button(tools, text="＋ 加入宏文件", style="Accent.TButton", command=add_files).pack(side="left")
        ttk.Button(tools, text="上移", style="Compact.TButton", command=lambda: move_selected(-1)).pack(side="left", padx=(8, 3))
        ttk.Button(tools, text="下移", style="Compact.TButton", command=lambda: move_selected(1)).pack(side="left", padx=3)
        ttk.Button(tools, text="删除", style="Compact.TButton", command=remove_selected).pack(side="left", padx=3)
        ttk.Label(tools, textvariable=summary_var, style="Subtle.TLabel").pack(side="right")

        editor = ttk.Frame(body, style="Toolbar.TFrame", padding=(12, 10))
        editor.grid(row=4, column=0, sticky="ew", pady=(10, 0))
        ttk.Label(editor, text="所选项速度", style="Option.TLabel").pack(side="left")
        validate_speed = self.root.register(self._validate_speed_input)
        speed_entry = ttk.Entry(editor, textvariable=speed_var, width=8, style="Option.TEntry", font=text_font, validate="key", validatecommand=(validate_speed, "%P"))
        speed_entry.pack(side="left", padx=(8, 14))
        ttk.Label(editor, text="所选项次数", style="Option.TLabel").pack(side="left")
        repeat_entry = ttk.Entry(editor, textvariable=repeat_var, width=10, style="Option.TEntry", font=text_font)
        repeat_entry.pack(side="left", padx=(8, 14))
        ttk.Label(editor, textvariable=path_var, style="Toolbar.TLabel").pack(side="left", fill="x", expand=True)
        ttk.Label(
            body,
            text="生成后会保留所有源文件，且不会载入或修改当前时间轴。",
            style="Subtle.TLabel",
            font=text_font,
        ).grid(row=5, column=0, sticky="w", pady=(10, 0))

        def confirm():
            if self.recording or self.playing:
                messagebox.showwarning(APP_NAME, "录制或回放中不能生成编排宏。", parent=dialog)
                return
            if not items:
                messagebox.showinfo(APP_NAME, "请先加入至少一个宏文件。", parent=dialog)
                return
            selected = current_index[0]
            if selected is not None:
                value = repeat_var.get().strip()
                if not value.isdigit() or int(value) < 1:
                    messagebox.showerror(APP_NAME, "编排次数必须是大于等于 1 的整数。", parent=dialog)
                    repeat_entry.focus_set()
                    return
                items[selected]["repeat"] = int(value)
                try:
                    items[selected]["speed"] = parse_composition_speed(speed_var.get())
                except ValueError as exc:
                    messagebox.showerror(APP_NAME, f"{exc}。", parent=dialog)
                    speed_entry.focus_set()
                    return
            macros_dir = Path(__file__).with_name("macros")
            macros_dir.mkdir(exist_ok=True)
            output_path = filedialog.asksaveasfilename(
                parent=dialog,
                title="保存编排后的总宏",
                initialdir=macros_dir,
                initialfile=f"composed_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json",
                defaultextension=".json",
                filetypes=[("Macro JSON", "*.json"), ("All files", "*.*")],
            )
            if not output_path:
                return
            normalized_output = os.path.normcase(str(Path(output_path).resolve()))
            source_paths = {os.path.normcase(str(item["path"].resolve())) for item in items}
            if normalized_output in source_paths:
                messagebox.showerror(APP_NAME, "总宏不能覆盖参与编排的源宏文件，请使用新的文件名。", parent=dialog)
                return
            try:
                target_screen = virtual_screen_bounds()
                sequence = [
                    {
                        "events": item["events"],
                        "speed": item["speed"],
                        "repeat": item["repeat"],
                        "screen": item["payload"].get("screen"),
                    }
                    for item in items
                ]
                composed = compose_macro_events(sequence, target_screen=target_screen)
                has_mouse_moves = any(_is_mouse_move(event) for event in composed)
                save_macro_payload(output_path, composed, has_mouse_moves, target_screen)
            except Exception as exc:
                messagebox.showerror(APP_NAME, f"编排保存失败：{exc}", parent=dialog)
                return
            self.status_var.set("编排宏已保存")
            self.log(f"已生成编排宏：{output_path}（{len(composed)} 个事件）；当前时间轴未改变。")
            messagebox.showinfo(
                APP_NAME,
                f"总宏已保存：\n{output_path}\n\n源宏文件和当前时间轴均未改变。",
                parent=dialog,
            )
            dialog.destroy()

        def normalize_composition_speed(_event=None):
            try:
                value = float(speed_var.get().strip())
            except (TypeError, ValueError, OverflowError):
                value = 1.0
            if not math.isfinite(value):
                value = 1.0
            value = min(SPEED_MAX, max(SPEED_MIN, value))
            speed_var.set(f"{value:.2f}".rstrip("0").rstrip("."))
            return "break" if _event is not None and getattr(_event, "keysym", "") == "Return" else None

        speed_var.trace_add("write", on_speed_changed)
        repeat_var.trace_add("write", on_repeat_changed)
        speed_entry.bind("<FocusOut>", lambda _event: normalize_composition_speed())
        speed_entry.bind("<Return>", normalize_composition_speed)
        tree.bind("<<TreeviewSelect>>", on_select)
        tree.tag_configure("even", background="#151c24")
        tree.tag_configure("odd", background="#1b2028")
        tree.tag_configure("drag_target", background="#3d668a", foreground="#ffffff")
        bind_treeview_drag_reorder(
            tree,
            lambda: len(items),
            move_composer_item,
            enabled=lambda: not self.recording and not self.playing,
        )
        buttons = ttk.Frame(body)
        buttons.grid(row=6, column=0, sticky="ew", pady=(14, 0))
        ttk.Button(buttons, text="取消", command=dialog.destroy).pack(side="right", padx=(8, 0))
        ttk.Button(buttons, text="生成并保存", style="Accent.TButton", command=confirm).pack(side="right")
        dialog.bind("<Escape>", lambda _event: dialog.destroy())
        dialog.update_idletasks()
        width = min(1120, max(900, self.root.winfo_width() - 180))
        height = min(720, max(560, self.root.winfo_height() - 180))
        x = max(0, self.root.winfo_x() + (self.root.winfo_width() - width) // 2)
        y = max(0, self.root.winfo_y() + (self.root.winfo_height() - height) // 2)
        dialog.geometry(f"{width}x{height}+{x}+{y}")

    def play_selected_step(self):
        if self.selected_index is None or self.recording or self.playing:
            return
        with self.events_lock:
            event = dict(self.events[self.selected_index]) if self.selected_index < len(self.events) else None
        if not event:
            return
        message = int(event.get("message", WM_MOUSEMOVE)) if event.get("kind") == "mouse" else None
        leaves_input_held = (
            event.get("kind") == "key" and event.get("action") != "up"
        ) or (
            event.get("kind") == "mouse" and message in MOUSE_BUTTON_DOWN_MESSAGES
        )
        prompt = (
            "当前步骤是按下操作。单步执行后不会自动释放，按键或鼠标键可能会一直保持按下。确定要继续吗？"
            if leaves_input_held
            else "将执行当前选中的输入事件，是否继续？"
        )
        if not messagebox.askyesno(APP_NAME, prompt):
            return
        try:
            self._replay_event(event, set(), set(), self.loaded_screen or self.recording_screen)
            self.log("已执行当前步骤。")
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"步骤执行失败：{exc}")

    def clear_events(self):
        if self.recording:
            messagebox.showwarning(APP_NAME, "录制中不能清空。")
            return
        if self.playing:
            messagebox.showwarning(APP_NAME, "回放中不能清空。")
            return
        with self.events_lock:
            had_events = bool(self.events)
        if had_events:
            self._push_undo()
        with self.events_lock:
            self.events = []
        self.selected_index = None
        self.selected_indices = []
        self.current_file = None
        self.dirty = True
        self.file_var.set("未保存")
        self.status_var.set("就绪")
        self.update_count()
        self.log("已清空事件。")

    def save_macro(self):
        if self.recording:
            self.stop_recording()
        with self.events_lock:
            events = list(self.events)
        if not events:
            messagebox.showinfo(APP_NAME, "没有可保存的事件。")
            return
        macros_dir = Path(__file__).with_name("macros")
        macros_dir.mkdir(exist_ok=True)
        default_name = f"macro_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        path = filedialog.asksaveasfilename(
            title="保存宏",
            initialdir=macros_dir,
            initialfile=default_name,
            defaultextension=".json",
            filetypes=[("Macro JSON", "*.json"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            save_macro_payload(path, events, self.record_moves_var.get(), self.recording_screen or self.loaded_screen)
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"保存失败：{exc}")
            return
        self.current_file = Path(path)
        self.dirty = False
        self.file_var.set(str(self.current_file))
        self.log(f"已保存：{self.current_file}")

    def load_macro(self):
        if self.recording:
            messagebox.showwarning(APP_NAME, "录制中不能载入。")
            return
        path = filedialog.askopenfilename(
            title="载入宏",
            initialdir=Path(__file__).with_name("macros"),
            filetypes=[("Macro JSON", "*.json"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            payload, events = load_macro_payload(path)
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"载入失败：{exc}")
            return
        self._push_undo()
        with self.events_lock:
            self.events = events
        self.selected_index = None
        self.selected_indices = []
        self.loaded_screen = payload.get("screen")
        self.current_file = Path(path)
        self.dirty = False
        self.file_var.set(str(self.current_file))
        self.status_var.set("已载入")
        if self.loaded_screen and self.loaded_screen != virtual_screen_bounds():
            self.log("检测到当前显示器布局与录制时不同，播放时将自动映射坐标。")
        self.update_count()
        self.log(f"已载入：{self.current_file}")

    def play(self):
        if self.recording:
            messagebox.showwarning(APP_NAME, "录制中不能回放。")
            return
        if self.playing:
            self.toggle_pause()
            return
        with self.events_lock:
            events = list(self.events)
        if not events:
            messagebox.showinfo(APP_NAME, "没有可播放的事件。")
            return
        try:
            self._normalize_speed()
            speed = min(SPEED_MAX, max(SPEED_MIN, float(self.speed_var.get())))
            repeat = parse_repeat_settings(self.repeat_mode_var.get(), self.repeat_var.get())
        except (TypeError, ValueError) as exc:
            messagebox.showerror(APP_NAME, str(exc) or "速度和重复次数必须是数字。")
            return
        if repeat is None:
            prompt = "回放会持续循环，直到你按“停止”或 F10。它会控制当前鼠标和键盘，请切到目标窗口后点击“是”开始。"
        else:
            prompt = "回放会控制当前鼠标和键盘。请切到目标窗口后点击“是”开始。"
        if not messagebox.askyesno(APP_NAME, prompt):
            return
        self.playing = True
        self._set_playback_editing_locked(True)
        self.stop_playback.clear()
        self.pause_playback.clear()
        self.play_button.config(text="Ⅱ  暂停   F9")
        self.progress_var.set("准备回放…")
        self.status_var.set("回放中 · 准备回放…")
        thread = threading.Thread(target=self._play_worker, args=(events, speed, repeat, self.loaded_screen or self.recording_screen), daemon=True)
        thread.start()

    def toggle_pause(self):
        if not self.playing:
            return
        if self.pause_playback.is_set():
            self.pause_playback.clear()
            self.play_button.config(text="Ⅱ  暂停   F9")
            self.status_var.set(f"回放中 · {self.progress_var.get()}")
        else:
            self.pause_playback.set()
            self.play_button.config(text="▶  继续   F9")
            self.status_var.set(f"已暂停 · {self.progress_var.get()}")

    def _wait_until(self, seconds, progress_start=None, progress_end=None, total_time=None, loop_index=0, repeat=1):
        seconds = max(0.0, float(seconds))
        started = time.perf_counter()
        paused_total = 0.0
        pause_started = None
        held_current = progress_start
        last_emit = 0.0
        while True:
            if self.stop_playback.is_set():
                return True
            now = time.perf_counter()
            if self.pause_playback.is_set():
                if pause_started is None:
                    pause_started = now
                    if progress_start is not None:
                        active_elapsed = max(0.0, now - started - paused_total)
                        ratio = 1.0 if seconds <= 0 else min(1.0, active_elapsed / seconds)
                        held_current = progress_start if progress_end is None else progress_start + (progress_end - progress_start) * ratio
                if progress_start is not None and now - last_emit >= 0.04:
                    self.ui_queue.put(("progress", self._format_playback_progress(held_current, progress_end, total_time, loop_index, repeat)))
                    last_emit = now
                time.sleep(0.01)
                continue
            if pause_started is not None:
                paused_total += now - pause_started
                pause_started = None
            active_elapsed = max(0.0, now - started - paused_total)
            if progress_start is not None:
                ratio = 1.0 if seconds <= 0 else min(1.0, active_elapsed / seconds)
                current = progress_start if progress_end is None else progress_start + (progress_end - progress_start) * ratio
                if now - last_emit >= 0.04 or ratio >= 1.0:
                    self.ui_queue.put(("progress", self._format_playback_progress(current, progress_end, total_time, loop_index, repeat)))
                    last_emit = now
            remaining = seconds - active_elapsed
            if remaining <= 0:
                return False
            if remaining > 0.003:
                time.sleep(min(remaining - 0.001, 0.02))
            else:
                time.sleep(0.001)

    @staticmethod
    def _format_playback_progress(current, target=None, total_time=None, loop_index=0, repeat=1):
        current = max(0.0, float(current or 0.0))
        total = max(0.0, float(total_time or target or 0.0))
        iteration = f"第 {loop_index + 1} 次" if repeat is None else f"第 {loop_index + 1}/{repeat} 次"
        suffix = " · 循环" if repeat is None else ""
        return f"{iteration}{suffix} · {current:.2f}s / {total:.2f}s"

    def _play_worker(self, events, speed, repeat, source_screen):
        active_keys, active_buttons = set(), set()
        total_time = max((event_end_time(item) for item in events), default=0.0)
        try:
            loop_index = 0
            while repeat is None or loop_index < repeat:
                if self.stop_playback.is_set():
                    break
                previous_time = 0.0
                iteration = f"第 {loop_index + 1} 次" if repeat is None else f"第 {loop_index + 1}/{repeat} 次"
                self._queue_log(f"开始{iteration}回放。")
                for event in events:
                    if self.stop_playback.is_set():
                        break
                    event_time = float(event.get("t", previous_time))
                    wait_time = max(0.0, (event_time - previous_time) / speed)
                    if wait_time and self._wait_until(
                        wait_time,
                        progress_start=previous_time,
                        progress_end=event_time,
                        total_time=total_time,
                        loop_index=loop_index,
                        repeat=repeat,
                    ):
                        break
                    self._replay_event(event, active_keys, active_buttons, source_screen, speed, total_time, loop_index, repeat)
                    previous_time = event_end_time(event)
                    self.ui_queue.put(("progress", self._format_playback_progress(previous_time, previous_time, total_time, loop_index, repeat)))
                loop_index += 1
            final_status = "已停止" if self.stop_playback.is_set() else "回放完成"
        except Exception as exc:
            self._queue_log(f"回放出错：{exc}")
            final_status = "回放出错"
        finally:
            release_failures = self._release_inputs(active_keys, active_buttons)
            if release_failures:
                details = "\n".join(f"• {failure}" for failure in release_failures)
                warning = (
                    f"回放结束时未能自动释放以下输入，已分别重试 {INPUT_RELEASE_ATTEMPTS} 次：\n\n"
                    f"{details}\n\n"
                    "请手动按下并松开这些按键或鼠标键。若目标程序以管理员身份运行，"
                    "请用相同权限运行本程序后重试。"
                )
                self._queue_log(f"输入释放失败：{'；'.join(release_failures)}")
                self.ui_queue.put(("release_warning", warning))
                final_status = f"{final_status} · 输入释放失败"
        self.ui_queue.put(("playback_done", final_status))

    def _release_inputs(self, active_keys, active_buttons):
        failures = []
        for vk, scan, extended in list(active_keys):
            error = retry_input_release(send_keyboard, vk, scan, True, extended)
            if error is None:
                active_keys.discard((vk, scan, extended))
            else:
                failures.append(f"按键 {key_display_name(vk)}：{error}")
        for button, data in list(active_buttons):
            error = retry_input_release(send_mouse, button, data)
            if error is None:
                active_buttons.discard((button, data))
            else:
                failures.append(f"{mouse_release_name(button, data)}：{error}")
        return failures

    def _replay_event(self, event, active_keys=None, active_buttons=None, source_screen=None, speed=1.0, total_time=None, loop_index=0, repeat=1):
        active_keys = active_keys if active_keys is not None else set()
        active_buttons = active_buttons if active_buttons is not None else set()
        kind = event.get("kind")
        if kind == "key":
            key_id = (int(event["vk"]), int(event.get("scan", 0)), bool(event.get("extended")))
            send_keyboard(
                int(event["vk"]),
                int(event.get("scan", 0)),
                event.get("action") == "up",
                bool(event.get("extended")),
            )
            if event.get("action") == "up":
                active_keys.discard(key_id)
            else:
                active_keys.add(key_id)
            return
        if kind != "mouse":
            return
        x, y = map_screen_point(event.get("x", 0), event.get("y", 0), source_screen)
        # Low-level hooks can report a few pixels beyond an edge while the
        # pointer crosses a monitor boundary. SetCursorPos clamps those points;
        # clamp explicitly so the reachability check does not treat that normal
        # Windows behavior as a playback failure.
        x, y = clamp_screen_point(x, y)
        message = int(event.get("message", WM_MOUSEMOVE))
        mouse_data = int(event.get("data", 0))
        move_cursor_precisely(x, y)
        button_inputs = MOUSE_BUTTON_INPUTS.get(message)
        if button_inputs:
            press_flag, release_flag = button_inputs
            button_data = high_word(mouse_data) if message in X_BUTTON_MESSAGES else 0
            send_mouse(press_flag, button_data)
            active_button = (release_flag, button_data)
            if message in MOUSE_BUTTON_DOWN_MESSAGES:
                active_buttons.add(active_button)
            else:
                active_buttons.discard(active_button)
            return

        wheel_flag = MOUSE_WHEEL_INPUTS.get(message)
        if wheel_flag:
            send_mouse(wheel_flag, signed_word(high_word(mouse_data)))

    def stop_all(self):
        if self.recording:
            self.stop_recording()
        if self.playing:
            self.stop_playback.set()
            self.log("正在停止回放。")

    def close(self):
        if getattr(self, "dirty", False) and not messagebox.askyesno(
            APP_NAME,
            "当前有未保存的宏，确定要关闭吗？",
            parent=self.root,
        ):
            return
        self.stop_all()
        self._stop_key_capture()
        if self.hotkey_thread:
            self.hotkey_thread.stop()
        self.root.destroy()


def main():
    enable_dpi_awareness()
    root = tk.Tk()
    try:
        # Use a readable desktop scale. Explicit fonts below are intentionally
        # larger than Tk's defaults so the app remains legible on high-DPI screens.
        root.call("tk", "scaling", 1.25)
    except tk.TclError:
        pass
    app = MacroApp(root)
    root.after(500, app._balance_editor_panes)
    app.log("启动完成。F8 开始/停止录制，F9 播放/暂停/继续，F10 停止录制或回放。")
    root.mainloop()


if __name__ == "__main__":
    main()
