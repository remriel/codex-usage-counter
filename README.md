# Codex Usage Counter

## See your Codex limits at a glance

Codex Usage Counter is a small Windows app that shows how much of your Codex allowance you have used, how much is left, and how quickly you are using it.

The current number is always available in the Windows notification area, so you can check it without opening a dashboard or guessing when you will hit a limit.

[Download the latest Windows version](../../releases/latest)

## What the numbers mean

| On the screen | In plain English |
| --- | --- |
| **5-hour used** | How much of your short-term Codex allowance you have used. |
| **Weekly used** | How much of your weekly Codex allowance you have used. |
| **Remaining** | How much allowance is still available. |
| **Pace** | How fast your usage is increasing. |
| **ETA to limit** | An estimate of when you could reach the limit if you keep using Codex at the same pace. |
| **Reset** | When that allowance becomes available again. |
| **Tray number** | The more urgent of your current limits, visible without hovering. |

The app also shows token activity for the current Codex task. Tokens are a rough measure of how much text and reasoning Codex is processing; they are useful for spotting busy tasks, but they are not a replacement for the official usage percentage.

## Why it is useful

- Check your short-term and weekly allowance without interrupting your work.
- See whether you are spending usage slowly or quickly.
- Get a rough time estimate before reaching a limit.
- Notice reset times before starting a long task.
- Look back at your history to see when usage was highest.
- Keep the app running quietly in the notification area.

## See it in action

### The counter window

![Codex Usage Counter showing 5-hour and weekly usage, remaining allowance, pace, reset times, ETA, and token activity](screenshots/counter.png)

### The Statistics page

![Codex Usage Counter Statistics showing continuous filled usage history, separate pace lines, and token activity](screenshots/statistics.png)

The Statistics page keeps the main ideas separate so they are easier to read:

- **Usage** is shown as filled history blocks for the 5-hour and weekly limits.
- **Pace** is shown as two lines so you can see how quickly usage is changing.
- **Token activity** is shown below as its own signal.
- Click, drag, or scroll through the chart to make the cards show a specific point in time.
- Choose 1 hour, 3 hours, 12 hours, 24 hours, 48 hours, 4 days, 7 days, 30 days, or Daily.

## Quick start on Windows

1. Download `CodexUsageCounter.exe` from the [latest release](../../releases/latest).
2. Run it. The counter window opens, and the current number appears in the notification area.
3. Open **Settings** if you want to choose Used or Remaining, change how often it checks, enable Start with Windows, or adjust the milestone sound and popup.

The downloaded app is self-contained. You do not need to install Python.

## How it works

Codex saves small summary records on your computer. This app reads those records to find the 5-hour and weekly allowance values and to build a local history.

It identifies the two limits by their time window: 5 hours and 7 days. This keeps the display correct even if Codex changes the order in which it reports them.

The app checks for new information every two minutes by default. It can also notice a changed active session sooner. **Refresh now** always performs an immediate check outside the normal schedule.

The estimates are intentionally simple: they use the usage history available on your computer. A busy task, cached context, model choice, or delayed usage update can change the real pace, so the official Codex usage dashboard remains the authoritative source.

## Privacy

The app reads local summary data only. It does not read or send:

- Your conversations.
- API keys, passwords, or cookies.
- Browser data.
- `auth.json`.

The usage dashboard button opens the official dashboard in your browser when you want the authoritative account view.

## Start with Windows

You can enable or disable **Start with Windows** in Settings.

When building from source, the startup shortcut can also be installed with:

```powershell
.\install-startup.ps1
```

Use `-Remove` to remove the current-user startup shortcut.

## Run from source

```powershell
python .\codex_usage_counter.py
```

The source app uses Python's built-in Tk interface and Windows APIs for the notification-area tray icon.

## Build a standalone executable

From the project directory:

```powershell
python -m pip install pyinstaller
.\build.ps1
```

The build creates `dist\CodexUsageCounter.exe`. The executable is self-contained and does not require Python on the computer where it is run.
