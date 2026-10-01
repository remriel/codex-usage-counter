# Current progress

Objective: Fix 5-hour “Collecting” pace and gaps caused by quota corrections being mistaken for resets; publish hotfix v1.1.29.

Progress: [######----] 60% toward published hotfix.

- [x] Compare current local reading with current Codex usage: both showed 5-hour 73-74%.
- [x] Find repeated downward corrections with the same reset time in recent local history.
- [x] Change rate segmentation: only a changed reset timestamp marks a new quota window; downward corrections in the same window continue the regression.
- [x] Build completed once after source change with `build.ps1 -SkipAssetGeneration`.
- [ ] Commit, push and merge the hotfix.
- [ ] Package and publish the new Windows executable and source archive.
- [ ] Replace installed v1.1.28 with the hotfix while preserving history/settings.

No build blockers. Source-level local telemetry delay remains possible.
