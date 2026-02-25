# UNICEF Jobs – Filtered RSS Feed

A Python script that downloads the official UNICEF careers RSS feed, filters it for relevant professional positions, and publishes a clean RSS 2.0 feed via GitHub Pages.

**Output feed URL:** <https://cinfoposte.github.io/unicef-jobs/unicef_jobs.xml>

## Filters

- **Included:** Professional grades P-1 through P-5 and Director grades D-1/D-2
- **Included:** Internships and fellowships
- **Excluded:** Consultancies, individual contractors (ICA/IICA)
- **Excluded:** General Service (G/GS), National Officer (NO/NOA–NOD), Service Contract (SB), and other non-P/D grade families (LSC, etc.)
- Output is limited to the **50 newest** items, sorted by publication date

## Local usage

```bash
pip install -r requirements.txt
python unicef_jobs.py
```

The script writes `unicef_jobs.xml` in the repository root and prints a summary of included/excluded items.

## Automation

A GitHub Actions workflow (`.github/workflows/update_feed.yml`) runs daily and on manual dispatch. It regenerates the feed and commits the updated XML only when there are changes.
