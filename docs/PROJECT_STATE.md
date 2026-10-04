# Project state

## Architecture and decisions

- Windows Tk app with a background telemetry reader, main-thread update queue, native tray icon, and PyInstaller one-file packaging. Python 3.14 needs Tcl/Tk data staging.
- Local JSONL files cannot provide timely allowance changes during Codex Cloud work. Account allowances now use the installed Codex executable's documented `account/rateLimits/read` app-server method.
- `account_usage.py`: hidden stdio child, initialize/initialized handshake, independent 15-second polling, bounded requests, capped retry backoff, and child cleanup. No agent thread or inference turn is created.
- The counter passes its selected `codex_home` to Codex. Codex manages sign-in; the counter does not read or copy credentials, cookies, or browser profiles. The child's inherited `OPENAI_API_KEY` is removed to avoid API-key auth for a ChatGPT allowance query.
- `CombinedUsageReader` prefers account snapshots and retains local telemetry as fallback. Failures preserve the last success timestamp. Select the `codex` bucket and identify windows by duration.
- Model and token details remain local-only and are included only when local allowance telemetry is at most 30 seconds old. Account polling must not freshen old local task data or invent cloud token totals.
- Local monitoring remains 500 ms for changes, a two-second fallback, and full discovery every 30 seconds. History remains isolated by selected profile.
- Same-window downward corrections are valid. Rate segmentation uses changed reset timestamps. This fix is in main at `6fef7e2` (PR #39); previous pending v1.1.29 instructions were stale.

## Discoveries and constraints

- Installed CLI: `%LOCALAPPDATA%\OpenAI\Codex\bin\codex.exe`, version `0.130.0-alpha.5`.
- Current settings select `%USERPROFILE%\.codex`; a live account read succeeded. Older `.codex-chatgpt` returned HTTP 401. Never silently switch profiles.
- Closing stdin immediately after requests can end app-server before asynchronous responses arrive. Keep the connection open and correlate response IDs.
- Account polling follows aggregate allowances, without per-cloud-task attribution or cloud task token totals.
- Requires a discoverable Codex executable and existing ChatGPT sign-in. Missing CLI/service/auth must produce a clear fallback.

## Relevant files and references

- `account_usage.py`: transport and lifecycle.
- `codex_usage_counter.py`: `CombinedUsageReader`, `UsageSnapshot`, source labels, history.
- `test_account_usage.py`: cloud-source regression coverage.
- https://learn.chatgpt.com/docs/app-server#6-rate-limits-chatgpt
- https://learn.chatgpt.com/docs/cloud

## Completed delivery

- All 58 tests passed; two live account polls succeeded with an empty local sessions directory and cleaned up the child.
- Windows build completed and installed/output hashes matched. Existing profile settings and history were preserved.
- Generated staging files acquired read-only flags on this drive. Clear those flags only within verified build/dist trees before repeated packaging; do not reuse a stale executable after build failure.
- Native Computer Use screenshot capture timed out. A direct Tk verification instance captured live account data using isolated history, empty local sessions, and normalized 96 DPI. This screenshot does not establish high-DPI layout acceptance.
- Source and notes are synchronized on the cloud-usage fix branch. A dedicated cloud task acceptance run remains unobserved.

## RESUME HERE - completed

Implementation, regression checks, live account polling, Windows build, installed replacement, and delivery are complete. Follow up only on a new request or a cloud-task discrepancy. Cloud task token/model attribution remains unavailable through this allowance reader.
