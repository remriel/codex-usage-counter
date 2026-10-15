# PC update complete

Objective: Update the installed counter to the published v1.2.2 release.

Progress: [##########] 100% — installed, restarted, and verified.

## October 15 local installation update

- User explicitly requested updating the PC after publication.
- Updated the existing installation to the published v1.2.2 executable; SHA-256 C20DD898174AB0E50A128CC08D3D4292BA777CF7C555C00523B3C6B00B1C3BE7 matches the release.
- Backed up the previous v1.2.1 executable and persisted settings/history before replacement. Backup is retained in task outputs under v1.2.2/pc-update-backup-20261015-090333/.
- Stopped only the installed counter's processes and its owned app-server child, replaced the executable, and restarted from the existing installation path.
- Both packaged parent/child processes are alive, the counter window responds, and its account app-server child is alive.
- Settings hash is unchanged, both existing history files are present, and all three persisted data files were byte-identical through replacement before restart.
- The error log has no new entries after restart. PC_UPDATE.json in task outputs contains the verification and rollback details; private backup/settings/history files are not committed to GitHub.

Blockers: none.

Next steps:
1. No update work remains. Resume only for a new request or regression.

# Release execution record (historical)

# Release progress

Objective: Publish Codex Usage Counter v1.2.2 (October 15, 2026).

Progress: [##########] 100% — release published as latest; assets and source tag verified.

- [x] Reconcile repository, PR #42, and release state.
- [x] Merge PR #42 into main and set APP_VERSION=1.2.2.
- [x] Pass all 72 application and seven packaging tests.
- [x] Rebuild the Windows executable and verify its embedded version/code and Tcl/Tk data.
- [x] Tag exact source, generate source ZIP, upload executable/source/checksums, and publish as latest.
- [x] Verify published asset sizes/digests and save final continuity documents and evidence.

Blockers: none.

Published release: https://github.com/remriel/codex-usage-counter/releases/tag/v1.2.2
Latest public, non-prerelease release confirmed through GitHub's releases/latest endpoint.
PR #42 is merged at a8f8e08e0f8e4a0b03188adae3e967a4e5c36214.
Tag v1.2.2 points to 7b0fba2d91a8c4cb666526808d45f29100d067f1.

Executable: CodexUsageCounter.exe, 15,253,521 bytes.
SHA-256: C20DD898174AB0E50A128CC08D3D4292BA777CF7C555C00523B3C6B00B1C3BE7.
Source: CodexUsageCounter-source-v1.2.2.zip, 2,089,217 bytes, generated from the immutable tag.
SHA-256: 3EB711A80945B5D570828CC6E76EF238A7A1D22107A37750440E5D441D38AA0D.
Published SHA256SUMS-v1.2.2.txt covers both assets. All three GitHub asset digests match local delivery files.

Verification: 72 application tests and seven packaging tests pass, no skips. Frozen APP_VERSION=1.2.2, hot-path bytecode/source agreement, and Tcl/Tk assets are verified. The first native geometry test attempt hit transient Windows event settling; the full rerun passed. Build preparation cleared read-only flags only in validated generated paths, rejecting reparse points.

The executable was rebuilt for 1.2.2; the previous 1.2.1-metadata review build was not published. Local installation was not changed by the publication request.
Release source contains the pre-publication build record. Final publication evidence is synchronized in a later documentation-only commit on main.

Delivery: outputs/v1.2.2 contains release files, test/build logs, build and publication verification, and final PROGRESS.md/PROJECT_STATE.md. Root output continuity files also reflect this release.

Next steps:
1. No release work remains. Resume only for a new request or regression.

# Previous execution state (historical)

# Current progress

Objective: Fix reproduced bugs and optimize Codex Usage Counter performance (October 14, 2026).

Progress: [##########] 100% — verified fixes synchronized in PR #42; Windows review build and delivery artifacts complete.

- [x] Reconcile clean main fef6a1b, project notes, and GitHub issues.
- [x] Capture baseline performance and reproduce bugs with isolated synthetic fixtures.
- [x] Implement bounded history updates, incremental telemetry reads, metadata ordering, timestamp validation, atomic persistence, and resilient process cleanup.
- [x] Add 13 regressions and verify all 72 application tests plus seven Tcl/Tk packaging tests; no skips.
- [x] Benchmark final implementation and save docs/PERFORMANCE.md.
- [x] Synchronize the fix branch and open/attach GitHub PR #42.
- [x] Build and inspect the Windows executable; deliver source, evidence, and continuity files.

Current implementation: fix/usage-correctness-performance. Existing release metadata remains 1.2.1.
Blockers: none. Packaging succeeded with isolated PyInstaller 6.22.3 and the project Tcl/Tk staging helper.
Verification: python -m unittest discover -v; python -m unittest discover -s scripts -p test_*.py -v; python scripts/benchmark_usage.py; git diff --check.
Performance: 43,200 history points, median five runs; record 877.343 -> 0.274 ms (JSON writes excluded); appended telemetry 560.018 -> 1.141 ms. Full evidence is in docs/PERFORMANCE.md.

GitHub: https://github.com/remriel/codex-usage-counter/pull/42 (open, ready for review, mergeable with clean merge state). Implementation commit: e7ef05334a57cffd5206a732227a594c0bbc1c8e.

Build: CodexUsageCounter.exe, 15253464 bytes, SHA-256 51BCDCE071B9598AE637C4504D24F9814F9EB59B2A04113AB384B7065683AFAE. Packaged hot-path bytecode matches the verified source, and Tcl/Tk data is present. This is a review build using the existing 1.2.1 version metadata; the installed app and published release are unchanged.

Delivery files: CodexUsageCounter.exe, CodexUsageCounter-performance-fix-source.zip, PERFORMANCE_REPORT.md, verification/test logs, PROGRESS.md, PROJECT_STATE.md, and SHA256SUMS.txt in task outputs.

Next steps:
1. No implementation or delivery work remains for this request.
2. Review/merge PR #42; a versioned public release and installation are separate follow-up work.

# Previous work (historical)

# Current progress

Objective: Publish Codex Usage Counter v1.2.1 with the verified 175% Windows display scaling fix.

Progress: [##########] 100% - v1.2.1 published as latest, assets verified, and matching executable installed.

- [x] Reconcile project notes, clean git state, PR #41, and existing releases.
- [x] Set APP_VERSION to 1.2.1.
- [x] Run application and Tcl/Tk packaging tests.
- [x] Push release preparation and merge PR #41.
- [x] Build the Windows release executable from main.
- [x] Tag v1.2.1 and create the source archive from that tag.
- [x] Publish GitHub release with executable and source assets.
- [x] Verify published asset hashes and update the local installation.
- [x] Synchronize final documentation and copy it to outputs.

Implementation: PR #41 merged into main as c5d3af5. APP_VERSION is 1.2.1. Release executable contains that version (verified from its embedded Python archive). Tag v1.2.1 points to 9d8c333671999bc2ba27fd116ec4318fee230e7c. GitHub confirms v1.2.1 is the latest public, non-prerelease release.

Blockers: none.

Verification: 59 application tests and 7 packaging tests passed after the version update. build.ps1 -SkipAssetGeneration succeeded after clearing read-only flags on validated generated files AND directories. Packaged version constant verified as 1.2.1. Executable is 15,223,625 bytes, SHA-256 9D6D9399059A2C4E69766FD9FCAF2A91E156471AAA4A9B8D3B918535EA37E044. Installed executable matches the build, retains the normal packaged parent/child process pair, and settings hash is unchanged. Previous untagged DPI build is backed up in outputs.

Release: https://github.com/remriel/codex-usage-counter/releases/tag/v1.2.1

Published executable: CodexUsageCounter.exe, 15,223,625 bytes, SHA-256 9D6D9399059A2C4E69766FD9FCAF2A91E156471AAA4A9B8D3B918535EA37E044.
Published source: CodexUsageCounter-source-v1.2.1.zip, 2,073,440 bytes, SHA-256 A58ABFD5C31A8F0521A18F093C0E75AB653A91EA3FDCA00FDCCC8E94EC9FEEC7.
Both GitHub asset digests match the local files. The source archive is generated from the immutable release tag; final publication notes are recorded in a later documentation-only commit on main.

Next steps:
1. No release work remains. Resume only for a new request or a reported issue.

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
