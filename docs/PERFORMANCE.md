# Codex Usage Counter performance and correctness audit

Date: October 14, 2026

Synthetic benchmark: 43,200 minute-level history points (30 days), and a 3.4 MB session log. Each result is the median of five runs on the same Windows/Python 3.14.7 environment. History timings isolate computation from JSON persistence; session timings include file reads. These are hot-path measurements, not complete UI latency guarantees.

| Operation | Before (ms) | After (ms) | Speedup |
| --- | ---: | ---: | ---: |
| Record history sample | 877.343 | 0.274 | 3202.0× |
| Read recent chart history | 8.235 | 0.006 | 1372.5× |
| Compute recent pace | 10.069 | 0.403 | 25.0× |
| Refresh appended telemetry | 560.018 | 1.141 | 490.8× |
| Refresh unchanged telemetry | 0.978 | 0.43 | 2.3× |

Verification: 72 application tests and 7 Tcl/Tk packaging tests passed, including native Tk Statistics sizing and input interactions. No tests skipped. Added 13 regression cases; before the fixes, 10 failed and one errored.

Fixed behavior:

- Bounded history validation and binary-search time-range queries.
- Incremental telemetry appends, with partial UTF-8/JSON retry and file identity/boundary checks.
- Token-only records update local totals without changing the allowance timestamp.
- Older model/effort and token events cannot overwrite newer metadata.
- Epoch-zero event timestamps remain valid and correctly become stale.
- Invalid/unrepresentable history and reset timestamps are rejected.
- Empty session directories respect the recursive discovery interval.
- Settings/history writes use atomic replacement; failed replacement preserves the previous JSON.
- Account process cleanup tolerates exit/kill and stream-close races.

The audit covers reproduced bugs and regression paths; it cannot establish that every possible defect is eliminated. The existing account protocol, 15-second account polling, design, profile isolation, and local-data privacy boundaries are retained. The existing persistence schedule still writes JSON synchronously; the history benchmark excludes this periodic disk cost.

Reproduce with: python scripts/benchmark_usage.py
