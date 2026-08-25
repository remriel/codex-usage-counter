# Current task

## Objective

Add privacy-safe near-real-time Codex token tracking and explain how token activity relates to weekly allowance percentage changes.

## Current state

- Branch: `agent/add-48h-4d-statistics`
- Base commit: `b85d8f8` (`Start rate collection sooner`)
- Token tracking is implemented, packaged, and ready to commit.
- The final packaged app is running from `dist\CodexUsageCounter.exe`; an earlier packaged run persisted advancing token totals automatically during an active Codex task.

## Decisions

- Read only aggregate token telemetry from `%USERPROFILE%\.codex\sessions`; never read conversation content.
- Treat token-to-percentage figures as observed efficiency, not a fixed conversion. Model choice, caching, reasoning, and delayed allowance reporting can change the relationship.
- Track current-task token totals and token pace on the main screen.
- Add aligned token activity and efficiency metrics to Statistics without placing token rate on the percentage-rate axis.
- Trigger a refresh quickly when an already-discovered active session file changes; retain configured polling as the fallback.

## Relevant files

- `codex_usage_counter.py`: reader, history, rate calculations, main UI, Statistics UI.
- `README.md`: user-facing telemetry/privacy documentation.

## Verification performed

- Inspected sanitized aggregate fields from recent local `token_count` events.
- Confirmed official OpenAI Responses usage fields include input, cached-input details, output, reasoning details, and total tokens.
- `python -m py_compile codex_usage_counter.py` passed.
- Synthetic token-rate and tokens-per-percentage-point calculations passed known fixtures.
- `build.ps1` completed successfully with PyInstaller.
- The final executable was rebuilt after the freshness fix and relaunched successfully (the two matching processes are the normal PyInstaller parent/child pair).
- The packaged process read and persisted aggregate token totals, input, cached input, output, reasoning, last response, and task identity while totals advanced from about 133.4M to 134.2M.
- Window discovery succeeded, but the desktop automation helper could not activate the app window after two attempts, so visual interaction was stopped per its safety guidance.

## Next steps

1. Commit and push the completed feature to GitHub.
