from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
from bisect import bisect_left, bisect_right
import json
import math
import os
import queue
import subprocess
import sys
import threading
import time
import webbrowser
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
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
ACTIVE_SIGNAL_MAX_AGE_SECONDS = 10 * 60
RATE_WINDOW_MINUTES = 45
RATE_MIN_POINTS = 2
RATE_MIN_SPAN_SECONDS = 60
TOKEN_RATE_WINDOW_MINUTES = 5
SIGNAL_WATCH_INTERVAL_MS = 1000
TRANSIENT_DROP_RECOVERY_MINUTES = 5
RESET_TIME_TOLERANCE_SECONDS = 2
STARTUP_SHORTCUT_NAME = "Codex Usage Counter.lnk"
FIVE_HOUR_WINDOW_MINUTES = 5 * 60
WEEKLY_WINDOW_MINUTES = 7 * 24 * 60
STATS_CARD_TOP = 10.0
STATS_CARD_HEIGHT = 42.0
STATS_CARD_ROW_GAP = 4.0
STATS_CARD_LABEL_OFFSET_Y = 11.0
STATS_CARD_VALUE_OFFSET_Y = 29.0
STATS_PLOT_TOP = 192.0
STATS_WIDE_MIN_WIDTH = 1100
STATS_WIDE_CARD_COLUMNS = 6
STATS_NARROW_CARD_COLUMNS = 4
STATS_MIN_HOURLY_ZOOM_MINUTES = 1

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
    "cyan": "#62e6ef",
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


_SHOW_EVENT_NAME = "Local\\CodexUsageCounter.ShowWindow"
_EVENT_MODIFY_STATE = 0x0002
_WAIT_OBJECT_0 = 0x00000000
_instance_mutex: Optional[int] = None
_show_event: Optional[int] = None


def signal_existing_instance() -> None:
    if os.name != "nt":
        return
    event = _kernel32.OpenEventW(_EVENT_MODIFY_STATE, False, _SHOW_EVENT_NAME)
    if event:
        _kernel32.SetEvent(event)
        _kernel32.CloseHandle(event)


def acquire_single_instance() -> bool:
    """Keep startup shortcuts and manual launches from creating duplicate counters."""

    global _instance_mutex, _show_event
    if os.name != "nt":
        return True
    handle = _kernel32.CreateMutexW(None, False, "Local\\CodexUsageCounter")
    if not handle:
        return True
    if _kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        _kernel32.CloseHandle(handle)
        signal_existing_instance()
        return False
    _instance_mutex = handle
    _show_event = _kernel32.CreateEventW(None, False, False, _SHOW_EVENT_NAME)
    return True


def clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def nice_positive_scale(maximum: float) -> float:
    """Choose a compact, zero-based chart scale with modest headroom."""

    if not math.isfinite(maximum) or maximum <= 0:
        return 1.0
    target = maximum * 1.12
    magnitude = 10 ** math.floor(math.log10(target))
    for multiplier in (1.0, 2.0, 2.5, 5.0, 10.0):
        candidate = multiplier * magnitude
        if candidate >= target:
            return candidate
    return 10.0 * magnitude


def format_rate_axis(value: float, scale: float) -> str:
    """Format a pace-axis tick without hiding small but meaningful rates."""

    if scale >= 10:
        decimals = 0
    elif scale >= 1:
        decimals = 1
    elif scale >= 0.1:
        decimals = 2
    else:
        decimals = 3
    return f"+{value:.{decimals}f}/hr"


def number(value: Any) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def metadata_text(value: Any) -> Optional[str]:
    """Keep a short, non-content session metadata value when it is present."""

    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned[:80] if cleaned else None


def format_model_effort(model: Any, effort: Any) -> str:
    """Format the active execution context without exposing session content."""

    values = [metadata_text(model), metadata_text(effort)]
    return " · ".join(value for value in values if value) or "context unavailable"


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


def format_statistics_span(hours: float) -> str:
    """Describe a detailed chart window with the smallest useful local-history unit."""

    minutes = max(STATS_MIN_HOURLY_ZOOM_MINUTES, int(round(hours * 60)))
    if minutes < 60:
        return f"{minutes} MINUTE" if minutes == 1 else f"{minutes} MINUTES"
    if minutes % (24 * 60) == 0:
        days = minutes // (24 * 60)
        return f"{days} DAY" if days == 1 else f"{days} DAYS"
    if minutes % 60 == 0:
        whole_hours = minutes // 60
        return f"{whole_hours} HOUR" if whole_hours == 1 else f"{whole_hours} HOURS"
    whole_hours, remainder_minutes = divmod(minutes, 60)
    return f"{whole_hours}H {remainder_minutes:02d}M"


def format_token_count(value: Optional[float]) -> str:
    if value is None or not math.isfinite(value) or value < 0:
        return "--"
    if value >= 1_000_000_000:
        return f"{value / 1_000_000_000:.1f}B"
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"{value / 1_000:.1f}K"
    return f"{value:.0f}"


