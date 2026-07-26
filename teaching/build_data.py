#!/usr/bin/env python3
"""Build the ESPM teaching-statistics data file used by dashboard.html.

Pipeline:
  1. Harvest every ESPM class from the Berkeleytime GraphQL API (mirrors Berkeley's SIS
     Course/Class APIs). Gives enrollment, units, career (UGRD/GRAD), component.
  2. Deduplicate -- the API returns duplicate rows for cross-listed (C-prefixed) classes.
  3. Re-scrape each class-term from the official UCB Class Schedule for authoritative
     enrollment AND the instructor list (Berkeleytime's instructor data is incomplete).
  4. Scrape the ESPM departmental directory for each person's title, so graduate-student
     instructors can be told from faculty and Academic Senate status can be determined.
  5. Emit espm_data.json, and dashboard.html with the data inlined (so it works from
     file:// without a web server).

Usage:
    python3 build_data.py                # full refresh
    python3 build_data.py --skip-harvest # reuse raw_catalog.json, re-scrape the rest
    python3 build_data.py --render-only  # just rebuild dashboard.html from espm_data.json

Requires only the standard library plus `curl` on PATH.
"""

import argparse
import html
import json
import re
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
GRAPHQL = "https://berkeleytime.com/api/graphql"
SCHEDULE = "https://classes.berkeley.edu/content"
DIRECTORY = "https://ourenvironment.berkeley.edu/people/faculty"

SUBJECT = "ESPM"
# ourenvironment.berkeley.edu/robots.txt asks for Crawl-delay: 10. Honour it.
DIRECTORY_CRAWL_DELAY = 10
# classes.berkeley.edu sets no crawl delay; /content/ (what we fetch) is not
# disallowed. This is a courtesy pause, ~4 requests/second.
SCHEDULE_DELAY = 0.25
FIRST_YEAR = 2019          # Berkeleytime catalog coverage begins Fall 2019
LAST_YEAR = 2027

# Umbrella / independent-study numbers: many unrelated sections share one course number,
# so aggregating them produces meaningless "courses". Kept in the data but flagged.
UMBRELLA = {
    "98", "99", "197", "198", "199", "290", "296", "298", "299", "300", "301", "302",
    "375", "376", "400", "601", "602", "N299", "98BC", "198BC", "150", "194A",
}

CATALOG_QUERY = """query($y:Int!,$s:Semester!,$p:Int!){
  catalogSearch(year:$y, semester:$s, filters:{departments:["%s"]}, page:$p, pageSize:100){
    totalCount
    results { year semester sessionId courseNumber number title courseTitle
              unitsMin unitsMax academicCareer enrolledCount maxEnroll
              primaryComponent }
  }
}""" % SUBJECT


# Identify the tool so site admins can see who is fetching and why. Some hosts also
# reject requests with no User-Agent outright.
USER_AGENT = ("espm-dashboards/1.0 "
              "(+https://github.com/boettiger-lab/espm-dashboards)")


def curl(url, post=None, timeout=60, want_status=False):
    """Fetch a URL. With want_status, return (status_code, body) instead of just body."""
    cmd = ["curl", "-sL", "-m", str(timeout), "-A", USER_AGENT]
    if want_status:
        cmd += ["-w", "\n%{http_code}"]
    if post is not None:
        cmd += ["-X", "POST", "-H", "Content-Type: application/json", "-d", post]
    cmd.append(url)
    out = subprocess.run(cmd, capture_output=True, text=True).stdout
    if not want_status:
        return out
    body, _, status = out.rpartition("\n")
    return (status.strip() or "000"), body


def gql(year, semester, page):
    body = json.dumps({"query": CATALOG_QUERY,
                       "variables": {"y": year, "s": semester, "p": page}})
    status, raw = curl(GRAPHQL, post=body, want_status=True)
    try:
        doc = json.loads(raw)
    except json.JSONDecodeError:
        # Don't swallow this. A blocked request, a WAF interstitial or an outage all
        # land here, and reporting the status plus a snippet is the difference between
        # a diagnosable failure and a silently empty dataset.
        snippet = " ".join(raw.split())[:300] or "(empty response)"
        print(f"    {GRAPHQL} returned HTTP {status}, not JSON, for "
              f"{semester} {year} p{page}: {snippet}", file=sys.stderr)
        return None
    if "errors" in doc:
        print(f"    API error {year} {semester} p{page}: "
              f"{doc['errors'][0].get('message')}", file=sys.stderr)
        return None
    return doc.get("data", {}).get("catalogSearch")


