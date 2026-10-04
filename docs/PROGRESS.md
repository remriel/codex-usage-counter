# Current progress

Objective: Package and publish Codex Usage Counter v1.2.0 with Codex Cloud allowance tracking.

Progress: [########--] 80% toward published release.

- [x] Confirm PR #40 merged into main at commit 847b91d.
- [x] Confirm previous release asset format: Windows EXE plus versioned source ZIP.
- [x] Define application version 1.2.0 in shared source metadata.
- [x] Run release verification suite: 58 tests passed.
- [x] Build Windows executable from main commit 703f491 with Tcl/Tk libraries staged.
- [ ] Copy release executable and source archive into outputs from the v1.2.0 tag.
- [ ] Replace installed executable and verify the packaged hashes match.
- [ ] Tag v1.2.0 and publish both assets with notes.
- [ ] Verify the GitHub release and update final project notes.

Build SHA256: EFEBD0EEC5EA5B211488A3F11F8872D20EDAF3CD0CBCE0D20EC41D3535D4E372.
Build size: 15,222,715 bytes.

Current state: build passed; v1.2.0 has not been tagged or published.

Blockers: none.

Next steps: archive the exact tagged source; upload source and EXE assets to GitHub; install the build; verify final hashes and release URL; update deliverables and project notes.
