# Current progress

Objective: Package and publish Codex Usage Counter v1.2.0 with Codex Cloud allowance tracking.

Progress: [######----] 60% toward published release.

- [x] Confirm PR #40 merged into main at commit 847b91d.
- [x] Confirm the existing release package format: Windows EXE plus versioned source ZIP.
- [x] Define application version 1.2.0 in shared source metadata.
- [ ] Run the release verification suite.
- [ ] Build the Windows executable from main and create the source archive from the same commit.
- [ ] Replace the installed executable; verify the package hashes match.
- [ ] Tag v1.2.0 and publish release assets with notes.
- [ ] Copy final PROGRESS.md and PROJECT_STATE.md to the deliverables directory.

Current state: release version metadata is committed locally; previous installed v1.1.29 and the earlier v1.2.0 untagged build remain untouched until the release build succeeds.

Blockers: none.

Next steps: validate, build, package, tag, publish, install, and verify the release.
