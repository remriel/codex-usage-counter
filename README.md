# Codex Usage Counter

A live Windows tray counter for Codex 5-hour and weekly usage, rates, ETAs, reset countdowns, and token activity.

[Download the latest Windows release](../../releases/latest)

## What it does

- Shows independent 5-hour and weekly Codex allowance percentages, reset countdowns, pace, and ETA.
- Shows the currently tracked model and reasoning effort prominently on the main counter, such as **TRACKING · SOL · HIGH**.
- Tracks current-task input, cached-input, output, reasoning, total, and last-response token counts from local Codex aggregate telemetry.
- Refreshes within about a second when an already-discovered active Codex session file changes; configurable polling remains the fallback.
- Shows timestamped model and reasoning-effort changes on the detailed Hourly chart with thin, subtle amber model markers and thin, subtle dashed-coral effort markers.
- Shows the active or selected model and reasoning effort as a large, simplified **Hourly** Statistics header label such as **SOL · HIGH**; aggregate Daily and Weekly views omit context because it can change within an interval.
- Keeps Statistics focused on **Hourly** (the default; mouse-wheel zoom from one minute through all retained history), **Daily**, and **Weekly** views.
- Displays the most constrained allowance directly in the notification-area tray icon, so the number is useful without hovering; the tooltip identifies both windows.
- Shows a custom in-app tray milestone popup above the Windows notification area; it does not use Windows toast or balloon notifications.
- Polls local usage every two minutes by default; every automatic read refreshes usage, rate, and ETA together, while **Refresh now** reads immediately outside that schedule.
- Supports Used or Remaining display mode, always-on-top behavior, optional Start with Windows behavior, optional custom milestone chime, configurable polling, trigger percentage, and popup duration.
- Keeps minute-level usage history in the zoomable **Hourly** view, plus stock-chart-style **Daily** and **Weekly** intervals with reset-aware 5-hour/weekly totals, pace, and token activity. Daily and Weekly usage and pace bars include thin trend lines with circular points across adjacent recorded intervals.
- Opens Statistics as a fullscreen view with a responsive, edge-to-edge chart canvas.
- Keeps matching 5-hour and weekly cards side by side and updates those cards when an Hourly, Daily, or Weekly point is clicked, dragged across, or selected with the mouse wheel.
- Provides continuous 5-hour and weekly usage bars in separate lanes, overlays 5-hour and weekly pace in one pane with separate color-coded Y-axes, fixes weekly pace at 0–100 pts/hr while 5-hour pace scales independently, packs active sessions together by removing idle time from the Hourly x-axis, and keeps pace paths broken at every session boundary so no false connecting lines are drawn.

## Quick start on Windows

1. Download `CodexUsageCounter.exe` from the [latest release](../../releases/latest).
2. Run it. The app starts in the notification area and opens its counter window.
3. Use **Settings** to choose Used or Remaining, enable or disable Start with Windows, and adjust polling or milestone behavior.
4. To start it with Windows, run `install-startup.ps1` from the downloaded package, or use the packaged app path when prompted.

The executable is self-contained and does not require Python to be installed.

## Screenshots

### Counter window

![Codex Usage Counter showing the tracked model and effort above separate 5-hour and weekly allowance cards, rates, ETAs, and token activity](screenshots/counter.png)

### Statistics view

![Codex Usage Counter Hourly Statistics showing the prominent model and effort context, continuous 5-hour and weekly usage, pace lines, and token activity](screenshots/statistics.png)

## How the data is read

The counter reads aggregate `rate_limits` and `token_count` events, plus the model and reasoning-effort metadata required for chart annotations, from JSONL files under:

```text
%USERPROFILE%\.codex\sessions
```

The reader identifies allowance windows by their reported duration: 300 minutes for the 5-hour limit and 10,080 minutes for the weekly limit. It does not assume `primary` always means weekly, so both windows remain correct if their field positions change. Historical weekly samples remain available; the 5-hour history begins when Codex first reports that window and is not fabricated for earlier periods.

It does not read `auth.json`, API keys, cookies, browser profiles, or store conversation content. The only extra session context it retains is a short model identifier and reasoning-effort value, timestamped with a local usage sample. Token totals are scoped to the currently active local Codex task. Token-to-percentage statistics are observed relationships, not fixed conversions: model behavior, caching, reasoning, concurrent tasks, and delayed allowance reporting can change them. The usage dashboard link opens the official dashboard for the authoritative view. Because the counter is based on local session telemetry, it can show a stale signal until a newer Codex event is written; **Refresh now** forces an immediate local read.

Closing the window hides it to the tray. Use the tray menu’s **Quit** command to exit.

## Run from source

```powershell
python .\codex_usage_counter.py
```

The source app uses Python’s built-in Tk interface and Windows APIs for the tray icon. It has no third-party runtime dependency.

## Build a standalone executable

From the project directory:

```powershell
python -m pip install pyinstaller
.\build.ps1
```

The build script regenerates the numeric tray icon set in `assets\taskbar` and creates `dist\CodexUsageCounter.exe` with PyInstaller.

## Start with Windows

After building, run:

```powershell
.\install-startup.ps1
```

Use `-Remove` to remove the current-user Startup shortcut.
