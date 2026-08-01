# Chart Flash Animation + Daily Summary Report — Design

Date: 2026-07-31
Phase: Phase 5 (paper trading) — both features are pure UX/reporting additions.
**Constraint: neither task touches trading/scoring logic** — no changes to filter
thresholds, conviction scoring, `analyst.py` prompt content, `journal.py` outcome
resolution, or `config.py` trading parameters.

---

## Task 1 — Live chart flash during local scans

### Problem

`display.py` currently flashes each scanned ticker as text (`scanning ▸ DXCM ✅
passed`). We want a real visual chart per symbol during local scans, cosmetic only.

### Constraint discovered during brainstorming

A plain Windows terminal (PowerShell/cmd) cannot render inline raster images — no
Sixel or Kitty graphics protocol support. "Flashing chart images inside the terminal
text" is not achievable as literally stated. Confirmed with the user; agreed
alternative below.

### Approach

A single persistent **matplotlib desktop window**, opened once at the start of a
local scan, redrawn per symbol, and closed at the end of the scan:

- `plt.ion()` interactive mode; one `Figure`/`Axes` reused across all symbols (never
  create a new window per symbol — that would be the fragile approach).
- Each symbol: clear axes, draw a simple OHLC/candlestick plot (matplotlib
  `Rectangle` + `Line2D` primitives — no new dependency) from the **last 30 daily
  bars** of `daily_df`, title with `{symbol} — {passed/filtered}`, then
  `plt.pause(config.TICKER_DELAY)` (reuses the existing 0.2s constant).
- Scope mirrors the existing text animation: **every symbol scanned**, survivors and
  filtered-out alike (not survivors-only).
- Window closes when the scan loop finishes (or on exception, via try/finally).

### Gating

Two independent gates must both pass, matching the existing `animation_enabled()`
pattern:

```python
def chart_animation_enabled() -> bool:
    return bool(config.SCAN_CHART_ANIMATION) and os.getenv("CI") is None
```

- New `config.SCAN_CHART_ANIMATION = False` (default off — opt-in, since it opens a
  GUI window; distinct from `SCAN_ANIMATION` which stays on by default for text).
- `os.getenv("CI")` check is identical to the existing text-animation gate — GitHub
  Actions runners have no display, so this must never attempt to render there
  regardless of the config flag.
- When disabled, the new chart function must be a true no-op: it must not import/call
  anything from `matplotlib.pyplot` that would touch global pyplot state, so CI runs
  are unaffected in behavior or timing.

### Integration point

- New function in `display.py`: `flash_chart(symbol: str, daily_df: pd.DataFrame,
  passed: bool) -> None`.
- `main.py`'s existing loop (currently calling `display.flash_ticker(...)` for
  survivors and filtered-out entries) gains a sibling call to `display.flash_chart(...)`,
  passing `universe_frames[symbol]["daily"]`. Both calls coexist — text animation is
  not removed or altered.
- A window-lifecycle helper (e.g. `close_chart_window()`) called once after the loop
  finishes, so repeated scans (or the test suite) don't leak matplotlib figures.

### Testing

- `chart_animation_enabled()` gating logic: table-test the four combinations of
  `SCAN_CHART_ANIMATION` × `CI` env var, same style as the existing `animation_enabled()`
  tests.
- `flash_chart()` when disabled: assert no matplotlib figure is created (mock/spy on
  `plt.figure`/`plt.ion`, assert not called) — this is what keeps CI runs unaffected
  without actually needing a display.
- No test attempts real rendering or a real window in CI.

---

## Task 2 — Separate daily summary email report

### Problem

Hit-rate stats currently live inside each scan's digest. Add a **separate**, once-daily
email summarizing that day's paper-trading activity, distinct from the twice-daily
alert digests.

### Content

- What alerts fired today (premarket + preclose combined).
- How any older open alerts resolved today (win/loss/scratch/ambiguous).
- Running overall hit rate / adjusted hit rate / scratch rate, via `journal.compute_stats`
  **unmodified** — this task only calls it, never edits its logic.
- Descriptive summary of *today's* alert batch only: long vs short count, swing vs
  intraday count, average conviction. Not a new scoring system — just counts/means
  over today's fired alerts.

### New code

- `digest.py`: `build_daily_report(journal: dict, today: datetime.date) -> str`.
  Filters `journal["alerts"]` (or equivalent existing structure) to (a) alerts with
  today's date fired, and (b) alerts resolved today regardless of fire date. Builds a
  plain-text report using the existing digest formatting conventions (same module,
  same style as `build_digest`/`build_no_setup_digest`).
- `notify.py`: `send_daily_report(message: str) -> bool`. Email-only — no Telegram,
  no Discord. Reuses the existing SMTP path/credentials from `send_email`, distinct
  subject line ("Trade Scanner — Daily Report"). Wrapped in its own try/except,
  consistent with the existing senders.
- `main.py`: new entry point `run_daily_report()` plus a CLI mode to invoke it
  separately from `run_scan` (e.g. `main.py --daily-report`). Only reads
  `journal.json` and calls `compute_stats`/`build_daily_report`/`send_daily_report` —
  does **not** invoke `data.py`, `filter.py`, `news.py`, or `analyst.py`.

### Scheduling

- New entry in `config.SCAN_TIMES_PT`, e.g. `"daily_report": time(13, 15)` (1:15 PM
  PT — 15 minutes after the 1:00 PM market close, giving the preclose scan's alerts
  and same-day resolutions time to settle).
- New cron trigger in `.github/workflows/scan.yml`, same dual-offset DST-safe pattern
  as the existing scans:
  ```yaml
  # 1:15 PM PT → 20:15 UTC (PDT) / 21:15 UTC (PST)
  - cron: '15 20,21 * * 1-5'
  ```
- The workflow step for this trigger runs `main.py --daily-report` instead of the
  normal scan entry point. Reuses the existing `within_scan_tolerance` mechanism
  (matched against the new `daily_report` schedule entry) so a same-day rerun/retry
  doesn't fire outside its window.
- Does not touch the existing two scan cron entries or their steps.

### Testing

- `build_daily_report`: constructed/fake journal dict fixtures (no real `journal.json`
  file), covering: alerts fired today, older alerts resolved today, empty-day case
  (no alerts fired or resolved), and that `compute_stats`'s existing output shape is
  consumed correctly (no changes to `compute_stats` itself).
- `send_daily_report`: mocked SMTP (same mocking approach as existing `send_email`
  tests), asserting subject line and no-crash-on-missing-credentials behavior.
- No real email sends in tests.

---

## Out of scope

- No changes to filter thresholds, conviction scoring, `analyst.py` prompts,
  `journal.py` outcome-resolution logic, or any `config.py` trading parameter.
- No Discord/Telegram delivery for the daily report (email only, per requirement).
- No candlestick chart in the daily report email — Task 1 and Task 2 are unrelated
  visualizations (one local/live, one email/text).
- No historical backfill of daily reports for days before this feature ships.
