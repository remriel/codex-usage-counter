from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import json
import os
import queue
import subprocess
import sys
import threading
import time
import webbrowser
from dataclasses import dataclass
from datetime import datetime, timezone
from collections import deque
from pathlib import Path
from typing import Any, Optional

import tkinter as tk
from tkinter import messagebox

try:
    import winsound
except ImportError:
    winsound = None


USAGE_DASHBOARD_URL = "https://chatgpt.com/codex/settings/usage"
REFRESH_SECONDS = 120
CONFIG_DIR = Path(os.environ.get("APPDATA", str(Path.home()))) / "CodexUsageCounter"
CONFIG_FILE = CONFIG_DIR / "settings.json"
HISTORY_FILE = CONFIG_DIR / "usage_history.json"
HISTORY_RETENTION_DAYS = 30
HISTORY_MAX_POINTS = 60000
STARTUP_SHORTCUT_NAME = "Codex Usage Counter.lnk"

COLORS = {
    "ink": "#0d1224",
    "panel": "#141b35",
    "panel_raised": "#192242",
    "line": "#273154",
    "text": "#f5f2ea",
    "muted": "#8e98bb",
    "soft": "#b8c1dd",
    "mint": "#c8f4e5",
    "coral": "#ff756f",
    "violet": "#a99cff",
    "amber": "#f5c779",
}


def asset_path(name: str) -> Path:
    """Resolve an asset both from source and from a PyInstaller bundle."""

    bundle_root = getattr(sys, "_MEIPASS", None)
    if bundle_root:
        candidate = Path(bundle_root) / "assets" / name
    else:
        candidate = Path(__file__).resolve().parent / "assets" / name
    return candidate


def startup_shortcut_path() -> Path:
    appdata = os.environ.get("APPDATA")
    base = Path(appdata) if appdata else Path.home()
    return base / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / STARTUP_SHORTCUT_NAME


_instance_mutex: Optional[int] = None


def acquire_single_instance() -> bool:
    """Keep startup shortcuts and manual launches from creating duplicate counters."""

    global _instance_mutex
    if os.name != "nt":
        return True
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.CreateMutexW(None, False, "Local\\CodexUsageCounter")
    if not handle:
        return True
    if kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        kernel32.CloseHandle(handle)
        return False
    _instance_mutex = handle
    return True


def clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def number(value: Any) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_timestamp(value: Any) -> Optional[float]:
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    except ValueError:
        return None


def format_window(minutes: Optional[int]) -> str:
    if not minutes:
        return "Usage window"
    if minutes % (7 * 24 * 60) == 0:
        days = minutes // (7 * 24 * 60)
        return f"{days} week" if days == 1 else f"{days} weeks"
    if minutes % (24 * 60) == 0:
        days = minutes // (24 * 60)
        return f"{days} day" if days == 1 else f"{days} days"
    if minutes % 60 == 0:
        hours = minutes // 60
        return f"{hours} hour" if hours == 1 else f"{hours} hours"
    return f"{minutes} min"


