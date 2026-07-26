# espm-dashboards

**Live: <https://s3-west.nrp-nautilus.io/espm-dashboards/index.html>**

Published to object storage from a workstation, not from CI — see
[Publishing](#publishing) for why, and for how to refresh it.

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

**Built output is gitignored, not committed.** `./publish.sh` builds and uploads to a
public S3 bucket, so nothing generated enters git history: the repo holds the method, the
bucket holds the current artifact. Note what that means in practice — these pages assemble
public records into views more pointed than their sources, including a ranking of named
colleagues with their appointments attached, and that is on the public web at the URL above.
Deleting the bucket contents takes the site down without touching the tool.

**Public sources only, at the rate they ask for.** No logins, no student-level records, no
FERPA-protected data. Each script documents which sites it touches and honours their
`robots.txt`, including crawl delays. If you adapt one, keep that.

## Publishing

```sh
./publish.sh                # build (~10 min), then upload
./publish.sh --no-build     # re-upload the existing build
./publish.sh --dry-run      # assemble into _site/ without uploading
```

**This runs from a workstation, deliberately, and there is no CI deploy.** Two reasons:

1. **The build cannot run in GitHub Actions.** The Berkeleytime API sits behind Cloudflare
   bot protection, which answers GitHub's datacenter IP ranges with an HTTP 403 challenge
   page instead of JSON. That check is intentional and this project does not try to defeat
   it. A first attempt at a Pages workflow failed exactly this way; the build script now
   reports the status and response so the cause is visible rather than silently producing an
   empty dashboard.
2. **No bucket credentials in CI.** Publishing needs write access to the bucket, and that
   credential stays in local `rclone` config rather than in repository secrets.

Requires `rclone` with a configured remote (default `nrp`). The bucket needs a public
download policy, set once:

```sh
mc anonymous set download nrp/espm-dashboards
```

The script refuses to upload a dashboard that still contains the data placeholder or comes
in under 100 KB, then verifies over plain HTTP that the published objects are readable.

Note that MinIO and Ceph RGW do not resolve directory-style URLs to an index document the
way AWS S3 website endpoints do — a bare prefix returns a 404 or an XML listing — so links
name `index.html` explicitly.

## Requirements

Python 3 (standard library only) and `curl` to build; `rclone` additionally to publish.
No packages to install, no virtualenv.

```sh
git clone https://github.com/boettiger-lab/espm-dashboards
cd espm-dashboards/teaching
python3 build_data.py      # ~10 minutes
open dashboard.html        # standalone; no server needed
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
