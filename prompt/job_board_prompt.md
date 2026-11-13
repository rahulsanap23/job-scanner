# Prompt: Daily Data Engineering Job Board from Target Companies' Career Sites

This replaces the LinkedIn-CSV prompt. The data now comes from each target company's own careers
site, collected every morning by the scanner in this repo. The rules below are the same ones the
scanner applies in code (`scanner/filters.py`), so Claude's daily job is to check the judgement calls
and publish the page, not to re-filter thousands of rows.

```
GitHub Actions, 6 AM + 3 PM IST (retries 7:00, 3:30)   Scheduled Claude task, 8 AM + 4 PM IST (daily)
┌──────────────────────────────────────┐           ┌─────────────────────────────────────┐
│ scanner/run.py                       │  commits  │ 1. download data/*.json             │
│  · reads config/companies.yaml       │ ───────▶  │ 2. review flagged rows (Part 1)     │
│  · calls each careers site's API     │           │ 3. python3 -m scanner.board --drop  │
│  · LinkedIn fallback + cross-check   │           │ 4. publish the board artifact       │
│  · applies Part 1 filters            │           │                                     │
│  · writes data/jobs.json, report     │           │ 5. short summary of new roles       │
│ scanner/board.py → board/index.html  │           └─────────────────────────────────────┘
└──────────────────────────────────────┘
```

═══════════════════════════════════════════
PART 0 — SOURCES AND SCOPE
═══════════════════════════════════════════

Step 0a — Companies
Only the target companies are searched. There is no "other companies" bucket any more. The list is
`config/companies.yaml` (the same allowlist as the LinkedIn prompt, with UBS covering Credit Suisse,
FIS covering Worldpay, and the Mizuho/Mizhuo duplicate merged). To add or remove a company, edit that
file; each entry says which careers platform its jobs come from.

Step 0b — Where each company's jobs come from
Each company is read from its own careers site's public job feed: Workday, Greenhouse, Lever, Ashby,
SmartRecruiters, Oracle Recruiting Cloud, Eightfold, SuccessFactors, Avature, iCIMS Jibe, Jobvite,
Workable, the Goldman Sachs, Atlassian and Visa sites, amazon.jobs, and the DirectEmployers network
(for Google, Honeywell, Cummins, Teradata, HSBC, SAP and Panasonic Avionics).

Step 0c — Companies that can't be scanned
Some companies have no public job feed or block automated access (for example Tesla, UBS, BNP Paribas
and most Indian banks). They are listed in `data/scan_report.json` with status "not scanned". A company
whose site failed today has status "error". Both are shown in the page footer with a link to their
careers page and a LinkedIn search link, so nothing is dropped silently.

Step 0d — LinkedIn fallback and cross-check
LinkedIn's public, logged-out job search is used in two ways, with one search per company ("data
engineer <company>", Maharashtra, posted in the last 60 days, first 10 results), paced a few seconds apart.
- Fallback: for every company whose own site gave nothing today (status "not scanned" or "error", or a
  site that returned zero postings), LinkedIn is the only source. This list is rebuilt from the latest
  scan every time, so it grows and shrinks as sites fail and recover.
- Cross-check: every other company is searched too. A LinkedIn role that passes Part 1 counts as already
  covered when the careers site gave the same title (words compared after normalising; bare numbers
  such as "-4" ignored, levels such as II/III not) in an overlapping location tag. If the careers site
  had that role but dropped it (for example older than 60 days by its exact date), the careers site wins
  and the LinkedIn copy is only logged. Anything else is a role the careers-site scan missed: it is
  added with `crosscheck: true` and shown as "via LinkedIn · missed by careers site". These rows are not
  flagged for review just for being cross-check finds.
- Remote India: twice a day, one general search ("data engineer", India, remote, 60 days, 3 pages) is
  matched against every target company's name, for remote roles listed as just "India" that the
  Maharashtra searches don't show. Matches are marked remote in India and feed both uses above.
