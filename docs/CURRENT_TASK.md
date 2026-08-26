# Current task

## Objective

Separate the Statistics display into visually independent Usage, Pace, and Token Activity panes while preserving its shared time cursor and point-level cards.

## Current state

- Branch: `agent/add-48h-4d-statistics`
- Base commit: `93ab1bf` (`Track Codex token usage in real time`)
- Local telemetry now reports a 300-minute primary window and a 10,080-minute secondary window.
- The old v1.1.15 executable selects `primary` blindly, causing it to show 5-hour usage as if it were weekly.
- Dual-window reading, history, main UI, tray behavior, milestones, rates, ETA, and Statistics are implemented, packaged, installed, and running.
- Fresh README screenshots were captured from the current dual-window UI and Statistics view.
- The current install is the unversioned `outputs\CodexUsageCounter\CodexUsageCounter.exe`; Start with Windows targets it.
- The prior v1.1.15 executable/archive and four stale versioned README files were removed. Settings and history were preserved.
- Statistics now includes a Daily interval with one bar per local calendar day across the retained 30-day history.
- Daily cards show reset-aware total usage, average level, average/peak rate, token totals/pacing, and recorded sample span for the selected day.
- Matching 5-hour and weekly Used, Remaining, Rate, and ETA cards are adjacent in the regular Statistics views; daily pairs use the same layout.
- Regular minute-level Statistics ranges render 5-hour and weekly usage as paired cyan/violet bars, while their coral/amber pace lines are in their own pane.
- The Daily view uses the same three-pane model: paired daily totals, paired daily average pace, and daily token totals.

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
- Keep the Statistics cards as the primary selection readout: clicking or dragging the chart updates all card values to the selected timestamp.
- Treat Daily as an interval, like a trading chart: one calendar day per bar, with missing days left blank rather than fabricated.
- Sum positive allowance changes across reset boundaries for daily totals; display sampled daily averages separately.
- Pixel-bin dense regular usage histories to roughly one paired bar per two horizontal pixels; keep every raw point available for selection and card updates.
- Separate usage and pace vertically instead of overlaying them: the shared x-axis/cursor supports direct comparison, while independent y-axes avoid visual competition between state and change.

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
- Selection interaction test passed: all 12 Statistics card values changed to a historical point and the readout now only identifies the selected timestamp.
- The final executable was rebuilt after the selection and mouse-wheel changes, installed over the unversioned launcher, and relaunched successfully; build and installed SHA-256 hashes match.
- The stale unversioned `dist\CodexUsageCounter-source.zip` archive was moved to the Recycle Bin; settings, history, source, and the current executable were preserved.
- A synthetic reset fixture produced 10 weekly points, 19 five-hour points, an 8.25% weekly average, and 400 daily tokens exactly as expected.
- Tk render checks passed for 25 retained daily bars, all 12 daily cards, selected-day card updates, and paired 5-hour/weekly card ordering in both daily and minute-level views.
- The PyInstaller build completed successfully; the installed executable SHA-256 matches the build and the normal two-process packaged app is running.
- A 30-day Tk render kept all 15,618 raw usage points interactive while drawing only 137 visible usage bars in a 526-pixel plot; both usage windows and selected-point cards remained functional.
- The paired-bar PyInstaller build completed successfully; the installed executable hash matches and the normal two-process packaged app is running.
- A source-level Tk render check verified exactly three non-overlapping panes in both regular and Daily modes, with the shared selection cursor still present.
- The PyInstaller build passed. The rebuilt executable was installed at `outputs\CodexUsageCounter\CodexUsageCounter.exe`, its SHA-256 matched the build output, and the normal two-process packaged app launched successfully.
- A scan of Desktop, Downloads, Documents, and the project outputs found no older explicitly versioned `CodexUsageCounter-v*` executables or source archives to remove.

## Next steps

1. Commit and push the verified pane separation. Create a numbered downloadable release only when explicitly requested.