def harvest_catalog():
    """Step 1+2: all ESPM classes from the API, deduplicated."""
    rows = []
    for year in range(FIRST_YEAR, LAST_YEAR + 1):
        for sem in ("Spring", "Summer", "Fall"):
            first = gql(year, sem, 1)
            if not first or not first["results"]:
                continue
            got = list(first["results"])
            page = 2
            while len(got) < first["totalCount"] and page <= 20:
                nxt = gql(year, sem, page)
                if not nxt or not nxt["results"]:
                    break
                got += nxt["results"]
                page += 1
                time.sleep(0.2)
            print(f"  {year} {sem}: {len(got)}")
            rows += got
            time.sleep(0.2)

    seen, deduped = set(), []
    for r in rows:
        key = (r["year"], r["semester"], r["courseNumber"], r["number"],
               r["enrolledCount"], r["primaryComponent"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(r)
    print(f"  harvested {len(rows)} rows, {len(deduped)} after dedupe")
    if not deduped:
        sys.exit(
            "\nHarvest returned nothing, so there is no data to build from.\n"
            f"Check whether {GRAPHQL} is reachable from this host:\n"
            f"  curl -s -X POST {GRAPHQL} -H 'Content-Type: application/json' \\\n"
            "       -d '{\"query\":\"{terms{year semester}}\"}' | head -c 300\n"
            "The API has been observed to refuse datacenter IP ranges, so a CI runner "
            "may be blocked where a workstation is not. Errors above give the status.")
    return deduped


SEM_ORDER = {"Spring": 0, "Summer": 1, "Fall": 2}


def schedule_url(rec):
    comp = (rec["primaryComponent"] or "LEC").lower()
    num = rec["number"]
    return (f"{SCHEDULE}/{rec['year']}-{rec['semester'].lower()}-{SUBJECT.lower()}-"
            f"{rec['courseNumber'].lower()}-{num}-{comp}-{num}")


INSTR_RE = re.compile(r'class="sf--instructors">(.*?)</div>', re.S)
IDENT_RE = re.compile(r"(\d{4} (?:Spring|Summer|Fall) " + SUBJECT + r" \S+)")


def scrape_offering(rec):
    """Step 3: official enrollment + instructor list for one class-term."""
    url = schedule_url(rec)
    page = curl(url, timeout=40)
    flat = html.unescape(re.sub(r"\s+", " ", re.sub("<[^>]+>", " ", page)))

    ident = IDENT_RE.search(flat)
    # Guard against a URL that silently resolves to a different class.
    ok = bool(ident) and f"{SUBJECT} {rec['courseNumber']}" in ident.group(1)

    enrolled = re.search(r"Enrolled: (\d+)", flat)
    capacity = re.search(r"Capacity: (\d+)", flat)
    waitlist = re.search(r"Waitlisted: (\d+)", flat)

    names = []
    m = INSTR_RE.search(page)
    if m:
        text = re.sub(r"\s+", " ", html.unescape(re.sub("<[^>]+>", "", m.group(1)))).strip()
        names = [n.strip() for n in text.split(",") if n.strip()]

    return {
        "official_enrolled": int(enrolled.group(1)) if enrolled else None,
        "official_capacity": int(capacity.group(1)) if capacity else None,
        "official_waitlisted": int(waitlist.group(1)) if waitlist else None,
        "instructors": names,
        "page_matched": ok,
        "url": url,
    }


DIR_ROW = re.compile(
    r'views-field-title"\s*>\s*<a href="/people/([^"]+)">([^<]+)</a>.*?'
    r'person-title"\s*>\s*(.*?)\s*</td>.*?person-type"\s*>\s*(.*?)\s*</td>', re.S)


def scrape_directory():
    """Step 4: name -> {title, type} for everyone the department lists.

    ourenvironment.berkeley.edu/robots.txt asks for `Crawl-delay: 10`, so this waits
    10s between pages. It is roughly a dozen pages, so the pass takes ~2 minutes;
    use --skip-scrape reruns rather than re-fetching it needlessly.
    (classes.berkeley.edu sets no crawl delay and does not disallow /content/, which
    is what scrape_offering uses.)
    """
    people = {}
    for page in range(0, 30):
        if page:
            time.sleep(DIRECTORY_CRAWL_DELAY)
        raw = curl(f"{DIRECTORY}?page={page}", timeout=40)
        rows = DIR_ROW.findall(raw)
        if not rows:
            break
        before = len(people)
        for slug, name, title, ptype in rows:
            clean = lambda s: html.unescape(re.sub(r"\s+", " ", s)).strip()
            people[clean(name)] = {"slug": slug, "title": clean(title),
                                   "type": clean(ptype)}
        if len(people) == before:
            break
        print(f"    page {page}: {len(people)} people so far")
    print(f"  directory: {len(people)} people")
    return people


# Appointment buckets. "senate" = Academic Senate faculty, the merit-review comparison set.
# The Professor of Teaching (LSOE) series IS Senate. Professor of the Graduate School is a
# post-retirement title and is treated as emeritus.
def classify(title, ptype):
    blob = f"{title} {ptype}"
    if "Graduate Student" in blob:
        return "graduate-student"
    if "Postdoctoral" in blob:
        return "postdoc"
    if "Staff" in blob or "Specialist" in ptype and "Professor" not in title:
        return "staff"
    if "Adjunct" in blob:
        return "adjunct"
    if "Emerit" in blob or "In Memoriam" in blob:
        return "emeritus"
    if "Professor of the Graduate School" in title:
        return "emeritus"
    if "Cooperative Extension" in title:
        return "cooperative-extension"
    if "Lecturer" in blob:
        return "lecturer"
    if "Professor" in title:
        return "senate"
    return "unknown"


def _norm(s):
    return s.lower().replace("'", "").replace("-", "").replace(".", "").replace(",", "")


def match_person(full_name, directory):
    """Match a schedule name to a directory entry.

    Names disagree between the two sources in ways a last-token match gets wrong:
    the schedule may carry middle names ('Rodrigo P P Almeida'), and compound
    surnames appear truncated on one side or the other -- the directory's
    'Albert Ruhi' is the schedule's 'Albert Ruhi Vidal', and 'Kathryn Teigen De
    Master' appears as 'Kathryn Patrice Teigen De Master'.

    So: match on *any* shared surname token, but require the first initial to
    agree, and never fall back to a first-initial mismatch. A wrong match is far
    worse than no match -- it can file a professor as a graduate student, or vice
    versa, purely because they share a surname. Ambiguous cases return None and
    surface as 'unverified' in the dashboard.
    """
    tokens = [_norm(t) for t in full_name.split() if t]
    if not tokens:
        return None
    given, rest = tokens[0], set(tokens[1:])
    if not rest:
        rest = {tokens[0]}
    # A person may be listed under a preferred or middle name: the schedule's
    # 'Erica B Rosenblum' is the directory's 'Bree Rosenblum'. So accept the
    # directory's first initial against ANY of the schedule's given-name tokens,
    # while still requiring a shared surname token.
    initials = {t[:1] for t in tokens[:-1]} or {given[:1]}

    best = None
    for name, info in directory.items():
        dtok = [_norm(t) for t in name.split() if t]
        if not dtok:
            continue
        if dtok[0][:1] not in initials:
            continue
        dsur = set(dtok[1:]) or {dtok[0]}
        shared = rest & dsur
        if not shared:
            continue
        # Prefer the candidate sharing the most name tokens, then an exact given name.
        score = (len(shared), dtok[0] == given)
        if best is None or score > best[0]:
            best = (score, name, info)
    return (best[1], best[2]) if best else None


def load_aliases():
    """Optional hand-maintained overrides: {"Schedule Name": "Directory Name"}.

    Automatic matching cannot resolve every case -- an instructor may be absent from
    the ESPM directory entirely (common for cross-listed courses taught from another
    department), or listed under a name that shares no token with the schedule's.
    Anything the build reports as 'unverified' can be pinned here, and an explicit,
    reviewable file is the honest place for those judgments.
    """
    path = HERE / "aliases.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        print(f"  aliases.json is not valid JSON ({exc}); ignoring", file=sys.stderr)
        return {}
    return {k: v for k, v in data.items() if isinstance(v, str)}


def build(records, directory, aliases=None):
    """Step 5: assemble the offering-level data the dashboard consumes."""
    people = {}
    aliases = aliases or {}

    def resolve(name):
        if name in people:
            return people[name]
        target = aliases.get(name)
        if target:
            info = directory.get(target)
            if info is None:
                print(f"  alias for {name!r} points at {target!r}, "
                      f"which is not in the directory", file=sys.stderr)
            hit = (target, info) if info else None
        else:
            hit = match_person(name, directory)
        if hit is None:
            entry = {"directory_name": None, "title": None, "type": None,
                     "appointment": "unknown"}
        else:
            dname, info = hit
            entry = {"directory_name": dname, "title": info["title"],
                     "type": info["type"],
                     "appointment": classify(info["title"], info["type"])}
        people[name] = entry
        return entry

    # collapse classes into course-terms (a course may run several lecture sections)
    grouped = defaultdict(lambda: {"enrolled": 0, "capacity": 0, "instructors": set(),
                                   "sections": 0})
    meta = {}
    for r in records:
        s = r.get("scrape") or {}
        enrolled = s.get("official_enrolled")
        if enrolled is None:
            enrolled = r.get("enrolledCount")
        if enrolled is None:
            continue
        key = (r["courseNumber"], r["year"], r["semester"])
        g = grouped[key]
        g["enrolled"] += enrolled
        g["capacity"] += s.get("official_capacity") or r.get("maxEnroll") or 0
        g["sections"] += 1
        for n in s.get("instructors", []):
            g["instructors"].add(n)
        m = meta.setdefault(r["courseNumber"], {})
        m["career"] = r["academicCareer"]
        m["units"] = (r["unitsMin"] if r["unitsMin"] == r["unitsMax"] else None)
        m["units_min"] = r["unitsMin"]
        m["units_max"] = r["unitsMax"]
        m["component"] = r["primaryComponent"]
        if r.get("courseTitle") or r.get("title"):
            m.setdefault("title", r.get("courseTitle") or r.get("title"))

    offerings = []
    for (course, year, sem), g in sorted(
            grouped.items(), key=lambda kv: (kv[0][0], kv[0][1], SEM_ORDER[kv[0][2]])):
        listed = sorted(g["instructors"])
        for n in listed:
            resolve(n)
        # Instructors of record = listed people who are not GSIs or postdocs.
        of_record = [n for n in listed
                     if people[n]["appointment"] not in ("graduate-student", "postdoc")]
        appts = sorted({people[n]["appointment"] for n in of_record})
        offerings.append({
            "course": course,
            "year": year,
            "semester": sem,
            "term": f"{sem} {year}",
            "sort": year * 10 + SEM_ORDER[sem],
            "enrolled": g["enrolled"],
            "capacity": g["capacity"] or None,
            "sections": g["sections"],
            "listed_instructors": listed,
            "instructors_of_record": of_record,
            "appointments": appts,
            "co_taught": len(of_record) > 1,
            "umbrella": course in UMBRELLA,
        })

    courses = {}
    for c, m in meta.items():
        digits = "".join(ch for ch in c if ch.isdigit())
        number = int(digits[:3]) if digits else 0
        courses[c] = {
            "title": m.get("title"),
            "career": m.get("career"),
            "units": m.get("units"),
            "units_min": m.get("units_min"),
            "units_max": m.get("units_max"),
            "component": m.get("component"),
            "level": ("graduate" if m.get("career") == "GRAD"
                      else "upper-division" if number >= 100 else "lower-division"),
            "umbrella": c in UMBRELLA,
            "cross_listed": c.startswith("C") or c.startswith("H"),
        }

    terms = sorted({(o["year"], o["semester"]) for o in offerings},
                   key=lambda t: t[0] * 10 + SEM_ORDER[t[1]])

    # A term whose instruction has not finished reports partial enrollment, so it must not
    # anchor a fixed-length window ("last 3 years" ending on a term that hasn't happened
    # silently understates the most recent course). Berkeley terms end roughly mid-May
    # (Spring), mid-August (Summer) and mid-December (Fall).
    today = time.strftime("%Y-%m-%d")
    term_end = {"Spring": "05-20", "Summer": "08-20", "Fall": "12-20"}

    def complete(year, semester):
        return today > f"{year}-{term_end[semester]}"

    return {
        "generated": today,
        "subject": SUBJECT,
        "terms": [{"year": y, "semester": s, "term": f"{s} {y}",
                   "sort": y * 10 + SEM_ORDER[s], "complete": complete(y, s)}
                  for y, s in terms],
        "courses": courses,
        "people": people,
        "offerings": offerings,
        "umbrella_numbers": sorted(UMBRELLA),
    }


DOC_HEAD = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
"""


def render(data, template, out, fragment_out=None):
    """Write a standalone HTML document, and optionally the bare fragment.

    The fragment (no doctype/html/head/body) is what a host that supplies its own
    document skeleton wants; the standalone document is what opens correctly from
    file:// and can be emailed as-is.
    """
    payload = json.dumps(data, separators=(",", ":"))
    body = template.replace("/*__DATA__*/null", payload)

    if fragment_out is not None:
        fragment_out.write_text(body)
        print(f"  wrote {fragment_out.name} ({fragment_out.stat().st_size / 1024:.0f} KB)"
              f" — fragment, for hosts that supply their own <head>")

    # Split the fragment's <title>/<style> into the head, leaving the rest as the body.
    head_bits, rest = [], body
    m = re.match(r"\s*(<title>.*?</title>)\s*", rest, re.S)
    if m:
        head_bits.append(m.group(1))
        rest = rest[m.end():]
    m = re.match(r"\s*(<style>.*?</style>)\s*", rest, re.S)
    if m:
        head_bits.append(m.group(1))
        rest = rest[m.end():]

    doc = DOC_HEAD + "\n".join(head_bits) + "\n</head>\n<body>\n" + rest + "\n</body>\n</html>\n"
    out.write_text(doc)
    print(f"  wrote {out.name} ({out.stat().st_size / 1024:.0f} KB) — standalone, opens from file://")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-harvest", action="store_true",
                    help="reuse raw_catalog.json instead of re-querying the API")
    ap.add_argument("--skip-scrape", action="store_true",
                    help="reuse scraped.json instead of re-scraping the class schedule")
    ap.add_argument("--render-only", action="store_true",
                    help="rebuild dashboard.html from an existing espm_data.json")
    ap.add_argument("--limit", type=int, default=0, help="scrape only N offerings (testing)")
    args = ap.parse_args()

    raw_path = HERE / "raw_catalog.json"
    scraped_path = HERE / "scraped.json"
    data_path = HERE / "espm_data.json"
    template_path = HERE / "dashboard.template.html"
    out_path = HERE / "dashboard.html"
    frag_path = HERE / "dashboard.fragment.html"

    if args.render_only:
        render(json.loads(data_path.read_text()), template_path.read_text(),
               out_path, frag_path)
        return

    if args.skip_harvest and raw_path.exists():
        records = json.loads(raw_path.read_text())
        print(f"reusing {raw_path.name}: {len(records)} classes")
    else:
        print("[1/4] harvesting catalog from Berkeleytime API")
        records = harvest_catalog()
        raw_path.write_text(json.dumps(records))

    targets = [r for r in records
               if r["courseNumber"] not in UMBRELLA and r["enrolledCount"] is not None]
    if args.limit:
        targets = targets[:args.limit]

    if args.skip_scrape and scraped_path.exists():
        by_key = json.loads(scraped_path.read_text())
        print(f"reusing {scraped_path.name}: {len(by_key)} offerings")
    else:
        print(f"[2/4] scraping {len(targets)} class-terms from classes.berkeley.edu")
        by_key = {}
        for i, r in enumerate(targets, 1):
            key = f"{r['year']}|{r['semester']}|{r['courseNumber']}|{r['number']}"
            by_key[key] = scrape_offering(r)
            if i % 50 == 0:
                print(f"  {i}/{len(targets)}")
            time.sleep(SCHEDULE_DELAY)
        scraped_path.write_text(json.dumps(by_key, indent=1))

    for r in records:
        key = f"{r['year']}|{r['semester']}|{r['courseNumber']}|{r['number']}"
        if key in by_key:
            r["scrape"] = by_key[key]

    print("[3/4] scraping ESPM departmental directory")
    directory = scrape_directory()
    (HERE / "directory.json").write_text(json.dumps(directory, indent=1))

    print("[4/4] assembling data file")
    aliases = load_aliases()
    if aliases:
        print(f"  applying {len(aliases)} alias override(s) from aliases.json")
    data = build([r for r in records if r["courseNumber"] not in UMBRELLA],
                 directory, aliases)
    data_path.write_text(json.dumps(data, indent=1))
    print(f"  {len(data['offerings'])} offerings, {len(data['courses'])} courses, "
          f"{len(data['people'])} instructors")

    unknown = sorted(n for n, p in data["people"].items() if p["appointment"] == "unknown")
    if unknown:
        print(f"  {len(unknown)} instructors not found in the directory "
              f"(shown as 'unverified' in the dashboard):")
        for n in unknown[:15]:
            print(f"    - {n}")

    render(data, template_path.read_text(), out_path, frag_path)


if __name__ == "__main__":
    main()