LinkedIn usually stops answering after about a dozen searches from one machine, so the searching is
spread out: the `linkedin-refresh` workflow runs every hour between the scans (22 runs a day) and
searches 8 companies per run from a fresh GitHub runner (`scanner/linkedin_refresh.py`). Each run:
fallback companies that are due (not checked in the last 11 hours, so about twice a day each; a company
whose careers site failed in the latest scan goes first and its site is retried once before searching
LinkedIn) take the slots not reserved for the cross-check; the cross-check takes at least a third of
the slots (3 of 8), least recently checked first, so it never stops even when many sites fail on the
same day; spare slots go to the other group. When the remote search is due it uses 3 of the run's 8
slots. With about 48 fallback companies that cross-checks each other company about every 2 days.
Results go into `data/linkedin_cache.json` (one entry per company plus `_remote_india`). The daily scan
reads that cache; for fallback companies it also searches any not checked in the last 24 hours, stops
at the first rate limit, and reuses results up to 4 days old. The cross-check only reads the cache. Both workflows merge the cache company by company (newest wins) before
pushing, so neither overwrites the other. `meta.linkedin` reads like "45 of 45 companies checked in the
last 24 h (this scan: searched 0 companies)", and lists the companies that weren't; `meta.linkedin_crosscheck`
says how many scanned companies were cross-checked in the last 4 days and how many roles the careers
sites missed (`meta.missed_by_site`). A LinkedIn result counts only if its company name matches
the target's name or aliases after removing legal suffixes (Pvt Ltd, India, Technologies…). An exact
core-name match is kept as is; a looser match (the target's words are inside a longer LinkedIn company
name) is kept and flagged "company listed as '<LinkedIn name>'". These rows go through the same Part 1
filters and are marked "via LinkedIn". Remote-only LinkedIn listings that just say "India" come only
from the remote-India search above (the location filter needs a city or a remote marker).

Step 0e — Logs that explain a missing role
- `data/rejected.json`: every posting with a data title that was dropped, with its reason (location,
  older than 60 days with the exact date, a LinkedIn copy of a role the careers site dropped), plus
  titles with the word "data" in Pune/Mumbai/remote that the title rules left out. Answer "why isn't X
  on the board?" from this file.