def format_token_rate(value: Optional[float]) -> str:
    formatted = format_token_count(value)
    return "--" if formatted == "--" else f"{formatted}/min"


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
        self.points: list[dict[str, Any]] = self._load()
        self._last_saved_at = 0.0
        self._daily_cache_key: Optional[tuple[Any, ...]] = None
        self._daily_cache: list[dict[str, Any]] = []

    @staticmethod
    def _point(item: Any) -> Optional[dict[str, Any]]:
        if not isinstance(item, dict):
            return None
        timestamp = number(item.get("timestamp"))
        used_percent = number(item.get("used_percent"))
        if timestamp is None or used_percent is None or not math.isfinite(timestamp) or not math.isfinite(used_percent):
            return None
        point: dict[str, Any] = {"timestamp": timestamp, "used_percent": clamp(used_percent, 0, 100)}
        resets_at = number(item.get("resets_at"))
        window_minutes = number(item.get("window_minutes"))
        if resets_at is not None and math.isfinite(resets_at):
            point["resets_at"] = resets_at
        if window_minutes is not None and math.isfinite(window_minutes) and window_minutes > 0:
            point["window_minutes"] = window_minutes
        five_hour_used = number(item.get("five_hour_used_percent"))
        five_hour_resets = number(item.get("five_hour_resets_at"))
        five_hour_minutes = number(item.get("five_hour_window_minutes"))
        if five_hour_used is not None and math.isfinite(five_hour_used):
            point["five_hour_used_percent"] = clamp(five_hour_used, 0, 100)
        if five_hour_resets is not None and math.isfinite(five_hour_resets):
            point["five_hour_resets_at"] = five_hour_resets
        if five_hour_minutes is not None and math.isfinite(five_hour_minutes) and five_hour_minutes > 0:
            point["five_hour_window_minutes"] = five_hour_minutes
        for field in (
            "input_tokens",
            "cached_input_tokens",
            "output_tokens",
            "reasoning_tokens",
            "total_tokens",
            "last_tokens",
            "context_window",
        ):
            value = number(item.get(field))
            if value is not None and math.isfinite(value) and value >= 0:
                point[field] = value
        session_id = item.get("session_id")
        if isinstance(session_id, str) and session_id:
            point["session_id"] = session_id
        model = metadata_text(item.get("model"))
        effort = metadata_text(item.get("reasoning_effort"))
        if model is not None:
            point["model"] = model
        if effort is not None:
            point["reasoning_effort"] = effort
        return point

    @staticmethod
    def _same_limit_window(first: dict[str, Any], second: dict[str, Any]) -> bool:
        first_reset = first.get("resets_at")
        second_reset = second.get("resets_at")
        if first_reset is None or second_reset is None:
            return False
        if abs(first_reset - second_reset) > RESET_TIME_TOLERANCE_SECONDS:
            return False
        if max(first["timestamp"], second["timestamp"]) > max(first_reset, second_reset) + RESET_TIME_TOLERANCE_SECONDS:
            return False
        first_minutes = first.get("window_minutes")
        second_minutes = second.get("window_minutes")
        return first_minutes is None or second_minutes is None or first_minutes == second_minutes

    @staticmethod
    def _different_limit_window(first: dict[str, Any], second: dict[str, Any]) -> bool:
        first_reset = first.get("resets_at")
        second_reset = second.get("resets_at")
        if first_reset is not None and second_reset is not None:
            return abs(first_reset - second_reset) > RESET_TIME_TOLERANCE_SECONDS
        first_minutes = first.get("window_minutes")
        second_minutes = second.get("window_minutes")
        return first_minutes is not None and second_minutes is not None and first_minutes != second_minutes

    @classmethod
    def _sanitize(cls, points: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Remove stale-session dips without hiding a real allowance reset."""

        valid_points: list[dict[str, Any]] = []
        for item in points:
            point = cls._point(item)
            if point is not None:
                valid_points.append(point)

        ordered: list[dict[str, Any]] = []
        for point in sorted(valid_points, key=lambda candidate: candidate["timestamp"]):
            if ordered:
                previous = ordered[-1]
                same_minute = int(previous["timestamp"] // 60) == int(point["timestamp"] // 60)
                same_usage = math.isclose(previous["used_percent"], point["used_percent"], abs_tol=0.001)
                previous_reset = previous.get("resets_at")
                point_reset = point.get("resets_at")
                same_reset = (
                    previous_reset is None and point_reset is None
                ) or (
                    previous_reset is not None
                    and point_reset is not None
                    and abs(previous_reset - point_reset) <= RESET_TIME_TOLERANCE_SECONDS
                )
                same_window = previous.get("window_minutes") == point.get("window_minutes")
                same_five_hour = (
                    previous.get("five_hour_used_percent") == point.get("five_hour_used_percent")
                    and previous.get("five_hour_resets_at") == point.get("five_hour_resets_at")
                    and previous.get("five_hour_window_minutes") == point.get("five_hour_window_minutes")
                )
                same_session = previous.get("session_id") == point.get("session_id")
                same_context = (
                    previous.get("model") == point.get("model")
                    and previous.get("reasoning_effort") == point.get("reasoning_effort")
                )
                if same_minute and same_usage and same_reset and same_window and same_five_hour and same_session and same_context:
                    ordered[-1] = point
                    continue
            ordered.append(point)
        cleaned: list[dict[str, Any]] = []
        recovery_seconds = TRANSIENT_DROP_RECOVERY_MINUTES * 60
        for index, point in enumerate(ordered):
            if cleaned and point["used_percent"] < cleaned[-1]["used_percent"]:
                previous = cleaned[-1]
                if cls._same_limit_window(previous, point):
                    continue
                if not cls._different_limit_window(previous, point):
                    deadline = point["timestamp"] + recovery_seconds
                    recovered = False
                    for future in ordered[index + 1 :]:
                        if future["timestamp"] > deadline or cls._different_limit_window(previous, future):
                            break
                        if future["used_percent"] >= previous["used_percent"]:
                            recovered = True
                            break
                    if recovered:
                        continue
            cleaned.append(point)
        return cleaned[-HISTORY_MAX_POINTS:]

    @classmethod
    def _load(cls) -> list[dict[str, Any]]:
        try:
            payload = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return []
        if not isinstance(payload, list):
            return []
        return cls._sanitize(payload)

    def record(self, snapshot: UsageSnapshot) -> None:
        if snapshot.used_percent is None or snapshot.is_stale:
            return
        now = time.time()
        timestamp = snapshot.timestamp if snapshot.timestamp is not None else now
        if not math.isfinite(timestamp):
            return
        point: dict[str, Any] = {"timestamp": timestamp, "used_percent": clamp(float(snapshot.used_percent), 0, 100)}
        if snapshot.resets_at is not None and math.isfinite(snapshot.resets_at):
            point["resets_at"] = float(snapshot.resets_at)
        if snapshot.window_minutes is not None and snapshot.window_minutes > 0:
            point["window_minutes"] = float(snapshot.window_minutes)
        if snapshot.five_hour_used_percent is not None and math.isfinite(snapshot.five_hour_used_percent):
            point["five_hour_used_percent"] = clamp(float(snapshot.five_hour_used_percent), 0, 100)
        if snapshot.five_hour_resets_at is not None and math.isfinite(snapshot.five_hour_resets_at):
            point["five_hour_resets_at"] = float(snapshot.five_hour_resets_at)
        if snapshot.five_hour_window_minutes is not None and snapshot.five_hour_window_minutes > 0:
            point["five_hour_window_minutes"] = float(snapshot.five_hour_window_minutes)
        for field in (
            "input_tokens",
            "cached_input_tokens",
            "output_tokens",
            "reasoning_tokens",
            "total_tokens",
            "last_tokens",
            "context_window",
        ):
            value = getattr(snapshot, field)
            if value is not None and math.isfinite(value) and value >= 0:
                point[field] = float(value)
        if snapshot.source_path:
            point["session_id"] = Path(snapshot.source_path).stem
        if snapshot.model:
            point["model"] = snapshot.model
        if snapshot.reasoning_effort:
            point["reasoning_effort"] = snapshot.reasoning_effort

        point_bucket = int(timestamp // 60)
        started_new_bucket = not self.points
        if self.points:
            latest = self.points[-1]
            latest_bucket = int(latest["timestamp"] // 60)
            started_new_bucket = point_bucket != latest_bucket
            if point_bucket < latest_bucket:
                return
            if latest["timestamp"] > timestamp:
                return
            if latest["timestamp"] == timestamp:
                if latest == point:
                    return
                candidate_points = [*self.points[:-1], point]
            else:
                candidate_points = [*self.points, point]
        else:
            candidate_points = [point]

        updated_points = self._sanitize(candidate_points)
        if updated_points == self.points:
            return
        cutoff = now - HISTORY_RETENTION_DAYS * 24 * 60 * 60
        self.points = [item for item in updated_points if item["timestamp"] >= cutoff][-HISTORY_MAX_POINTS:]
        self._daily_cache_key = None
        self._daily_cache = []
        self.save(force=started_new_bucket)

    def save(self, force: bool = False) -> None:
        now = time.time()
        if not force and now - self._last_saved_at < 30:
            return
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            HISTORY_FILE.write_text(json.dumps(self.points), encoding="utf-8")
            self._last_saved_at = now
        except OSError:
            pass

    def since(self, hours: float) -> list[dict[str, Any]]:
        cutoff = time.time() - hours * 60 * 60
        return [item for item in self.points if item["timestamp"] >= cutoff]

    def hourly(self, hours: float) -> list[dict[str, Any]]:
        """Collapse samples to the latest reading in each local hour bucket."""

        buckets: dict[int, dict[str, Any]] = {}
        for point in self.since(hours):
            bucket = int(point["timestamp"] // 3600) * 3600
            buckets[bucket] = point
        return [buckets[key] for key in sorted(buckets)]

    def chart_points(self, hours: float) -> list[dict[str, Any]]:
        """Return the minute-level series used by every statistics range."""

        return self._sanitize(self.since(hours))

    def daily_statistics(self, days: int = HISTORY_RETENTION_DAYS) -> list[dict[str, Any]]:
        """Aggregate retained samples into stock-chart-style local calendar days."""

        days = max(1, int(days))
        now_local = datetime.now().astimezone()
        latest = self.points[-1] if self.points else {}
        cache_key = (
            days,
            now_local.date().isoformat(),
            len(self.points),
            latest.get("timestamp"),
            latest.get("used_percent"),
            latest.get("five_hour_used_percent"),
            latest.get("resets_at"),
            latest.get("five_hour_resets_at"),
            latest.get("total_tokens"),
            latest.get("session_id"),
        )
        if getattr(self, "_daily_cache_key", None) == cache_key:
            return self._daily_cache
        today_start = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
        range_start = today_start - timedelta(days=days - 1)
        range_start_epoch = range_start.timestamp()
        all_points = self._sanitize(self.points)
        points = [point for point in all_points if point["timestamp"] >= range_start_epoch]
        if not points:
            self._daily_cache_key = cache_key
            self._daily_cache = []
            return []

        def day_start_for(timestamp: float) -> float:
            local = datetime.fromtimestamp(timestamp).astimezone()
            return local.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()

        buckets: dict[float, list[dict[str, Any]]] = {}
        for point in points:
            buckets.setdefault(day_start_for(point["timestamp"]), []).append(point)

        def mean(values: list[float]) -> Optional[float]:
            return sum(values) / len(values) if values else None

        def bucket_values(
            series: list[dict[str, Any]],
            field: str,
        ) -> dict[float, list[float]]:
            result: dict[float, list[float]] = {}
            for item in series:
                value = number(item.get(field))
                if value is None or item["timestamp"] < range_start_epoch:
                    continue
                result.setdefault(day_start_for(item["timestamp"]), []).append(value)
            return result

        usage_totals: dict[str, dict[float, float]] = {
            "used_percent": {},
            "five_hour_used_percent": {},
        }
        for value_field, resets_field in (
            ("used_percent", "resets_at"),
            ("five_hour_used_percent", "five_hour_resets_at"),
        ):
            previous: Optional[dict[str, Any]] = None
            for point in all_points:
                current_value = number(point.get(value_field))
                if current_value is None:
                    continue
                day_start = day_start_for(point["timestamp"])
                delta = 0.0
                if previous is not None:
                    previous_value = number(previous.get(value_field))
                    previous_reset = number(previous.get(resets_field))
                    current_reset = number(point.get(resets_field))
                    reset_changed = (
                        previous_reset is not None
                        and current_reset is not None
                        and abs(current_reset - previous_reset) > RESET_TIME_TOLERANCE_SECONDS
                    )
                    if reset_changed:
                        delta = current_value
                    elif previous_value is not None and current_value >= previous_value:
                        delta = current_value - previous_value
                if day_start >= range_start_epoch and delta > 0:
                    daily_totals = usage_totals[value_field]
                    daily_totals[day_start] = daily_totals.get(day_start, 0.0) + delta
                previous = point

        hours = days * 24 + 24
        weekly_rates = bucket_values(self.rate_series(hours, all_points), "rate_per_hour")
        five_hour_rates = bucket_values(
            self.rate_series(hours, all_points, value_field="five_hour_used_percent"),
            "rate_per_hour",
        )
        token_rates = bucket_values(self.token_rate_series(hours, all_points), "token_rate_per_minute")

        token_totals: dict[float, float] = {}
        previous_tokens_by_session: dict[str, float] = {}
        for point in all_points:
            session_id = point.get("session_id")
            token_total = number(point.get("total_tokens"))
            if not isinstance(session_id, str) or not session_id or token_total is None:
                continue
            previous_total = previous_tokens_by_session.get(session_id)
            delta = token_total if previous_total is None else max(0.0, token_total - previous_total)
            day_start = day_start_for(point["timestamp"])
            if day_start >= range_start_epoch and delta > 0:
                token_totals[day_start] = token_totals.get(day_start, 0.0) + delta
            previous_tokens_by_session[session_id] = token_total

        daily_points: list[dict[str, Any]] = []
        for day_start in sorted(buckets):
            day_points = buckets[day_start]
            day_local = datetime.fromtimestamp(day_start).astimezone()
            next_day_start = (day_local + timedelta(days=1)).timestamp()
            weekly_levels = [float(point["used_percent"]) for point in day_points]
            five_hour_levels = [
                float(point["five_hour_used_percent"])
                for point in day_points
                if number(point.get("five_hour_used_percent")) is not None
            ]
            weekly_rate_values = weekly_rates.get(day_start, [])
            five_hour_rate_values = five_hour_rates.get(day_start, [])
            token_rate_values = token_rates.get(day_start, [])
            point: dict[str, Any] = {
                "timestamp": day_start + (next_day_start - day_start) / 2,
                "day_start": day_start,
                "daily": True,
                "used_percent": usage_totals["used_percent"].get(day_start, 0.0),
                "five_hour_used_percent": usage_totals["five_hour_used_percent"].get(day_start, 0.0),
                "daily_weekly_average": mean(weekly_levels),
                "daily_five_hour_average": mean(five_hour_levels),
                "rate_per_hour": mean(weekly_rate_values),
                "five_hour_rate_per_hour": mean(five_hour_rate_values),
                "daily_weekly_peak_rate": max(weekly_rate_values) if weekly_rate_values else None,
                "daily_five_hour_peak_rate": max(five_hour_rate_values) if five_hour_rate_values else None,
                "daily_total_tokens": token_totals.get(day_start, 0.0),
                "total_tokens": token_totals.get(day_start, 0.0),
                "token_rate_per_minute": mean(token_rate_values),
                "daily_peak_token_rate": max(token_rate_values) if token_rate_values else None,
                "daily_samples": len(day_points),
                "daily_span_seconds": max(0.0, day_points[-1]["timestamp"] - day_points[0]["timestamp"]),
            }
            last_tokens = number(day_points[-1].get("last_tokens"))
            if last_tokens is not None:
                point["last_tokens"] = last_tokens
            last_model = next((metadata_text(item.get("model")) for item in reversed(day_points) if metadata_text(item.get("model"))), None)
            last_effort = next(
                (metadata_text(item.get("reasoning_effort")) for item in reversed(day_points) if metadata_text(item.get("reasoning_effort"))),
                None,
            )
            if last_model is not None:
                point["model"] = last_model
            if last_effort is not None:
                point["reasoning_effort"] = last_effort
            daily_points.append(point)
        self._daily_cache_key = cache_key
        self._daily_cache = daily_points
        return daily_points

    def weekly_statistics(self, days: int = HISTORY_RETENTION_DAYS) -> list[dict[str, Any]]:
        """Aggregate the daily history into local calendar weeks for the Weekly view."""

        daily_points = self.daily_statistics(days)
        if not daily_points:
            return []

        def week_start_for(timestamp: float) -> float:
            local = datetime.fromtimestamp(timestamp).astimezone()
            day_start = local.replace(hour=0, minute=0, second=0, microsecond=0)
            return (day_start - timedelta(days=day_start.weekday())).timestamp()

        def mean(values: list[float]) -> Optional[float]:
            return sum(values) / len(values) if values else None

        buckets: dict[float, list[dict[str, Any]]] = {}
        for point in daily_points:
            day_start = number(point.get("day_start"))
            if day_start is not None:
                buckets.setdefault(week_start_for(day_start), []).append(point)

        weekly_points: list[dict[str, Any]] = []
        for week_start in sorted(buckets):
            week_points = buckets[week_start]
            week_end = week_start_for(week_start + 7 * 24 * 60 * 60 - 1) + 7 * 24 * 60 * 60

            def values_for(field: str) -> list[float]:
                return [value for item in week_points if (value := number(item.get(field))) is not None]

            point: dict[str, Any] = {
                "timestamp": week_start + (week_end - week_start) / 2,
                "week_start": week_start,
                "weekly": True,
                "used_percent": sum(values_for("used_percent")),
                "five_hour_used_percent": sum(values_for("five_hour_used_percent")),
                "daily_weekly_average": mean(values_for("daily_weekly_average")),
                "daily_five_hour_average": mean(values_for("daily_five_hour_average")),
                "rate_per_hour": mean(values_for("rate_per_hour")),
                "five_hour_rate_per_hour": mean(values_for("five_hour_rate_per_hour")),
                "daily_weekly_peak_rate": max(values_for("daily_weekly_peak_rate"), default=None),
                "daily_five_hour_peak_rate": max(values_for("daily_five_hour_peak_rate"), default=None),
                "daily_total_tokens": sum(values_for("daily_total_tokens")),
                "total_tokens": sum(values_for("daily_total_tokens")),
                "token_rate_per_minute": mean(values_for("token_rate_per_minute")),
                "daily_peak_token_rate": max(values_for("daily_peak_token_rate"), default=None),
                "daily_samples": sum(int(number(item.get("daily_samples")) or 0) for item in week_points),
                "daily_span_seconds": sum(number(item.get("daily_span_seconds")) or 0.0 for item in week_points),
            }
            for field in ("last_tokens", "model", "reasoning_effort"):
                value = next((item.get(field) for item in reversed(week_points) if item.get(field) is not None), None)
                if value is not None:
                    point[field] = value
            weekly_points.append(point)
        return weekly_points

    @staticmethod
    def _slope(points: list[dict[str, Any]]) -> Optional[float]:
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
        hours: float,
        points: Optional[list[dict[str, Any]]] = None,
        value_field: str = "used_percent",
    ) -> list[dict[str, Any]]:
        """Return recent trend rates using a reset-aware regression plus EMA."""

        points = self._sanitize(points if points is not None else self.chart_points(hours))
        points = [point for point in points if number(point.get(value_field)) is not None]
        series: list[dict[str, Any]] = []
        smoothed: Optional[float] = None
        segment = 0
        if not points:
            return series

        origin = points[0]["timestamp"]
        window: deque[tuple[dict[str, Any], float, float]] = deque()
        sum_x = 0.0
        sum_y = 0.0
        sum_x_squared = 0.0
        sum_xy = 0.0

        for point in points:
            cutoff = point["timestamp"] - RATE_WINDOW_MINUTES * 60
            had_window = bool(window)
            while window and window[0][0]["timestamp"] < cutoff:
                _, old_x, old_y = window.popleft()
                sum_x -= old_x
                sum_y -= old_y
                sum_x_squared -= old_x * old_x
                sum_xy -= old_x * old_y
            if had_window and not window:
                smoothed = None
                segment += 1

            if window and float(point[value_field]) < float(window[-1][0][value_field]):
                window.clear()
                sum_x = 0.0
                sum_y = 0.0
                sum_x_squared = 0.0
                sum_xy = 0.0
                smoothed = None
                segment += 1

            x = (point["timestamp"] - origin) / 3600
            y = float(point[value_field])
            window.append((point, x, y))
            sum_x += x
            sum_y += y
            sum_x_squared += x * x
            sum_xy += x * y

            count = len(window)
            denominator = count * sum_x_squared - sum_x * sum_x
            elapsed_seconds = point["timestamp"] - window[0][0]["timestamp"]
            if count < RATE_MIN_POINTS or elapsed_seconds < RATE_MIN_SPAN_SECONDS or denominator <= 0:
                continue
            raw_rate = (count * sum_xy - sum_x * sum_y) / denominator
            if not math.isfinite(raw_rate):
                continue
            raw_rate = max(0.0, raw_rate)
            smoothed = raw_rate if smoothed is None else smoothed * 0.65 + raw_rate * 0.35
            series.append(
                {
                    "timestamp": point["timestamp"],
                    "used_percent": y,
                    "value_field": value_field,
                    "rate_per_hour": smoothed,
                    "segment": float(segment),
                }
            )
        return series

    def token_rate_series(
        self,
        hours: float,
        points: Optional[list[dict[str, Any]]] = None,
    ) -> list[dict[str, Any]]:
        """Return an aligned, lightly smoothed current-task token rate."""

        points = self._sanitize(points if points is not None else self.chart_points(hours))
        series: list[dict[str, Any]] = []
        previous: Optional[dict[str, Any]] = None
        smoothed: Optional[float] = None
        for point in points:
            token_total = number(point.get("total_tokens"))
            session_id = point.get("session_id")
            if token_total is None or not session_id:
                continue
            if previous is None or previous.get("session_id") != session_id:
                previous = point
                smoothed = None
                continue
            previous_total = number(previous.get("total_tokens"))
            elapsed_seconds = point["timestamp"] - previous["timestamp"]
            if (
                previous_total is None
                or elapsed_seconds <= 0
                or elapsed_seconds > TOKEN_RATE_WINDOW_MINUTES * 60
                or token_total < previous_total
            ):
                previous = point
                smoothed = None
                continue
            raw_rate = (token_total - previous_total) * 60 / elapsed_seconds
            if math.isfinite(raw_rate):
                smoothed = raw_rate if smoothed is None else smoothed * 0.6 + raw_rate * 0.4
                series.append(
                    {
                        "timestamp": point["timestamp"],
                        "token_rate_per_minute": max(0.0, smoothed),
                        "total_tokens": token_total,
                        "session_id": session_id,
                    }
                )
            previous = point
        return series

    def token_efficiency(
        self,
        hours: float,
        points: Optional[list[dict[str, Any]]] = None,
        value_field: str = "used_percent",
        resets_field: str = "resets_at",
    ) -> Optional[float]:
        """Return observed current-task tokens per allowance percentage point."""

        points = self._sanitize(points if points is not None else self.chart_points(hours))
        token_points = [point for point in points if number(point.get("total_tokens")) is not None and point.get("session_id")]
        if len(token_points) < 2:
            return None
        latest = token_points[-1]
        cutoff = latest["timestamp"] - RATE_WINDOW_MINUTES * 60
        latest_usage = number(latest.get(value_field))
        latest_reset = number(latest.get(resets_field))
        if latest_usage is None or latest_reset is None:
            return None
        candidates = [
            point
            for point in token_points
            if point["timestamp"] >= cutoff
            and point.get("session_id") == latest.get("session_id")
            and number(point.get(resets_field)) is not None
            and abs(float(point[resets_field]) - latest_reset) <= RESET_TIME_TOLERANCE_SECONDS
            and number(point.get(value_field)) is not None
            and float(point[value_field]) < latest_usage
        ]
        if not candidates:
            return None
        baseline = candidates[0]
        token_delta = float(latest["total_tokens"]) - float(baseline["total_tokens"])
        usage_delta = latest_usage - float(baseline[value_field])
        if token_delta <= 0 or usage_delta <= 0:
            return None
        return token_delta / usage_delta


@dataclass(frozen=True)
class UsageSnapshot:
    used_percent: Optional[float] = None
    window_minutes: Optional[int] = None
    resets_at: Optional[float] = None
    five_hour_used_percent: Optional[float] = None
    five_hour_window_minutes: Optional[int] = None
    five_hour_resets_at: Optional[float] = None
    plan_type: Optional[str] = None
    timestamp: Optional[float] = None
    source_path: Optional[str] = None
    input_tokens: Optional[float] = None
    cached_input_tokens: Optional[float] = None
    output_tokens: Optional[float] = None
    reasoning_tokens: Optional[float] = None
    total_tokens: Optional[float] = None
    last_tokens: Optional[float] = None
    context_window: Optional[float] = None
    model: Optional[str] = None
    reasoning_effort: Optional[str] = None
    error: Optional[str] = None

    @property
    def has_data(self) -> bool:
        return self.used_percent is not None or self.five_hour_used_percent is not None

    @property
    def is_stale(self) -> bool:
        return bool(self.timestamp and time.time() - self.timestamp > ACTIVE_SIGNAL_MAX_AGE_SECONDS)


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
        self._latest_snapshot: Optional[UsageSnapshot] = None
        self._watch_signatures: dict[str, tuple[int, int]] = {}
        self._watch_day_mtime_ns: Optional[int] = None
        self._full_context_scanned_paths: set[str] = set()

    @staticmethod
    def _tail_lines(path: Path, max_bytes: int = 4 * 1024 * 1024) -> list[str]:
        try:
            size = path.stat().st_size
            scan_bytes = min(size, min(max_bytes, 384 * 1024))
            data = b""
            while True:
                with path.open("rb") as handle:
                    start = max(0, size - scan_bytes)
                    handle.seek(start)
                    if start:
                        handle.readline()
                    data = handle.read()
                has_limits = b'"rate_limits"' in data
                has_context = b'"model"' in data and (b'"reasoning_effort"' in data or b'"effort"' in data)
                if (has_limits and has_context) or scan_bytes >= size or scan_bytes >= max_bytes:
                    break
                scan_bytes = min(size, max_bytes, scan_bytes * 2)
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

    @staticmethod
    def _event_context(event: dict[str, Any]) -> tuple[Optional[str], Optional[str]]:
        """Extract only model/effort metadata from known Codex session structures."""

        payload = event.get("payload")
        if not isinstance(payload, dict):
            return None, None

        thread_settings = payload.get("thread_settings")
        if not isinstance(thread_settings, dict):
            thread_settings = {}
        collaboration = payload.get("collaboration_mode")
        if not isinstance(collaboration, dict):
            collaboration = {}
        collaboration_settings = collaboration.get("settings")
        if not isinstance(collaboration_settings, dict):
            collaboration_settings = {}
        thread_collaboration = thread_settings.get("collaboration_mode")
        if not isinstance(thread_collaboration, dict):
            thread_collaboration = {}
        thread_collaboration_settings = thread_collaboration.get("settings")
        if not isinstance(thread_collaboration_settings, dict):
            thread_collaboration_settings = {}
        state = payload.get("state")
        if not isinstance(state, dict):
            state = {}
        provenance = payload.get("base_instructions")
        if not isinstance(provenance, dict):
            provenance = {}
        provenance = provenance.get("provenance")
        if not isinstance(provenance, dict):
            provenance = {}

        model = next(
            (
                candidate
                for candidate in (
                    metadata_text(payload.get("model")),
                    metadata_text(thread_settings.get("model")),
                    metadata_text(collaboration_settings.get("model")),
                    metadata_text(thread_collaboration_settings.get("model")),
                    metadata_text(state.get("model")),
                    metadata_text(provenance.get("model")),
                )
                if candidate is not None
            ),
            None,
        )
        effort = next(
            (
                candidate
                for candidate in (
                    metadata_text(payload.get("reasoning_effort")),
                    metadata_text(payload.get("effort")),
                    metadata_text(thread_settings.get("reasoning_effort")),
                    metadata_text(collaboration_settings.get("reasoning_effort")),
                    metadata_text(thread_collaboration_settings.get("reasoning_effort")),
                )
                if candidate is not None
            ),
            None,
        )
        return model, effort

    def _full_file_context(self, path: Path) -> tuple[Optional[str], Optional[str], float]:
        """Find the latest model/effort state once when an active file's tail lacks it."""

        latest_model: Optional[str] = None
        latest_effort: Optional[str] = None
        latest_timestamp = 0.0
        try:
            with path.open("r", encoding="utf-8", errors="ignore") as handle:
                for line in handle:
                    if not any(key in line for key in ('"model"', '"reasoning_effort"', '"effort"')):
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    model, effort = self._event_context(event)
                    if model is None and effort is None:
                        continue
                    timestamp = parse_timestamp(event.get("timestamp")) or 0.0
                    if model is not None:
                        latest_model = model
                    if effort is not None:
                        latest_effort = effort
                    latest_timestamp = max(latest_timestamp, timestamp)
        except (OSError, UnicodeError):
            pass
        return latest_model, latest_effort, latest_timestamp

    @staticmethod
    def _allowance_windows(limits: dict[str, Any]) -> tuple[Optional[dict[str, Any]], Optional[dict[str, Any]]]:
        """Return the 5-hour and weekly windows using duration, not field position."""

        windows = [limits.get("primary"), limits.get("secondary")]
        windows = [window for window in windows if isinstance(window, dict)]
        five_hour = next(
            (window for window in windows if int(number(window.get("window_minutes")) or 0) == FIVE_HOUR_WINDOW_MINUTES),
            None,
        )
        weekly = next(
            (window for window in windows if int(number(window.get("window_minutes")) or 0) == WEEKLY_WINDOW_MINUTES),
            None,
        )
        if five_hour is None:
            five_hour = next(
                (
                    window
                    for window in windows
                    if 0 < int(number(window.get("window_minutes")) or 0) <= FIVE_HOUR_WINDOW_MINUTES
                ),
                None,
            )
        if weekly is None:
            longer = [
                window
                for window in windows
                if int(number(window.get("window_minutes")) or 0) > FIVE_HOUR_WINDOW_MINUTES
            ]
            if longer:
                weekly = max(longer, key=lambda window: int(number(window.get("window_minutes")) or 0))
            elif len(windows) == 1 and number(windows[0].get("window_minutes")) is None:
                weekly = windows[0]
        return five_hour, weekly

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

    def _current_day_directory(self) -> Path:
        local = datetime.now()
        return self.sessions_dir / f"{local.year:04d}" / f"{local.month:02d}" / f"{local.day:02d}"

    def _capture_watch_state(self, cache: dict[str, tuple[int, int, Optional[UsageSnapshot]]]) -> None:
        self._watch_signatures = {
            path: (entry[0], entry[1])
            for path, entry in list(cache.items())[:8]
        }
        try:
            self._watch_day_mtime_ns = self._current_day_directory().stat().st_mtime_ns
        except OSError:
            self._watch_day_mtime_ns = None

    def local_signal_changed(self) -> bool:
        """Cheaply detect active-session appends between scheduled full scans."""

        for path_text, signature in self._watch_signatures.items():
            try:
                metadata = Path(path_text).stat()
            except OSError:
                return True
            if (metadata.st_mtime_ns, metadata.st_size) != signature:
                return True
        try:
            day_mtime = self._current_day_directory().stat().st_mtime_ns
        except OSError:
            day_mtime = None
        return day_mtime != self._watch_day_mtime_ns

    def _read_file(
        self,
        path: Path,
        file_mtime: float,
        fallback_snapshot: Optional[UsageSnapshot] = None,
        scan_full_context: bool = False,
    ) -> Optional[UsageSnapshot]:
        """Read allowance/token records plus model and effort metadata, never message text."""

        latest: Optional[UsageSnapshot] = None
        latest_token_values: dict[str, Optional[float]] = {}
        latest_model = fallback_snapshot.model if fallback_snapshot is not None else None
        latest_effort = fallback_snapshot.reasoning_effort if fallback_snapshot is not None else None
        latest_context_timestamp = 0.0
        for line in self._tail_lines(path):
            contains_limits = '"rate_limits"' in line
            contains_context = any(key in line for key in ('"model"', '"reasoning_effort"', '"effort"'))
            if not contains_limits and not contains_context:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            timestamp = parse_timestamp(event.get("timestamp")) or file_mtime
            if contains_context:
                model, effort = self._event_context(event)
                if model is not None:
                    latest_model = model
                    latest_context_timestamp = max(latest_context_timestamp, timestamp)
                if effort is not None:
                    latest_effort = effort
                    latest_context_timestamp = max(latest_context_timestamp, timestamp)
            if not contains_limits:
                continue
            limits = self._rate_limits(event)
            if not limits:
                continue
            five_hour, weekly = self._allowance_windows(limits)
            five_hour_used = number(five_hour.get("used_percent")) if five_hour else None
            weekly_used = number(weekly.get("used_percent")) if weekly else None
            if five_hour_used is None and weekly_used is None:
                continue
            payload = event.get("payload")
            info = payload.get("info") if isinstance(payload, dict) else None
            total_usage = info.get("total_token_usage") if isinstance(info, dict) else None
            last_usage = info.get("last_token_usage") if isinstance(info, dict) else None
            if not isinstance(total_usage, dict):
                total_usage = {}
            if not isinstance(last_usage, dict):
                last_usage = {}
            token_values = {
                "input_tokens": number(total_usage.get("input_tokens")),
                "cached_input_tokens": number(total_usage.get("cached_input_tokens")),
                "output_tokens": number(total_usage.get("output_tokens")),
                "reasoning_tokens": number(total_usage.get("reasoning_output_tokens")),
                "total_tokens": number(total_usage.get("total_tokens")),
                "last_tokens": number(last_usage.get("total_tokens")),
                "context_window": number(info.get("model_context_window")) if isinstance(info, dict) else None,
            }
            if token_values["total_tokens"] is not None:
                latest_token_values = token_values
            elif latest_token_values:
                token_values = latest_token_values
            snapshot = UsageSnapshot(
                used_percent=clamp(weekly_used, 0, 100) if weekly_used is not None else None,
                window_minutes=int(number(weekly.get("window_minutes")) or 0) or None if weekly else None,
                resets_at=number(weekly.get("resets_at")) if weekly else None,
                five_hour_used_percent=clamp(five_hour_used, 0, 100) if five_hour_used is not None else None,
                five_hour_window_minutes=int(number(five_hour.get("window_minutes")) or 0) or None if five_hour else None,
                five_hour_resets_at=number(five_hour.get("resets_at")) if five_hour else None,
                plan_type=str(limits.get("plan_type") or "").strip() or None,
                timestamp=timestamp,
                source_path=str(path),
                input_tokens=token_values.get("input_tokens"),
                cached_input_tokens=token_values.get("cached_input_tokens"),
                output_tokens=token_values.get("output_tokens"),
                reasoning_tokens=token_values.get("reasoning_tokens"),
                total_tokens=token_values.get("total_tokens"),
                last_tokens=token_values.get("last_tokens"),
                context_window=token_values.get("context_window"),
                model=latest_model,
                reasoning_effort=latest_effort,
            )
            if latest is None or timestamp > (latest.timestamp or 0):
                latest = snapshot
        if scan_full_context:
            model, effort, context_timestamp = self._full_file_context(path)
            if model is not None:
                latest_model = model
            if effort is not None:
                latest_effort = effort
            latest_context_timestamp = max(latest_context_timestamp, context_timestamp)
        if latest is None:
            latest = fallback_snapshot
        if latest is not None and (latest_model is not None or latest_effort is not None):
            latest = replace(
                latest,
                timestamp=max(latest.timestamp or 0, latest_context_timestamp) or latest.timestamp,
                model=latest_model or latest.model,
                reasoning_effort=latest_effort or latest.reasoning_effort,
            )
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
                snapshot = self._read_file(path, metadata.st_mtime, cached[2] if cached is not None else None)
                if (
                    cached is not None
                    and metadata.st_size > cached[1]
                    and cached[2] is not None
                    and (snapshot is None or (cached[2].timestamp or 0) > (snapshot.timestamp or 0))
                ):
                    snapshot = cached[2]
            next_cache[cache_key] = (signature[0], signature[1], snapshot)
            if snapshot is not None and snapshot.timestamp is not None:
                if latest is None or snapshot.timestamp > latest[0]:
                    latest = (snapshot.timestamp, snapshot)
        self._file_cache = next_cache
        self._capture_watch_state(next_cache)

        if latest:
            candidate = latest[1]
            if (
                (candidate.model is None or candidate.reasoning_effort is None)
                and candidate.source_path is not None
                and candidate.source_path not in self._full_context_scanned_paths
            ):
                source_path = Path(candidate.source_path)
                try:
                    source_mtime = source_path.stat().st_mtime
                except OSError:
                    source_mtime = 0.0
                refreshed = self._read_file(
                    source_path,
                    source_mtime,
                    candidate,
                    scan_full_context=True,
                )
                self._full_context_scanned_paths.add(candidate.source_path)
                if refreshed is not None:
                    candidate = refreshed
                    cached_entry = next_cache.get(str(source_path))
                    if cached_entry is not None:
                        next_cache[str(source_path)] = (cached_entry[0], cached_entry[1], candidate)
                        self._file_cache = next_cache
            previous = self._latest_snapshot
            if previous is not None:
                previous_timestamp = previous.timestamp or 0
                candidate_timestamp = candidate.timestamp or 0
                same_window = (
                    previous.resets_at is not None
                    and candidate.resets_at is not None
                    and abs(previous.resets_at - candidate.resets_at) <= RESET_TIME_TOLERANCE_SECONDS
                    and candidate_timestamp <= max(previous.resets_at, candidate.resets_at) + RESET_TIME_TOLERANCE_SECONDS
                    and (
                        previous.window_minutes is None
                        or candidate.window_minutes is None
                        or previous.window_minutes == candidate.window_minutes
                    )
                )
                if candidate_timestamp < previous_timestamp:
                    return previous
                if (
                    same_window
                    and previous.used_percent is not None
                    and candidate.used_percent is not None
                    and candidate.used_percent < previous.used_percent
                ):
                    candidate = replace(
                        candidate,
                        used_percent=previous.used_percent,
                        window_minutes=previous.window_minutes,
                        resets_at=previous.resets_at,
                    )
                same_five_hour_window = (
                    previous.five_hour_resets_at is not None
                    and candidate.five_hour_resets_at is not None
                    and abs(previous.five_hour_resets_at - candidate.five_hour_resets_at) <= RESET_TIME_TOLERANCE_SECONDS
                    and candidate_timestamp
                    <= max(previous.five_hour_resets_at, candidate.five_hour_resets_at) + RESET_TIME_TOLERANCE_SECONDS
                )
                if (
                    same_five_hour_window
                    and previous.five_hour_used_percent is not None
                    and candidate.five_hour_used_percent is not None
                    and candidate.five_hour_used_percent < previous.five_hour_used_percent
                ):
                    candidate = replace(
                        candidate,
                        five_hour_used_percent=previous.five_hour_used_percent,
                        five_hour_window_minutes=previous.five_hour_window_minutes,
                        five_hour_resets_at=previous.five_hour_resets_at,
                    )
            self._latest_snapshot = candidate
            return candidate
        if self._latest_snapshot is not None:
            return self._latest_snapshot
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

    # ctypes defaults Win32 function results to a 32-bit c_long. Explicitly
    # declare handle-returning APIs so 64-bit HWND/HICON values are not
    # truncated before they are handed to the Windows shell.
    _kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    _kernel32.CreateMutexW.restype = wintypes.HANDLE
    _kernel32.CreateEventW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
    _kernel32.CreateEventW.restype = wintypes.HANDLE
    _kernel32.OpenEventW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    _kernel32.OpenEventW.restype = wintypes.HANDLE
    _kernel32.SetEvent.argtypes = [wintypes.HANDLE]
    _kernel32.SetEvent.restype = wintypes.BOOL
    _kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    _kernel32.WaitForSingleObject.restype = wintypes.DWORD
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.CloseHandle.restype = wintypes.BOOL
    _kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
    _kernel32.GetModuleHandleW.restype = wintypes.HINSTANCE

    _user32.CreateWindowExW.argtypes = [
        wintypes.DWORD,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.HWND,
        wintypes.HMENU,
        wintypes.HINSTANCE,
        wintypes.LPVOID,
    ]
    _user32.CreateWindowExW.restype = wintypes.HWND
    _user32.LoadImageW.argtypes = [
        wintypes.HINSTANCE,
        wintypes.LPCWSTR,
        wintypes.UINT,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.UINT,
    ]
    _user32.LoadImageW.restype = wintypes.HICON
    _user32.DestroyIcon.argtypes = [wintypes.HICON]
    _user32.DestroyIcon.restype = wintypes.BOOL
    _user32.DestroyWindow.argtypes = [wintypes.HWND]
    _user32.DestroyWindow.restype = wintypes.BOOL
    _user32.RegisterClassW.argtypes = [ctypes.POINTER(_WNDCLASSW)]
    _user32.RegisterClassW.restype = wintypes.ATOM
    _user32.UnregisterClassW.argtypes = [wintypes.LPCWSTR, wintypes.HINSTANCE]
    _user32.UnregisterClassW.restype = wintypes.BOOL
    _user32.GetCursorPos.argtypes = [ctypes.POINTER(_POINT)]
    _user32.GetCursorPos.restype = wintypes.BOOL
    _user32.RegisterWindowMessageW.argtypes = [wintypes.LPCWSTR]
    _user32.RegisterWindowMessageW.restype = wintypes.UINT
    _user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    _user32.PostMessageW.restype = wintypes.BOOL
    _user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    _user32.DefWindowProcW.restype = _LRESULT
    _user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
    _user32.GetMessageW.restype = ctypes.c_int
    _user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
    _user32.TranslateMessage.restype = wintypes.BOOL
    _user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
    _user32.DispatchMessageW.restype = _LRESULT
    _user32.PostQuitMessage.argtypes = [ctypes.c_int]
    _shell32.Shell_NotifyIconW.argtypes = [wintypes.DWORD, ctypes.POINTER(_NOTIFYICONDATAW)]
    _shell32.Shell_NotifyIconW.restype = wintypes.BOOL


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
        self._taskbar_created_message = 0
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
        self._taskbar_created_message = _user32.RegisterWindowMessageW("TaskbarCreated")

        def wnd_proc(hwnd: int, msg: int, wparam: int, lparam: int) -> int:
            if msg == self._taskbar_created_message:
                self._add_icon(self._tooltip)
                return 0
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
        self.last_alert_buckets: dict[str, int] = {}
        self.refresh_in_flight = False
        self.refresh_after_id: Optional[str] = None
        self.icon_image: Optional[tk.PhotoImage] = None
        self.tray_icon_percent: Optional[int] = None
        self.last_tray_tooltip: Optional[str] = None
        self.tray_popup = TrayMilestonePopup(self.root, self.settings)
        self.settings_window: Optional[tk.Toplevel] = None
        self.stats_window: Optional[tk.Toplevel] = None
        self.stats_canvas: Optional[tk.Canvas] = None
        self.stats_canvas_size = (0, 0)
        self.stats_readout: Optional[tk.Label] = None
        self.stats_card_label_items: list[int] = []
        self.stats_card_value_items: list[int] = []
        self.stats_live_card_data: dict[str, Any] = {}
        self.stats_usage_points: list[dict[str, Any]] = []
        self.stats_rate_context_points: list[dict[str, Any]] = []
        self.stats_period_hours = 1.0
        self.stats_daily_view = False
        self.stats_weekly_view = False
        self.stats_plot_points: list[dict[str, Any]] = []
        self.stats_plot_start = 0.0
        self.stats_plot_end = 0.0
        self.stats_plot_left = 50
        self.stats_plot_right = 625
        self.stats_usage_top = 128
        self.stats_usage_bottom = 286
        self.stats_five_hour_usage_top = 128
        self.stats_five_hour_usage_bottom = 203
        self.stats_weekly_usage_top = 211
        self.stats_weekly_usage_bottom = 286
        self.stats_rate_top = 343
        self.stats_rate_bottom = 508
        self.stats_rate_scale = 1.0
        self.stats_daily_rate_scales: dict[str, float] = {"five_hour": 1.0, "weekly": 1.0}
        self.stats_daily_rate_lanes: dict[str, tuple[float, float]] = {
            "five_hour": (343.0, 421.0),
            "weekly": (429.0, 508.0),
        }
        self.stats_usage_scale = 100.0
        self.stats_token_top = 0
        self.stats_token_bottom = 0
        self.stats_token_scale = 1.0
        self.stats_selected_timestamp: Optional[float] = None
        self.canvas = tk.Canvas(
            self.root,
            width=460,
            height=440,
            bg=COLORS["ink"],
            highlightthickness=0,
            bd=0,
        )
        self.canvas.pack(fill="both", expand=True)

        self.refresh_button = self._make_button("Refresh now", self.refresh_now, COLORS["panel_raised"], 22, 394, 100)
        self.hide_button = self._make_button("Hide to tray", self.hide_to_tray, COLORS["panel_raised"], 130, 394, 110)
        self.settings_button = self._make_button("Settings", self.open_settings, COLORS["panel_raised"], 248, 394, 86)
        self.dashboard_button = self._make_button("Dashboard", self.open_dashboard, COLORS["coral"], 342, 394, 96)
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
        self.root.after(SIGNAL_WATCH_INTERVAL_MS, self._watch_local_signal)
        self.root.after(30000, self._refresh_countdown)
        self.root.after(250, self._poll_show_request)

    def _initial_geometry(self) -> str:
        try:
            width, height = 460, 440
            screen_w = self.root.winfo_screenwidth()
            screen_h = self.root.winfo_screenheight()
        except tk.TclError:
            return "460x440+40+40"
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
        available = [
            value
            for value in (snapshot.five_hour_used_percent, snapshot.used_percent)
            if value is not None
        ]
        if not available:
            return None
        # The tray number represents whichever allowance is closer to its limit.
        used_percent = max(available)
        if self.settings.display_mode == "remaining":
            return 100 - used_percent
        return used_percent

    def _display_value(self, used_percent: Optional[float]) -> Optional[float]:
        if used_percent is None:
            return None
        return 100 - used_percent if self.settings.display_mode == "remaining" else used_percent

    def _display_label(self) -> str:
        return "remaining" if self.settings.display_mode == "remaining" else "used"

    def _current_rate(self, value_field: str = "used_percent") -> Optional[float]:
        """Return the latest smoothed recent usage rate in percentage points per hour."""

        period_hours = max(1, (RATE_WINDOW_MINUTES + 59) // 60)
        points = self.history.chart_points(period_hours)
        rate_points = self.history.rate_series(period_hours, points, value_field=value_field)
        field_points = [point for point in points if number(point.get(value_field)) is not None]
        if not field_points or not rate_points or abs(rate_points[-1]["timestamp"] - field_points[-1]["timestamp"]) > 1:
            return None
        rate = rate_points[-1]["rate_per_hour"]
        return rate if math.isfinite(rate) else None

    def _current_token_rate(self) -> Optional[float]:
        points = self.history.chart_points(1)
        token_rates = self.history.token_rate_series(1, points)
        if not token_rates or self.snapshot.timestamp is None:
            return None
        latest = token_rates[-1]
        if latest.get("session_id") != (Path(self.snapshot.source_path).stem if self.snapshot.source_path else None):
            return None
        if self.snapshot.timestamp - latest["timestamp"] > ACTIVE_SIGNAL_MAX_AGE_SECONDS:
            return None
        return latest["token_rate_per_minute"]

    @staticmethod
    def _format_rate(rate: Optional[float]) -> str:
        return f"{rate:+.1f} pts/hr" if rate is not None and math.isfinite(rate) else "Collecting"

    @staticmethod
    def _format_eta(
        current: Optional[float],
        rate: Optional[float],
        resets_at: Optional[float] = None,
    ) -> str:
        if current is None or not math.isfinite(current):
            return "n/a"
        if current >= 100:
            return "now"
        if rate is None or not math.isfinite(rate) or rate <= 0:
            return "n/a"
        hours_to_limit = (100 - current) / rate
        if not math.isfinite(hours_to_limit) or hours_to_limit < 0:
            return "n/a"
        now = time.time()
        if (
            resets_at is not None
            and math.isfinite(resets_at)
            and resets_at > now
            and now + hours_to_limit * 3600 >= resets_at
        ):
            return "after reset"
        if hours_to_limit >= 24 * 365:
            return ">1y"
        if hours_to_limit < 1:
            return f"{max(1, int(round(hours_to_limit * 60)))}m"
        if hours_to_limit < 24:
            return f"{hours_to_limit:.1f}h"
        return f"{hours_to_limit / 24:.1f}d"

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

        if not snapshot.has_data:
            status_text, status_color = "WAITING", COLORS["amber"]
        elif snapshot.is_stale:
            status_text, status_color = "STALE", COLORS["amber"]
        else:
            status_text, status_color = "LIVE", COLORS["mint"]
        self._rounded_rect(385, 22, 438, 48, 9, status_color)
        self.canvas.create_text(411, 35, text=status_text, fill=COLORS["ink"], font=("Segoe UI", 8, "bold"))

        def draw_allowance_card(
            x1: int,
            x2: int,
            label: str,
            used: Optional[float],
            resets_at: Optional[float],
            color: str,
        ) -> None:
            self._rounded_rect(x1, 72, x2, 178, 14, COLORS["panel"], COLORS["line"])
            shown = self._display_value(used)
            self.canvas.create_text(x1 + 14, 88, text=label, anchor="w", fill=color, font=("Segoe UI", 10, "bold"))
            self.canvas.create_text(
                x1 + 14,
                123,
                text=f"{shown:.0f}%" if shown is not None else "--",
                anchor="w",
                fill=COLORS["text"],
                font=("Segoe UI", 25, "bold"),
            )
            self.canvas.create_text(x1 + 88, 123, text=self._display_label(), anchor="w", fill=COLORS["muted"], font=("Segoe UI", 9, "bold"))
            self.canvas.create_text(x1 + 14, 146, text=f"RESET {format_countdown(resets_at)}", anchor="w", fill=COLORS["soft"], font=("Segoe UI", 9, "bold"))
            self.canvas.create_rectangle(x1 + 14, 162, x2 - 14, 168, fill=COLORS["line"], outline="")
            if shown is not None:
                bar_right = x1 + 14 + clamp(shown, 0, 100) / 100 * (x2 - x1 - 28)
                self.canvas.create_rectangle(x1 + 14, 162, bar_right, 168, fill=color, outline="")

        draw_allowance_card(16, 224, "5-HOUR LIMIT", snapshot.five_hour_used_percent, snapshot.five_hour_resets_at, COLORS["cyan"])
        draw_allowance_card(236, 444, "WEEKLY LIMIT", snapshot.used_percent, snapshot.resets_at, COLORS["violet"])

        five_hour_rate = self._current_rate("five_hour_used_percent") if snapshot.has_data and not snapshot.is_stale else None
        weekly_rate = self._current_rate() if snapshot.has_data and not snapshot.is_stale else None
        five_hour_eta = "" if not snapshot.has_data or snapshot.is_stale else self._format_eta(snapshot.five_hour_used_percent, five_hour_rate, snapshot.five_hour_resets_at)
        weekly_eta = "" if not snapshot.has_data or snapshot.is_stale else self._format_eta(snapshot.used_percent, weekly_rate, snapshot.resets_at)
        self._rounded_rect(16, 190, 444, 302, 14, COLORS["panel"], COLORS["line"])
        self.canvas.create_line(230, 202, 230, 290, fill=COLORS["line"], width=2)
        for x, label, rate, eta, color in (
            (30, "5-HOUR PACE", five_hour_rate, five_hour_eta, COLORS["cyan"]),
            (244, "WEEKLY PACE", weekly_rate, weekly_eta, COLORS["violet"]),
        ):
            self.canvas.create_text(x, 207, text=label, anchor="w", fill=color, font=("Segoe UI", 9, "bold"))
            self.canvas.create_text(x, 235, text=self._format_rate(rate), anchor="w", fill=COLORS["amber"], font=("Segoe UI", 15, "bold"))
            self.canvas.create_text(x, 266, text="ETA TO LIMIT", anchor="w", fill=COLORS["muted"], font=("Segoe UI", 9, "bold"))
            self.canvas.create_text(x + 92, 266, text=eta, anchor="w", fill=COLORS["mint"], font=("Segoe UI", 12, "bold"))
            resets = snapshot.five_hour_resets_at if label.startswith("5-") else snapshot.resets_at
            self.canvas.create_text(x, 288, text=f"resets in {format_countdown(resets)}", anchor="w", fill=COLORS["soft"], font=("Segoe UI", 9, "bold"))

        token_rate = self._current_token_rate() if snapshot.total_tokens is not None and not snapshot.is_stale else None
        self._rounded_rect(16, 314, 444, 354, 10, COLORS["panel_raised"], COLORS["line"])
        self.canvas.create_text(30, 327, text="TASK TOKENS", anchor="w", fill=COLORS["muted"], font=("Segoe UI", 8, "bold"))
        self.canvas.create_text(30, 344, text=format_token_count(snapshot.total_tokens), anchor="w", fill=COLORS["cyan"], font=("Segoe UI", 12, "bold"))
        self.canvas.create_text(141, 327, text="TOKENS/MIN", anchor="w", fill=COLORS["muted"], font=("Segoe UI", 8, "bold"))
        self.canvas.create_text(141, 344, text=format_token_rate(token_rate), anchor="w", fill=COLORS["coral"], font=("Segoe UI", 10, "bold"))
        cached_share = None
        if snapshot.input_tokens is not None and snapshot.input_tokens > 0 and snapshot.cached_input_tokens is not None:
            cached_share = clamp(snapshot.cached_input_tokens / snapshot.input_tokens * 100, 0, 100)
        token_detail = f"LAST {format_token_count(snapshot.last_tokens)}"
        if cached_share is not None:
            token_detail += f"  ·  {cached_share:.0f}% CACHED"
        self.canvas.create_text(270, 338, text=token_detail, anchor="w", fill=COLORS["soft"], font=("Segoe UI", 8, "bold"))

        if snapshot.error:
            footer = f"{snapshot.error}  ·  {format_updated(self.last_checked_at).replace('Updated ', 'Checked ', 1)}"
            footer_color = COLORS["amber"]
        else:
            checked = format_updated(self.last_checked_at).replace("Updated ", "Checked ", 1)
            signal = format_updated(snapshot.timestamp).replace("Updated ", "signal ", 1)
            footer = f"{checked}  ·  {signal}"
            footer_color = COLORS["muted"]
        self.canvas.create_text(20, 372, text=footer, anchor="w", fill=footer_color, font=("Segoe UI", 8))

        if display_percent is not None:
            tray_percent = int(round(clamp(display_percent, 0, 100)))
            self._set_tray_icon(tray_percent)
            five_hour_display = self._display_value(snapshot.five_hour_used_percent)
            weekly_display = self._display_value(snapshot.used_percent)
            five_hour_text = f"{five_hour_display:.0f}%" if five_hour_display is not None else "--"
            weekly_text = f"{weekly_display:.0f}%" if weekly_display is not None else "--"
            taskbar_label = f"Codex Usage • 5H {five_hour_text} • Week {weekly_text} {self._display_label()}"
            tray_tip = f"Codex • 5H {five_hour_text} • Week {weekly_text} {self._display_label()}"
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

    def _poll_show_request(self) -> None:
        if not self.root.winfo_exists():
            return
        if os.name == "nt" and _show_event:
            if _kernel32.WaitForSingleObject(_show_event, 0) == _WAIT_OBJECT_0:
                self.show_window()
        self.root.after(250, self._poll_show_request)

    def _poll_refresh(self) -> None:
        self.refresh_after_id = None
        self.refresh_async()

    def _watch_local_signal(self) -> None:
        if not self.root.winfo_exists():
            return
        if self.reader.local_signal_changed():
            self.refresh_async()
        self.root.after(SIGNAL_WATCH_INTERVAL_MS, self._watch_local_signal)

    def _schedule_next_refresh(self) -> None:
        """Schedule the next automatic usage, rate, and ETA read after this one completes."""

        if self.refresh_after_id is not None or not self.root.winfo_exists():
            return
        self.refresh_after_id = self.root.after(
            self.settings.refresh_interval_seconds * 1000,
            self._poll_refresh,
        )

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
        self._schedule_next_refresh()

    def refresh_now(self) -> None:
        """Read immediately, independently of the two-minute polling timer."""

        self.refresh_async()

    def _maybe_show_milestone(self, result: UsageSnapshot) -> None:
        alerts: list[str] = []
        for key, label, used_percent in (
            ("5-hour", "5H", result.five_hour_used_percent),
            ("weekly", "WEEK", result.used_percent),
        ):
            if used_percent is None:
                continue
            bucket = int(used_percent // self.settings.milestone_step)
            previous = self.last_alert_buckets.get(key)
            if previous is None or bucket < previous:
                # Establish a baseline or quietly re-arm after that window resets.
                self.last_alert_buckets[key] = bucket
                continue
            if bucket > previous:
                self.last_alert_buckets[key] = bucket
                display_value = self._display_value(used_percent)
                if display_value is not None:
                    alerts.append(f"{label} {display_value:.0f}% {self._display_label()}")
        if alerts:
            self.tray_popup.show("  ·  ".join(alerts))
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
                    self._raise_statistics()
                    self._render_statistics()
                    return
            except tk.TclError:
                pass

        self.stats_period_hours = 1.0
        self.stats_daily_view = False
        self.stats_weekly_view = False
        dialog = tk.Toplevel(self.root)
        self.stats_window = dialog
        dialog.title("Codex Usage Statistics")
        dialog.configure(bg=COLORS["ink"])
        dialog.resizable(True, True)
        dialog.transient(self.root)
        dialog.attributes("-fullscreen", True)
        dialog.attributes("-topmost", True)
        dialog.bind("<Escape>", lambda _event: self.close_statistics())

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
            text="5-hour · weekly · tokens",
            bg=COLORS["ink"],
            fg=COLORS["muted"],
            font=("Segoe UI", 8),
        ).pack(side="left", padx=(9, 0), pady=(2, 0))
        tk.Button(
            toolbar,
            text="Close",
            command=self.close_statistics,
            bg=COLORS["coral"],
            fg=COLORS["ink"],
            activebackground=COLORS["violet"],
            activeforeground=COLORS["ink"],
            relief="flat",
            bd=0,
            highlightthickness=0,
            font=("Segoe UI", 8, "bold"),
        ).pack(side="right", ipadx=10, ipady=3)

        period_buttons = tk.Frame(dialog, bg=COLORS["ink"])
        period_buttons.pack(fill="x", padx=18, pady=(0, 7))
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
        tk.Button(period_buttons, text="Hourly", command=self.set_stats_hourly, **button_style).pack(
            side="left", padx=(0, 5), ipadx=9, ipady=3
        )
        tk.Button(period_buttons, text="Daily", command=self.set_stats_daily, **button_style).pack(
            side="left", padx=(0, 5), ipadx=9, ipady=3
        )
        tk.Button(period_buttons, text="Weekly", command=self.set_stats_weekly, **button_style).pack(
            side="left", padx=(0, 0), ipadx=9, ipady=3
        )

        self.stats_readout = tk.Label(
            dialog,
            text="HOURLY · LAST 1 HOUR  ·  mouse wheel zooms from 1 minute · click or drag inspects points",
            bg=COLORS["ink"],
            fg=COLORS["muted"],
            anchor="w",
            font=("Segoe UI", 8),
        )
        self.stats_readout.pack(fill="x", padx=18, pady=(0, 4))

        self.stats_canvas = tk.Canvas(
            dialog,
            bg=COLORS["panel"],
            highlightthickness=0,
            bd=0,
        )
        self.stats_canvas.pack(fill="both", expand=True)
        self.stats_canvas.bind("<Button-1>", self._select_statistics_point)
        self.stats_canvas.bind("<B1-Motion>", self._select_statistics_point)
        self.stats_canvas.bind("<MouseWheel>", self._zoom_statistics_with_wheel)
        self.stats_canvas.bind("<Configure>", self._resize_statistics)
        dialog.protocol("WM_DELETE_WINDOW", self.close_statistics)
        dialog.update_idletasks()
        self._raise_statistics()
        dialog.after(50, self._raise_statistics)
        self._render_statistics()

    def _raise_statistics(self) -> None:
        dialog = self.stats_window
        if dialog is None:
            return
        try:
            if not dialog.winfo_exists():
                return
            dialog.attributes("-topmost", True)
            dialog.lift()
            dialog.focus_force()
        except tk.TclError:
            pass

    def _resize_statistics(self, event: Any) -> None:
        size = (max(1, int(event.width)), max(1, int(event.height)))
        if size == self.stats_canvas_size:
            return
        self.stats_canvas_size = size
        self._render_statistics()

    def set_stats_period(self, hours: float) -> None:
        self.stats_period_hours = max(STATS_MIN_HOURLY_ZOOM_MINUTES / 60, float(hours))
        self.stats_daily_view = False
        self.stats_weekly_view = False
        self.stats_selected_timestamp = None
        self._render_statistics()

    def set_stats_hourly(self) -> None:
        self.set_stats_period(1.0)

    def set_stats_daily(self) -> None:
        self.stats_period_hours = float(HISTORY_RETENTION_DAYS * 24)
        self.stats_daily_view = True
        self.stats_weekly_view = False
        self.stats_selected_timestamp = None
        self._render_statistics()

    def set_stats_weekly(self) -> None:
        self.stats_period_hours = float(HISTORY_RETENTION_DAYS * 24)
        self.stats_daily_view = False
        self.stats_weekly_view = True
        self.stats_selected_timestamp = None
        self._render_statistics()

    def _maximum_hourly_zoom_hours(self) -> float:
        if not self.history.points:
            return 1.0
        oldest_timestamp = min(point["timestamp"] for point in self.history.points)
        recorded_hours = max(1.0, (time.time() - oldest_timestamp) / 3600)
        return min(float(HISTORY_RETENTION_DAYS * 24), recorded_hours)

    def _hourly_zoom_steps_minutes(self) -> list[int]:
        """Return familiar, readable wheel-zoom stops down to the recorded minute resolution."""

        maximum_minutes = max(60, math.ceil(self._maximum_hourly_zoom_hours() * 60))
        steps = [1, 2, 3, 5, 10, 15, 30, 60]
        while steps[-1] < maximum_minutes:
            steps.append(steps[-1] * 2)
        result = [step for step in steps if step <= maximum_minutes]
        if result[-1] != maximum_minutes:
            result.append(maximum_minutes)
        return result

    def zoom_statistics(self, direction: int) -> None:
        """Zoom the detailed Hourly chart through every span in recorded history."""

        if self.stats_daily_view or self.stats_weekly_view:
            return
        steps = self._hourly_zoom_steps_minutes()
        current_minutes = max(STATS_MIN_HOURLY_ZOOM_MINUTES, int(round(self.stats_period_hours * 60)))
        if direction > 0:
            target_minutes = steps[min(len(steps) - 1, bisect_right(steps, current_minutes))]
        else:
            target_minutes = steps[max(0, bisect_left(steps, current_minutes) - 1)]
        target = target_minutes / 60
        if not math.isclose(target, self.stats_period_hours, abs_tol=1 / 120):
            self.set_stats_period(target)

    def _zoom_statistics_with_wheel(self, event: Any) -> str:
        self.zoom_statistics(1 if getattr(event, "delta", 0) < 0 else -1)
        return "break"

    def close_statistics(self) -> None:
        if self.stats_window is not None:
            try:
                self.stats_window.destroy()
            except tk.TclError:
                pass
        self.stats_window = None
        self.stats_canvas = None
        self.stats_canvas_size = (0, 0)
        self.stats_readout = None
        self.stats_card_label_items = []
        self.stats_card_value_items = []
        self.stats_live_card_data = {}
        self.stats_usage_points = []
        self.stats_rate_context_points = []
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

    def _scroll_statistics_point(self, event: Any) -> None:
        """Move the selected point one sample at a time with the mouse wheel."""

        if not self.stats_plot_points:
            return
        if self.stats_selected_timestamp is None:
            current_index = len(self.stats_plot_points) - 1
        else:
            current_index = min(
                range(len(self.stats_plot_points)),
                key=lambda index: abs(self.stats_plot_points[index]["timestamp"] - self.stats_selected_timestamp),
            )
        direction = -1 if getattr(event, "delta", 0) > 0 else 1
        selected_index = max(0, min(len(self.stats_plot_points) - 1, current_index + direction))
        self.stats_selected_timestamp = self.stats_plot_points[selected_index]["timestamp"]
        self._draw_statistics_selection()

    def _update_statistics_cards(self, point: Optional[dict[str, Any]]) -> None:
        """Make the visible cards describe the selected chart point, or live data."""

        if not self.stats_card_value_items or self.stats_canvas is None:
            return
        data = point if point is not None else self.stats_live_card_data
        if (self.stats_daily_view or self.stats_weekly_view) and (data.get("daily") or data.get("weekly")):
            def points_text(value: Optional[float]) -> str:
                return f"{value:.1f} pts" if value is not None and math.isfinite(value) else "--"

            def level_text(value: Optional[float]) -> str:
                return f"{value:.1f}%" if value is not None and math.isfinite(value) else "--"

            sample_count = number(data.get("daily_samples"))
            span_seconds = number(data.get("daily_span_seconds"))
            span_text = "--"
            if span_seconds is not None:
                span_minutes = max(0, int(span_seconds // 60))
                span_text = f"{span_minutes // 60}h {span_minutes % 60:02d}m"
            samples_text = f"{int(sample_count)} · {span_text}" if sample_count is not None else span_text
            labels = [
                "5H TOTAL USED",
                "WEEK TOTAL USED",
                "5H AVG LEVEL",
                "WEEK AVG LEVEL",
                "5H AVG PACE",
                "WEEK AVG PACE",
                "5H PEAK PACE",
                "WEEK PEAK PACE",
                "TOTAL TOKENS",
                "AVG TOKEN PACE",
                "PEAK TOKEN PACE",
                "SAMPLES · RECORDED SPAN",
            ]
            values = [
                points_text(number(data.get("five_hour_used_percent"))),
                points_text(number(data.get("used_percent"))),
                level_text(number(data.get("daily_five_hour_average"))),
                level_text(number(data.get("daily_weekly_average"))),
                self._format_rate(number(data.get("five_hour_rate_per_hour"))),
                self._format_rate(number(data.get("rate_per_hour"))),
                self._format_rate(number(data.get("daily_five_hour_peak_rate"))),
                self._format_rate(number(data.get("daily_weekly_peak_rate"))),
                format_token_count(number(data.get("daily_total_tokens"))),
                format_token_rate(number(data.get("token_rate_per_minute"))),
                format_token_rate(number(data.get("daily_peak_token_rate"))),
                samples_text,
            ]
            for item_id, label in zip(self.stats_card_label_items, labels):
                self.stats_canvas.itemconfigure(item_id, text=label)
            for item_id, value in zip(self.stats_card_value_items, values):
                self.stats_canvas.itemconfigure(item_id, text=value)
            return

        five_hour_used = number(data.get("five_hour_used_percent"))
        weekly_used = number(data.get("used_percent"))
        five_hour_rate = number(data.get("five_hour_rate_per_hour"))
        weekly_rate = number(data.get("rate_per_hour"))
        five_hour_reset = number(data.get("five_hour_resets_at"))
        weekly_reset = number(data.get("resets_at"))
        total_tokens = number(data.get("total_tokens"))
        last_tokens = number(data.get("last_tokens"))
        token_rate = number(data.get("token_rate_per_minute"))

        scoped_points = self.stats_rate_context_points or self.stats_usage_points
        if point is not None:
            scoped_points = [
                candidate
                for candidate in scoped_points
                if candidate["timestamp"] <= point["timestamp"]
            ]
        weekly_tokens_per_point = self.history.token_efficiency(self.stats_period_hours, scoped_points)
        five_hour_tokens_per_point = self.history.token_efficiency(
            self.stats_period_hours,
            scoped_points,
            value_field="five_hour_used_percent",
            resets_field="five_hour_resets_at",
        )

        def percent_text(value: Optional[float]) -> str:
            return f"{value:.0f}%" if value is not None and math.isfinite(value) else "--"

        labels = [
            "5-HOUR USED",
            "WEEKLY USED",
            "5-HOUR REMAINING",
            "WEEKLY REMAINING",
            "5-HOUR PACE",
            "WEEKLY PACE",
            "5-HOUR ETA",
            "WEEKLY ETA",
            "CURRENT TASK TOKENS",
            "LAST RESPONSE",
            "TOKEN PACE",
            "TOKENS / 1%  5H · WEEK",
        ]
        values = [
            percent_text(five_hour_used),
            percent_text(weekly_used),
            percent_text(100 - five_hour_used if five_hour_used is not None else None),
            percent_text(100 - weekly_used if weekly_used is not None else None),
            self._format_rate(five_hour_rate),
            self._format_rate(weekly_rate),
            self._format_eta(five_hour_used, five_hour_rate, five_hour_reset),
            self._format_eta(weekly_used, weekly_rate, weekly_reset),
            format_token_count(total_tokens),
            format_token_count(last_tokens),
            format_token_rate(token_rate),
            f"{format_token_count(five_hour_tokens_per_point)} · {format_token_count(weekly_tokens_per_point)}",
        ]
        for item_id, label in zip(self.stats_card_label_items, labels):
            self.stats_canvas.itemconfigure(item_id, text=label)
        for item_id, value in zip(self.stats_card_value_items, values):
            self.stats_canvas.itemconfigure(item_id, text=value)

    def _draw_statistics_selection(self) -> None:
        canvas = self.stats_canvas
        if canvas is None:
            return
        canvas.delete("stats-selection")
        if self.stats_selected_timestamp is None or not self.stats_plot_points:
            self._update_statistics_cards(None)
            if self.stats_readout is not None:
                context = format_model_effort(self.snapshot.model, self.snapshot.reasoning_effort)
                if self.stats_daily_view or self.stats_weekly_view:
                    interval = "WEEK" if self.stats_weekly_view else "DAY"
                    self.stats_readout.configure(
                        text=f"LATEST RECORDED {interval}  ·  live context: {context}  ·  click, drag, or scroll through bars"
                    )
                else:
                    self.stats_readout.configure(
                        text=(
                            f"HOURLY · LAST {format_statistics_span(self.stats_period_hours)}  ·  "
                            f"live context: {context}  ·  mouse wheel zooms · click or drag inspects a point"
                        )
                    )
            return
        selected = min(
            self.stats_plot_points,
            key=lambda point: abs(point["timestamp"] - self.stats_selected_timestamp),
        )
        fraction = (selected["timestamp"] - self.stats_plot_start) / max(1, self.stats_plot_end - self.stats_plot_start)
        x = self.stats_plot_left + clamp(fraction, 0, 1) * (self.stats_plot_right - self.stats_plot_left)
        if not (self.stats_daily_view or self.stats_weekly_view):
            canvas.create_line(
                x,
                self.stats_usage_top - 6,
                x,
                max(self.stats_rate_bottom, self.stats_token_bottom) + 4,
                fill=COLORS["soft"],
                dash=(4, 3),
                width=1,
                tags="stats-selection",
            )
        used = selected.get("used_percent")
        five_hour_used = selected.get("five_hour_used_percent")
        rate = selected.get("rate_per_hour")
        five_hour_rate = selected.get("five_hour_rate_per_hour")
        token_rate = selected.get("token_rate_per_minute")
        if self.stats_daily_view or self.stats_weekly_view:
            for value, color, lane_top, lane_bottom in (
                (
                    used,
                    COLORS["violet"],
                    self.stats_weekly_usage_top,
                    self.stats_weekly_usage_bottom,
                ),
                (
                    five_hour_used,
                    COLORS["cyan"],
                    self.stats_five_hour_usage_top,
                    self.stats_five_hour_usage_bottom,
                ),
            ):
                plotted_value = number(value)
                if plotted_value is None or self.stats_usage_scale <= 0:
                    continue
                usage_y = lane_bottom - (
                    clamp(plotted_value, 0, self.stats_usage_scale) / self.stats_usage_scale
                ) * (lane_bottom - lane_top)
                canvas.create_oval(
                    x - 5,
                    usage_y - 5,
                    x + 5,
                    usage_y + 5,
                    fill=color,
                    outline=COLORS["ink"],
                    width=2,
                    tags="stats-selection",
                )
            for value, color, lane_key in (
                (rate, COLORS["violet"], "weekly"),
                (five_hour_rate, COLORS["cyan"], "five_hour"),
            ):
                plotted_rate = number(value)
                rate_scale = self.stats_daily_rate_scales.get(lane_key, self.stats_rate_scale)
                lane_top, lane_bottom = self.stats_daily_rate_lanes.get(
                    lane_key,
                    (self.stats_rate_top, self.stats_rate_bottom),
                )
                if plotted_rate is None or rate_scale <= 0:
                    continue
                rate_y = lane_top + (
                    (rate_scale - clamp(plotted_rate, 0, rate_scale)) / rate_scale
                ) * (lane_bottom - lane_top)
                canvas.create_oval(
                    x - 5,
                    rate_y - 5,
                    x + 5,
                    rate_y + 5,
                    fill=color,
                    outline=COLORS["ink"],
                    width=2,
                    tags="stats-selection",
                )
            daily_tokens = number(selected.get("daily_total_tokens"))
            if daily_tokens is not None and self.stats_token_scale > 0:
                token_y = self.stats_token_top + (
                    (self.stats_token_scale - clamp(daily_tokens, 0, self.stats_token_scale))
                    / self.stats_token_scale
                ) * (self.stats_token_bottom - self.stats_token_top)
                canvas.create_oval(
                    x - 5,
                    token_y - 5,
                    x + 5,
                    token_y + 5,
                    fill=COLORS["mint"],
                    outline=COLORS["ink"],
                    width=2,
                    tags="stats-selection",
                )
            local = datetime.fromtimestamp(selected["timestamp"]).astimezone()
            self._update_statistics_cards(selected)
            if self.stats_readout is not None:
                self.stats_readout.configure(
                    text=(
                        f"SELECTED {local.strftime('%a, %b %d')}  ·  "
                        f"ending context: {format_model_effort(selected.get('model'), selected.get('reasoning_effort'))}"
                    )
                )
            return
        if used is not None:
            usage_y = self.stats_weekly_usage_bottom - (clamp(used, 0, 100) / 100) * (
                self.stats_weekly_usage_bottom - self.stats_weekly_usage_top
            )
            canvas.create_oval(
                x - 5,
                usage_y - 5,
                x + 5,
                usage_y + 5,
                fill=COLORS["violet"],
                outline=COLORS["ink"],
                width=2,
                tags="stats-selection",
            )
        if rate is not None:
            plotted_rate = clamp(rate, 0, self.stats_rate_scale)
            rate_y = self.stats_rate_top + ((self.stats_rate_scale - plotted_rate) / self.stats_rate_scale) * (self.stats_rate_bottom - self.stats_rate_top)
            canvas.create_oval(
                x - 5,
                rate_y - 5,
                x + 5,
                rate_y + 5,
                fill=COLORS["violet"],
                outline=COLORS["ink"],
                width=2,
                tags="stats-selection",
            )
        if token_rate is not None and self.stats_token_scale > 0:
            token_y = self.stats_token_top + (
                (self.stats_token_scale - clamp(token_rate, 0, self.stats_token_scale)) / self.stats_token_scale
            ) * (self.stats_token_bottom - self.stats_token_top)
            canvas.create_oval(
                x - 5,
                token_y - 5,
                x + 5,
                token_y + 5,
                fill=COLORS["mint"],
                outline=COLORS["ink"],
                width=2,
                tags="stats-selection",
            )
        if five_hour_rate is not None:
            plotted_five_hour_rate = clamp(five_hour_rate, 0, self.stats_rate_scale)
            five_hour_rate_y = self.stats_rate_top + ((self.stats_rate_scale - plotted_five_hour_rate) / self.stats_rate_scale) * (self.stats_rate_bottom - self.stats_rate_top)
            canvas.create_oval(
                x - 5,
                five_hour_rate_y - 5,
                x + 5,
                five_hour_rate_y + 5,
                fill=COLORS["cyan"],
                outline=COLORS["ink"],
                width=2,
                tags="stats-selection",
            )
        if five_hour_used is not None:
            five_hour_y = self.stats_five_hour_usage_bottom - (clamp(five_hour_used, 0, 100) / 100) * (
                self.stats_five_hour_usage_bottom - self.stats_five_hour_usage_top
            )
            canvas.create_oval(
                x - 5,
                five_hour_y - 5,
                x + 5,
                five_hour_y + 5,
                fill=COLORS["cyan"],
                outline=COLORS["ink"],
                width=2,
                tags="stats-selection",
            )
        local = datetime.fromtimestamp(selected["timestamp"]).astimezone()
        when = f"{local.strftime('%b %d')} {local.strftime('%I:%M %p').lstrip('0')}"
        self._update_statistics_cards(selected)
        if self.stats_readout is not None:
            self.stats_readout.configure(
                text=(
                    f"SELECTED {when}  ·  "
                    f"context: {format_model_effort(selected.get('model'), selected.get('reasoning_effort'))}"
                )
            )

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
            text=f"HOURLY TREND  ·  LAST {format_statistics_span(self.stats_period_hours)}",
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
            if self.stats_period_hours < 1:
                return local.strftime("%I:%M %p").lstrip("0")
            if self.stats_period_hours <= 24:
                return local.strftime("%I %p").lstrip("0")
            return local.strftime("%b %d")

        for fraction in (0, 0.5, 1):
            x = left + fraction * (right - left)
            canvas.create_text(x, bottom + 18, text=axis_label(start_time + fraction * span), fill=COLORS["muted"], font=("Segoe UI", 8))

    def _statistics_card_layout(self, canvas_width: int, card_count: int) -> dict[str, float]:
        """Keep summary cards compact on wide screens so the charts stay primary."""

        columns = STATS_WIDE_CARD_COLUMNS if canvas_width >= STATS_WIDE_MIN_WIDTH else STATS_NARROW_CARD_COLUMNS
        columns = min(columns, max(1, card_count))
        rows = max(1, math.ceil(card_count / columns))
        card_margin = 14.0
        card_gap = 10.0
        card_width = (canvas_width - card_margin * 2 - card_gap * (columns - 1)) / columns
        cards_bottom = STATS_CARD_TOP + rows * STATS_CARD_HEIGHT + (rows - 1) * STATS_CARD_ROW_GAP
        history_title_y = cards_bottom + 16.0
        history_subtitle_y = history_title_y + 15.0

        return {
            "columns": float(columns),
            "card_margin": card_margin,
            "card_gap": card_gap,
            "card_width": card_width,
            "history_title_y": history_title_y,
            "history_subtitle_y": history_subtitle_y,
            "plot_top": history_subtitle_y + 17.0,
        }

    def _statistics_pane_geometry(self, canvas_height: int, plot_top: float = STATS_PLOT_TOP) -> dict[str, float]:
        """Lay out the three linked Statistics panes within the available canvas."""

        plot_bottom = float(max(620, canvas_height - 34))
        pane_gap = 16.0
        pane_header = 16.0
        available_height = plot_bottom - plot_top
        token_pane_height = max(72.0, available_height * 0.20)
        primary_height = available_height - token_pane_height - pane_gap * 2
        usage_pane_height = primary_height * 0.54
        rate_pane_height = primary_height - usage_pane_height

        usage_pane_top = plot_top
        usage_pane_bottom = usage_pane_top + usage_pane_height
        rate_pane_top = usage_pane_bottom + pane_gap
        rate_pane_bottom = rate_pane_top + rate_pane_height
        token_pane_top = rate_pane_bottom + pane_gap

        return {
            "usage_pane_top": usage_pane_top,
            "usage_pane_bottom": usage_pane_bottom,
            "usage_top": usage_pane_top + pane_header,
            "usage_bottom": usage_pane_bottom,
            "rate_pane_top": rate_pane_top,
            "rate_pane_bottom": rate_pane_bottom,
            "rate_top": rate_pane_top + pane_header,
            "rate_bottom": rate_pane_bottom,
            "token_pane_top": token_pane_top,
            "token_pane_bottom": plot_bottom,
            "token_top": token_pane_top + pane_header,
            "token_bottom": plot_bottom,
        }

    def _draw_statistics_panes(
        self,
        canvas: tk.Canvas,
        left: float,
        right: float,
        geometry: dict[str, float],
        usage_label: str,
        token_label: str,
    ) -> None:
        """Draw visually separate panes while preserving a shared time axis."""

        panes = (
            ("usage", usage_label, COLORS["soft"]),
            ("rate", "PACE · POINTS / HOUR", COLORS["soft"]),
            ("token", token_label, COLORS["mint"]),
        )
        for key, label, color in panes:
            pane_top = geometry[f"{key}_pane_top"]
            pane_bottom = geometry[f"{key}_pane_bottom"]
            canvas.create_rectangle(
                left,
                pane_top,
                right,
                pane_bottom,
                fill=COLORS["panel"],
                outline=COLORS["line"],
                width=1,
                tags=("stats-pane", f"stats-{key}-pane"),
            )
            canvas.create_text(
                left + 10,
                pane_top + 10,
                text=label,
                anchor="w",
                fill=color,
                font=("Segoe UI", 8, "bold"),
            )
            if key in ("usage", "rate"):
                canvas.create_text(
                    right - 76,
                    pane_top + 10,
                    text="5-HOUR",
                    anchor="e",
                    fill=COLORS["cyan"],
                    font=("Segoe UI", 8, "bold"),
                )
                canvas.create_text(
                    right - 10,
                    pane_top + 10,
                    text="WEEKLY",
                    anchor="e",
                    fill=COLORS["violet"],
                    font=("Segoe UI", 8, "bold"),
                )

    def _statistics_usage_lanes(self, top: float, bottom: float) -> dict[str, tuple[float, float]]:
        """Return two compact small-multiple lanes that share one usage scale."""

        lane_gap = 8.0
        lane_height = max(1.0, (bottom - top - lane_gap) / 2)
        five_hour_bottom = top + lane_height
        return {
            "five_hour": (top, five_hour_bottom),
            "weekly": (five_hour_bottom + lane_gap, bottom),
        }

    def _draw_statistics_usage_lanes(
        self,
        canvas: tk.Canvas,
        left: float,
        right: float,
        top: float,
        bottom: float,
        scale: float,
        scale_suffix: str,
    ) -> dict[str, tuple[float, float]]:
        """Draw directly labeled 5-hour and weekly usage lanes."""

        lanes = self._statistics_usage_lanes(top, bottom)
        for key, label, color in (
            ("five_hour", "5H", COLORS["cyan"]),
            ("weekly", "WEEK", COLORS["violet"]),
        ):
            lane_top, lane_bottom = lanes[key]
            canvas.create_rectangle(
                left,
                lane_top,
                right,
                lane_bottom,
                fill=COLORS["panel_raised"],
                outline=COLORS["line"],
                width=1,
                tags=("stats-usage-lane", f"stats-{key}-usage-lane"),
            )
            canvas.create_text(
                left - 10,
                (lane_top + lane_bottom) / 2,
                text=label,
                anchor="e",
                fill=color,
                font=("Segoe UI", 8, "bold"),
            )
            midpoint_y = lane_bottom - 0.5 * (lane_bottom - lane_top)
            canvas.create_line(left, midpoint_y, right, midpoint_y, fill=COLORS["line"], dash=(3, 5))
            canvas.create_text(
                right - 6,
                lane_top + 2,
                text=f"{scale:.0f}{scale_suffix}",
                anchor="ne",
                fill=COLORS["muted"],
                font=("Segoe UI", 7),
            )
        self.stats_five_hour_usage_top, self.stats_five_hour_usage_bottom = lanes["five_hour"]
        self.stats_weekly_usage_top, self.stats_weekly_usage_bottom = lanes["weekly"]
        return lanes

    @staticmethod
    def _context_changes(points: list[dict[str, Any]]) -> list[tuple[float, str, str]]:
        """Return only state transitions, so context never becomes chart clutter."""

        changes: list[tuple[float, str, str]] = []
        previous_model: Optional[str] = None
        previous_effort: Optional[str] = None
        for point in points:
            timestamp = number(point.get("timestamp"))
            if timestamp is None:
                continue
            model = metadata_text(point.get("model"))
            effort = metadata_text(point.get("reasoning_effort"))
            if model is not None and previous_model is not None and model != previous_model:
                changes.append((timestamp, "model", model))
            if effort is not None and previous_effort is not None and effort != previous_effort:
                changes.append((timestamp, "effort", effort))
            if model is not None:
                previous_model = model
            if effort is not None:
                previous_effort = effort
        return changes

    def _draw_statistics_context_markers(
        self,
        canvas: tk.Canvas,
        points: list[dict[str, Any]],
        start_time: float,
        end_time: float,
        left: float,
        right: float,
        top: float,
        bottom: float,
    ) -> None:
        """Annotate model/effort changes behind the metrics they help explain."""

        span = max(1.0, end_time - start_time)
        for timestamp, change_type, _value in self._context_changes(points):
            x = left + clamp((timestamp - start_time) / span, 0, 1) * (right - left)
            if change_type == "model":
                color, width, dash, offset, stipple = COLORS["amber"], 1, (), -0.5, "gray50"
            else:
                color, width, dash, offset, stipple = COLORS["coral"], 1, (2, 4), 0.5, "gray50"
            marker_x = x + offset
            canvas.create_line(
                marker_x,
                top + 1,
                marker_x,
                bottom - 1,
                fill=color,
                width=width,
                dash=dash,
                stipple=stipple,
                tags=("stats-context-marker", f"stats-{change_type}-marker"),
            )
            canvas.create_polygon(
                marker_x - 3,
                top + 1,
                marker_x + 3,
                top + 1,
                marker_x,
                top + 5,
                fill=color,
                outline="",
                stipple=stipple,
                tags=("stats-context-marker", f"stats-{change_type}-marker"),
            )

    def _draw_statistics_daily_rate_lanes(
        self,
        canvas: tk.Canvas,
        left: float,
        right: float,
        top: float,
        bottom: float,
        scales: dict[str, float],
    ) -> dict[str, tuple[float, float]]:
        """Draw daily pace as two independently scaled, directly labeled lanes."""

        lanes = self._statistics_usage_lanes(top, bottom)
        self.stats_daily_rate_lanes = lanes
        self.stats_daily_rate_scales = {
            key: max(0.0001, float(scales.get(key, 1.0)))
            for key in ("five_hour", "weekly")
        }
        for key, label, color in (
            ("five_hour", "5H AVG", COLORS["cyan"]),
            ("weekly", "WEEK AVG", COLORS["violet"]),
        ):
            lane_top, lane_bottom = lanes[key]
            scale = self.stats_daily_rate_scales[key]
            canvas.create_rectangle(
                left,
                lane_top,
                right,
                lane_bottom,
                fill=COLORS["panel_raised"],
                outline=COLORS["line"],
                width=1,
                tags=("stats-daily-rate-lane", f"stats-{key}-daily-rate-lane"),
            )
            canvas.create_text(
                left - 10,
                (lane_top + lane_bottom) / 2,
                text=label,
                anchor="e",
                fill=color,
                font=("Segoe UI", 8, "bold"),
            )
            midpoint_y = lane_bottom - 0.5 * (lane_bottom - lane_top)
            canvas.create_line(left, midpoint_y, right, midpoint_y, fill=COLORS["line"], dash=(3, 5))
            canvas.create_line(left, lane_bottom, right, lane_bottom, fill=COLORS["soft"], width=1)
            canvas.create_text(
                right - 6,
                lane_top + 2,
                text=format_rate_axis(scale, scale),
                anchor="ne",
                fill=COLORS["muted"],
                font=("Segoe UI", 7),
            )
            canvas.create_text(
                right - 6,
                lane_bottom - 2,
                text="0/hr",
                anchor="se",
                fill=COLORS["muted"],
                font=("Segoe UI", 7),
            )
        return lanes

    def _render_daily_statistics(self) -> None:
        """Render one stock-chart-style bar per local day or local week."""

        canvas = self.stats_canvas
        if canvas is None:
            return
        canvas.delete("all")
        canvas_width = max(640, int(canvas.winfo_width()))
        canvas_height = max(560, int(canvas.winfo_height()))
        is_weekly = self.stats_weekly_view
        interval_name = "WEEKLY" if is_weekly else "DAILY"
        interval_label = "week" if is_weekly else "day"
        interval_days = 7 if is_weekly else 1
        bucket_key = "week_start" if is_weekly else "day_start"
        daily_points = (
            self.history.weekly_statistics(HISTORY_RETENTION_DAYS)
            if is_weekly
            else self.history.daily_statistics(HISTORY_RETENTION_DAYS)
        )
        self.stats_usage_points = self.history.chart_points(HISTORY_RETENTION_DAYS * 24)
        self.stats_plot_points = daily_points
        self.stats_live_card_data = daily_points[-1] if daily_points else ({"weekly": True} if is_weekly else {"daily": True})

        card_groups = (
            (
                ("5H TOTAL USED", "--", COLORS["cyan"]),
                ("WEEK TOTAL USED", "--", COLORS["violet"]),
                ("5H AVG LEVEL", "--", COLORS["cyan"]),
                ("WEEK AVG LEVEL", "--", COLORS["violet"]),
            ),
            (
                ("5H AVG PACE", "--", COLORS["cyan"]),
                ("WEEK AVG PACE", "--", COLORS["violet"]),
                ("5H PEAK PACE", "--", COLORS["cyan"]),
                ("WEEK PEAK PACE", "--", COLORS["violet"]),
            ),
            (
                ("TOTAL TOKENS", "--", COLORS["mint"]),
                ("AVG TOKEN PACE", "--", COLORS["mint"]),
                ("PEAK TOKEN PACE", "--", COLORS["mint"]),
                ("SAMPLES · RECORDED SPAN", "--", COLORS["soft"]),
            ),
        )
        cards = [card for group in card_groups for card in group]
        card_layout = self._statistics_card_layout(canvas_width, len(cards))
        card_margin = card_layout["card_margin"]
        card_gap = card_layout["card_gap"]
        card_width = card_layout["card_width"]
        card_columns = int(card_layout["columns"])
        self.stats_card_label_items = []
        self.stats_card_value_items = []
        for card_index, (label, value, color) in enumerate(cards):
            row_index = card_index // card_columns
            column_index = card_index % card_columns
            y1 = STATS_CARD_TOP + row_index * (STATS_CARD_HEIGHT + STATS_CARD_ROW_GAP)
            y2 = y1 + STATS_CARD_HEIGHT
            x1 = card_margin + column_index * (card_width + card_gap)
            x2 = x1 + card_width
            canvas.create_rectangle(x1, y1, x2, y2, fill=COLORS["panel_raised"], outline=COLORS["line"])
            label_item = canvas.create_text(
                x1 + 12,
                y1 + STATS_CARD_LABEL_OFFSET_Y,
                text=label,
                anchor="w",
                fill=COLORS["muted"],
                font=("Segoe UI", 7, "bold"),
            )
            value_item = canvas.create_text(
                x1 + 12,
                y1 + STATS_CARD_VALUE_OFFSET_Y,
                text=value,
                anchor="w",
                fill=color,
                font=("Segoe UI", 12 if card_index < 4 else 10, "bold"),
            )
            self.stats_card_label_items.append(label_item)
            self.stats_card_value_items.append(value_item)

        now_local = datetime.now().astimezone()
        today_start = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
        if is_weekly:
            current_week_start = today_start - timedelta(days=today_start.weekday())
            visible_weeks = math.ceil(HISTORY_RETENTION_DAYS / 7)
            range_start = current_week_start - timedelta(days=(visible_weeks - 1) * 7)
            range_end = current_week_start + timedelta(days=7)
        else:
            visible_weeks = 0
            range_start = today_start - timedelta(days=HISTORY_RETENTION_DAYS - 1)
            range_end = today_start + timedelta(days=1)
        start_time = range_start.timestamp()
        end_time = range_end.timestamp()
        span = max(1.0, end_time - start_time)
        left, right = 58, max(250, canvas_width - 72)
        self.stats_plot_start = start_time
        self.stats_plot_end = end_time
        self.stats_plot_left = left
        self.stats_plot_right = right

        def x_for(epoch: float) -> float:
            return left + clamp((epoch - start_time) / span, 0, 1) * (right - left)

        canvas.create_text(
            18,
            card_layout["history_title_y"],
            text=f"{interval_name} HISTORY",
            anchor="w",
            fill=COLORS["soft"],
            font=("Segoe UI", 8, "bold"),
        )
        canvas.create_text(
            canvas_width - 18,
            card_layout["history_title_y"],
            text=f"one {interval_label} per bar · select any {interval_label} to update the cards",
            anchor="e",
            fill=COLORS["muted"],
            font=("Segoe UI", 8),
        )
        canvas.create_text(
            18,
            card_layout["history_subtitle_y"],
            text=(f"LAST {visible_weeks} CALENDAR WEEKS" if is_weekly else f"LAST {HISTORY_RETENTION_DAYS} CALENDAR DAYS"),
            anchor="w",
            fill=COLORS["muted"],
            font=("Segoe UI", 8),
        )
        canvas.create_text(
            canvas_width - 18,
            card_layout["history_subtitle_y"],
            text=(
                "missing weeks stay blank; totals sum recorded reset-aware daily increases"
                if is_weekly
                else "missing days stay blank; totals use recorded reset-aware increases"
            ),
            anchor="e",
            fill=COLORS["muted"],
            font=("Segoe UI", 8),
        )

        geometry = self._statistics_pane_geometry(canvas_height, card_layout["plot_top"])
        usage_top = geometry["usage_top"]
        usage_bottom = geometry["usage_bottom"]
        rate_top = geometry["rate_top"]
        rate_bottom = geometry["rate_bottom"]
        token_top = geometry["token_top"]
        token_bottom = geometry["token_bottom"]
        self.stats_usage_top = usage_top
        self.stats_usage_bottom = usage_bottom
        self.stats_rate_top = rate_top
        self.stats_rate_bottom = rate_bottom
        self.stats_token_top = token_top
        self.stats_token_bottom = token_bottom
        self._draw_statistics_panes(
            canvas,
            left,
            right,
            geometry,
            f"USAGE TOTALS · POINTS / {interval_label.upper()}",
            f"{interval_name} TOKEN TOTAL",
        )

        usage_values = [
            value
            for point in daily_points
            for value in (number(point.get("used_percent")), number(point.get("five_hour_used_percent")))
            if value is not None and math.isfinite(value)
        ]
        observed_usage_max = max(usage_values, default=0.0)
        usage_scale = max(20.0, math.ceil(observed_usage_max / 20.0) * 20.0)
        self.stats_usage_scale = usage_scale
        usage_lanes = self._draw_statistics_usage_lanes(
            canvas,
            left,
            right,
            usage_top,
            usage_bottom,
            usage_scale,
            " pts",
        )

        daily_rate_values = {
            "five_hour": [
                value
                for point in daily_points
                if (value := number(point.get("five_hour_rate_per_hour"))) is not None and math.isfinite(value)
            ],
            "weekly": [
                value
                for point in daily_points
                if (value := number(point.get("rate_per_hour"))) is not None and math.isfinite(value)
            ],
        }
        daily_rate_scales = {
            key: nice_positive_scale(max(values, default=0.0))
            for key, values in daily_rate_values.items()
        }
        # The Daily view compares change over time within each allowance.  Its two
        # rates live on very different orders of magnitude, so sharing the regular
        # 0–20/hour scale flattens the weekly series beyond recognition.
        self.stats_rate_scale = max(daily_rate_scales.values(), default=1.0)
        daily_rate_lanes = self._draw_statistics_daily_rate_lanes(
            canvas,
            left,
            right,
            rate_top,
            rate_bottom,
            daily_rate_scales,
        )

        for point in daily_points:
            bucket_start = number(point.get(bucket_key))
            if bucket_start is None:
                continue
            bucket_local = datetime.fromtimestamp(bucket_start).astimezone()
            next_bucket_start = (bucket_local + timedelta(days=interval_days)).timestamp()
            bar_left = x_for(bucket_start)
            bar_right = x_for(next_bucket_start)
            weekly_total = number(point.get("used_percent")) or 0.0
            five_hour_total = number(point.get("five_hour_used_percent")) or 0.0
            for lane_key, total, color, tag in (
                ("five_hour", five_hour_total, COLORS["cyan"], "stats-five-hour-usage-bar"),
                ("weekly", weekly_total, COLORS["violet"], "stats-weekly-usage-bar"),
            ):
                lane_top, lane_bottom = usage_lanes[lane_key]
                y = lane_bottom - (clamp(total, 0, usage_scale) / usage_scale) * (lane_bottom - lane_top)
                canvas.create_rectangle(
                    bar_left,
                    y,
                    bar_right + 0.5,
                    lane_bottom,
                    fill=color,
                    outline="",
                    tags=("stats-usage-bar", tag),
                )

        def draw_daily_rate_bars(field: str, color: str, lane_key: str) -> None:
            """Use one full-width bar per recorded day; Daily has no connected trend lines."""

            lane_top, lane_bottom = daily_rate_lanes[lane_key]
            rate_scale = daily_rate_scales[lane_key]
            for point in daily_points:
                value = number(point.get(field))
                bucket_start = number(point.get(bucket_key))
                if value is None or bucket_start is None:
                    continue
                bucket_local = datetime.fromtimestamp(bucket_start).astimezone()
                next_bucket_start = (bucket_local + timedelta(days=interval_days)).timestamp()
                bar_left = x_for(bucket_start)
                bar_right = x_for(next_bucket_start)
                y = lane_top + ((rate_scale - clamp(value, 0, rate_scale)) / rate_scale) * (lane_bottom - lane_top)
                canvas.create_rectangle(
                    bar_left,
                    y,
                    bar_right + 0.5,
                    lane_bottom,
                    fill=color,
                    outline="",
                    tags=("stats-daily-rate-bar", f"stats-{lane_key}-daily-rate-bar"),
                )

        draw_daily_rate_bars("five_hour_rate_per_hour", COLORS["cyan"], "five_hour")
        draw_daily_rate_bars("rate_per_hour", COLORS["violet"], "weekly")
        if not daily_points:
            canvas.create_text(
                (left + right) / 2,
                (usage_top + usage_bottom) / 2,
                text=f"Keep the counter running to build {interval_label} bars.",
                fill=COLORS["muted"],
                font=("Segoe UI", 10),
            )
        elif not any(
            number(point.get(field)) is not None
            for point in daily_points
            for field in ("five_hour_rate_per_hour", "rate_per_hour")
        ):
            canvas.create_text(
                (left + right) / 2,
                (rate_top + rate_bottom) / 2,
                text=f"More samples are needed to estimate {interval_label} pace.",
                fill=COLORS["muted"],
                font=("Segoe UI", 10),
            )

        token_values = [
            float(point["daily_total_tokens"])
            for point in daily_points
            if number(point.get("daily_total_tokens")) is not None
        ]
        observed_token_max = max(token_values, default=0.0)
        if observed_token_max > 0:
            magnitude = 10 ** math.floor(math.log10(max(1.0, observed_token_max)))
            token_scale = max(magnitude, math.ceil(observed_token_max / magnitude) * magnitude)
        else:
            token_scale = 1_000.0
        self.stats_token_scale = token_scale
        canvas.create_line(left, token_bottom, right, token_bottom, fill=COLORS["soft"], width=2)
        canvas.create_text(canvas_width - 10, token_top, text=format_token_count(token_scale), anchor="e", fill=COLORS["muted"], font=("Segoe UI", 8))
        canvas.create_text(canvas_width - 10, token_bottom, text="0", anchor="e", fill=COLORS["muted"], font=("Segoe UI", 8))
        for point in daily_points:
            bucket_start = number(point.get(bucket_key))
            if bucket_start is None:
                continue
            bucket_local = datetime.fromtimestamp(bucket_start).astimezone()
            next_bucket_start = (bucket_local + timedelta(days=interval_days)).timestamp()
            bar_left = x_for(bucket_start)
            bar_right = x_for(next_bucket_start)
            total = number(point.get("daily_total_tokens")) or 0.0
            y = token_top + ((token_scale - clamp(total, 0, token_scale)) / token_scale) * (token_bottom - token_top)
            canvas.create_rectangle(
                bar_left,
                y,
                bar_right + 0.5,
                token_bottom,
                fill=COLORS["mint"],
                outline="",
                tags="stats-daily-token-bar",
            )

        def axis_label(epoch: float) -> str:
            return datetime.fromtimestamp(epoch).astimezone().strftime("%b %d")

        for fraction in (0, 0.25, 0.5, 0.75, 1):
            x = left + fraction * (right - left)
            canvas.create_text(x, token_bottom + 18, text=axis_label(start_time + fraction * span), fill=COLORS["muted"], font=("Segoe UI", 8))
        self._draw_statistics_selection()

    def _render_statistics(self) -> None:
        canvas = self.stats_canvas
        if canvas is None:
            return
        if self.stats_daily_view or self.stats_weekly_view:
            self._render_daily_statistics()
            return
        canvas.delete("all")
        canvas_width = max(640, int(canvas.winfo_width()))
        canvas_height = max(560, int(canvas.winfo_height()))

        end_time = time.time()
        start_time = end_time - self.stats_period_hours * 3600
        rate_context_hours = max(self.stats_period_hours, RATE_WINDOW_MINUTES / 60)
        rate_context_points = self.history.chart_points(rate_context_hours)
        usage_points = [point for point in rate_context_points if point["timestamp"] >= start_time]
        self.stats_usage_points = usage_points
        self.stats_rate_context_points = rate_context_points
        rate_points = [
            point
            for point in self.history.rate_series(rate_context_hours, rate_context_points)
            if point["timestamp"] >= start_time
        ]
        five_hour_rate_points = [
            point
            for point in self.history.rate_series(
                rate_context_hours,
                rate_context_points,
                value_field="five_hour_used_percent",
            )
            if point["timestamp"] >= start_time
        ]
        token_rate_points = [
            point
            for point in self.history.token_rate_series(rate_context_hours, rate_context_points)
            if point["timestamp"] >= start_time
        ]
        current = usage_points[-1]["used_percent"] if usage_points else self.snapshot.used_percent
        five_hour_points = [point for point in usage_points if number(point.get("five_hour_used_percent")) is not None]
        five_hour_current = five_hour_points[-1]["five_hour_used_percent"] if five_hour_points else self.snapshot.five_hour_used_percent
        remaining = 100 - current if current is not None else None
        five_hour_remaining = 100 - five_hour_current if five_hour_current is not None else None
        current_rate = None
        if usage_points and rate_points and abs(rate_points[-1]["timestamp"] - usage_points[-1]["timestamp"]) <= 1:
            current_rate = rate_points[-1]["rate_per_hour"]
        five_hour_current_rate = None
        if five_hour_points and five_hour_rate_points and abs(five_hour_rate_points[-1]["timestamp"] - five_hour_points[-1]["timestamp"]) <= 1:
            five_hour_current_rate = five_hour_rate_points[-1]["rate_per_hour"]
        current_token_rate = self._current_token_rate() if not self.snapshot.is_stale else None
        current_tokens = self.snapshot.total_tokens
        if current_tokens is None:
            token_points = [point for point in usage_points if number(point.get("total_tokens")) is not None]
            current_tokens = token_points[-1]["total_tokens"] if token_points else None
        tokens_per_point = self.history.token_efficiency(rate_context_hours, rate_context_points)
        five_hour_tokens_per_point = self.history.token_efficiency(
            rate_context_hours,
            rate_context_points,
            value_field="five_hour_used_percent",
            resets_field="five_hour_resets_at",
        )
        self.stats_live_card_data = {
            "five_hour_used_percent": five_hour_current,
            "five_hour_resets_at": self.snapshot.five_hour_resets_at,
            "used_percent": current,
            "resets_at": self.snapshot.resets_at,
            "five_hour_rate_per_hour": five_hour_current_rate,
            "rate_per_hour": current_rate,
            "total_tokens": current_tokens,
            "last_tokens": self.snapshot.last_tokens,
            "token_rate_per_minute": current_token_rate,
            "model": self.snapshot.model,
            "reasoning_effort": self.snapshot.reasoning_effort,
        }

        def rate_text(value: Optional[float]) -> str:
            return self._format_rate(value)

        def axis_label(epoch: float) -> str:
            local = datetime.fromtimestamp(epoch).astimezone()
            if self.stats_period_hours < 1:
                return local.strftime("%I:%M %p").lstrip("0")
            if self.stats_period_hours <= 24:
                return local.strftime("%I %p").lstrip("0")
            return f"{local.strftime('%b %d')} {local.strftime('%I %p').lstrip('0')}"

        usage_cards = [
            ("5-HOUR USED", f"{five_hour_current:.0f}%" if five_hour_current is not None else "--", COLORS["cyan"]),
            ("WEEKLY USED", f"{current:.0f}%" if current is not None else "--", COLORS["violet"]),
            ("5-HOUR REMAINING", f"{five_hour_remaining:.0f}%" if five_hour_remaining is not None else "--", COLORS["cyan"]),
            ("WEEKLY REMAINING", f"{remaining:.0f}%" if remaining is not None else "--", COLORS["violet"]),
        ]
        pace_cards = [
            ("5-HOUR PACE", rate_text(five_hour_current_rate), COLORS["cyan"]),
            ("WEEKLY PACE", rate_text(current_rate), COLORS["violet"]),
            ("5-HOUR ETA", self._format_eta(five_hour_current, five_hour_current_rate, self.snapshot.five_hour_resets_at), COLORS["cyan"]),
            ("WEEKLY ETA", self._format_eta(current, current_rate, self.snapshot.resets_at), COLORS["violet"]),
        ]
        token_cards = [
            ("CURRENT TASK TOKENS", format_token_count(current_tokens), COLORS["mint"]),
            ("LAST RESPONSE", format_token_count(self.snapshot.last_tokens), COLORS["soft"]),
            ("TOKEN PACE", format_token_rate(current_token_rate), COLORS["mint"]),
            (
                "TOKENS / 1%  5H · WEEK",
                f"{format_token_count(five_hour_tokens_per_point)} · {format_token_count(tokens_per_point)}",
                COLORS["mint"],
            ),
        ]
        cards = usage_cards + pace_cards + token_cards
        card_layout = self._statistics_card_layout(canvas_width, len(cards))
        card_margin = card_layout["card_margin"]
        card_gap = card_layout["card_gap"]
        card_width = card_layout["card_width"]
        card_columns = int(card_layout["columns"])
        self.stats_card_label_items = []
        self.stats_card_value_items = []
        for card_index, (label, value, color) in enumerate(cards):
            row_index = card_index // card_columns
            column_index = card_index % card_columns
            y1 = STATS_CARD_TOP + row_index * (STATS_CARD_HEIGHT + STATS_CARD_ROW_GAP)
            y2 = y1 + STATS_CARD_HEIGHT
            x1 = card_margin + column_index * (card_width + card_gap)
            x2 = x1 + card_width
            canvas.create_rectangle(x1, y1, x2, y2, fill=COLORS["panel_raised"], outline=COLORS["line"])
            label_item = canvas.create_text(
                x1 + 12,
                y1 + STATS_CARD_LABEL_OFFSET_Y,
                text=label,
                anchor="w",
                fill=COLORS["muted"],
                font=("Segoe UI", 7, "bold"),
            )
            value_item = canvas.create_text(
                x1 + 12,
                y1 + STATS_CARD_VALUE_OFFSET_Y,
                text=value,
                anchor="w",
                fill=color,
                font=("Segoe UI", 12 if card_index < 4 else 10, "bold"),
            )
            self.stats_card_label_items.append(label_item)
            self.stats_card_value_items.append(value_item)

        span = max(1, end_time - start_time)
        left, right = 50, max(250, canvas_width - 64)
        self.stats_plot_start = start_time
        self.stats_plot_end = end_time
        self.stats_plot_left = left
        self.stats_plot_right = right
        plot_by_timestamp: dict[float, dict[str, Any]] = {}
        for point in usage_points:
            plot_point = {"timestamp": point["timestamp"], "used_percent": point["used_percent"]}
            for field in (
                "five_hour_used_percent",
                "resets_at",
                "five_hour_resets_at",
                "total_tokens",
                "input_tokens",
                "cached_input_tokens",
                "output_tokens",
                "reasoning_tokens",
                "last_tokens",
                "model",
                "reasoning_effort",
            ):
                if field in point:
                    plot_point[field] = point[field]
            plot_by_timestamp.setdefault(point["timestamp"], {}).update(plot_point)
        for point in rate_points:
            plot_by_timestamp.setdefault(point["timestamp"], {}).update(
                {"timestamp": point["timestamp"], "rate_per_hour": point["rate_per_hour"]}
            )
        for point in five_hour_rate_points:
            plot_by_timestamp.setdefault(point["timestamp"], {}).update(
                {"timestamp": point["timestamp"], "five_hour_rate_per_hour": point["rate_per_hour"]}
            )
        for point in token_rate_points:
            plot_by_timestamp.setdefault(point["timestamp"], {}).update(
                {
                    "timestamp": point["timestamp"],
                    "token_rate_per_minute": point["token_rate_per_minute"],
                    "total_tokens": point["total_tokens"],
                }
            )
        self.stats_plot_points = [plot_by_timestamp[key] for key in sorted(plot_by_timestamp)]

        def x_for(epoch: float) -> float:
            return left + clamp((epoch - start_time) / span, 0, 1) * (right - left)

        def draw_continuous_recorded_path(points: list[dict[str, Any]], y_for: Any, color: str) -> None:
            """Connect recorded active points without inventing intermediate selectable samples."""

            coordinates: list[float] = []
            for point in points:
                coordinates.extend((x_for(point["timestamp"]), y_for(point)))
            if len(coordinates) >= 4:
                canvas.create_line(*coordinates, fill=color, width=3)

        canvas.create_text(
            18,
            card_layout["history_title_y"],
            text="LIVE HISTORY",
            anchor="w",
            fill=COLORS["soft"],
            font=("Segoe UI", 8, "bold"),
        )
        canvas.create_text(
            canvas_width - 18,
            card_layout["history_title_y"],
            text="select a point to update every card",
            anchor="e",
            fill=COLORS["muted"],
            font=("Segoe UI", 8),
        )
        scope_label = format_statistics_span(self.stats_period_hours)
        canvas.create_text(
            18,
            card_layout["history_subtitle_y"],
            text=f"LAST {scope_label}",
            anchor="w",
            fill=COLORS["muted"],
            font=("Segoe UI", 8),
        )
        canvas.create_text(
            canvas_width - 18,
            card_layout["history_subtitle_y"],
            text="subtle amber = model · subtle coral dash = effort · no backfilled context",
            anchor="e",
            fill=COLORS["muted"],
            font=("Segoe UI", 8),
        )

        geometry = self._statistics_pane_geometry(canvas_height, card_layout["plot_top"])
        usage_top = geometry["usage_top"]
        usage_bottom = geometry["usage_bottom"]
        rate_top = geometry["rate_top"]
        rate_bottom = geometry["rate_bottom"]
        token_top = geometry["token_top"]
        token_bottom = geometry["token_bottom"]
        self.stats_usage_top = usage_top
        self.stats_usage_bottom = usage_bottom
        self.stats_rate_top = rate_top
        self.stats_rate_bottom = rate_bottom
        self.stats_token_top = token_top
        self.stats_token_bottom = token_bottom
        self._draw_statistics_panes(
            canvas,
            left,
            right,
            geometry,
            "USAGE HISTORY · 0–100%",
            "TOKEN ACTIVITY",
        )
        self._draw_statistics_context_markers(
            canvas,
            usage_points,
            start_time,
            end_time,
            left,
            right,
            geometry["usage_pane_top"],
            geometry["token_pane_bottom"],
        )
        usage_lanes = self._draw_statistics_usage_lanes(
            canvas,
            left,
            right,
            usage_top,
            usage_bottom,
            100.0,
            "%",
        )

        if usage_points:
            plot_width = max(1.0, right - left)
            bin_width = 4.0
            max_bin_index = max(0, int(plot_width // bin_width))
            usage_bins: dict[int, dict[str, Any]] = {}
            for point in usage_points:
                x = x_for(point["timestamp"])
                bin_index = int(clamp((x - left) // bin_width, 0, max_bin_index))
                bucket = usage_bins.setdefault(bin_index, {"timestamp": point["timestamp"]})
                bucket["timestamp"] = point["timestamp"]
                bucket["used_percent"] = point["used_percent"]
                if number(point.get("five_hour_used_percent")) is not None:
                    bucket["five_hour_used_percent"] = point["five_hour_used_percent"]

            bar_points = [usage_bins[key] for key in sorted(usage_bins)]
            last_five_hour_value: Optional[float] = None
            for point in bar_points:
                five_hour_value = number(point.get("five_hour_used_percent"))
                if five_hour_value is not None:
                    last_five_hour_value = five_hour_value
                elif last_five_hour_value is not None:
                    point["five_hour_used_percent"] = last_five_hour_value
            bar_x_values = [x_for(point["timestamp"]) for point in bar_points]
            for index, point in enumerate(bar_points):
                x = bar_x_values[index]
                if len(bar_x_values) == 1:
                    bar_left = max(left, x - bin_width / 2)
                    bar_right = min(right, x + bin_width / 2)
                else:
                    previous_x = bar_x_values[index - 1] if index > 0 else x - (bar_x_values[1] - x)
                    next_x = bar_x_values[index + 1] if index + 1 < len(bar_x_values) else x + (x - bar_x_values[index - 1])
                    bar_left = max(left, (previous_x + x) / 2)
                    bar_right = min(right, (x + next_x) / 2)
                weekly_used = number(point.get("used_percent"))
                five_hour_used = number(point.get("five_hour_used_percent"))
                for lane_key, value, color, tag in (
                    ("five_hour", five_hour_used, COLORS["cyan"], "stats-five-hour-usage-bar"),
                    ("weekly", weekly_used, COLORS["violet"], "stats-weekly-usage-bar"),
                ):
                    if value is None:
                        continue
                    lane_top, lane_bottom = usage_lanes[lane_key]
                    y = lane_bottom - (clamp(value, 0, 100) / 100) * (lane_bottom - lane_top)
                    canvas.create_rectangle(
                        bar_left,
                        y,
                        bar_right + 0.5,
                        lane_bottom,
                        fill=color,
                        outline="",
                        tags=("stats-usage-bar", tag),
                    )
        if not usage_points and not five_hour_points:
            canvas.create_text((left + right) / 2, (usage_top + usage_bottom) / 2, text="Keep the counter running to build this graph.", fill=COLORS["muted"], font=("Segoe UI", 10))

        rate_values = sorted(
            abs(point["rate_per_hour"])
            for point in [*rate_points, *five_hour_rate_points]
            if math.isfinite(point["rate_per_hour"])
        )
        if rate_values:
            observed_max = max(rate_values)
            rate_scale = max(20.0, math.ceil(observed_max / 20.0) * 20.0)
        else:
            rate_scale = 20.0
        self.stats_rate_scale = rate_scale
        for value in (rate_scale, rate_scale / 2, 0):
            y = rate_top + ((rate_scale - value) / rate_scale) * (rate_bottom - rate_top)
            canvas.create_line(left, y, right, y, fill=COLORS["soft"] if value == 0 else COLORS["line"], width=2 if value == 0 else 1)
            sign = "+" if value > 0 else ""
            decimals = 0 if rate_scale >= 100 else 1
            canvas.create_text(canvas_width - 10, y, text=f"{sign}{value:.{decimals}f}", anchor="e", fill=COLORS["muted"], font=("Segoe UI", 8))
        if rate_points:
            draw_continuous_recorded_path(
                rate_points,
                lambda point: rate_top
                + ((rate_scale - clamp(point["rate_per_hour"], 0, rate_scale)) / rate_scale)
                * (rate_bottom - rate_top),
                COLORS["violet"],
            )
        if five_hour_rate_points:
            draw_continuous_recorded_path(
                five_hour_rate_points,
                lambda point: rate_top
                + ((rate_scale - clamp(point["rate_per_hour"], 0, rate_scale)) / rate_scale)
                * (rate_bottom - rate_top),
                COLORS["cyan"],
            )
        if not rate_points and not five_hour_rate_points:
            canvas.create_text((left + right) / 2, (rate_top + rate_bottom) / 2, text="More samples are needed to estimate a trend rate.", fill=COLORS["muted"], font=("Segoe UI", 10))
        token_values = [point["token_rate_per_minute"] for point in token_rate_points if math.isfinite(point["token_rate_per_minute"])]
        if token_values:
            observed_token_max = max(token_values)
            magnitude = 10 ** math.floor(math.log10(max(1.0, observed_token_max)))
            token_scale = max(magnitude, math.ceil(observed_token_max / magnitude) * magnitude)
        else:
            token_scale = 1_000.0
        self.stats_token_scale = token_scale
        canvas.create_line(left, token_bottom, right, token_bottom, fill=COLORS["soft"], width=2)
        canvas.create_text(canvas_width - 10, token_top, text=format_token_rate(token_scale), anchor="e", fill=COLORS["muted"], font=("Segoe UI", 8))
        canvas.create_text(canvas_width - 10, token_bottom, text="0/min", anchor="e", fill=COLORS["muted"], font=("Segoe UI", 8))
        if token_rate_points:
            bar_width = max(1, min(8, int((right - left) / max(1, len(token_rate_points)) * 0.7)))
            for point in token_rate_points:
                x = x_for(point["timestamp"])
                y = token_top + (
                    (token_scale - clamp(point["token_rate_per_minute"], 0, token_scale)) / token_scale
                ) * (token_bottom - token_top)
                canvas.create_line(x, token_bottom, x, y, fill=COLORS["mint"], width=bar_width)
        else:
            canvas.create_text((left + right) / 2, (token_top + token_bottom) / 2, text="Token activity appears after two current-task samples.", fill=COLORS["muted"], font=("Segoe UI", 9))

        for fraction in (0, 0.5, 1):
            x = left + fraction * (right - left)
            canvas.create_text(x, token_bottom + 18, text=axis_label(start_time + fraction * span), fill=COLORS["muted"], font=("Segoe UI", 8))
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
            self.last_alert_buckets = {
                key: int(value // self.settings.milestone_step)
                for key, value in (
                    ("5-hour", self.snapshot.five_hour_used_percent),
                    ("weekly", self.snapshot.used_percent),
                )
                if value is not None
            }
            self.settings.save()
            self.root.attributes("-topmost", self.settings.always_on_top)
            if self.refresh_after_id:
                self.root.after_cancel(self.refresh_after_id)
            self.refresh_after_id = None
            self.refresh_async()
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
        global _instance_mutex, _show_event
        try:
            self.history.save(force=True)
            self.tray_popup.close()
            self.close_statistics()
            self.tray.stop()
        finally:
            if os.name == "nt":
                if _show_event:
                    _kernel32.CloseHandle(_show_event)
                    _show_event = None
                if _instance_mutex:
                    _kernel32.CloseHandle(_instance_mutex)
                    _instance_mutex = None
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
