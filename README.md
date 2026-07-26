# espm-dashboards

**Live: <https://boettiger-lab.github.io/espm-dashboards/>** — rebuilt weekly by GitHub
Actions.

Small, self-contained dashboards over public UC Berkeley ESPM data. Each lives in its own
subdirectory with a build script and a template; running the script produces one standalone
HTML file that needs no server and works from `file://`.

| Dashboard | What it answers |
|---|---|
| [`teaching/`](teaching/) | How large are ESPM courses, over any review window, compared only to courses that are actually comparable? |

## The shared pattern

Every dashboard here follows the same shape, for the same reason:

**A Python build step, then a static page.** The interesting questions need data from sources
a browser cannot reach — Berkeley's class schedule and the departmental directory serve no
CORS headers, so client-side JavaScript can fetch neither the instructor of record nor
anyone's appointment. The build script gathers everything server-side and bakes it into the
HTML. The cost is that output is a snapshot; re-run the script to refresh it. The build date
is shown in the page header.

**Built output is gitignored, not committed.** `.github/workflows/pages.yml` runs the build
in CI and publishes the result to GitHub Pages, so nothing generated enters git history — the
published site is the only built copy, and it is always reproducible from the script. Note
what that means in practice: these pages assemble public records into views more pointed than
their sources, including a ranking of named colleagues with their appointments attached. That
is on the public web at the URL above. Removing the `schedule:` trigger, or the workflow
entirely, takes the site down without touching the tool.

**Public sources only, at the rate they ask for.** No logins, no student-level records, no
FERPA-protected data. Each script documents which sites it touches and honours their
`robots.txt`, including crawl delays. If you adapt one, keep that.

## Requirements

Python 3 (standard library only) and `curl`. No packages to install, no virtualenv.

```sh
git clone https://github.com/boettiger-lab/espm-dashboards
cd espm-dashboards/teaching
python3 build_data.py      # ~10 minutes
open dashboard.html
```

## Caveats worth reading before quoting a number

Each subdirectory's README documents its own, but two are general:

- **Enrollment is a crude proxy for teaching.** It says nothing about course level,
  pedagogical load, lab or field components, GSI support, or how a course serves a program's
  requirements. Use the per-term detail and the exclusion audit, not just the rank.
- **Terms still in progress report partial numbers.** The dashboards detect and flag these,
  but check before quoting a mean that includes one.

## License

BSD 3-Clause. See [LICENSE](LICENSE).
