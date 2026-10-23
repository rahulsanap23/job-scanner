# job-scanner

Daily scan of target companies' own careers sites for data-engineering roles in **Pune, Mumbai,
Navi Mumbai and Remote (India)**, published as a filterable job board.

```
GitHub Actions (5:47 AM IST, retry 7:47)  ──commits──▶  data/jobs.json, data/scan_report.json, board/index.html
Scheduled Claude task (weekdays 8:30 AM IST) ──reads──▶  reviews flagged rows, publishes the board, sends a summary
```

## What's where

| Path | What it is |
| --- | --- |
| `config/companies.yaml` | The target companies and which careers platform each one's jobs come from |
| `scanner/run.py` | Runs every company, applies the filters, writes `data/` |
| `scanner/filters.py` | Title, location and recency rules (Part 1 of the prompt) |
| `scanner/adapters/` | One reader per careers platform (Workday, Greenhouse, Lever, Ashby, Oracle, Eightfold, SuccessFactors, Avature, …) |
| `scanner/board.py`, `board/template.html` | Builds the job-board page (Parts 2–3 of the prompt) |
| `prompt/job_board_prompt.md` | The full spec: sources, filters, page structure, design |
| `data/jobs.json` | Today's matching roles (with `new` and `flags`) |
| `data/scan_report.json` | Per-company status: ok / partial / error / not scanned, with counts and examples of near misses |
| `data/seen.json` | When each role was first seen (drives the NEW tag) |
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
`rejected_examples` shows near misses (wrong city or older than 30 days).
