# Usage Counter Correctness, Performance, and Packaging Fixes

## Objective

Fix source-selection, history-isolation, hourly-bucketing, and refresh-performance issues on branch `fix/usage-counter-correctness-performance`, and make the Windows build work with Python 3.14's zip-packaged Tcl/Tk.

## Implementation (committed on this branch)

- Source selection: an explicit `codex_home` from `settings.json` always wins (used as-is even when missing), then an existing `~/.codex-chatgpt`, then `CODEX_HOME`, then `~/.codex`. Single-profile behavior is unchanged.
- Per-source history isolation: canonical `~/.codex` keeps the legacy `usage_history.json` (preserved, never migrated or deleted); every other source uses `usage_history-<digest>.json` where the digest is the first 16 hex chars of `sha256(os.path.normcase(resolved_home))`, so DeepSeek and ChatGPT samples cannot mix.
- Hourly bucketing: `UsageHistory.hourly` collapses samples on local-hour boundaries via `datetime.fromtimestamp(...).astimezone()` instead of `timestamp // 3600`, fixing UTC+05:30 offsets and DST fall-back duplicates.
- Refresh performance: `CodexTelemetryReader._candidate_files()` returns `(Path, os.stat_result)` pairs so cache-hit refreshes reuse the discovery stat instead of statting each selected candidate again; newest-48 ranking, tie order, unreadable-file skips, and signature-based invalidation are unchanged.
- Packaging: `build.ps1` gained `-SkipAssetGeneration` and calls `scripts/prepare_tk_data.py`, which stages zip-packaged Tcl/Tk into `_tcl_data`/`_tk_data` (Tcl/Tk 9 on Python 3.14 Windows) that PyInstaller's hooks miss. `scripts/test_prepare_tk_data.py` proves archive-controlled prefixes cannot extract or delete outside `<build_dir>/tk_staging`.

## Tests

- Full suite: `python -m pytest -q` → **33 passed, 27 subtests passed** (source selection, history isolation, local-hour bucketing, stat/cache invalidation, extraction safety, plus existing regressions).
- `git diff --check` clean; `python -m py_compile` passed.

## Build and deployment

- Rebuilt `dist\CodexUsageCounter.exe` with PyInstaller through `build.ps1` (`-SkipAssetGeneration`); SHA-256 `5D34D50647E2E44C28703F177C3E15372901397023C6014370C99C0CB34778DA`.
- Installed stable build to `%LOCALAPPDATA%\Programs\CodexUsageCounter` (installed hash matches); startup shortcut updated.
- Running packaged process PID 21032 reports window title `Codex Usage  5H 42%  Week 2% remaining` (hidden to tray).
- Settings pinned to `C:\Users\Gev\.codex-chatgpt`; mixed legacy history preserved with backup at `%APPDATA%\CodexUsageCounter\backup-20260914-171835`.
- The executable is not committed; `dist/`, `build/`, work logs, and regenerated assets stay out of git.

## GitHub sync

- Source, tests, README, progress, `build.ps1`, and Tcl/Tk staging scripts are ready to commit and push on `fix/usage-counter-correctness-performance`, then open a PR (not merged). PR URL and commit SHA are recorded in the workspace-root `docs/PROGRESS.md` handoff after push.

## Next steps

- Review/merge the PR only when requested; publish a GitHub release only when explicitly requested.
