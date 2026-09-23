# Current progress

## Objective

Update Codex Usage Counter for GPT-6 model identification, fix background refresh crashes and stale usage, install the verified build, remove older executable copies, and synchronize GitHub.

## Current state

- Working branch: `fix/gpt6-counter-stability`, created from clean `main` at `1b6e6f2` (`v1.1.26` release record).
- The corrected executable is installed at `%LOCALAPPDATA%\Programs\CodexUsageCounter\CodexUsageCounter.exe`, SHA-256 `BA93F3449B79DA26B1F2E1692C8B82C22E2C1F13634632DED6BC05229AB11857`. The startup shortcut targets this path.
- Settings select `C:\Users\Gev\.codex-chatgpt`; preserve settings and both history files.
- Current source suite: `python -m pytest -q` passes **39 tests, 30 subtests**; `pyflakes` and `py_compile` pass.

## Findings

- Official OpenAI documentation lists `gpt-6-astra`, `gpt-6-sol`, and `gpt-6-luna`. Current label formatting hides the version, making GPT-6 and GPT-5.6 Sol/Luna indistinguishable.
- Live local telemetry uses `limit_id=codex` with 300-minute and 10,080-minute windows. Some `premium` events contain no allowance windows; existing reader skips them.
- `_read_file` advances the allowance snapshot timestamp to the latest model-context event. A session with newer context but older limits can therefore show stale percentages as LIVE and write incorrect history points. Observed on the user's current profile.
- `refresh_async` calls `root.after` from a worker thread. This is unsafe around Tk shutdown and is a plausible background crash path. The windowed build has no persistent exception trace.
- No matching Windows Application or WER crash report was found. The user reports exits during normal background use; the old executable was launched for comparison.
- The interim packaged build logged a concrete `NameError` in `_render_statistics` when Hourly Statistics had no recent points: `end` was undefined. The renderer now uses `end_time`; a real Tk regression covers empty Hourly, Daily, and Weekly views.
- Four older executable copies were identified in archived July and September project folders; all four were moved to the Recycle Bin after the corrected stable install was verified. Settings, history, and the source repositories remain intact.
- A fresh executable inventory found only the verified build in the repository `dist` folder and at the stable install path; both hashes match. A matching deliverable copy is in the current task's `outputs` folder.

## Implementation and verification

- Allowance freshness now comes only from allowance events; context-only sessions can update the shown model independently. Non-`codex` limit buckets are ignored.
- GPT-6 Astra/Sol/Luna labels include their generation, distinct from GPT-5.6 names.
- Reader workers use a queue; Tk receives results on its own thread. Recurring refresh callbacks reschedule after recoverable errors. Exceptions and native faults are written to `%APPDATA%\CodexUsageCounter\errors.log`.
- Malformed non-object settings and out-of-range numeric timestamps no longer cause startup/render failures.
- Regression suite passes 39 tests and 30 subtests. `pyflakes` found and helped remove an existing unused test assignment; no undefined names remain. `git diff --check` and bytecode compilation pass.
- Real Tk smoke passed with empty history across all three views and with 1,080 real history points while a hidden Statistics view received a background refresh; no callback errors.
- The first packaged build launched but failed on the empty Hourly branch; it must be replaced. Do not reuse hash `81826C19...` for release.
- The corrected packaged build launched from the stable install path, both one-file processes remained responsive, and `%APPDATA%\CodexUsageCounter\errors.log` did not grow during the installation smoke. The old logged exceptions remain for diagnosis.
- Keep temporary lint dependencies outside the repository: placing `pyflakes` under ignored `work/` made an unrestricted `pytest` run discover the package's own tests. The folder was moved to the current task's scratch directory, and the repository suite again passed 39 tests and 30 subtests.

## Next steps

Synchronize the source through a GitHub PR, publish v1.1.27 with the verified executable and source archive, then record release completion in this file.
