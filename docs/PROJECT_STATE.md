# Project state

## Architecture and decisions

- Standalone Windows Tk app, ctypes tray thread, background telemetry worker and main-thread result queue. No runtime third-party dependency. PyInstaller one-file packaging; Python 3.14 needs staged Tcl/Tk ZIP data.
- Reads local aggregate allowance events from configured Codex home; the official dashboard remains authoritative. Does not read credentials or call authenticated quota APIs. A local refresh cannot manufacture quota events that Codex has not written.
- Allowance timestamps and model-context timestamps remain independent. Newer allowance events must be trusted even when percentages decrease: historical-maximum clamping caused persistent mismatches. History preserves these corrections too.
- Frequent refreshes use cached candidates and today's/yesterday's directories; full recursive discovery runs every 30 seconds. This bounds work across long histories while finding unusual older-date writes.
- Watch all 48 candidates every 500 ms rather than only eight. Fixed automatic two-second fallback. Per user instruction, remove polling interval from UI and persisted settings; ignore legacy values.

## Discoveries and constraints

- Fresh upstream includes v1.1.27 timestamp and Tk-threading fixes. Do not reapply the old v1.1.26 diagnosis blindly.
- Previous progress claimed 39 tests; actual baseline unittest suite ran 32 tests.
- Installed executable uses `%LOCALAPPDATA%/Programs/CodexUsageCounter`; settings select `C:/Users/Gev/.codex`, with 15-second fallback. Preserve settings/history during replacement.
- Tray-action errors must not prevent reader result delivery, or `refresh_in_flight` can stay true forever. Configure the refresh button before setting that latch.
- Skip expensive statistics redraws for unchanged samples or hidden Statistics windows.

## Relevant files

`codex_usage_counter.py`: `CodexTelemetryReader`, `UsageApp.refresh_async`, `_poll_tray`, `_finish_refresh`, `UsageHistory._sanitize`. Tests: `test_codex_usage_counter.py`, `test_realtime_refresh.py`. Packaging: `build.ps1`, `scripts/prepare_tk_data.py`.

## RESUME HERE

Implementation, 54 tests/33 subtests, real Tk watcher/settings smoke, packaging and canonical installation are complete. Watcher-to-result latency was 284.5 ms; all executable hashes match. Finish GitHub PR and handoff; consult PROGRESS.md for exact artifact hash. Local events can lag the app usage tool by one percentage point during ongoing work, so do not promise exact server parity. No long stability soak was performed. Preserve prior executable/JSON backup in the task scratch folder.

- Hidden main canvas skips rate/chart computation; `show_window` schedules a redraw. Stale numeric tray percentages are replaced with the ordinary icon and STALE tooltip.
- Same-size synthetic rewrites must explicitly change mtime in Windows tests; rapid writes can otherwise share the cache signature. Real append detection uses both mtime and size.
