# PSX report reader

Free AI models read the public annual reports of companies listed on the Pakistan Stock Exchange and keep only
figures that pass strict checks. The figures feed a private research tracker.

- `toolkit/annual.py` picks the reports that add missing years (newest first; a multi year summary page covers
  6 to 10 years, so the next pick jumps past it), downloads them from the KHI Stocks archive or PSX, keeps only the
  key pages (`toolkit/lib/prep.py`), and asks two models of different makers to copy the figures as printed.
- A figure is kept only when two models give the exact same number, the number is printed on the pages, and it
  passes the arithmetic checks (sales less cost of sales is gross profit, profit before tax less tax is profit,
  owners' profit plus minority is profit, equity and current assets within total assets). A third model settles
  disagreements. OCR pages keep only figures an identity confirms. The printed company name must match the ticker.
- `toolkit/reader.py` is the free model panel: Gemini and Gemma (GEMINI_API_KEY), plus Groq, Mistral, OpenRouter,
  Z.ai, NVIDIA and Cloudflare when their keys are set as Actions secrets.

Output: `data/files/report_extracts.json` (checked figures, Rs million, per share in rupees), `data/files/annual/`
(state of every report, raw model reads, log). Runs every hour (`.github/workflows/reader.yml`); writing anything into
`.github/run-now` starts a run.

`data/db/market/` holds only the list of listed companies and which years the tracker already has (no figures).

## Free lane for the tracker's regular jobs (2 Nov 2026)
`lane-daily.yml` and `lane-jobs.yml` run the PSX Tracker's daily, report import, weekly and monthly jobs here,
where robot minutes are free. They open the private tracker repository with a hidden key, run its own scripts
there and save straight back into it. No data, holdings or keys are kept in this repository, and the run logs
show only one line per job.
