# Project state

## Architecture and decisions

- Windows Tk tray app. PyInstaller one-file package; Python 3.14 needs Tcl/Tk data staging in `build.ps1`.
- Account allowances are read from the installed Codex app-server via documented `account/rateLimits/read`, independently from local JSONL telemetry.
- `account_usage.py` runs a hidden stdio app-server child, initializes once, polls every 15 seconds, uses request timeouts and capped retry backoff, and cleans up on exit.
- Counter passes the selected `codex_home` to the child. Existing Codex sign-in handles authentication. No local auth files, browser profiles, conversation text, or API keys are read or persisted.
- Account buckets are isolated to `codex`; allowance windows are identified by duration. Local task model/token metadata expires after 30 seconds without a new local allowance event.
- App-server request metadata uses `APP_VERSION` from `version.py`; current release candidate is v1.2.0.

## Discoveries and constraints

- Current settings use the default ChatGPT profile. A live account read worked there. A different stale profile returned 401, so the app never silently switches profiles.
- Account polling works with no local session files and tracks account-level cloud allowance usage. OpenAI's account response does not attribute percentages, model names, or token totals to an individual cloud task.
- The account reader needs a discoverable Codex executable and signed-in selected profile.
- Generated `build` and `dist` files can be read-only on this drive. Before a rebuild, resolve and validate those directories under the repository, reject reparse points, and clear only read-only flags inside those generated trees.
- PR #40 merged to main as 847b91d. Version 1.2.0 adds cloud account polling.
- Release build from main 703f491 completed successfully. Windows executable SHA-256: `EFEBD0EEC5EA5B211488A3F11F8872D20EDAF3CD0CBCE0D20EC41D3535D4E372`.
- The 58-test suite passed before packaging. The current executable was built with the production `build.ps1 -SkipAssetGeneration` workflow.

## Relevant files

- `version.py`: release version constant.
- `account_usage.py`: Codex app-server client and polling lifecycle.
- `codex_usage_counter.py`: CombinedUsageReader, UI source status and usage history.
- `test_account_usage.py`: cloud-reader tests.
- `build.ps1`, `scripts/prepare_tk_data.py`: standalone Windows build and Tcl/Tk packaging.

## RESUME HERE

Tag the release-prep commit as v1.2.0, generate `CodexUsageCounter-source-v1.2.0.zip` from that exact tag, publish it and `CodexUsageCounter.exe`, then replace the installed executable and verify hashes. Update this document and PROGRESS.md after GitHub confirms the release assets.
