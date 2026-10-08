# Project state

## Architecture and decisions

- Windows Tk tray app. PyInstaller one-file package; Python 3.14 needs Tcl/Tk data staging in `build.ps1`.
- Account allowances are read from the installed Codex app-server via documented `account/rateLimits/read`, independently from local JSONL telemetry.
- `account_usage.py` runs a hidden stdio app-server child, initializes once, polls every 15 seconds, uses request timeouts and capped retry backoff, and cleans up on exit.
- Counter passes the selected `codex_home` to the child. Existing Codex sign-in handles authentication. No local auth files, browser profiles, conversation text, or API keys are read or persisted.
- Account buckets are isolated to `codex`; allowance windows are identified by duration. Local task model/token metadata expires after 30 seconds without a new local allowance event.
- App-server request metadata uses `APP_VERSION` from `version.py`.
- The 59-test regression suite covers account parsing, profile selection, timeout/error handling, cleanup, local telemetry, and real-Tk Statistics layout and interactions. Seven additional tests cover Tcl/Tk packaging.

## Discoveries and constraints

- At 175% Windows display scaling on the current 1920 x 1080 display, DPI-unaware Tk sees a 1097 x 617 screen at approximately 96 DPI. Windows scales both fonts and canvas coordinates uniformly; changing Tk's font scaling or enabling DPI awareness alone would not fix the clipping.
- Statistics renderers force a minimum 560-pixel canvas height and `_statistics_pane_geometry` forces plot_bottom >= 620. The actual fullscreen canvas is shorter after toolbar and readout layout. This is the confirmed cause of the user's cut-off Statistics window.
- The fix makes `_statistics_canvas_dimensions` the shared viewport source for Hourly/Daily/Weekly, sizes pane bottoms from available height, and provides a vertical scrollbar only below a 480-pixel canvas height. Hourly wheel zoom remains intact; Shift+wheel scrolls any view and plain wheel scrolls Daily/Weekly. Pane headers reserve 24 pixels; aggregated-view left margins include the full pace labels.
- Native Tk tests must map the parent window before opening a transient Statistics window and allow the Windows geometry event queue to settle before asserting sizes or generating events. Otherwise tests can inspect stale dimensions or dispatch events to an unmapped canvas.
- When replacing the installed executable, normalize its path before matching process paths. After terminating the old PyInstaller parent/child pair and its own app-server child, retry transient image-file locks briefly before copying. The first immediate copy attempt can race Windows releasing the image.
- Keep Statistics rendering and interactions in the same Tk coordinate space. Use available canvas geometry; short-screen fallback must keep all content reachable. Preserve approved assets and the existing visual design for this focused bug fix.
- `docs/CURRENT_TASK.md` is historical and predates the v1.2.0 completion. Use this document and `docs/PROGRESS.md` for the current task.
- Current settings use the default ChatGPT profile. A live account read worked there. A different stale profile returned 401, so the app never silently switches profiles.
- Account polling works with no local session files and tracks account-level cloud allowance usage. The account response does not attribute allowance percentages, model names, or token totals to individual cloud tasks.
- The account reader needs a discoverable Codex executable and a signed-in selected profile.
- Generated `build` and `dist` files can be read-only on this drive. Before a rebuild, resolve and validate those directories under the repository, reject reparse points, and clear only read-only flags inside those generated trees.
- PR #40 merged to main as 847b91d. Release v1.2.0 is tagged at c1b467e.
- The v1.2.0 release build matched the published asset before the October 7 DPI fix. The currently installed executable is the verified maintenance build recorded below.

## Relevant files

- `version.py`: release version constant.
- `account_usage.py`: Codex app-server client and polling lifecycle.
- `codex_usage_counter.py`: CombinedUsageReader, UI source status, and usage history.
- `test_account_usage.py`: cloud-reader tests.
- `build.ps1`, `scripts/prepare_tk_data.py`: standalone Windows build and Tcl/Tk packaging.
- `UsageApp._statistics_canvas_dimensions`, `_statistics_pane_geometry`, `_scroll_statistics_canvas`: responsive Statistics viewport and short-screen scrolling.
- `StatisticsRenderTests.test_scaled_fullscreen_views_keep_every_pane_and_axis_visible`: five effective screen sizes, all views, complete item bounds, scrollbar reachability, selection, zoom, and pan.

## October 7 DPI maintenance build

- Fixed Statistics clipping at 175% Windows display scaling; actual before/after screenshots use isolated synthetic data and the current 1097 x 617 effective screen.
- Verified all 59 application tests and 7 packaging tests; no skipped application tests. Native generated events cover scrolling, zooming, selection, and panning. All pane and time-axis bounds fit their viewport or the reachable scroll region.
- Built with `build.ps1 -SkipAssetGeneration`; executable SHA-256 `A3163047831BBE9272DC4DB327985D49BB2C46B1D14216F97B54D0D365F42D2C`, 15,224,826 bytes.
- Installed at `C:\Users\Gev\AppData\Local\Programs\CodexUsageCounter\CodexUsageCounter.exe`, hash matches build/delivery. Settings hash unchanged; existing history files remain in place. Packaged parent/child processes and its app-server child are alive, the window responds, and live allowances are shown. Error log has no new entries.
- Backup executable in task outputs has the original v1.2.0 hash `EFEBD0EEC5EA5B211488A3F11F8872D20EDAF3CD0CBCE0D20EC41D3535D4E372`.
- No new version tag or release has been published. Source version metadata remains 1.2.0 while the fix branch is prepared for review.
- Fix source is synchronized on `fix/statistics-dpi-scaling` in https://github.com/remriel/codex-usage-counter/pull/41 (open, ready for review). Implementation commit is `2e6ec1f`; later completion commits update handoff documentation only. Main has not been merged by this task.
- Delivery outputs contain the executable, source archive, actual-175%-scaling before/after screenshots for all three views, and copies of these continuity documents. The screenshots use synthetic data rather than private usage history.

## Published release v1.2.0

- Release page: https://github.com/remriel/codex-usage-counter/releases/tag/v1.2.0
- Executable: `CodexUsageCounter.exe`, 15,222,715 bytes, SHA-256 `EFEBD0EEC5EA5B211488A3F11F8872D20EDAF3CD0CBCE0D20EC41D3535D4E372`.
- Source: `CodexUsageCounter-source-v1.2.0.zip`, generated from tag v1.2.0 (commit c1b467e), SHA-256 `4833A16821CAD1461246B02BD8E164727CDC5F3A52AA56701F8A09F0D3BD6E92`.
- The release is marked latest. At release time the installed executable had the same SHA-256 as the published EXE. The October 7 maintenance build above now replaces that local installation. Settings and usage history were preserved.
- Verification: 58 tests passed; two live account reads updated allowances with zero local session files and cleaned up the app-server child. Release assets verified through GitHub.
- Screenshot: `outputs/cloud-counter.png`. Captured from the app UI at 96 DPI with live account data and isolated empty local sessions.

## RESUME HERE

October 8: user explicitly requested publication of the release. Prepare v1.2.1, merge PR #41, rebuild with APP_VERSION=1.2.1, tag the source, publish executable/source assets, verify their hashes, and update the local installation. The DPI implementation is already verified; current source edits are version metadata and release progress only. PR #41 is open and mergeable, branch fix/statistics-dpi-scaling was clean at 637b9a9 before release preparation.

v1.2.0 remains the latest published release. Per-cloud-task token totals and model attribution are unavailable through account allowance polling.
