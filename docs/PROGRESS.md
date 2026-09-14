# Current progress

## Objective

Add a tokens/min trend overlay to the Statistics token activity pane without changing the existing usage or pace calculations.

## Current state

- Repository: `work/codex-usage-counter`, clean at the start of this task.
- Existing Statistics views: Hourly, Daily, Weekly.
- Hourly token activity currently renders point-to-point token throughput as bars.
- Daily and Weekly token activity currently renders total-token bars and now has a plotted tokens/min overlay with a separate rate scale.

## Decisions

- Keep token activity bars and add a clearly labeled contrasting tokens/min line.
- Use separate token-count and tokens/min scales in Daily and Weekly because those are different units.
- In Hourly, use observed point-to-point throughput for the bars and the smoothed tokens/min series for the overlay line. In Daily and Weekly, retain total-token bars and plot the aggregate average tokens/min line.
- Preserve session-aware gaps; the overlay must not connect across inactive intervals.

## Verification plan

- Add regression coverage for raw and smoothed token-rate values.
- Run the existing Python tests and a Tk chart-render smoke check.
- Build the Windows executable once after source changes.

## Completed

- Added a coral tokens/min trend line over the token activity pane in Hourly, Daily, and Weekly Statistics.
- Hourly bars now show observed point-to-point token throughput while the line shows the smoothed tokens/min pace; inactive-session gaps remain blank.
- Daily and Weekly retain total-token bars and add a tokens/min line with a separate rate scale so counts and rates are not mixed on one axis.
- Selection markers now track the tokens/min line as well as the token-total bar.
- Updated the README feature description and added regression coverage for observed versus smoothed token rates.
- Verification passed: `python -m py_compile`, 11 unittest cases, `git diff --check`, and a real Tk render smoke through Hourly, Daily, and Weekly Statistics.
- Built and installed the current executable at `outputs\\CodexUsageCounter\\CodexUsageCounter.exe`; installed SHA-256 is `70783A34280BA6E035D6F4FE26E7F259F5A134FC8EB5E8211D30AD06462EFC99`. Two canonical packaged processes are running.

## Next steps

No task work remains. Source synchronization is the final step; publishing a new GitHub release was not requested.
