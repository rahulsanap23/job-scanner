# job-scanner

Daily scan of target companies' own careers sites for data-engineering roles in **Pune, Mumbai,
Navi Mumbai and Remote (India)**, published as a filterable job board.

```
GitHub Actions (6 AM + 3 PM IST, retries 7:00 + 3:30)  ──commits──▶  data/jobs.json, data/scan_report.json, data/descriptions.json, board/index.html
linkedin-refresh (every hour)                          ──commits──▶  data/linkedin_cache.json, data/descriptions.json (LinkedIn fallback + cross-check)
Scheduled Claude task (daily 8 AM + 4 PM IST)          ──reads──▶  reviews flagged rows, publishes the board, sends a summary
```

## What's where

| Path | What it is |
| --- | --- |
| `config/companies.yaml` | The target companies and which careers platform each one's jobs come from |
| `scanner/run.py` | Runs every company, applies the filters, writes `data/` |
| `scanner/filters.py` | Title, location and recency rules (Part 1 of the prompt): roles posted in the last 60 days |
| `scanner/dates.py` | Fills in dates the careers-site list didn't give, from the posting page's hidden `datePosted` |
| `scanner/adapters/` | One reader per careers platform (Workday, Greenhouse, Lever, Ashby, Oracle, Eightfold, SuccessFactors, Avature, …) |
| `scanner/linkedin.py` | LinkedIn's public job search, matched by company name: the only source for companies whose own site gave nothing, and a cross-check for every other company (roles their site scan missed are added as "missed by careers site"); plus a remote-India search |
| `scanner/linkedin_refresh.py`, `.github/workflows/linkedin-refresh.yml` | Searches LinkedIn for 8 companies every hour and keeps `data/linkedin_cache.json` fresh for the next scan: fallback companies about twice a day, at least a third of the searches for the cross-check, and the remote-India search twice a day; also looks up a few job descriptions the careers sites didn't give (same company and title only) |
| `scanner/descriptions.py` | Saves each board role's full job description to `data/descriptions.json`, once per role: careers site first (in the daily scan), LinkedIn as the fallback (in linkedin-refresh, up to 3 tries). The scheduled Claude task reads it to pick each role's top 10 skills |
| `scanner/board.py`, `board/template.html` | Builds the job-board page (Parts 2–3 of the prompt) |
| `prompt/job_board_prompt.md` | The full spec: sources, filters, page structure, design |
| `data/jobs.json` | Today's matching roles (with `new` and `flags`) |
| `data/scan_report.json` | Per-company status: ok / partial / error / not scanned, with counts, examples of near misses, `alerts` and the LinkedIn cross-check result |
| `data/rejected.json` | Every role with a data title that was dropped, and why (city, older than 60 days with its date, …): the answer to "why isn't this role on the board?" |
| `data/history.json` | Each company's posting count for its last 14 scans; a big drop (or Workday paging cut short) shows up in `meta.scanner_alerts` and the board footer |
| `data/seen.json` | When each role was first seen (drives the NEW tag) |
| `data/descriptions.json` | Full description per role URL: status `ok`, `needs_linkedin` (waiting for the LinkedIn lookup) or `failed` (not found anywhere) |
| `probe/` | Maintenance helper for checking new careers endpoints from GitHub Actions |

## Run it

- **Now:** Actions tab → *daily-job-scan* → *Run workflow*.
- **Locally:** `pip install -r requirements.txt && python -m scanner.run && python -m scanner.board`
  (`--only "Citi,Barclays" --debug` to test a few companies).
- **Tests:** `python -m pytest tests`

## Add or fix a company

Edit `config/companies.yaml`. The header of that file shows how to read platform ids off a careers
URL. A company with no `sources` is still listed on the board under "not scanned automatically" with a
link, so it is never silently skipped. Check `data/scan_report.json` after the next run: `fetched` is
how many India postings were read, `title_matches` how many had a data title, and
`rejected_examples` shows near misses (wrong city or older than 60 days); `data/rejected.json` has all of them.
