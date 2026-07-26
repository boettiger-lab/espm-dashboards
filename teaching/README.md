# ESPM teaching statistics dashboard

A self-contained tool for pulling ESPM course enrollment statistics for any review window,
with the comparison set filtered the way merit review actually reads it: Academic Senate
faculty only, co-taught offerings separated out, GSIs distinguished from instructors of
record.

**Run `build_data.py`, get `dashboard.html`.** That output is a single self-contained file
with the data baked in — no server, no network access needed to view it. Open it in a
browser, email it, or drop it on a web host; it works from `file://`.

The built dashboard is **not committed** to this repo. It bakes in a ranking of named
colleagues with their appointments attached, and while every input is public, the assembled
view is yours to generate and decide who sees. Build takes about ten minutes.

## Why a build step instead of a live dashboard

The three facts needed to do this correctly come from three different places, and only one
of them is reachable from a browser:

| Fact | Source | Browser-accessible? |
|---|---|---|
| Enrollment, units, career level | Berkeleytime GraphQL API | yes |
| **Instructor of record** | `classes.berkeley.edu` | no — no CORS |
| **Appointment (Senate / lecturer / GSI)** | `ourenvironment.berkeley.edu` | no — no CORS |

A purely client-side dashboard could fetch enrollments but not the two fields that make the
analysis correct. So `build_data.py` gathers all three server-side and bakes them into the
HTML. The trade is that the data is a snapshot — re-run the script to refresh it. The build
date is shown in the dashboard header.

## Refreshing the data

Requires Python 3 (standard library only) and `curl`.

```sh
python3 build_data.py                # full refresh, ~6 minutes
python3 build_data.py --skip-harvest # reuse the API pull, re-scrape schedule + directory
python3 build_data.py --render-only  # rebuild dashboard.html from existing espm_data.json
python3 build_data.py --limit 20     # quick smoke test
```

Rate limits are deliberate and match what each site asks for:

- `classes.berkeley.edu` sets no `Crawl-delay` and does not disallow `/content/` (the pages
  this fetches), so the scrape pauses 0.25s — about 4 requests/second. A full run covers
  roughly 720 class-terms, ~5 minutes.
- `ourenvironment.berkeley.edu` asks for `Crawl-delay: 10`, so the directory pass waits 10s
  between pages — a dozen pages, ~2 minutes. Use `--skip-scrape` on reruns rather than
  re-fetching it.

Both are governed by `SCHEDULE_DELAY` and `DIRECTORY_CRAWL_DELAY` at the top of the script.
Please don't lower them.

### Files

| File | Role |
|---|---|
| `build_data.py` | the pipeline |
| `dashboard.template.html` | the UI; `/*__DATA__*/null` is where the data is injected |
| `dashboard.html` | **generated, gitignored** — the thing you open and share |
| `espm_data.json` | generated — the data on its own, if you want to analyse it elsewhere |
| `raw_catalog.json`, `scraped.json`, `directory.json` | generated caches, for `--skip-*` reruns |

## Adapting it to another department

Change `SUBJECT` in `build_data.py` from `"ESPM"` to another subject code, and point
`DIRECTORY` at that department's people listing. Everything else is subject-agnostic
**except** the directory scraper: `DIR_ROW` matches the Drupal "OpenBerkeley person" table
used by `ourenvironment.berkeley.edu`. A department on a different CMS needs a new parser for
that one function, or a hand-maintained `directory.json` of `{"Name": {"title": ..., "type":
...}}`.

## Methodology

The dashboard's "Method, sources, and caveats" panel documents this, and the **Excluded
offerings** table shows every offering the current filters removed and why, so any ranking
can be audited rather than taken on trust. In brief:

- **Filtering is per offering, not per course.** A course keeps its qualifying terms even if
  another term was taught by someone outside the comparison set.
- **Co-teaching means two or more instructors of record in the same term.** Different
  instructors in different years is not co-teaching.
- **Graduate students are removed from instructor lists first**, because Berkeley's schedule
  lists GSIs in the same field as faculty. Without this step a solo-taught course looks
  co-taught.
- **Senate faculty** includes the Professor of Teaching (LSOE) series. It excludes lecturers,
  adjunct professors, emeriti, Professors of the Graduate School (a post-retirement title),
  Cooperative Extension appointments, and anyone absent from the directory. Appointment
  classification lives in `classify()` in `build_data.py` — a short, readable function, and
  the place to change these calls if your department reads them differently.
- **Umbrella numbers are dropped entirely** (98, 99, 197–199, 290, 296–302, 375, 376, 400,
  601, 602, Berkeley Connect, 150, 194A): many unrelated sections share one number at
  varying units, so aggregating them is meaningless. ESPM 290 in one term can hold seven
  different seminars at 1–4 units each.

### Known data quirks, all handled

- The API returns **duplicate rows for cross-listed classes** (C- and H-prefixed). Naive
  summing double-counts them; the build deduplicates.
- **Berkeleytime's instructor lists are incomplete** — it reported only one instructor for
  ESPM 50AC where the official page lists two. Instructors always come from the official
  schedule.
- Enrollment occasionally disagrees between the two sources; the official schedule wins.
- **Cross-listed courses undercount**, recording only students registered under the ESPM
  subject code. They are flagged with `*` in the table; their true sizes are larger.
- Catalog coverage begins **Fall 2019**. Summer sessions usually report no enrollment.
- Terms still enrolling report partial numbers — check the term before quoting a mean.

## Caution

Enrollment is one input to a teaching case and a crude one. It says nothing about course
level, pedagogical load, lab or field components, GSI support, or how a course fits a
program's requirements. A required lower-division survey and an upper-division computational
course are not made comparable by putting their headcounts in the same column. Use the
per-term detail and the exclusion audit, not just the rank.

## A note on sharing

The dashboard you build names colleagues, labels their appointments (lecturer, adjunct,
emeritus, graduate student), and ranks them by course enrollment. Every input is public —
the class schedule and the departmental directory, neither behind a login — but the
assembled view is more pointed than its sources. Worth a moment's thought about audience
before circulating a built copy. That is why `dashboard.html` and `espm_data.json` are
gitignored rather than shipped.

`dashboard.fragment.html` is also produced: the same page without the
`<!DOCTYPE>`/`<head>`/`<body>` wrapper, for hosts that supply their own document skeleton.
For any normal use, use `dashboard.html`.
