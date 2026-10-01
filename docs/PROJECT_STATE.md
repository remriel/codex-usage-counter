# Project state

## Architecture and decisions

- Windows Tk desktop app with a background reader, main-thread update queue, and native tray icon. PyInstaller one-file packaging; Python 3.14 requires Tcl/Tk zip-data staging.
- Source is aggregate allowance data in local Codex session JSONL. Server usage is authoritative and can update before this source emits a matching event.
- Automatic refresh: inspect up to 48 session candidates at 500 ms, fallback reader every two seconds, and full recursive discovery every 30 seconds.
- Allowance percentage corrections within one reset window are valid newer readings. They must not be clamped or treated as a new allowance reset.
- Rate regression uses `resets_at` / `five_hour_resets_at` to detect actual quota resets. A downward corrected sample in the same window must not break the rate-line segment or clear its regression window.

## Current release and follow-up

- Public release v1.1.28 is live. It includes fast refresh and correction handling but contains a pace-series bug: any decreasing sample clears the rolling regression and increments the line segment, even when the 5-hour reset timestamp remains unchanged. Repeated corrections can leave the pace card on “Collecting” and create visual gaps.
- Live evidence during follow-up: local 5-hour allowance matches the connected Codex usage reading, with updates every few seconds. Last eight same-window samples covered approximately two minutes; corrections reset the rate segment unnecessarily.
- Hotfix changes rate segmentation to use a changed reset boundary. Same-window corrections stay in the rolling regression.
- Current fix branch: `fix/pace-reset-gaps`. A new Windows build completed after the code change. Do not claim a published hotfix until the branch is merged and tag v1.1.29 is released.

## Relevant files

- `codex_usage_counter.py`: `UsageHistory.rate_series`, `_current_rate`, `_render_statistics`.
- `build.ps1`, `scripts/prepare_tk_data.py`.

## RESUME HERE

Publish the already built hotfix, update the installed executable, and tell the user the new release URL. Respect the build-once-publish workflow; no extra test or post-publish audit was requested.
