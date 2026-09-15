# Usage Counter Correctness, Performance, and Packaging Fixes

## Objective

Fix source-selection, history-isolation, hourly-bucketing, and refresh-performance issues on branch `fix/usage-counter-correctness-performance`, and make the Windows build work with Python 3.14's zip-packaged Tcl/Tk.

## Implementation

- Source selection: an explicit `codex_home` from `settings.json` always wins (used as-is even when missing), then an existing `~/.codex-chatgpt`, then `CODEX_HOME`, then `~/.codex`. Single-profile behavior is unchanged.
- Per-source history isolation: canonical `~/.codex` keeps the legacy `usage_history.json` (preserved, never migrated or deleted); every other source uses `usage_history-<digest>.json` where the digest is the first 16 hex chars of `sha256(os.path.normcase(resolved_home))`, so DeepSeek and ChatGPT samples cannot mix.
- Hourly bucketing: `UsageHistory.hourly` collapses samples on local-hour boundaries via `datetime.fromtimestamp(...).astimezone()` instead of `timestamp // 3600`, fixing UTC+05:30 offsets and DST fall-back duplicates.
- Refresh performance: `CodexTelemetryReader._candidate_files()` returns `(Path, os.stat_result)` pairs so cache-hit refreshes reuse the discovery stat instead of statting each selected candidate again; newest-48 ranking, tie order, unreadable-file skips, and signature-based invalidation are unchanged.
- Packaging: `build.ps1` gained `-SkipAssetGeneration` and calls `scripts/prepare_tk_data.py`, which stages zip-packaged Tcl/Tk into `_tcl_data`/`_tk_data` (Tcl/Tk 9 on Python 3.14 Windows) that PyInstaller's hooks miss. `scripts/test_prepare_tk_data.py` proves archive-controlled prefixes cannot extract or delete outside `<build_dir>/tk_staging`.

## Tests

- Full suite: `python -m pytest -q` → **33 passed, 27 subtests passed** (source selection, history isolation, local-hour bucketing, stat/cache invalidation, extraction safety, plus existing regressions).
- `git diff --check` clean; `python -m py_compile` passed.

## Build and deployment

- Rebuilt `dist\CodexUsageCounter.exe` with PyInstaller through `build.ps1` (`-SkipAssetGeneration`); SHA-256 `7F643EAB1480834F79AAE4B79A37406D0963429EC2FF588149D705F06FCBED9F`.
- Replaced the previous stable executable at `%LOCALAPPDATA%\Programs\CodexUsageCounter` with the new build; the installed hash matches and the old executable was sent to the Recycle Bin.
- Packaged smoke test showed a live title with both `5H` and `Week` values. The stable running process is using the ChatGPT profile and its hashed history file; the legacy `usage_history.json` remains unchanged.
- Settings pinned to `C:\Users\Gev\.codex-chatgpt`; mixed legacy history preserved with backup at `%APPDATA%\CodexUsageCounter\backup-20260914-171835`.
- The executable is not committed; `dist/`, `build/`, work logs, and regenerated assets stay out of git.

## GitHub sync

- PR #36 was merged into `main`; release commit `213cf210c20d741006eb1a81b73276d2e2cafc59` contains the verified source, tests, README, progress, `build.ps1`, and Tcl/Tk staging scripts.
- Published [v1.1.26](https://github.com/remriel/codex-usage-counter/releases/tag/v1.1.26) with the Windows executable and matching source archive. GitHub asset digests are executable `sha256:7f643eab1480834f79aae4b79a37406d0963429ec2ff588149d705f06fcbed9f` and source `sha256:4d600a81886794a6229c5b050437e42f569c1cec5953673166bbd459d0364224`.
- The prior installed executable, three stale Downloads copies, and the stale plugin output were sent to the Recycle Bin; the stable install and plugin output now match the v1.1.26 executable.

## Next steps

- No release work remains. Future app changes should start from `main` and update this handoff after each verified build or release.