def format_countdown(reset_epoch: Optional[float]) -> str:
    if not reset_epoch:
        return "reset time unavailable"
    remaining = max(0, int(reset_epoch - time.time()))
    if remaining <= 0:
        return "resetting now"
    days, remainder = divmod(remaining, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, _ = divmod(remainder, 60)
    if days:
        return f"{days}d {hours:02d}h"
    if hours:
        return f"{hours}h {minutes:02d}m"
    return f"{minutes}m"


def format_updated(timestamp: Optional[float]) -> str:
    if not timestamp:
        return "No local signal yet"
    local = datetime.fromtimestamp(timestamp).astimezone()
    return f"Updated {local.strftime('%I:%M %p').lstrip('0')}"


@dataclass
class AppSettings:
    display_mode: str = "used"
    always_on_top: bool = True
    start_with_windows: bool = False
    sound_alert: bool = False
    milestone_step: int = 10
    milestone_duration: int = 5
    refresh_interval_seconds: int = 120

    @classmethod
    def load(cls) -> "AppSettings":
        try:
            payload = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return cls()
        display_mode = payload.get("display_mode")
        if display_mode not in ("used", "remaining"):
            display_mode = "used"
        milestone_step = int(number(payload.get("milestone_step")) or 10)
        if milestone_step not in (1, 5, 10, 20, 25, 50):
            milestone_step = 10
        milestone_duration = int(number(payload.get("milestone_duration")) or 5)
        if milestone_duration not in (1, 2, 5, 10):
            milestone_duration = 5
        refresh_interval_seconds = int(number(payload.get("refresh_interval_seconds")) or REFRESH_SECONDS)
        if refresh_interval_seconds not in (15, 30, 60, 120, 300, 600, 900):
            refresh_interval_seconds = REFRESH_SECONDS
        return cls(
            display_mode=display_mode,
            always_on_top=bool(payload.get("always_on_top", True)),
            start_with_windows=bool(payload.get("start_with_windows", startup_shortcut_path().exists())),
            sound_alert=bool(payload.get("sound_alert", False)),
            milestone_step=milestone_step,
            milestone_duration=milestone_duration,
            refresh_interval_seconds=refresh_interval_seconds,
        )

    def save(self) -> None:
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            CONFIG_FILE.write_text(
                json.dumps(
                    {
                        "display_mode": self.display_mode,
                        "always_on_top": self.always_on_top,
                        "start_with_windows": self.start_with_windows,
                        "sound_alert": self.sound_alert,
                        "milestone_step": self.milestone_step,
                        "milestone_duration": self.milestone_duration,
                        "refresh_interval_seconds": self.refresh_interval_seconds,
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
        except OSError:
            pass


class UsageHistory:
    """Small local sample store for the optional statistics view."""

    def __init__(self) -> None:
        self.points: list[dict[str, float]] = self._load()

    @staticmethod
    def _load() -> list[dict[str, float]]:
        try:
            payload = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return []
        if not isinstance(payload, list):
            return []
        buckets: dict[int, dict[str, float]] = {}
        for item in payload:
            if not isinstance(item, dict):
                continue
            timestamp = number(item.get("timestamp"))
            used_percent = number(item.get("used_percent"))
            if timestamp is not None and used_percent is not None:
                point = {"timestamp": timestamp, "used_percent": clamp(used_percent, 0, 100)}
                buckets[int(timestamp // 60) * 60] = point
        return [buckets[key] for key in sorted(buckets)][-HISTORY_MAX_POINTS:]

    def record(self, snapshot: UsageSnapshot) -> None:
        if snapshot.used_percent is None:
            return
        now = time.time()
        point = {"timestamp": now, "used_percent": float(snapshot.used_percent)}
        if self.points and int(self.points[-1]["timestamp"] // 60) == int(now // 60):
            if self.points[-1]["used_percent"] == point["used_percent"]:
                return
            self.points[-1] = point
        else:
            self.points.append(point)
        cutoff = now - HISTORY_RETENTION_DAYS * 24 * 60 * 60
        self.points = [item for item in self.points if item["timestamp"] >= cutoff][-HISTORY_MAX_POINTS:]
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            HISTORY_FILE.write_text(json.dumps(self.points), encoding="utf-8")
        except OSError:
            pass

    def since(self, hours: int) -> list[dict[str, float]]:
        cutoff = time.time() - hours * 60 * 60
        return [item for item in self.points if item["timestamp"] >= cutoff]

    def hourly(self, hours: int) -> list[dict[str, float]]:
        """Collapse samples to the latest reading in each local hour bucket."""

        buckets: dict[int, dict[str, float]] = {}
        for point in self.since(hours):
            bucket = int(point["timestamp"] // 3600) * 3600
            buckets[bucket] = point
        return [buckets[key] for key in sorted(buckets)]

    def chart_points(self, hours: int) -> list[dict[str, float]]:
        """Return the minute-level series used by every statistics range."""

        return self.since(hours)

    @staticmethod
    def _slope(points: list[dict[str, float]]) -> Optional[float]:
        """Estimate percentage points per hour using elapsed-time regression."""

        if len(points) < 2:
            return None
        origin = points[0]["timestamp"]
        xs = [(point["timestamp"] - origin) / 3600 for point in points]
        ys = [point["used_percent"] for point in points]
        mean_x = sum(xs) / len(xs)
        mean_y = sum(ys) / len(ys)
        denominator = sum((value - mean_x) ** 2 for value in xs)
        if denominator <= 0:
            return None
        return sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / denominator

    def rate_series(
        self,
        hours: int,
        points: Optional[list[dict[str, float]]] = None,
    ) -> list[dict[str, float]]:
        """Return hourly trend rates using a reset-aware regression plus EMA."""

        points = points if points is not None else self.chart_points(hours)
        series: list[dict[str, float]] = []
        smoothed: Optional[float] = None
        if not points:
            return series

        origin = points[0]["timestamp"]
        window: deque[tuple[dict[str, float], float, float]] = deque()
        sum_x = 0.0
        sum_y = 0.0
        sum_x_squared = 0.0
        sum_xy = 0.0

        for point in points:
            cutoff = point["timestamp"] - 3 * 3600
            while window and window[0][0]["timestamp"] < cutoff:
                _, old_x, old_y = window.popleft()
                sum_x -= old_x
                sum_y -= old_y
                sum_x_squared -= old_x * old_x
                sum_xy -= old_x * old_y

            if window and point["used_percent"] < window[-1][0]["used_percent"] - 5:
                window.clear()
                sum_x = 0.0
                sum_y = 0.0
                sum_x_squared = 0.0
                sum_xy = 0.0
                smoothed = None

            x = (point["timestamp"] - origin) / 3600
            y = point["used_percent"]
            window.append((point, x, y))
            sum_x += x
            sum_y += y
            sum_x_squared += x * x
            sum_xy += x * y

            count = len(window)
            denominator = count * sum_x_squared - sum_x * sum_x
            if count < 2 or denominator <= 0:
                continue
            raw_rate = (count * sum_xy - sum_x * sum_y) / denominator
            smoothed = raw_rate if smoothed is None else smoothed * 0.65 + raw_rate * 0.35
            series.append(
                {
                    "timestamp": point["timestamp"],
                    "used_percent": point["used_percent"],
                    "rate_per_hour": smoothed,
                }
            )
        return series


@dataclass(frozen=True)
class UsageSnapshot:
    used_percent: Optional[float] = None
    window_minutes: Optional[int] = None
    resets_at: Optional[float] = None
    plan_type: Optional[str] = None
    timestamp: Optional[float] = None
    source_path: Optional[str] = None
    error: Optional[str] = None

    @property
    def has_data(self) -> bool:
        return self.used_percent is not None

    @property
    def is_stale(self) -> bool:
        return bool(self.timestamp and time.time() - self.timestamp > 30 * 60)


class CodexTelemetryReader:
    """Read the latest rate-limit event already written by local Codex sessions.

    This deliberately limits itself to the sessions directory. It does not open
    auth.json, API keys, cookies, or any browser profile data.
    """

    def __init__(self) -> None:
        configured_home = os.environ.get("CODEX_HOME")
        self.codex_home = Path(configured_home).expanduser() if configured_home else Path.home() / ".codex"
        self.sessions_dir = self.codex_home / "sessions"
        self._file_cache: dict[str, tuple[int, int, Optional[UsageSnapshot]]] = {}

    @staticmethod
    def _tail_lines(path: Path, max_bytes: int = 384 * 1024) -> list[str]:
        try:
            size = path.stat().st_size
            with path.open("rb") as handle:
                if size > max_bytes:
                    handle.seek(-max_bytes, os.SEEK_END)
                    handle.readline()
                data = handle.read()
            return data.decode("utf-8", errors="ignore").splitlines()
        except (OSError, UnicodeError):
            return []

    @staticmethod
    def _rate_limits(event: dict[str, Any]) -> Optional[dict[str, Any]]:
        payload = event.get("payload")
        if not isinstance(payload, dict):
            return None
        limits = payload.get("rate_limits")
        if isinstance(limits, dict):
            return limits
        info = payload.get("info")
        if isinstance(info, dict) and isinstance(info.get("rate_limits"), dict):
            return info["rate_limits"]
        return None

    def _candidate_files(self) -> list[Path]:
        try:
            files = [
                item
                for item in self.sessions_dir.rglob("*.jsonl")
                if item.is_file()
            ]
        except OSError:
            return []
        files.sort(key=lambda item: item.stat().st_mtime, reverse=True)
        return files[:48]

    def _read_file(self, path: Path, file_mtime: float) -> Optional[UsageSnapshot]:
        latest: Optional[UsageSnapshot] = None
        for line in self._tail_lines(path):
            if '"rate_limits"' not in line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            limits = self._rate_limits(event)
            if not limits:
                continue
            primary = limits.get("primary")
            secondary = limits.get("secondary")
            selected = primary if isinstance(primary, dict) else secondary
            if not isinstance(selected, dict):
                continue
            used = number(selected.get("used_percent"))
            if used is None:
                continue
            timestamp = parse_timestamp(event.get("timestamp")) or file_mtime
            snapshot = UsageSnapshot(
                used_percent=clamp(used, 0, 100),
                window_minutes=int(number(selected.get("window_minutes")) or 0) or None,
                resets_at=number(selected.get("resets_at")),
                plan_type=str(limits.get("plan_type") or "").strip() or None,
                timestamp=timestamp,
                source_path=str(path),
            )
            if latest is None or timestamp > (latest.timestamp or 0):
                latest = snapshot
        return latest

    def read(self) -> UsageSnapshot:
        if not self.sessions_dir.exists():
            return UsageSnapshot(error="Codex session telemetry is not available yet")

        latest: Optional[tuple[float, UsageSnapshot]] = None
        next_cache: dict[str, tuple[int, int, Optional[UsageSnapshot]]] = {}
        for path in self._candidate_files():
            try:
                metadata = path.stat()
            except OSError:
                continue
            cache_key = str(path)
            signature = (metadata.st_mtime_ns, metadata.st_size)
            cached = self._file_cache.get(cache_key)
            if cached is not None and cached[:2] == signature:
                snapshot = cached[2]
            else:
                snapshot = self._read_file(path, metadata.st_mtime)
            next_cache[cache_key] = (signature[0], signature[1], snapshot)
            if snapshot is not None and snapshot.timestamp is not None:
                if latest is None or snapshot.timestamp > latest[0]:
                    latest = (snapshot.timestamp, snapshot)
        self._file_cache = next_cache

        if latest:
            return latest[1]
        return UsageSnapshot(error="Open Codex once to populate the local usage signal")


if os.name == "nt":
    _user32 = ctypes.windll.user32
    _shell32 = ctypes.windll.shell32
    _kernel32 = ctypes.windll.kernel32

    class _GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", ctypes.c_ulong),
            ("Data2", ctypes.c_ushort),
            ("Data3", ctypes.c_ushort),
            ("Data4", ctypes.c_ubyte * 8),
        ]

    class _NOTIFYICONDATAW(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("hWnd", wintypes.HWND),
            ("uID", wintypes.UINT),
            ("uFlags", wintypes.UINT),
            ("uCallbackMessage", wintypes.UINT),
            ("hIcon", wintypes.HICON),
            ("szTip", wintypes.WCHAR * 128),
            ("dwState", wintypes.DWORD),
            ("dwStateMask", wintypes.DWORD),
            ("szInfo", wintypes.WCHAR * 256),
            ("uTimeoutOrVersion", wintypes.UINT),
            ("szInfoTitle", wintypes.WCHAR * 64),
            ("dwInfoFlags", wintypes.DWORD),
            ("guidItem", _GUID),
            ("hBalloonIcon", wintypes.HICON),
        ]

    _LRESULT = ctypes.c_ssize_t
    _WNDPROC = ctypes.WINFUNCTYPE(_LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)

    class _WNDCLASSW(ctypes.Structure):
        _fields_ = [
            ("style", wintypes.UINT),
            ("lpfnWndProc", _WNDPROC),
            ("cbClsExtra", ctypes.c_int),
            ("cbWndExtra", ctypes.c_int),
            ("hInstance", wintypes.HINSTANCE),
            ("hIcon", wintypes.HICON),
            ("hCursor", wintypes.HCURSOR),
            ("hbrBackground", wintypes.HBRUSH),
            ("lpszMenuName", wintypes.LPCWSTR),
            ("lpszClassName", wintypes.LPCWSTR),
        ]

    class _POINT(ctypes.Structure):
        _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class TrayIcon:
    """Small ctypes-only notification-area icon, so the app has no tray dependency."""

    def __init__(self, icon_path: Path, on_action: Any) -> None:
        self.icon_path = icon_path
        self.on_action = on_action
        self._actions: queue.Queue[tuple[str, Any]] = queue.Queue()
        self._ui_actions: queue.Queue[tuple[str, Any]] = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        self._ready = threading.Event()
        self._hwnd: Optional[int] = None
        self._icon: Optional[int] = None
        self._icon_cache: dict[str, int] = {}
        self._tooltip = ""
        self._class_name = f"CodexUsageCounterTray_{os.getpid()}"
        self._callback_message = 0x8000 + 47
        self._wnd_proc: Any = None

    @property
    def available(self) -> bool:
        return os.name == "nt"

    def start(self, tooltip: str) -> None:
        if not self.available:
            return
        self._thread = threading.Thread(target=self._run, args=(tooltip,), daemon=True)
        self._thread.start()
        self._ready.wait(timeout=3)

    def _run(self, tooltip: str) -> None:
        assert os.name == "nt"

        def wnd_proc(hwnd: int, msg: int, wparam: int, lparam: int) -> int:
            if msg == self._callback_message:
                event = int(lparam)
                if event in (0x0202, 0x0203):  # left up / double click
                    self._ui_actions.put(("show", None))
                elif event in (0x0205, 0x007B):  # right up / context menu
                    point = _POINT()
                    _user32.GetCursorPos(ctypes.byref(point))
                    self._ui_actions.put(("menu", (point.x, point.y)))
                return 0
            if msg == 0x0010:  # WM_CLOSE
                self._delete_icon()
                _user32.DestroyWindow(hwnd)
                return 0
            if msg == 0x0002:  # WM_DESTROY
                _user32.PostQuitMessage(0)
                return 0
            return int(_user32.DefWindowProcW(hwnd, msg, wparam, lparam))

        self._wnd_proc = _WNDPROC(wnd_proc)
        instance = _kernel32.GetModuleHandleW(None)
        window_class = _WNDCLASSW()
        window_class.lpfnWndProc = self._wnd_proc
        window_class.hInstance = instance
        window_class.lpszClassName = self._class_name
        _user32.RegisterClassW(ctypes.byref(window_class))
        hwnd = _user32.CreateWindowExW(
            0,
            self._class_name,
            "Codex Usage Counter",
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            instance,
            None,
        )
        if not hwnd:
            self._ready.set()
            return
        self._hwnd = hwnd
        self._icon = self._load_icon(self.icon_path)
        self._add_icon(tooltip)
        self._ready.set()

        message = wintypes.MSG()
        while _user32.GetMessageW(ctypes.byref(message), 0, 0, 0) > 0:
            _user32.TranslateMessage(ctypes.byref(message))
            _user32.DispatchMessageW(ctypes.byref(message))
            self._drain_actions()
        self._delete_icon()
        self._destroy_icons()
        _user32.UnregisterClassW(self._class_name, instance)

    def _load_icon(self, icon_path: Path) -> Optional[int]:
        cache_key = str(icon_path)
        cached = self._icon_cache.get(cache_key)
        if cached:
            return cached
        handle = _user32.LoadImageW(
            0,
            cache_key,
            1,  # IMAGE_ICON
            0,
            0,
            0x00000010 | 0x00000040,  # LR_LOADFROMFILE | LR_DEFAULTSIZE
        )
        if not handle:
            return None
        handle_value = int(handle)
        self._icon_cache[cache_key] = handle_value
        return handle_value

    def _destroy_icons(self) -> None:
        for handle in self._icon_cache.values():
            _user32.DestroyIcon(handle)
        self._icon_cache.clear()
        self._icon = None

    def _drain_actions(self) -> None:
        while True:
            try:
                action, value = self._actions.get_nowait()
            except queue.Empty:
                return
            if action == "tooltip":
                self._modify_icon(str(value))
            elif action == "icon":
                self._set_icon(Path(str(value)))
            elif action == "quit" and self._hwnd:
                _user32.PostMessageW(self._hwnd, 0x0010, 0, 0)

    def _icon_data(self, tooltip: str) -> Any:
        data = _NOTIFYICONDATAW()
        data.cbSize = ctypes.sizeof(_NOTIFYICONDATAW)
        data.hWnd = self._hwnd
        data.uID = 1
        data.uFlags = 0x00000001 | 0x00000002 | 0x00000004  # MESSAGE | ICON | TIP
        data.uCallbackMessage = self._callback_message
        data.hIcon = self._icon
        data.szTip = tooltip[:127]
        return data

    def _add_icon(self, tooltip: str) -> None:
        if self._hwnd:
            self._tooltip = tooltip
            data = self._icon_data(tooltip)
            _shell32.Shell_NotifyIconW(0, ctypes.byref(data))  # NIM_ADD

    def _modify_icon(self, tooltip: Optional[str] = None) -> None:
        if self._hwnd:
            if tooltip is not None:
                self._tooltip = tooltip
            data = self._icon_data(self._tooltip)
            _shell32.Shell_NotifyIconW(1, ctypes.byref(data))  # NIM_MODIFY

    def _set_icon(self, icon_path: Path) -> None:
        if not self._hwnd:
            return
        icon = self._load_icon(icon_path)
        if icon is None:
            return
        self.icon_path = icon_path
        self._icon = icon
        self._modify_icon()

    def _delete_icon(self) -> None:
        if self._hwnd:
            data = self._icon_data("")
            _shell32.Shell_NotifyIconW(2, ctypes.byref(data))  # NIM_DELETE

    def update_tooltip(self, tooltip: str) -> None:
        if self._thread and self._thread.is_alive():
            self._actions.put(("tooltip", tooltip))
            if self._hwnd:
                _user32.PostMessageW(self._hwnd, self._callback_message, 0, 0)

    def update_icon(self, icon_path: Path) -> None:
        if self._thread and self._thread.is_alive():
            self._actions.put(("icon", str(icon_path)))
            if self._hwnd:
                _user32.PostMessageW(self._hwnd, self._callback_message, 0, 0)

    def poll_actions(self) -> None:
        while True:
            try:
                action, value = self._ui_actions.get_nowait()
            except queue.Empty:
                return
            self.on_action(action, value)

    def stop(self) -> None:
        if self._thread and self._thread.is_alive():
            self._actions.put(("quit", None))
            if self._hwnd:
                _user32.PostMessageW(self._hwnd, self._callback_message, 0, 0)
            self._thread.join(timeout=2)


class TrayMilestonePopup:
    """Custom in-app milestone card positioned above the notification area."""

    def __init__(self, root: tk.Tk, settings: AppSettings) -> None:
        self.root = root
        self.settings = settings
        self.window: Optional[tk.Toplevel] = None
        self.image: Optional[tk.PhotoImage] = None
        self._hide_after_id: Optional[str] = None
        self._load_image()

    def _load_image(self) -> None:
        if self.image is not None:
            return
        try:
            source_image = tk.PhotoImage(file=str(asset_path("usage-orbit-64.png")))
            self.image = source_image.subsample(2, 2)
        except tk.TclError:
            self.image = None

    def show(self, message: str) -> None:
        self._load_image()
        self.close()

        popup = tk.Toplevel(self.root)
        popup.overrideredirect(True)
        popup.attributes("-topmost", True)
        popup.configure(bg=COLORS["line"])
        try:
            popup.attributes("-toolwindow", True)
        except tk.TclError:
            pass

        card = tk.Frame(
            popup,
            bg=COLORS["panel"],
            bd=0,
            highlightthickness=1,
            highlightbackground=COLORS["line"],
        )
        card.pack(padx=1, pady=1)

        content = tk.Frame(card, bg=COLORS["panel"])
        content.pack(fill="both", padx=14, pady=12)
        if self.image is not None:
            tk.Label(
                content,
                image=self.image,
                bg=COLORS["panel"],
                bd=0,
                highlightthickness=0,
            ).pack(side="left", padx=(0, 11))

        copy = tk.Frame(content, bg=COLORS["panel"])
        copy.pack(side="left", anchor="center")
        tk.Label(
            copy,
            text="CODEX USAGE",
            bg=COLORS["panel"],
            fg=COLORS["mint"],
            font=("Segoe UI", 9, "bold"),
            anchor="w",
        ).pack(anchor="w")
        tk.Label(
            copy,
            text=message,
            bg=COLORS["panel"],
            fg=COLORS["text"],
            font=("Segoe UI", 10),
            anchor="w",
        ).pack(anchor="w", pady=(3, 0))

        popup.update_idletasks()
        width = popup.winfo_reqwidth()
        height = popup.winfo_reqheight()
        x = max(12, popup.winfo_screenwidth() - width - 24)
        y = max(12, popup.winfo_screenheight() - height - 72)
        popup.geometry(f"{width}x{height}+{x}+{y}")
        self.window = popup
        popup.lift()
        self._hide_after_id = self.root.after(
            max(1000, self.settings.milestone_duration * 1000),
            self.close,
        )

    def close(self) -> None:
        if self._hide_after_id:
            try:
                self.root.after_cancel(self._hide_after_id)
            except tk.TclError:
                pass
            self._hide_after_id = None
        if self.window is not None:
            try:
                self.window.destroy()
            except tk.TclError:
                pass
            self.window = None


class UsageApp:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.settings = AppSettings.load()
        self.root.title("Codex Usage Counter")
        self.root.configure(bg=COLORS["ink"])
        self.root.resizable(False, False)
        self.root.geometry(self._initial_geometry())
        self.root.attributes("-topmost", self.settings.always_on_top)
        self.root.protocol("WM_DELETE_WINDOW", self.hide_to_tray)
        self.root.bind("<Control-r>", lambda _event: self.refresh_async())

        try:
            self.root.iconbitmap(str(asset_path("usage-orbit.ico")))
        except tk.TclError:
            pass

        self.reader = CodexTelemetryReader()
        self.history = UsageHistory()
        self.snapshot = UsageSnapshot()
        self.last_checked_at: Optional[float] = None
        self.last_alert_bucket: Optional[int] = None
        self.refresh_in_flight = False
        self.refresh_after_id: Optional[str] = None
        self.icon_image: Optional[tk.PhotoImage] = None
        self.tray_icon_percent: Optional[int] = None
        self.last_tray_tooltip: Optional[str] = None
        self.tray_popup = TrayMilestonePopup(self.root, self.settings)
        self.settings_window: Optional[tk.Toplevel] = None
        self.stats_window: Optional[tk.Toplevel] = None
        self.stats_canvas: Optional[tk.Canvas] = None
        self.stats_readout: Optional[tk.Label] = None
        self.stats_period_hours = 24 * 7
        self.stats_plot_points: list[dict[str, Any]] = []
        self.stats_plot_start = 0.0
        self.stats_plot_end = 0.0
        self.stats_plot_left = 50
        self.stats_plot_right = 625
        self.stats_usage_top = 128
        self.stats_usage_bottom = 286
        self.stats_rate_top = 343
        self.stats_rate_bottom = 508
        self.stats_rate_scale = 1.0
        self.stats_selected_timestamp: Optional[float] = None
        self.canvas = tk.Canvas(
            self.root,
            width=460,
            height=350,
            bg=COLORS["ink"],
            highlightthickness=0,
            bd=0,
        )
        self.canvas.pack(fill="both", expand=True)

        self.refresh_button = self._make_button("Refresh now", self.refresh_now, COLORS["panel_raised"], 22, 304, 100)
        self.hide_button = self._make_button("Hide to tray", self.hide_to_tray, COLORS["panel_raised"], 130, 304, 110)
        self.settings_button = self._make_button("Settings", self.open_settings, COLORS["panel_raised"], 248, 304, 86)
        self.dashboard_button = self._make_button("Dashboard", self.open_dashboard, COLORS["coral"], 342, 304, 96)
        self.stats_button = self._make_button("Stats", self.open_statistics, COLORS["panel_raised"], 314, 20, 58)

        self.context_menu = tk.Menu(
            self.root,
            tearoff=False,
            bg=COLORS["panel"],
            fg=COLORS["text"],
            activebackground=COLORS["violet"],
            activeforeground=COLORS["ink"],
            relief="flat",
            bd=0,
        )
        self.context_menu.add_command(label="Show counter", command=self.show_window)
        self.context_menu.add_command(label="Refresh now", command=self.refresh_now)
        self.context_menu.add_command(label="Statistics", command=self.open_statistics)
        self.context_menu.add_command(label="Settings", command=self.open_settings)
        self.context_menu.add_command(label="Open usage dashboard", command=self.open_dashboard)
        self.context_menu.add_separator()
        self.context_menu.add_command(label="Quit", command=self.quit)

        self.tray = TrayIcon(asset_path("usage-orbit.ico"), self._tray_action)
        self.tray.start("Codex Usage Counter")
        self.root.after(100, self._poll_tray)
        self.root.after(200, self.refresh_async)
        self.refresh_after_id = self.root.after(self.settings.refresh_interval_seconds * 1000, self._poll_refresh)
        self.root.after(30000, self._refresh_countdown)

    def _initial_geometry(self) -> str:
        try:
            width, height = 460, 350
            screen_w = self.root.winfo_screenwidth()
            screen_h = self.root.winfo_screenheight()
        except tk.TclError:
            return "460x350+40+40"
        return f"{width}x{height}+{max(20, screen_w - width - 36)}+{max(20, screen_h - height - 80)}"

    def _make_button(self, text: str, command: Any, background: str, x: int, y: int, width: int) -> tk.Button:
        button = tk.Button(
            self.root,
            text=text,
            command=command,
            bg=background,
            fg=COLORS["ink"] if background == COLORS["coral"] else COLORS["text"],
            activebackground=COLORS["violet"],
            activeforeground=COLORS["ink"],
            relief="flat",
            bd=0,
            highlightthickness=0,
            cursor="hand2",
            font=("Segoe UI", 9, "bold"),
        )
        button.place(x=x, y=y, width=width, height=30)
        return button

    def _rounded_rect(self, x1: int, y1: int, x2: int, y2: int, radius: int, fill: str, outline: str = "") -> None:
        self.canvas.create_rectangle(x1 + radius, y1, x2 - radius, y2, fill=fill, outline=outline)
        self.canvas.create_rectangle(x1, y1 + radius, x2, y2 - radius, fill=fill, outline=outline)
        self.canvas.create_oval(x1, y1, x1 + radius * 2, y1 + radius * 2, fill=fill, outline=outline)
        self.canvas.create_oval(x2 - radius * 2, y1, x2, y1 + radius * 2, fill=fill, outline=outline)
        self.canvas.create_oval(x1, y2 - radius * 2, x1 + radius * 2, y2, fill=fill, outline=outline)
        self.canvas.create_oval(x2 - radius * 2, y2 - radius * 2, x2, y2, fill=fill, outline=outline)

    def _display_percent(self, snapshot: UsageSnapshot) -> Optional[float]:
        if snapshot.used_percent is None:
            return None
        if self.settings.display_mode == "remaining":
            return 100 - snapshot.used_percent
        return snapshot.used_percent

    def _display_label(self) -> str:
        return "remaining" if self.settings.display_mode == "remaining" else "used"

    def _set_start_with_windows(self, enabled: bool) -> bool:
        if os.name != "nt":
            return not enabled

        shortcut_path = startup_shortcut_path()
        if not enabled:
            try:
                if shortcut_path.exists():
                    shortcut_path.unlink()
                return True
            except OSError:
                return False

        if getattr(sys, "_MEIPASS", None):
            target_path = Path(sys.executable).resolve()
            arguments = ""
            working_directory = target_path.parent
            icon_path = target_path
        else:
            source_path = Path(__file__).resolve()
            target_path = Path(sys.executable).resolve()
            pythonw_path = target_path.with_name("pythonw.exe")
            if pythonw_path.exists():
                target_path = pythonw_path
            arguments = f'"{source_path}"'
            working_directory = source_path.parent
            icon_path = asset_path("usage-orbit.ico")

        def powershell_string(value: str) -> str:
            return "'" + value.replace("'", "''") + "'"

        command = "\n".join(
            (
                "$ErrorActionPreference = 'Stop'",
                f"$shortcutPath = {powershell_string(str(shortcut_path))}",
                f"$targetPath = {powershell_string(str(target_path))}",
                f"$workingDirectory = {powershell_string(str(working_directory))}",
                f"$arguments = {powershell_string(arguments)}",
                f"$iconPath = {powershell_string(str(icon_path))}",
                "New-Item -ItemType Directory -Path (Split-Path -Parent $shortcutPath) -Force | Out-Null",
                "$shell = New-Object -ComObject WScript.Shell",
                "$shortcut = $shell.CreateShortcut($shortcutPath)",
                "$shortcut.TargetPath = $targetPath",
                "$shortcut.WorkingDirectory = $workingDirectory",
                "$shortcut.Arguments = $arguments",
                "$shortcut.Description = 'Always-on-top Codex usage counter'",
                "$shortcut.IconLocation = \"$iconPath,0\"",
                "$shortcut.Save()",
            )
        )
        startup_info = subprocess.STARTUPINFO()
        startup_info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup_info.wShowWindow = subprocess.SW_HIDE
        try:
            result = subprocess.run(
                ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                startupinfo=startup_info,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000),
                check=False,
            )
        except OSError:
            return False
        return result.returncode == 0

    def _play_sound_alert(self) -> None:
        if winsound is None:
            return
        sound_path = asset_path("milestone-alert.wav")
        try:
            if sound_path.exists():
                winsound.PlaySound(
                    str(sound_path),
                    winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT,
                )
            else:
                winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
        except (RuntimeError, OSError):
            return

    def _set_tray_icon(self, percent: Optional[int]) -> None:
        if percent == self.tray_icon_percent:
            return
        icon_name = "usage-orbit.ico" if percent is None else f"taskbar/usage-orbit-{percent:03d}.ico"
        icon_path = asset_path(icon_name)
        if not icon_path.exists():
            return
        self.tray.update_icon(icon_path)
        self.tray_icon_percent = percent

    def _draw(self) -> None:
        snapshot = self.snapshot
        display_percent = self._display_percent(snapshot)
        self.canvas.delete("all")

        self._rounded_rect(16, 16, 54, 54, 12, COLORS["panel_raised"])
        if self.icon_image is None:
            try:
                self.icon_image = tk.PhotoImage(file=str(asset_path("usage-orbit-64.png")))
            except tk.TclError:
                self.icon_image = None
        if self.icon_image:
            self.canvas.create_image(35, 35, image=self.icon_image, anchor="center")

        self.canvas.create_text(68, 23, text="CODEX USAGE", anchor="w", fill=COLORS["text"], font=("Segoe UI", 10, "bold"))
        self.canvas.create_text(68, 42, text="A quiet signal for your active allowance", anchor="w", fill=COLORS["muted"], font=("Segoe UI", 8))

        if not snapshot.has_data:
            status_text, status_color = "WAITING", COLORS["amber"]
        elif snapshot.is_stale:
            status_text, status_color = "STALE", COLORS["amber"]
        else:
            status_text, status_color = "LIVE", COLORS["mint"]
        self._rounded_rect(385, 22, 438, 48, 9, status_color)
        self.canvas.create_text(411, 35, text=status_text, fill=COLORS["ink"], font=("Segoe UI", 8, "bold"))

        self._rounded_rect(16, 72, 444, 246, 18, COLORS["panel"], COLORS["line"])

        # Gauge track and used segment.
        self.canvas.create_arc(34, 91, 190, 247, start=135, extent=-290, style="arc", outline=COLORS["line"], width=13)
        if display_percent is not None:
            extent = -290 * clamp(display_percent, 0, 100) / 100
            if self.settings.display_mode == "remaining":
                gauge_color = COLORS["mint"] if display_percent >= 20 else COLORS["coral"]
            else:
                gauge_color = COLORS["coral"] if display_percent >= 80 else COLORS["violet"]
            self.canvas.create_arc(34, 91, 190, 247, start=135, extent=extent, style="arc", outline=gauge_color, width=13)
            center_value = f"{display_percent:.0f}%"
        else:
            center_value = "—"
        self.canvas.create_text(112, 158, text=center_value, fill=COLORS["text"], font=("Segoe UI", 28, "bold"))
        self.canvas.create_text(112, 190, text=self._display_label(), fill=COLORS["muted"], font=("Segoe UI", 9))

        self.canvas.create_text(218, 98, text="CURRENT WINDOW", anchor="w", fill=COLORS["muted"], font=("Segoe UI", 8, "bold"))
        self.canvas.create_text(
            218,
            121,
            text=format_window(snapshot.window_minutes),
            anchor="w",
            fill=COLORS["text"],
            font=("Segoe UI", 16, "bold"),
        )
        self.canvas.create_text(218, 157, text="PLAN", anchor="w", fill=COLORS["muted"], font=("Segoe UI", 8, "bold"))
        self.canvas.create_text(
            218,
            180,
            text=(snapshot.plan_type or "—").upper(),
            anchor="w",
            fill=COLORS["mint"],
            font=("Segoe UI", 15, "bold"),
        )
        self.canvas.create_text(218, 215, text="RESETS IN", anchor="w", fill=COLORS["muted"], font=("Segoe UI", 8, "bold"))
        self.canvas.create_text(
            298,
            215,
            text=format_countdown(snapshot.resets_at),
            anchor="w",
            fill=COLORS["soft"],
            font=("Segoe UI", 9, "bold"),
        )

        if snapshot.error:
            footer = f"{snapshot.error}  ·  {format_updated(self.last_checked_at).replace('Updated ', 'Checked ', 1)}"
            footer_color = COLORS["amber"]
        else:
            checked = format_updated(self.last_checked_at).replace("Updated ", "Checked ", 1)
            signal = format_updated(snapshot.timestamp).replace("Updated ", "signal ", 1)
            footer = f"{checked}  ·  {signal}"
            footer_color = COLORS["muted"]
        self.canvas.create_text(20, 269, text=footer, anchor="w", fill=footer_color, font=("Segoe UI", 8))

        if display_percent is not None:
            tray_percent = int(round(clamp(display_percent, 0, 100)))
            self._set_tray_icon(tray_percent)
            taskbar_label = f"Codex Usage • {display_percent:.0f}% {self._display_label()}"
            tray_tip = f"Codex Usage • {display_percent:.0f}% {self._display_label()} • resets in {format_countdown(snapshot.resets_at)}"
        else:
            self._set_tray_icon(None)
            taskbar_label = "Codex Usage Counter"
            tray_tip = "Codex Usage Counter • open Codex to connect"
        self.root.title(taskbar_label)
        if tray_tip != self.last_tray_tooltip:
            self.tray.update_tooltip(tray_tip)
            self.last_tray_tooltip = tray_tip

    def _refresh_countdown(self) -> None:
        if self.root.winfo_exists():
            self._draw()
            self.root.after(30000, self._refresh_countdown)

    def _poll_refresh(self) -> None:
        self.refresh_after_id = None
        self.refresh_async()
        if self.root.winfo_exists():
            self.refresh_after_id = self.root.after(self.settings.refresh_interval_seconds * 1000, self._poll_refresh)

    def refresh_async(self) -> None:
        if self.refresh_in_flight:
            return
        self.refresh_in_flight = True
        self.refresh_button.configure(state="disabled", text="Reading…")

        def worker() -> None:
            try:
                result = self.reader.read()
            except Exception:
                # Keep the polling loop alive if a session file changes mid-read.
                result = UsageSnapshot(error="Local usage read failed; will retry")
            self.root.after(0, lambda: self._finish_refresh(result))

        threading.Thread(target=worker, name="codex-usage-reader", daemon=True).start()

    def _finish_refresh(self, result: UsageSnapshot) -> None:
        self.refresh_in_flight = False
        self.last_checked_at = time.time()
        self.refresh_button.configure(state="normal", text="Refresh now")
        self._maybe_show_milestone(result)
        self.history.record(result)
        self.snapshot = result
        self._draw()
        if self.stats_window is not None:
            self._render_statistics()

    def refresh_now(self) -> None:
        """Read immediately, independently of the two-minute polling timer."""

        self.refresh_async()

    def _maybe_show_milestone(self, result: UsageSnapshot) -> None:
        if result.used_percent is None:
            return
        bucket = int(result.used_percent // self.settings.milestone_step)
        if self.last_alert_bucket is None:
            # Establish the baseline silently on startup.
            self.last_alert_bucket = bucket
            return
        if bucket < self.last_alert_bucket:
            # A reset or window change moved usage backwards; re-arm quietly.
            self.last_alert_bucket = bucket
            return
        if bucket > self.last_alert_bucket:
            self.last_alert_bucket = bucket
            self.tray_popup.show(f"Usage reached {result.used_percent:.0f}%")
            if self.settings.sound_alert:
                self._play_sound_alert()

    def _poll_tray(self) -> None:
        self.tray.poll_actions()
        if self.root.winfo_exists():
            self.root.after(100, self._poll_tray)

    def _tray_action(self, action: str, value: Any) -> None:
        if action == "show":
            self.show_window()
        elif action == "menu" and isinstance(value, tuple):
            try:
                self.context_menu.tk_popup(value[0], value[1])
            finally:
                self.context_menu.grab_release()

    def show_window(self) -> None:
        self.root.deiconify()
        self.root.attributes("-topmost", self.settings.always_on_top)
        self.root.lift()
        self.root.focus_force()

    def hide_to_tray(self) -> None:
        self.root.withdraw()

    def open_dashboard(self) -> None:
        webbrowser.open(USAGE_DASHBOARD_URL)

    def open_statistics(self) -> None:
        if self.stats_window is not None:
            try:
                if self.stats_window.winfo_exists():
                    self.stats_window.lift()
                    self.stats_window.focus_force()
                    self._render_statistics()
                    return
            except tk.TclError:
                pass

        self.stats_period_hours = 24 * 7
        dialog = tk.Toplevel(self.root)
        self.stats_window = dialog
        dialog.title("Codex Usage Statistics")
        dialog.configure(bg=COLORS["ink"])
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.geometry("680x660")

        toolbar = tk.Frame(dialog, bg=COLORS["ink"])
        toolbar.pack(fill="x", padx=18, pady=(14, 7))
        tk.Label(
            toolbar,
            text="USAGE OVER TIME",
            bg=COLORS["ink"],
            fg=COLORS["text"],
            font=("Segoe UI", 11, "bold"),
        ).pack(side="left")
        tk.Label(
            toolbar,
            text="usage + smoothed rate",
            bg=COLORS["ink"],
            fg=COLORS["muted"],
            font=("Segoe UI", 8),
        ).pack(side="left", padx=(9, 0), pady=(2, 0))

        period_buttons = tk.Frame(toolbar, bg=COLORS["ink"])
        period_buttons.pack(side="right")
        button_style = {
            "bg": COLORS["panel_raised"],
            "fg": COLORS["text"],
            "activebackground": COLORS["violet"],
            "activeforeground": COLORS["ink"],
            "relief": "flat",
            "bd": 0,
            "highlightthickness": 0,
            "font": ("Segoe UI", 8, "bold"),
        }
        tk.Button(period_buttons, text="3 hours", command=lambda: self.set_stats_period(3), **button_style).pack(
            side="left", padx=(0, 5), ipadx=7, ipady=3
        )
        tk.Button(period_buttons, text="24 hours", command=lambda: self.set_stats_period(24), **button_style).pack(
            side="left", padx=(0, 5), ipadx=7, ipady=3
        )
        tk.Button(period_buttons, text="12 hours", command=lambda: self.set_stats_period(12), **button_style).pack(
            side="left", padx=(0, 5), ipadx=7, ipady=3
        )
        tk.Button(period_buttons, text="7 days", command=lambda: self.set_stats_period(24 * 7), **button_style).pack(
            side="left", ipadx=7, ipady=3
        )
        tk.Button(period_buttons, text="30 days", command=lambda: self.set_stats_period(24 * 30), **button_style).pack(
            side="left", padx=(5, 0), ipadx=7, ipady=3
        )

        self.stats_readout = tk.Label(
            dialog,
            text="Click or drag across the plot to inspect a point",
            bg=COLORS["ink"],
            fg=COLORS["muted"],
            anchor="w",
            font=("Segoe UI", 8),
        )
        self.stats_readout.pack(fill="x", padx=18, pady=(0, 4))

        self.stats_canvas = tk.Canvas(
            dialog,
            width=644,
            height=560,
            bg=COLORS["panel"],
            highlightthickness=0,
            bd=0,
        )
        self.stats_canvas.pack(padx=18, pady=(0, 18))
        self.stats_canvas.bind("<Button-1>", self._select_statistics_point)
        self.stats_canvas.bind("<B1-Motion>", self._select_statistics_point)
        dialog.update_idletasks()
        width = dialog.winfo_width()
        height = dialog.winfo_height()
        x = max(0, (dialog.winfo_screenwidth() - width) // 2)
        y = max(0, (dialog.winfo_screenheight() - height) // 2)
        dialog.geometry(f"{width}x{height}+{x}+{y}")
        dialog.protocol("WM_DELETE_WINDOW", self.close_statistics)
        self._render_statistics()

    def set_stats_period(self, hours: int) -> None:
        self.stats_period_hours = hours
        self.stats_selected_timestamp = None
        self._render_statistics()

    def close_statistics(self) -> None:
        if self.stats_window is not None:
            try:
                self.stats_window.destroy()
            except tk.TclError:
                pass
        self.stats_window = None
        self.stats_canvas = None
        self.stats_readout = None
        self.stats_plot_points = []
        self.stats_selected_timestamp = None

    def _select_statistics_point(self, event: Any) -> None:
        if not self.stats_plot_points or self.stats_plot_end <= self.stats_plot_start:
            return
        x = clamp(float(event.x), self.stats_plot_left, self.stats_plot_right)
        fraction = (x - self.stats_plot_left) / (self.stats_plot_right - self.stats_plot_left)
        target = self.stats_plot_start + fraction * (self.stats_plot_end - self.stats_plot_start)
        selected = min(self.stats_plot_points, key=lambda point: abs(point["timestamp"] - target))
        self.stats_selected_timestamp = selected["timestamp"]
        self._draw_statistics_selection()

    def _draw_statistics_selection(self) -> None:
        canvas = self.stats_canvas
        if canvas is None:
            return
        canvas.delete("stats-selection")
        if self.stats_selected_timestamp is None or not self.stats_plot_points:
            if self.stats_readout is not None:
                self.stats_readout.configure(text="Click or drag across the plot to inspect a point")
            return
        selected = min(
            self.stats_plot_points,
            key=lambda point: abs(point["timestamp"] - self.stats_selected_timestamp),
        )
        fraction = (selected["timestamp"] - self.stats_plot_start) / max(1, self.stats_plot_end - self.stats_plot_start)
        x = self.stats_plot_left + clamp(fraction, 0, 1) * (self.stats_plot_right - self.stats_plot_left)
        canvas.create_line(
            x,
            self.stats_usage_top - 6,
            x,
            self.stats_rate_bottom + 4,
            fill=COLORS["soft"],
            dash=(4, 3),
            width=1,
            tags="stats-selection",
        )
        used = selected.get("used_percent")
        rate = selected.get("rate_per_hour")
        if used is not None:
            usage_y = self.stats_usage_bottom - (used / 100) * (self.stats_usage_bottom - self.stats_usage_top)
            canvas.create_oval(
                x - 5,
                usage_y - 5,
                x + 5,
                usage_y + 5,
                fill=COLORS["mint"],
                outline=COLORS["ink"],
                width=2,
                tags="stats-selection",
            )
        if rate is not None:
            rate_y = self.stats_rate_top + ((self.stats_rate_scale - rate) / (2 * self.stats_rate_scale)) * (self.stats_rate_bottom - self.stats_rate_top)
            canvas.create_oval(
                x - 5,
                rate_y - 5,
                x + 5,
                rate_y + 5,
                fill=COLORS["amber"],
                outline=COLORS["ink"],
                width=2,
                tags="stats-selection",
            )
        local = datetime.fromtimestamp(selected["timestamp"]).astimezone()
        when = f"{local.strftime('%b %d')} {local.strftime('%I:%M %p').lstrip('0')}"
        usage_text = f"usage {used:.1f}%" if used is not None else "usage n/a"
        rate_text = f"rate {rate:+.2f} pts/hr" if rate is not None else "rate collecting"
        if self.stats_readout is not None:
            self.stats_readout.configure(text=f"{when}  |  {usage_text}  |  {rate_text}")

    def _render_statistics_legacy(self) -> None:
        canvas = self.stats_canvas
        if canvas is None:
            return
        canvas.delete("all")

        raw_points = self.history.since(self.stats_period_hours)
        graph_points = self.history.hourly(self.stats_period_hours)
        current = raw_points[-1]["used_percent"] if raw_points else self.snapshot.used_percent
        peak = max((point["used_percent"] for point in raw_points), default=current)
        remaining = 100 - current if current is not None else None
        if len(raw_points) >= 2:
            elapsed_hours = max((raw_points[-1]["timestamp"] - raw_points[0]["timestamp"]) / 3600, 1 / 60)
            rate = (raw_points[-1]["used_percent"] - raw_points[0]["used_percent"]) / elapsed_hours
            rate_text = f"{rate:+.1f} pts/hr"
        else:
            rate_text = "Collecting"

        cards = [
            ("USED NOW", f"{current:.0f}%" if current is not None else "—", COLORS["violet"]),
            ("REMAINING", f"{remaining:.0f}%" if remaining is not None else "—", COLORS["mint"]),
            ("PEAK", f"{peak:.0f}%" if peak is not None else "—", COLORS["coral"]),
            ("RATE", rate_text, COLORS["amber"]),
        ]
        card_width = 146
        for index, (label, value, color) in enumerate(cards):
            x1 = 14 + index * 156
            x2 = x1 + card_width
            canvas.create_rectangle(x1, 14, x2, 76, fill=COLORS["panel_raised"], outline=COLORS["line"])
            canvas.create_text(x1 + 12, 29, text=label, anchor="w", fill=COLORS["muted"], font=("Segoe UI", 8, "bold"))
            canvas.create_text(x1 + 12, 55, text=value, anchor="w", fill=color, font=("Segoe UI", 14, "bold"))

        canvas.create_text(
            18,
            96,
            text=f"HOURLY TREND  ·  LAST {self.stats_period_hours} HOURS",
            anchor="w",
            fill=COLORS["soft"],
            font=("Segoe UI", 8, "bold"),
        )
        left, top, right, bottom = 50, 120, 625, 365
        for level in (0, 25, 50, 75, 100):
            y = bottom - (level / 100) * (bottom - top)
            canvas.create_line(left, y, right, y, fill=COLORS["line"], width=1)
            canvas.create_text(left - 9, y, text=f"{level}%", anchor="e", fill=COLORS["muted"], font=("Segoe UI", 8))

        start_time = time.time() - self.stats_period_hours * 3600
        end_time = time.time()
        span = max(1, end_time - start_time)
        if graph_points:
            coordinates: list[float] = []
            for point in graph_points:
                x = left + clamp((point["timestamp"] - start_time) / span, 0, 1) * (right - left)
                y = bottom - (point["used_percent"] / 100) * (bottom - top)
                coordinates.extend((x, y))
            if len(coordinates) >= 4:
                canvas.create_line(*coordinates, fill=COLORS["violet"], width=3)
            latest_x, latest_y = coordinates[-2:]
            canvas.create_oval(latest_x - 5, latest_y - 5, latest_x + 5, latest_y + 5, fill=COLORS["mint"], outline=COLORS["ink"], width=2)
        else:
            canvas.create_text(
                (left + right) / 2,
                (top + bottom) / 2,
                text="Keep the counter running to build this graph.",
                fill=COLORS["muted"],
                font=("Segoe UI", 10),
            )

        def axis_label(epoch: float) -> str:
            local = datetime.fromtimestamp(epoch).astimezone()
            if self.stats_period_hours <= 24:
                return local.strftime("%I %p").lstrip("0")
            return local.strftime("%b %d")

        for fraction in (0, 0.5, 1):
            x = left + fraction * (right - left)
            canvas.create_text(x, bottom + 18, text=axis_label(start_time + fraction * span), fill=COLORS["muted"], font=("Segoe UI", 8))

    def _render_statistics(self) -> None:
        canvas = self.stats_canvas
        if canvas is None:
            return
        canvas.delete("all")

        raw_points = self.history.since(self.stats_period_hours)
        usage_points = raw_points
        rate_points = self.history.rate_series(self.stats_period_hours, usage_points)
        current = raw_points[-1]["used_percent"] if raw_points else self.snapshot.used_percent
        remaining = 100 - current if current is not None else None
        current_rate = rate_points[-1]["rate_per_hour"] if rate_points else None

        def rate_text(value: Optional[float]) -> str:
            return f"{value:+.1f} pts/hr" if value is not None else "Collecting"

        def axis_label(epoch: float) -> str:
            local = datetime.fromtimestamp(epoch).astimezone()
            if self.stats_period_hours <= 24:
                return local.strftime("%I %p").lstrip("0")
            return f"{local.strftime('%b %d')} {local.strftime('%I %p').lstrip('0')}"

        def eta_text() -> str:
            if current is None or current_rate is None or current_rate <= 0 or current >= 100:
                return "n/a"
            hours_to_limit = (100 - current) / current_rate
            if hours_to_limit < 1:
                return f"{max(1, int(round(hours_to_limit * 60)))}m"
            if hours_to_limit < 24:
                return f"{hours_to_limit:.1f}h"
            return f"{hours_to_limit / 24:.1f}d"

        cards = [
            ("USED NOW", f"{current:.0f}%" if current is not None else "--", COLORS["violet"]),
            ("REMAINING", f"{remaining:.0f}%" if remaining is not None else "--", COLORS["mint"]),
            ("RATE NOW", rate_text(current_rate), COLORS["amber"]),
            ("ETA", eta_text(), COLORS["mint"]),
        ]
        card_width = 146
        for index, (label, value, color) in enumerate(cards):
            x1 = 14 + index * 156
            x2 = x1 + card_width
            canvas.create_rectangle(x1, 14, x2, 76, fill=COLORS["panel_raised"], outline=COLORS["line"])
            canvas.create_text(x1 + 12, 29, text=label, anchor="w", fill=COLORS["muted"], font=("Segoe UI", 8, "bold"))
            canvas.create_text(x1 + 12, 55, text=value, anchor="w", fill=color, font=("Segoe UI", 12, "bold"))

        start_time = time.time() - self.stats_period_hours * 3600
        end_time = time.time()
        span = max(1, end_time - start_time)
        left, right = 50, 625
        self.stats_plot_start = start_time
        self.stats_plot_end = end_time
        self.stats_plot_left = left
        self.stats_plot_right = right
        plot_by_timestamp: dict[float, dict[str, Any]] = {}
        for point in usage_points:
            plot_by_timestamp.setdefault(point["timestamp"], {}).update(
                {"timestamp": point["timestamp"], "used_percent": point["used_percent"]}
            )
        for point in rate_points:
            plot_by_timestamp.setdefault(point["timestamp"], {}).update(
                {"timestamp": point["timestamp"], "rate_per_hour": point["rate_per_hour"]}
            )
        self.stats_plot_points = [plot_by_timestamp[key] for key in sorted(plot_by_timestamp)]

        def x_for(epoch: float) -> float:
            return left + clamp((epoch - start_time) / span, 0, 1) * (right - left)

        canvas.create_text(18, 96, text="USAGE + RATE - MINUTE-LEVEL SAMPLES", anchor="w", fill=COLORS["soft"], font=("Segoe UI", 8, "bold"))
        canvas.create_text(625, 96, text="usage % left | rate pts/hr right", anchor="e", fill=COLORS["muted"], font=("Segoe UI", 8))
        scope_label = {24 * 7: "7 DAYS", 24 * 30: "30 DAYS"}.get(self.stats_period_hours, f"{self.stats_period_hours} HOURS")
        canvas.create_text(18, 111, text=f"LAST {scope_label}", anchor="w", fill=COLORS["muted"], font=("Segoe UI", 8))
        canvas.create_text(625, 111, text="3-hour regression + exponential smoothing", anchor="e", fill=COLORS["muted"], font=("Segoe UI", 8))

        plot_top, plot_bottom = 128, 508
        self.stats_usage_top = plot_top
        self.stats_usage_bottom = plot_bottom
        self.stats_rate_top = plot_top
        self.stats_rate_bottom = plot_bottom
        for level in (0, 25, 50, 75, 100):
            y = plot_bottom - (level / 100) * (plot_bottom - plot_top)
            canvas.create_line(left, y, right, y, fill=COLORS["line"], width=1)
            canvas.create_text(left - 9, y, text=f"{level}%", anchor="e", fill=COLORS["muted"], font=("Segoe UI", 8))

        if usage_points:
            coordinates: list[float] = []
            for point in usage_points:
                coordinates.extend((x_for(point["timestamp"]), plot_bottom - (point["used_percent"] / 100) * (plot_bottom - plot_top)))
            if len(coordinates) >= 4:
                canvas.create_line(*coordinates, fill=COLORS["violet"], width=3)
            latest_x, latest_y = coordinates[-2:]
            canvas.create_oval(latest_x - 5, latest_y - 5, latest_x + 5, latest_y + 5, fill=COLORS["mint"], outline=COLORS["ink"], width=2)
        else:
            canvas.create_text((left + right) / 2, (plot_top + plot_bottom) / 2, text="Keep the counter running to build this graph.", fill=COLORS["muted"], font=("Segoe UI", 10))

        rate_values = [point["rate_per_hour"] for point in rate_points]
        rate_scale = max(1.0, max((abs(value) for value in rate_values), default=1.0) * 1.25)
        self.stats_rate_scale = rate_scale
        for value in (rate_scale, 0, -rate_scale):
            y = plot_top + ((rate_scale - value) / (2 * rate_scale)) * (plot_bottom - plot_top)
            canvas.create_line(left, y, right, y, fill=COLORS["soft"] if value == 0 else COLORS["line"], width=2 if value == 0 else 1)
            sign = "+" if value > 0 else ""
            canvas.create_text(right + 8, y, text=f"{sign}{value:.1f}", anchor="w", fill=COLORS["muted"], font=("Segoe UI", 8))
        if rate_points:
            rate_coordinates: list[float] = []
            for point in rate_points:
                y = plot_top + ((rate_scale - point["rate_per_hour"]) / (2 * rate_scale)) * (plot_bottom - plot_top)
                rate_coordinates.extend((x_for(point["timestamp"]), y))
            if len(rate_coordinates) >= 4:
                canvas.create_line(*rate_coordinates, fill=COLORS["amber"], width=3)
        else:
            canvas.create_text((left + right) / 2, (plot_top + plot_bottom) / 2, text="More samples are needed to estimate a trend rate.", fill=COLORS["muted"], font=("Segoe UI", 10))

        for fraction in (0, 0.5, 1):
            x = left + fraction * (right - left)
            canvas.create_text(x, plot_bottom + 18, text=axis_label(start_time + fraction * span), fill=COLORS["muted"], font=("Segoe UI", 8))
        self._draw_statistics_selection()

    def open_settings(self) -> None:
        if self.settings_window is not None:
            try:
                if self.settings_window.winfo_exists():
                    self.settings_window.lift()
                    self.settings_window.focus_force()
                    return
            except tk.TclError:
                pass

        dialog = tk.Toplevel(self.root)
        self.settings_window = dialog
        dialog.title("Codex Usage Settings")
        dialog.configure(bg=COLORS["panel"])
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog_width, dialog_height = 390, 480
        self.root.update_idletasks()
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        root_x = self.root.winfo_x()
        root_y = self.root.winfo_y()
        root_height = self.root.winfo_height()
        x = int(clamp(root_x + 35, 20, max(20, screen_width - dialog_width - 20)))
        above_y = root_y - dialog_height - 12
        below_y = root_y + root_height + 12
        usable_bottom = screen_height - 48
        if above_y >= 20:
            y = above_y
        elif below_y + dialog_height <= usable_bottom:
            y = below_y
        else:
            y = max(20, usable_bottom - dialog_height)
        dialog.geometry(f"{dialog_width}x{dialog_height}+{x}+{y}")

        body = tk.Frame(dialog, bg=COLORS["panel"], padx=18, pady=16)
        body.pack(fill="both", expand=True)
        tk.Label(
            body,
            text="DISPLAY VALUE",
            bg=COLORS["panel"],
            fg=COLORS["muted"],
            font=("Segoe UI", 8, "bold"),
        ).pack(anchor="w")
        tk.Label(
            body,
            text="Choose what the counter emphasizes.",
            bg=COLORS["panel"],
            fg=COLORS["soft"],
            font=("Segoe UI", 9),
        ).pack(anchor="w", pady=(3, 9))

        mode_var = tk.StringVar(value=self.settings.display_mode)
        radio_style = {
            "bg": COLORS["panel"],
            "fg": COLORS["text"],
            "activebackground": COLORS["panel"],
            "activeforeground": COLORS["text"],
            "selectcolor": COLORS["panel_raised"],
            "highlightthickness": 0,
            "font": ("Segoe UI", 10),
        }
        tk.Radiobutton(body, text="Used", variable=mode_var, value="used", **radio_style).pack(anchor="w")
        tk.Radiobutton(body, text="Remaining", variable=mode_var, value="remaining", **radio_style).pack(anchor="w")

        topmost_var = tk.BooleanVar(value=self.settings.always_on_top)
        tk.Checkbutton(
            body,
            text="Keep counter always on top",
            variable=topmost_var,
            bg=COLORS["panel"],
            fg=COLORS["text"],
            activebackground=COLORS["panel"],
            activeforeground=COLORS["text"],
            selectcolor=COLORS["panel_raised"],
            highlightthickness=0,
            font=("Segoe UI", 10),
        ).pack(anchor="w", pady=(11, 0))

        startup_var = tk.BooleanVar(value=self.settings.start_with_windows)
        tk.Checkbutton(
            body,
            text="Start with Windows",
            variable=startup_var,
            bg=COLORS["panel"],
            fg=COLORS["text"],
            activebackground=COLORS["panel"],
            activeforeground=COLORS["text"],
            selectcolor=COLORS["panel_raised"],
            highlightthickness=0,
            font=("Segoe UI", 10),
        ).pack(anchor="w", pady=(5, 0))

        sound_var = tk.BooleanVar(value=self.settings.sound_alert)
        tk.Checkbutton(
            body,
            text="Play a sound at milestones",
            variable=sound_var,
            bg=COLORS["panel"],
            fg=COLORS["text"],
            activebackground=COLORS["panel"],
            activeforeground=COLORS["text"],
            selectcolor=COLORS["panel_raised"],
            highlightthickness=0,
            font=("Segoe UI", 10),
        ).pack(anchor="w", pady=(5, 0))

        popup_section = tk.Frame(body, bg=COLORS["panel"])
        popup_section.pack(fill="x", pady=(14, 0))
        tk.Label(
            popup_section,
            text="TRAY MILESTONE POPUP",
            bg=COLORS["panel"],
            fg=COLORS["muted"],
            font=("Segoe UI", 8, "bold"),
        ).pack(anchor="w")
        tk.Label(
            popup_section,
            text="Customize the trigger interval and popup duration.",
            bg=COLORS["panel"],
            fg=COLORS["soft"],
            font=("Segoe UI", 9),
        ).pack(anchor="w", pady=(3, 8))

        step_var = tk.StringVar(value=f"{self.settings.milestone_step}%")
        duration_label = "second" if self.settings.milestone_duration == 1 else "seconds"
        duration_var = tk.StringVar(value=f"{self.settings.milestone_duration} {duration_label}")
        refresh_options = {
            15: "15 seconds",
            30: "30 seconds",
            60: "1 minute",
            120: "2 minutes",
            300: "5 minutes",
            600: "10 minutes",
            900: "15 minutes",
        }
        refresh_var = tk.StringVar(value=refresh_options[self.settings.refresh_interval_seconds])
        option_style = {
            "bg": COLORS["panel_raised"],
            "fg": COLORS["text"],
            "activebackground": COLORS["violet"],
            "activeforeground": COLORS["ink"],
            "highlightthickness": 0,
            "relief": "flat",
            "bd": 0,
            "font": ("Segoe UI", 9),
        }
        popup_options = (
            ("Trigger every", step_var, ("1%", "5%", "10%", "20%", "25%", "50%")),
            ("Show for", duration_var, ("1 second", "2 seconds", "5 seconds", "10 seconds")),
            (
                "Check every",
                refresh_var,
                ("15 seconds", "30 seconds", "1 minute", "2 minutes", "5 minutes", "10 minutes", "15 minutes"),
            ),
        )
        for label_text, variable, values in popup_options:
            row = tk.Frame(popup_section, bg=COLORS["panel"])
            row.pack(fill="x", pady=2)
            tk.Label(
                row,
                text=label_text,
                bg=COLORS["panel"],
                fg=COLORS["text"],
                font=("Segoe UI", 9),
            ).pack(side="left")
            menu_button = tk.OptionMenu(row, variable, *values)
            menu_button.configure(**option_style)
            menu_button["menu"].configure(
                bg=COLORS["panel_raised"],
                fg=COLORS["text"],
                activebackground=COLORS["violet"],
                activeforeground=COLORS["ink"],
                bd=0,
                relief="flat",
                font=("Segoe UI", 9),
            )
            menu_button.pack(side="right", ipadx=8)

        buttons = tk.Frame(body, bg=COLORS["panel"])
        buttons.pack(fill="x", side="bottom", pady=(15, 0))

        def close_dialog() -> None:
            try:
                dialog.grab_release()
            except tk.TclError:
                pass
            dialog.destroy()
            self.settings_window = None

        def apply_dialog() -> None:
            start_with_windows = bool(startup_var.get())
            if not self._set_start_with_windows(start_with_windows):
                messagebox.showerror(
                    "Startup setting",
                    "Windows could not update the Startup shortcut. Check your Windows permissions and try again.",
                    parent=dialog,
                )
                return
            self.settings.display_mode = mode_var.get()
            self.settings.always_on_top = bool(topmost_var.get())
            self.settings.start_with_windows = start_with_windows
            self.settings.sound_alert = bool(sound_var.get())
            self.settings.milestone_step = int(step_var.get().rstrip("%"))
            self.settings.milestone_duration = int(duration_var.get().split()[0])
            self.settings.refresh_interval_seconds = next(
                seconds for seconds, label in refresh_options.items() if label == refresh_var.get()
            )
            if self.snapshot.used_percent is not None:
                self.last_alert_bucket = int(self.snapshot.used_percent // self.settings.milestone_step)
            self.settings.save()
            self.root.attributes("-topmost", self.settings.always_on_top)
            if self.refresh_after_id:
                self.root.after_cancel(self.refresh_after_id)
            self.refresh_after_id = self.root.after(
                self.settings.refresh_interval_seconds * 1000,
                self._poll_refresh,
            )
            self._draw()
            close_dialog()

        tk.Button(
            buttons,
            text="Cancel",
            command=close_dialog,
            bg=COLORS["panel_raised"],
            fg=COLORS["text"],
            activebackground=COLORS["violet"],
            activeforeground=COLORS["ink"],
            relief="flat",
            bd=0,
            highlightthickness=0,
            font=("Segoe UI", 9, "bold"),
        ).pack(side="right", padx=(8, 0), ipadx=10, ipady=4)
        tk.Button(
            buttons,
            text="Apply",
            command=apply_dialog,
            bg=COLORS["coral"],
            fg=COLORS["ink"],
            activebackground=COLORS["violet"],
            activeforeground=COLORS["ink"],
            relief="flat",
            bd=0,
            highlightthickness=0,
            font=("Segoe UI", 9, "bold"),
        ).pack(side="right", ipadx=10, ipady=4)
        dialog.protocol("WM_DELETE_WINDOW", close_dialog)
        dialog.grab_set()

    def quit(self) -> None:
        try:
            self.tray_popup.close()
            self.close_statistics()
            self.tray.stop()
        finally:
            self.root.destroy()

    def run(self) -> None:
        self._draw()
        self.root.mainloop()


def main() -> None:
    if not acquire_single_instance():
        return
    app = UsageApp()
    app.run()


if __name__ == "__main__":
    main()
