# Italian Vocabulary performance comparison

Measured on 2026-09-30 with Python 3.14.7 and PySide6 6.11.2 on Linux.
The baseline source was commit `f523a704b000352dca490d436c0d6d884254ba80`.
Raw measurements are in `benchmarks/before.json` and `benchmarks/after.json`.

## Method

Run `python3 benchmarks/profile_app.py --runs 5 --output /tmp/vocabulary-results.json`
from this directory. Each scenario runs in five fresh Python processes against
a temporary copy of the bundled 7,695-card default deck. Each repeated operation
is timed seven times per process. The table reports the median of those five
process medians. The before and final after measurements were taken without a
test suite running concurrently.

The window is shown with Qt's offscreen platform and the normal application
stylesheet. Window-open timing starts after Python/Qt imports and profile
opening; it includes window construction, display, and initial event processing.
Repeated operation timings cover their synchronous handlers, with no extra
event-loop rendering between calls. Word sorting alternates its two orders with
no selected row. RAM is sampled after opening the window and again after the
repeated operations and garbage collection. Peak RSS covers the whole worker.

The history scenario adds 100,000 deterministic first-round answers across 100
cards, with matching lifetime counters. All data is synthetic or bundled. Real
profiles, backups, evaluation jobs, and Codex authentication were never used.

## Results

| Measurement | Before | After | Reduction |
| --- | ---: | ---: | ---: |
| Library RAM after repeated refreshes/sorts (RSS) | 91.08 MiB | 89.18 MiB | 2.1% |
| RAM after repeated 20-card starts (RSS) | 94.58 MiB | 87.84 MiB | 7.1% |
| Private RAM after repeated 20-card starts | 46.20 MiB | 39.36 MiB | 14.8% |
| Peak RAM during 20-card starts (RSS) | 93.41 MiB | 87.68 MiB | 6.1% |
| Start a 20-card session | 53.72 ms | 46.25 ms | 13.9% |
| Library refresh, default deck | 43.15 ms | 41.40 ms | 4.0% |
| Word-sort handler, no selected row | 1.164 ms | 0.059 ms | 94.9% |
| Recall lookup with 100,000 answers | 231.44 ms | 14.00 ms | 93.9% |
| Library refresh with 100,000 answers | 268.69 ms | 49.53 ms | 81.6% |
| Window opening with 100,000 answers | 475.75 ms | 188.26 ms | 60.4% |

RSS includes shared Qt/Python library pages; private RAM reflects memory unique
to this process. PSS divides shared pages between processes and can vary with
unrelated applications. Linux's RSS sampling sources can differ slightly, so
the instantaneous RSS and the kernel peak RSS are recorded independently.
Small timing differences should be treated as noise rather than a guarantee.
The largest consistent gains are history lookup and short-session memory.
Visible desktop RAM and timings can differ with fonts, display backend, user
data, other running applications, and rendering. These are repeatable workload
measurements, not measurements of an existing live desktop instance.

## Changes and compatibility checks

- Store Library values in slotted records while retaining shared mutable study
  counters used by the base and sorted table rows. Public dictionary-returning
  storage/service calls keep their defaults.
- Keep only selection metadata for a fixed-size study pool, then load content
  for the selected cards. All-card sessions retain their full-content path.
- Use a bounded heap for smaller weighted samples, preserving the original
  ranking, stable ties, random draws, and seeded selection order.
- Fetch bounded recent answers in SQLite, backed by a first-round index. Keep
  timestamp and rowid tie ordering, first-round-only behavior, and custom limits.
- Index normal word ordering and avoid allocating ID lists or scanning for a
  selection when no row is selected. Compare unchanged history/pending sets once
  per table refresh.

Both benchmark versions returned all 7,695 cards with the same difficulty
counts: 2,928 Easy, 3,911 Medium, 856 Hard, and 0 Unknown. Every seeded 20-card
selection matched in all five before/after runs. Validation passed all 62 tests,
including added coverage for exact selection order and RNG state, history ties
and more than 500 requested IDs, selected-content hydration, and updating shared
table rows before a recall sort. Existing profile, backup, evaluation, dirty
editor, filtering, and study-round tests also passed. Python compilation and
`git diff --check` passed.

The new SQLite indexes preserve all saved records and history. Existing profiles
create them on their next open, which can briefly take longer and uses additional
disk space. No runtime data was edited during this investigation.