- `data/history.json`: each company's posting count for its last 14 scans. A company is flagged in
  `meta.scanner_alerts` (and its scan_report entry's `alerts`) when it reads under 40% of its average
  over the last 7 scans (average at least 5), or when a Workday search stopped paging before the site's
  total. The board footer lists these as "Scan may be missing roles".

═══════════════════════════════════════════
PART 1 — DATA PROCESSING (done by the scanner; Claude reviews)
═══════════════════════════════════════════

Step 0 — Matching methodology (applies to every filter below)
- Normalise case, extra whitespace and punctuation (periods, hyphens, ampersands, slashes) before
  comparing.
- Treat common abbreviations as equivalent: "Sr." = "Senior", "Eng."/"Engr" = "Engineer",
  "Dev" = "Developer", "Mgr" = "Manager".
- Company-name matching is no longer needed: each job comes from that company's own site, so the
  company is known exactly. Group names are the display names in `config/companies.yaml`.
- When a row is borderline, the scanner keeps it and adds a `flags` entry explaining why
  ("title borderline", "remote - country not stated", "Maharashtra - city not stated"). Flagged rows
  are what Claude reviews each day.

Step 1 — Combine and deduplicate
One row per posting. Two rows are duplicates when company, normalised title, location tags and
normalised location text all match (the same posting listed under two search terms, or the same role
reached through two sources). Keep the first.

Step 2 — Filter by job title
Keep a row if the normalised title contains any of:
- Data Engineer / Data Engineering (covers Senior, Sr., Lead, Staff, Principal, GCP, Azure, AWS,
  Manager of Data Engineering, and similar)
- Analytics Engineer
- Big Data (Big Data Engineer, Big Data Developer)
- PySpark Developer
- Software Engineer – Data
OR a data-engineering skill keyword: Spark, PySpark, Hadoop, Hive, ETL, ELT, Data Pipeline, Databricks,
Data Platform, Data Warehouse/Warehousing, DWH, Data Lake, Lakehouse, dbt, Airflow, Snowflake,
BigQuery, Redshift, Informatica, Talend, Data Integration, Data Infrastructure, DataOps, Data Fabric,
Azure Data Factory/ADF. (Kafka is not a keyword: it mostly pulls in general backend roles.)
OR the reversed word order: "Software Engineer … Data" and "Data … Software Engineer"/"Data … Developer".
Always exclude: data centre/center, data entry, internships, apprentices, trainees, working
students and campus/graduate programmes.
Exclude when the title only got in through the loose "data … engineer" pattern: data scientist / data
science, data privacy/protection/security/loss, data annotation/labelling/collection, data steward,
clinical data.
Flag as "title borderline" (kept, review needed): loose-pattern titles that also contain quality,
test, QA, support, storage, governance, management, migration, analyst, scientist, sales, presales,
solutions, consultant, architect, manager, director or head; any data-scientist title that got in
through a skill keyword; skill-keyword titles that read as analyst, test, sales, support, consultant,
director, head, admin or customer roles with no "engineer"/"developer" in them; "Airflow", "Snowflake",
"ADF" or "Hive" titles with no "data",
"engineer" or "developer" (industrial companies use "airflow" for HVAC roles).

Step 3 — Filter by location
Keep only rows whose location mentions Pune, Mumbai, Navi Mumbai, or a remote role open in India.
- Neighbourhoods count as their city: Hinjewadi, Kharadi, Magarpatta, Hadapsar, Baner, Balewadi,
  Viman Nagar, Yerwada, Wakad, Aundh, Kalyani Nagar, Pimpri-Chinchwad, Haveli, Khed, Talawade, Chakan,
  Bhosari → Pune; Goregaon, Powai, Andheri, Malad, Vikhroli, Bandra/BKC, Lower Parel, Worli, Kurla,
  Saki Naka, Marol, Santacruz, Ghatkopar, Mulund, Chembur and similar → Mumbai; Airoli, Vashi,
  Belapur, Ghansoli, Rabale, Mahape, Turbhe, Kharghar, Nerul, Sanpada → Navi Mumbai. "Bombay" and
  "Poona" count too. Thane is a separate city and does not count.
- "Navi Mumbai" does not also tag the job as Mumbai unless Mumbai proper is listed separately.
- Remote: match "Remote", "Remotely", "Work from home", "WFH", "Anywhere", "Virtual" (or the site's own
  remote flag). A remote job counts when it mentions India or an Indian city ("Remote - Bangalore" counts
  through the Remote part). A remote job that names another country or region (US, EMEA, Europe…) is
  excluded. A remote job naming no country is kept and flagged "remote - country not stated".
- Multi-location postings carry every matching tag (e.g. Pune + Remote). For Workday postings shown
  as "N Locations", the scanner opens the posting's own data to read the real list.
- A location of just "Maharashtra, India" with no city is kept and flagged.
- A posting whose location is only "Multiple Locations" (some Avature sites) is kept and flagged
  "multiple locations - check", unless the text names a non-Indian country.

Step 4 — Filter by recency
Keep rows posted within the last 60 days (India date). Relative dates ("Posted 5 Days Ago",
"2 weeks ago", "Yesterday") are converted to a day count. Exact dates come first:
- Workday lists anything older than 30 days only as "Posted 30+ Days Ago". For roles that already pass
  the title filter and are in Pune/Mumbai/remote, the scanner opens the posting's own data and reads its
  exact `startDate` (the date it was last posted; a reposted role shows the repost date).
- A vague "N+ days" with N under 60 that still has no exact date counts as unknown, not N+1 days.
- Roles with no date (Goldman Sachs, Jobvite, JazzHR, Kula and some page scrapers): the scanner reads
  `datePosted` from the posting page's hidden schema.org data; failing that, LinkedIn's date for the
  same title and city (`date_source`). Rows still without a date are kept ("date not given").
Greenhouse boards that only publish an "updated" date use it as the posting date.

Step 5 — Company scope
Every row is from a target company by construction (Part 0). No allowlist check is needed and no row
is dropped for its company.

Step 6 — Combined
A row survives only if it passes Steps 2, 3 and 4.

Step 6b — Claude's daily review (the only filtering Claude does)
Look at every row in `data/jobs.json` with a non-empty `flags` list, plus a quick skim of the other
titles for clear misfits. Decide keep or drop:
- "title borderline": keep if the role's main work is building or running data pipelines, data
  platforms, warehouses or lakes (Data Platform Architect, Lead Data Engineer – Manager, Analytics
  Engineering Manager). Drop if it is testing/QA of non-data software, sales or presales, customer
  support, storage hardware, data science or analysis without engineering, or HVAC/mechanical work.
- "remote - country not stated": keep unless the title or company makes another country obvious
  (e.g. "US Federal" or a US-only company).
- "Maharashtra - city not stated" and "multiple locations - check": keep; the reader opens the posting.
- "company listed as '<name>'" (LinkedIn rows): keep if it is the same company or its Indian entity
  (e.g. "BNP Paribas India Solutions", "FirstCry.com (BrainBees Solutions Ltd.)"); drop if it is a
  different company that merely shares a word (e.g. "Visa Global Logistics" for Visa).
- Unflagged rows: drop only clear errors (e.g. a non-data title that slipped through). When unsure,
  keep it.
Pass the URLs of dropped rows to the board builder (`--drop`). Never add rows the scanner didn't find.

═══════════════════════════════════════════
PART 2 — OUTPUT PAGE STRUCTURE (built by scanner/board.py from board/template.html)
═══════════════════════════════════════════

Step 7 — One ranked company list
- Group surviving rows by company display name.
- Sort the whole list by job count, highest first, as one ranking (ties alphabetical).
- No TARGET badge (every company is a target). Instead, a small "N new" tag shows how many of the
  company's roles were first seen in the last two days.
- Each company row collapses/expands to show its jobs: title, location, posted date. While a search is
  typed, matching companies open automatically.

Step 8 — Filters (client-side, combined with AND)
1. Location chips: All / Pune / Mumbai / Navi Mumbai / Remote, each with its count. Filtering is per job
   using its location tags (a job can carry several). A company with no matching job is hidden; a
   partly matching company shows "X of Y roles".
2. Show chips: All roles / New / Missed by careers site (cross-check rows) / Needs a check (rows with flags).
3. A search box matching company name or job title.
A live counter ("42 roles · 15 companies") shows whenever a filter or search is active. The chosen
location is remembered in the viewer's browser.

Step 9 — Job links: direct links to the posting
Each title links (new tab, small external-link icon) to the posting on the company's own careers site,
to its DirectEmployers listing (which forwards to it), or, for LinkedIn rows, to the LinkedIn
posting, with a small "via LinkedIn" pill ("via LinkedIn · missed by careers site" for cross-check rows). The list is rebuilt twice a day, so links are fresh; a closed
role drops off at the next scan. LinkedIn live-search links (the old Step 9 format) now appear only in
the footer, next to each company that couldn't be scanned.

Step 10 — Header and summary stats
- Eyebrow: "Career-site scan · Pune · Mumbai · Navi Mumbai · Remote India · <scan date>"
- Title: "Data engineering roles at your target companies"
- One-sentence subhead naming the checks each listing passed: own careers site, data-engineering title
  or skill, location, posted in the last 60 days or undated, plus the LinkedIn fallback and cross-check.
- Four stats: matching roles · companies hiring · new since last scan (roles that first appeared in
  this run) · careers sites scanned (scanned OK / total target companies).
- NEW pill and the "New (2 days)" filter mark roles first seen in the last two days.

Step 11 — Footer
(a) How title and location matching works (abbreviations, neighbourhoods, remote-in-India rule, what is
excluded). (b) Links go straight to the posting and are refreshed daily; "check" rows passed on a
judgement call. (c) "Scan may be missing roles": the companies in `meta.scanner_alerts`, with the reason. (d) Coverage in three groups: careers site couldn't be reached today; no usable careers
feed but checked on LinkedIn (with the number of roles found); not checked (e.g. LinkedIn rate-limited
every attempt for 4 days). Each company links to its careers page and to a LinkedIn search.

═══════════════════════════════════════════
PART 3 — VISUAL DESIGN SPEC (unchanged, plus a dark theme)
═══════════════════════════════════════════

One self-contained HTML page (inline CSS/JS, no build step), published as an artifact titled
"Target Company Job Scan".

Fonts (Google Fonts): "Fraunces" (serif, 400–700) for the page title, section heading and stat numbers;
"IBM Plex Sans" (400/500/600) for body text, company names, job titles; "IBM Plex Mono" (400/500/600)
for anything numeric/structured — job-count badges, rank numbers, posted dates, the eyebrow, filter
labels.

Light palette (cool paper): Background #EFF2EF · Surface #FFFFFF · Ink #16211D · Ink-soft #4A554F ·
Line #D6DBD5 · Teal #145C54 (links, accents) · Teal-deep #0D3E39 (headings, stat numbers, active chip) ·
Ochre #9A6B12 (count badge text) · Ochre-soft #F4E9D4 (count badge background).
Dark palette (follows the viewer's theme): Background #111816 · Surface #19211F · Ink #E3EAE6 ·
Ink-soft #A3AFA9 · Line #2B3632 · Teal #6BBFB2 · Teal-deep #9AD9CD · Ochre #E2B65E · Ochre-soft #3A2F18.

Layout: ~920px centred column. Header with bottom border, big serif title (clamp ~32–48px), 4-column
stat row with vertical dividers (2×2 on phones). Pill-shaped filter chips that wrap; active chip filled
teal-deep. Company rows: rank number (mono), company name (bold sans), optional "N new" tag (bordered
mono pill), job-count badge (ochre pill), chevron that rotates on expand. Expanded jobs sit in a
left-bordered indent; each shows title link / location / posted date in a 3-column grid (one column
on phones). NEW pill on new rows; "check: <reason>" pill on flagged rows. Responsive to ~375px.
