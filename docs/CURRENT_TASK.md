# Current task

## Objective

Track the restored 5-hour Codex allowance alongside the weekly allowance, package the corrected app, and remove previous local versions without deleting settings/history or source.

## Current state

- Branch: `agent/add-48h-4d-statistics`
- Base commit: `93ab1bf` (`Track Codex token usage in real time`)
- Local telemetry now reports a 300-minute primary window and a 10,080-minute secondary window.
- The old v1.1.15 executable selects `primary` blindly, causing it to show 5-hour usage as if it were weekly.
- Dual-window reading, history, main UI, tray behavior, milestones, rates, ETA, and Statistics are implemented, packaged, installed, and running.
- The current install is the unversioned `outputs\CodexUsageCounter\CodexUsageCounter.exe`; Start with Windows targets it.
- The prior v1.1.15 executable/archive and four stale versioned README files were removed. Settings and history were preserved.

## Decisions

- Read only aggregate token telemetry from `%USERPROFILE%\.codex\sessions`; never read conversation content.
- Treat token-to-percentage figures as observed efficiency, not a fixed conversion. Model choice, caching, reasoning, and delayed allowance reporting can change the relationship.
- Track current-task token totals and token pace on the main screen.
- Add aligned token activity and efficiency metrics to Statistics without placing token rate on the percentage-rate axis.
- Trigger a refresh quickly when an already-discovered active session file changes; retain configured polling as the fallback.
- Identify limits by `window_minutes`, never by primary/secondary position.
- Keep `used_percent` as the weekly history field for backward compatibility; store 5-hour values in explicit `five_hour_*` fields.
- Show the most constrained window in the numeric tray icon and both windows in the tooltip/main screen.
- Preserve previous weekly history and leave pre-feature 5-hour history missing rather than inventing values.

## Relevant files

- `codex_usage_counter.py`: reader, history, rate calculations, main UI, Statistics UI.
- `README.md`: user-facing telemetry/privacy documentation.

## Verification performed

- Inspected sanitized aggregate fields from recent local `token_count` events.
- Confirmed official OpenAI Responses usage fields include input, cached-input details, output, reasoning details, and total tokens.
- `python -m py_compile codex_usage_counter.py` passed.
- Synthetic token-rate and tokens-per-percentage-point calculations passed known fixtures.
- `build.ps1` completed successfully with PyInstaller.
- The final executable was rebuilt after the freshness fix and relaunched successfully (the two matching processes are the normal PyInstaller parent/child pair).
- The packaged process read and persisted aggregate token totals, input, cached input, output, reasoning, last response, and task identity while totals advanced from about 133.4M to 134.2M.
- Window discovery succeeded, but the desktop automation helper could not activate the app window after two attempts, so visual interaction was stopped per its safety guidance.
- Live reader returned 5-hour/300-minute and weekly/10,080-minute values from the same event.
- Duration-based classification passed with primary/secondary in either order.
- Weekly and 5-hour rate fixtures, reset segmentation, old-history compatibility, compilation, and diff checks passed.
- The source app launched with a window title containing both live limits; the Windows capture helper failed its capture and one recovery attempt, so no screenshot review was claimed.
- PyInstaller production build passed; installed and build executable SHA-256 hashes matched.
- A non-interactive Tk render test passed with 68 main-window canvas elements, 81 Statistics elements, and live 5-hour points in the Statistics inspection data.
- The installed build launched with `5H` and `Week` values in its title. The expected PyInstaller parent/child process pair is running.
- Desktop, Downloads, and Documents contain no remaining `CodexUsageCounter-v*.exe` or `CodexUsageCounter-source-v*.zip` files.

## Next steps

1. Commit and push GitHub.
