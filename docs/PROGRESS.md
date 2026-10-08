# Current progress

Objective: Publish Codex Usage Counter v1.2.1 with the verified 175% Windows display scaling fix.

Progress: [##--------] 20% - release scope confirmed and version updated.

- [x] Reconcile project notes, clean git state, PR #41, and existing releases.
- [x] Set APP_VERSION to 1.2.1.
- [ ] Run application and Tcl/Tk packaging tests.
- [ ] Push release preparation and merge PR #41.
- [ ] Build the Windows release executable from main.
- [ ] Tag v1.2.1 and create the source archive from that tag.
- [ ] Publish GitHub release with executable and source assets.
- [ ] Verify published asset hashes and update the local installation.
- [ ] Synchronize final documentation and copy it to outputs.

Implementation: existing verified Statistics viewport fix; only version metadata changes for this release. Current branch is fix/statistics-dpi-scaling. Latest published release remains v1.2.0.

Blockers: none.

Verification: previous fix passed 59 application tests and 7 packaging tests, plus screenshots at actual 175% scaling. Release-specific checks pending after version update.

Next steps:
1. Run tests, commit release preparation, and push.
2. Merge PR #41 and build the versioned executable from main.
3. Tag, archive, publish, verify assets, and update the installed executable.
4. Record completion and deliver release and continuity links.

## Previous DPI fix delivery

Objective: Fix Statistics being cut off at 175% Windows display scaling, verify the three views, build the corrected executable, and synchronize the fix with GitHub.

Progress: [##########] 100% - verified fix installed and running; source synchronized in PR #41; deliverables complete.

- [x] Locate and clone remriel/codex-usage-counter; confirm clean main at 52196f2.
- [x] Read project notes and reconcile them with source and git state.
- [x] Confirm 175% Windows scaling exposes a 1097 x 617 Tk screen.
- [x] Identify renderer minimum height (560) and forced plot bottom (620) exceeding the canvas.
- [x] Capture a synthetic-data reproduction with real Tk.
- [x] Make pane layout fit the viewport and keep content accessible on shorter screens.
- [x] Verify Hourly, Daily, Weekly, selection, zoom, and panning.
- [x] Run regression tests and build the Windows executable.
- [x] Back up v1.2.0 and install the fixed build in the existing application location.
- [x] Push the fix and open a pull request.
- [x] Deliver executable, screenshots, source archive, and final continuity documents.

Implementation: branch fix/statistics-dpi-scaling. Renderers use the actual canvas height, with a 480-pixel scrollable fallback on short screens. A 24-pixel pane header separates titles from chart ticks. Daily/Weekly axis margins keep pace labels inside the canvas.
Blockers: none.
Verification: all 59 application tests and 7 Tcl/Tk packaging tests pass. The new clipping regression failed on the old renderer and passes with the fix. Real-Tk checks cover effective 1920x1080, 1536x864, 1280x720, 1097x617, and 960x540 screens and all three views, plus generated events for selection, Shift+wheel scrolling, wheel zoom, and right-button panning. At actual 175% scaling, Hourly canvas is 1097x505 and all content now ends at y=496; Daily/Weekly canvas is 1097x517 with content ending at y=508. No Tk callback errors. Before/after screenshots are saved in the task outputs.

Build: build.ps1 -SkipAssetGeneration completed with Python 3.14.7 / Tk 9.0.4 / PyInstaller 6.21.0. The fixed executable is 15,224,826 bytes, SHA-256 A3163047831BBE9272DC4DB327985D49BB2C46B1D14216F97B54D0D365F42D2C.

Installation: C:\Users\Gev\AppData\Local\Programs\CodexUsageCounter\CodexUsageCounter.exe. Installed hash matches the output executable and source build. Normal parent/child processes are alive, the Tk window responds, and live allowance data appears in the window title. Settings hash is unchanged. The error log has no new entries. Previous v1.2.0 executable is backed up in task outputs. This is a maintenance build from the fix branch; no new release tag has been published.

GitHub: https://github.com/remriel/codex-usage-counter/pull/41 (open, ready for review). Implementation commit: 2e6ec1f. The branch is synchronized with origin. Main and the latest published release remain at v1.2.0 pending PR review and a future release.

Deliverables in the October 7 task outputs: CodexUsageCounter.exe, CodexUsageCounter-dpi-fix-source.zip, statistics-after-hourly-175.png, statistics-after-daily-175.png, statistics-after-weekly-175.png, PROGRESS.md, PROJECT_STATE.md, and the previous executable backup. Before-fix screenshots are also retained for comparison.

Next steps:
1. No implementation, build, installation, or verification work remains for this request.
2. Review/merge PR #41 and publish a new maintenance release only when requested.

## Previous release (v1.2.0)

- [x] Confirm PR #40 merged into main at commit 847b91d.
- [x] Define application version 1.2.0 in shared metadata.
- [x] Run release verification suite: 58 tests passed.
- [x] Build Windows executable with Tcl/Tk libraries staged.
- [x] Tag source as v1.2.0 at commit c1b467e.
- [x] Generate versioned source ZIP from the v1.2.0 tag.
- [x] Publish the executable and source archive in the GitHub release.
- [x] Replace installed executable and verify its SHA256 matches the published asset.
- [x] Save release assets, screenshot, and final project notes in outputs.

Release: https://github.com/remriel/codex-usage-counter/releases/tag/v1.2.0

Windows executable SHA256: EFEBD0EEC5EA5B211488A3F11F8872D20EDAF3CD0CBCE0D20EC41D3535D4E372
Source archive SHA256: 4833A16821CAD1461246B02BD8E164727CDC5F3A52AA56701F8A09F0D3BD6E92
Published assets: CodexUsageCounter.exe (15,222,715 bytes), CodexUsageCounter-source-v1.2.0.zip (2,074,211 bytes).
