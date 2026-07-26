#!/usr/bin/env bash
#
# Build the dashboards and publish them to S3 (NRP Ceph, or any S3 endpoint).
#
# Why this is a script you run rather than a GitHub Actions workflow: the Berkeleytime
# API sits behind Cloudflare bot protection, which answers GitHub's datacenter IP
# ranges with an HTTP 403 challenge page. That check is deliberate and this project
# does not try to defeat it, so the build has to happen somewhere with ordinary
# network access -- a workstation, or a runner on campus infrastructure.
#
# Publishing to object storage rather than a gh-pages branch keeps the built data out
# of git entirely: the repo holds the method, the bucket holds the current artifact.
#
# Requires: rclone with a configured remote (default `nrp`), python3, curl.
#
# Usage:
#   ./publish.sh                     build, then publish
#   ./publish.sh --no-build          publish the existing build (skip the ~10 min refresh)
#   ./publish.sh --dry-run           assemble into _site/ but do not upload
#   ./publish.sh --remote minio      use a different rclone remote
#   ./publish.sh --bucket my-bucket  use a different bucket
#
set -euo pipefail
cd "$(dirname "$0")"

REMOTE=nrp
BUCKET=espm-dashboards
BASE_URL=""
BUILD=1
PUSH=1

while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-build) BUILD=0; shift ;;
    --dry-run)  PUSH=0; shift ;;
    --remote)   REMOTE="$2"; shift 2 ;;
    --bucket)   BUCKET="$2"; shift 2 ;;
    --base-url) BASE_URL="$2"; shift 2 ;;
    -h|--help)  sed -n '2,22p' "$0"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

if [[ -z "$BASE_URL" ]]; then
  endpoint=$(rclone config show "$REMOTE" 2>/dev/null | awk -F'= ' '/^endpoint/{print $2}')
  if [[ -z "$endpoint" ]]; then
    echo "Could not read an endpoint for rclone remote '$REMOTE'." >&2
    echo "Configure it (rclone config) or pass --base-url explicitly." >&2
    exit 1
  fi
  BASE_URL="${endpoint%/}/$BUCKET"
fi

if [[ $BUILD -eq 1 ]]; then
  echo "==> building teaching dashboard"
  ( cd teaching && python3 build_data.py )
else
  echo "==> skipping build (--no-build)"
fi

echo "==> assembling _site"
rm -rf _site
mkdir -p _site/teaching
cp teaching/dashboard.html _site/teaching/index.html
cp teaching/espm_data.json _site/teaching/espm_data.json

# MinIO and Ceph RGW do not resolve directory-style URLs to an index document the way
# AWS S3 website endpoints do -- a bare prefix returns 404 or an XML listing. So every
# link here names its object explicitly, and the landing page is index.html at the root.
cat > _site/index.html <<HTML
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ESPM dashboards</title>
<style>
  :root { color-scheme: light; --bg:#f9f9f7; --fg:#0b0b0b; --dim:#52514e; --accent:#2a78d6; --line:rgba(11,11,11,.12); }
  @media (prefers-color-scheme: dark) { :root:where(:not([data-theme="light"])) { color-scheme: dark; --bg:#0d0d0d; --fg:#fff; --dim:#c3c2b7; --accent:#3987e5; --line:rgba(255,255,255,.14); } }
  :root[data-theme="dark"] { color-scheme: dark; --bg:#0d0d0d; --fg:#fff; --dim:#c3c2b7; --accent:#3987e5; --line:rgba(255,255,255,.14); }
  body { background:var(--bg); color:var(--fg); font:16px/1.6 system-ui,-apple-system,"Segoe UI",sans-serif; margin:0; padding:48px 20px; }
  main { max-width:44rem; margin:0 auto; }
  h1 { font-size:26px; margin:0 0 8px; letter-spacing:-.01em; text-wrap:balance; }
  p { color:var(--dim); }
  ul { list-style:none; padding:0; display:grid; gap:12px; margin:28px 0; }
  li a { display:block; padding:16px 18px; border:1px solid var(--line); border-radius:10px; text-decoration:none; color:var(--fg); }
  li a:hover, li a:focus-visible { border-color:var(--accent); outline:none; }
  li a strong { font-weight:600; }
  li a span { display:block; color:var(--dim); font-size:14.5px; margin-top:3px; }
  footer { color:var(--dim); font-size:14px; border-top:1px solid var(--line); padding-top:18px; margin-top:36px; }
  a { color:var(--accent); }
</style>
</head>
<body>
<main>
  <h1>ESPM dashboards</h1>
  <p>Dashboards over public UC Berkeley ESPM course data.</p>
  <ul>
    <li><a href="$BASE_URL/teaching/index.html"><strong>Teaching statistics</strong>
      <span>Course enrollments over any review window, compared only against courses
      that are actually comparable.</span></a></li>
  </ul>
  <footer>
    Built from the public class schedule and departmental directory. Enrollment is a crude
    proxy for teaching and says nothing about course level, pedagogical load, or how a
    course serves a program. Each page shows the date its data was built, and lists every
    offering its filters excluded. Source and method:
    <a href="https://github.com/boettiger-lab/espm-dashboards">boettiger-lab/espm-dashboards</a>.
  </footer>
</main>
</body>
</html>
HTML

echo "==> checking the build is real"
python3 - <<'PY'
import pathlib, sys
p = pathlib.Path("_site/teaching/index.html")
if not p.exists():
    sys.exit("no dashboard was produced")
size, text = p.stat().st_size, p.read_text()
if "/*__DATA__*/null" in text:
    sys.exit("dashboard still contains the data placeholder -- the build did not inject data")
if size < 100_000:
    sys.exit(f"dashboard is only {size} bytes; a full build is ~270 KB. "
             "Check the build log for a blocked or failed harvest.")
print(f"    dashboard OK: {size/1024:.0f} KB")
PY

if [[ $PUSH -eq 0 ]]; then
  echo "==> --dry-run: _site is ready, not uploading"
  exit 0
fi

echo "==> uploading to $REMOTE:$BUCKET"
# Content-Type matters: without it the browser downloads the page instead of rendering it.
rclone copy _site "$REMOTE:$BUCKET" \
  --s3-no-check-bucket \
  --header-upload "Cache-Control: public, max-age=900" \
  --checksum -v 2>&1 | grep -Ev '^\s*$' | tail -8

echo "==> verifying the published page is publicly readable"
for path in index.html teaching/index.html; do
  read -r code ctype < <(curl -s -o /dev/null -w '%{http_code} %{content_type}' -m 30 "$BASE_URL/$path")
  printf '    %-22s HTTP %s  %s\n' "$path" "$code" "$ctype"
  if [[ "$code" != "200" ]]; then
    echo "    published object is not readable -- check the bucket's anonymous policy:" >&2
    echo "      mc anonymous set download $REMOTE/$BUCKET" >&2
    exit 1
  fi
done

echo
echo "==> live:"
echo "    $BASE_URL/index.html"
echo "    $BASE_URL/teaching/index.html"
