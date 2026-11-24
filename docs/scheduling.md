# Starting the workflows on time (free, one-time setup)

GitHub's own `schedule:` trigger is "best effort": when GitHub is busy it starts runs late or
skips them. On 23–24 Nov 2026 the hourly `linkedin-refresh` ran only about four times a day, so
many companies went 48 hours without a LinkedIn check.

The fix is a free external scheduler, [cron-job.org](https://cron-job.org), that asks GitHub to
start the workflows at fixed times. GitHub's own schedule stays as a backup. Both workflows skip
themselves when the other trigger already ran (the scan if one finished in the last 100 minutes,
the LinkedIn refresh if LinkedIn was searched in the last 40 minutes), so nothing runs twice.

## 1. Create a GitHub token that can only start workflows in this repo

1. GitHub → your picture → **Settings** → **Developer settings** → **Personal access tokens** →
   **Fine-grained tokens** → **Generate new token**.
2. Name: `cron-job.org job-scanner`. Expiration: the longest offered (put a reminder in your
   calendar to renew it).
3. **Repository access** → **Only select repositories** → `rahulsanap23/job-scanner`.
4. **Permissions** → **Repository permissions** → **Actions: Read and write**. Leave everything
   else at "No access".
5. **Generate token** and copy it (it starts with `github_pat_`). It is shown only once.

This token can start and cancel workflow runs in this one repository, and nothing else: it
cannot read or change code, settings or other repositories.

## 2. Add two jobs on cron-job.org

Create a free account at <https://cron-job.org>, then for each job below: **Create cronjob**,
fill in the fields, and open the **Advanced** tab for the method, headers and body.

Common settings for both jobs:

| Field | Value |
|---|---|
| Request method | `POST` |
| Header | `Accept: application/vnd.github+json` |
| Header | `Authorization: Bearer <your github_pat_… token>` |
| Header | `X-GitHub-Api-Version: 2022-11-28` |
| Time zone | `Asia/Kolkata` |
| Notify on failure | on (cron-job.org emails you if GitHub refuses) |

GitHub answers a successful start with **204 No Content**; that is the expected result.

### Job 1: daily scan

- Title: `job-scanner scan`
- URL: `https://api.github.com/repos/rahulsanap23/job-scanner/actions/workflows/scan.yml/dispatches`
- Schedule: every day at **06:00** and **15:00** (custom: minutes `0`, hours `6,15`)
- Request body:

  ```json
  {"ref": "main", "inputs": {"trigger": "cron"}}
  ```

### Job 2: LinkedIn refresh

- Title: `job-scanner linkedin-refresh`
- URL: `https://api.github.com/repos/rahulsanap23/job-scanner/actions/workflows/linkedin-refresh.yml/dispatches`
- Schedule: every hour at minute **20**, except 05:20–06:59 and 14:20–15:59 (while the scans
  run). Custom: minutes `20`, hours `0-5,7-14,16-23`.
- Request body:

  ```json
  {"ref": "main", "inputs": {"trigger": "cron"}}
  ```

## 3. Check it works

Use **Test run** on each job: the response should be `204`, and a new run should appear under the
repository's **Actions** tab within a minute, started by "workflow_dispatch".

## Notes

- `"trigger": "cron"` is what lets a run skip itself when the other trigger already did the work.
  A run started from the Actions tab's **Run workflow** button uses `manual` and always runs.
- If the token expires, cron-job.org will report `401`: create a new token (step 1) and paste it
  into both jobs' `Authorization` headers.
- All of this is free: cron-job.org's free plan, GitHub Actions on a public repository, and a
  fine-grained token.
