# Current progress

Objective: Fix intermittent refresh stalls and quota mismatches, make refresh automatic and nearly real time, verify/install the Windows executable, and synchronize GitHub.

Progress: [#########-] 100% — implementation, verification, build, installation and GitHub synchronization complete.

## Completed work

- Watch all 48 candidates every 500 ms, detect day rollover, and discover new today/yesterday sessions during fast reads.
- Remove polling interval control, field and persistence. Ignore legacy saved intervals. Fixed automatic fallback: two seconds.
- Bound full recursive discovery to once per 30 seconds and reuse cached unchanged snapshots.
- Accept newer downward allowance corrections and preserve them in history; retain protection against older event timestamps.
- Isolate tray-action errors so worker results are still consumed; configure refresh controls before setting the in-flight latch.
- Avoid drawing hidden main canvas and hidden/unchanged Statistics. Redraw main canvas when shown.
- Stale quota signals use the ordinary tray icon and a STALE tooltip instead of a seemingly live numeric percentage.
- Full suite: 54 pytest tests and 33 subtests passed. Whitespace checks passed.
- Real Tk end-to-end watcher test: appended downward correction reached the UI result handler in 284.5 ms, no callback errors. Real settings dialog has no polling control.
- Production PyInstaller build passed with bundled Tcl/Tk. Installed executable, build and deliverable hashes match: `4c144565f029a11f0407122413c621693ee04d927b3940aa9ac1858d32fa16d7`.
- Installed canonical counter remained running with expected one-file parent/child processes; error log did not grow during startup smoke. Previous executable and JSON settings/history backed up outside repository.
- Real profile benchmark: cached refresh averages 10-17 ms before OS cache warmup; later warm measurement 2.22 ms. Initial recursive read varied 0.36-2.7 seconds.

## Constraints and blockers

No implementation blocker. Codex can update its server-side usage before it writes the local event. Observed a one-point delay during active work; do not claim exact real-time server parity. No long-duration stability soak or foreground tray inspection performed.

## Completion and next steps

Source commit `484a5bf` is pushed to `fix/realtime-refresh-sync`. PR: https://github.com/remriel/codex-usage-counter/pull/38. The verified executable is installed and copied to the task outputs directory. Settings were saved without the obsolete polling key, preserving display/source/startup/milestone preferences and 4,398 history points. Installed parent/child processes are responsive; persistent errors.log remains unchanged since September 22.

No implementation work remains. PR merge and a public release are still pending; this task does not claim a published release. If a future report describes a stop, inspect errors.log and current telemetry timestamps before changing the polling interval. An extended stability soak remains useful for rare failures.
