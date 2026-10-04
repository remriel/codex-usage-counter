# Project state

## Architecture and decisions

- Windows Tk tray app. PyInstaller one-file package; Python 3.14 needs Tcl/Tk data staging in `build.ps1`.
- Account allowances are read from the installed Codex app-server via documented `account/rateLimits/read`, independently from local JSONL telemetry.
- `account_usage.py` runs a hidden stdio app-server child, initializes once, polls every 15 seconds, uses request timeouts and capped retry backoff, and cleans up on exit.
- Counter passes the selected `codex_home` to the child. Existing Codex sign-in handles authentication. No local auth files, browser profiles, conversation text, or API keys are read or persisted.
- Account buckets are isolated to `codex`; allowance windows are identified by duration. Local task model/token metadata expires after 30 seconds without a new local allowance event.
- App-server request metadata uses `APP_VERSION` from `version.py`.
- The 58-test regression suite covers account parsing, profile selection, timeout/error handling, cleanup, and the existing local telemetry paths.

## Discoveries and constraints

- Current settings use the default ChatGPT profile. A live account read worked there. A different stale profile returned 401, so the app never silently switches profiles.
- Account polling works with no local session files and tracks account-level cloud allowance usage. The account response does not attribute allowance percentages, model names, or token totals to individual cloud tasks.
- The account reader needs a discoverable Codex executable and a signed-in selected profile.
- Generated `build` and `dist` files can be read-only on this drive. Before a rebuild, resolve and validate those directories under the repository, reject reparse points, and clear only read-only flags inside those generated trees.
- PR #40 merged to main as 847b91d. Release v1.2.0 is tagged at c1b467e.
- Production Windows build completed with `build.ps1 -SkipAssetGeneration`. The installed executable hash matches the published executable asset.

## Relevant files

- `version.py`: release version constant.
- `account_usage.py`: Codex app-server client and polling lifecycle.
- `codex_usage_counter.py`: CombinedUsageReader, UI source status, and usage history.
- `test_account_usage.py`: cloud-reader tests.
- `build.ps1`, `scripts/prepare_tk_data.py`: standalone Windows build and Tcl/Tk packaging.

## Published release v1.2.0

- Release page: https://github.com/remriel/codex-usage-counter/releases/tag/v1.2.0
- Executable: `CodexUsageCounter.exe`, 15,222,715 bytes, SHA-256 `EFEBD0EEC5EA5B211488A3F11F8872D20EDAF3CD0CBCE0D20EC41D3535D4E372`.
- Source: `CodexUsageCounter-source-v1.2.0.zip`, generated from tag v1.2.0 (commit c1b467e), SHA-256 `4833A16821CAD1461246B02BD8E164727CDC5F3A52AA56701F8A09F0D3BD6E92`.
- The release is marked latest. The installed executable has the same SHA-256 as the published EXE. Settings and usage history were preserved.
- Verification: 58 tests passed; two live account reads updated allowances with zero local session files and cleaned up the app-server child. Release assets verified through GitHub.
- Screenshot: `outputs/cloud-counter.png`. Captured from the app UI at 96 DPI with live account data and isolated empty local sessions.

## RESUME HERE

v1.2.0 is complete, published, and installed. Resume only for a new request or a reported regression. Per-cloud-task token totals and model attribution are unavailable through account allowance polling.
