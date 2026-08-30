# Current task

## Objective

Make Statistics visually cohesive and immediately understandable after the usage-history change from lines to bars.

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
- Usage bars now occupy two directly labeled small-multiple lanes inside one Usage pane: 5-hour above Weekly, both using the same scale and time axis.
- Cyan consistently identifies 5-hour data, violet identifies weekly data, and mint identifies token data across cards, bars, pace lines, and selection markers.
- Usage cards have stronger value typography than pace and token cards; pane headings contain their own direct series keys, removing the detached color legend.
- Daily Pace now uses two directly labeled zero-baseline lanes with independently rounded scales: 5-hour average pace and weekly average pace. Every Daily series—usage, pace, and token activity—uses full-day bars with no connected data lines. This keeps the values in points per hour while making the smaller weekly day-to-day changes legible instead of flattening them against the regular 0–20/hour scale.

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
- Keep bars for usage, but use aligned series lanes rather than alternating paired bars at each timestamp; this preserves the requested bar encoding without turning dense history into visual texture.
- Use series identity—not metric type—as the persistent color role. Pace is distinguished by its line mark and pane position, not unrelated coral/amber hues.
- Render usage lanes as contiguous step-filled blocks: adjacent recorded time bins share an edge and fill from zero to the recorded value. Forward-fill known 5-hour values across later recorded bins, but do not invent values before 5-hour telemetry begins. Daily bars fill their complete calendar-day slot while missing days remain blank.
- In Daily Pace only, scale each allowance independently from zero with modest headroom and directly display its scale in its own lane. Use the same scale/lane map for the rendered daily bars and selected-point marker; keep every Daily metric bar-based and do not draw connected data lines.

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
- Tk QA verified non-overlapping panes and 5-hour/weekly usage lanes in regular and Daily modes, lane-contained bars, linked selection, and retention of all 15,805 raw 30-day points for interaction. The visible 30-day bars were pixel-binned to 6 available 5-hour bars and 80 weekly bars from the current history.
- Windows visual capture failed after the documented single recovery attempt (`SetIsBorderRequired`/destroyed capture stream), so no screenshot-based acceptance claim was made.
- Continuity QA verified that every adjacent regular 5-hour and weekly usage block meets or overlaps by at most Canvas rounding, selected-point markers still render, and each recorded Daily bar fills its complete 17-pixel calendar-day slot. Missing Daily dates remain blank rather than fabricating totals.
- The final PyInstaller artifact was installed over the prior unversioned executable. Installed/build SHA-256 hashes match (`576F5FC23FA3671B901015F8A062006C213B8C8E76C23D10E1986CC8111C0348`), and the normal two-process packaged app launched successfully.
- The previous installed executable was replaced. A fresh scan found no other versioned `CodexUsageCounter-v*` executables or source archives on Desktop, Downloads, Documents, or in the project outputs, so no additional files required removal.
- Refreshed `screenshots\counter.png` and `screenshots\statistics.png` from the current UI using the physical Windows window bounds; the counter capture includes live 5-hour/weekly values and the Statistics capture includes the three-pane continuous usage layout.
- Rewrote `README.md` in plain language with a visual-first feature explanation, screenshot captions, quick start, privacy summary, and build instructions.
- Returned the public README and GitHub repository tagline to a concise, conventional project description at the user’s request; the refreshed screenshots were kept unchanged.
- A synthetic Daily Canvas render with weekly rates from 0.12 to 0.84 points/hour and 5-hour rates from 4 to 18 verified distinct daily scales (1.0 vs 25.0), 24.2 pixels of weekly vertical movement, four weekly markers, and aligned selected-point markers. `python -m py_compile codex_usage_counter.py` and `git diff --check` passed.
- A fresh PyInstaller production build completed successfully. The installed `outputs\CodexUsageCounter\CodexUsageCounter.exe` SHA-256 matches the build (`74178B88D651346E36400FC1AF3AED073204C30FB7E318294E52B3A52D325165`) and the normal two-process packaged app launched successfully.
- Moved two confirmed legacy delivery folders to the Recycle Bin: `outputs\release-v1.1.17` and `2026-08-09\https-github-com-remriel-codex-usage\outputs`. The first contained the old v1.1.17 executable/source archive; the second contained an older unversioned executable/source archive and its checksum. A fresh Desktop, Downloads, Documents, and current outputs scan found only the newly installed current executable.
- A synthetic Daily Canvas render verified four 5-hour pace bars, four weekly pace bars, and four token bars with adjacent weekly bar boundaries, zero connected daily-rate lines or point marks, and five selected-value overlays (two usage, two pace, one token). `python -m py_compile codex_usage_counter.py` and `git diff --check` passed.
- The all-bars Daily production build was installed at `outputs\CodexUsageCounter\CodexUsageCounter.exe`; its SHA-256 matches the current build (`746A61C0B2FC169B76BFC3F5EE53B07795409EFDF2949F0E233FC88D6F7BE3C9`) and the normal two-process packaged app launched successfully. A fresh delivery scan found no versioned or otherwise separate older executable/archive—only the current installed executable and its current source-build output remain.

## Next steps

1. Keep public screenshots and README copy synchronized with future UI releases.
