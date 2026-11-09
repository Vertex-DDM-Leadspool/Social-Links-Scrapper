# LeadsPool Social Scraper & CRM Database Enricher

A production-grade web crawler that automatically enriches your **Next.js + PostgreSQL CRM** (`LeadsPool-Outreach`). It pulls leads with website URLs from the database, crawls entry pages and subpages (`/contact`, `/about`, `/team`, root) using Playwright stealth anti-bot technology, extracts verified social media profiles (17+ platforms) and email addresses, and commits the enriched data back into the CRM database.

---

## Key Capabilities

1. **Direct Database Integration**:
   - Automatically pulls database credentials from `.env` (supports `DATABASE_URL` or individual `POSTGRES_*` variables).
   - Supports production PostgreSQL and local SQLite (`dev.db`).
   - Pulls active, non-archived companies where `website` is present.
   - Automatically skips already enriched companies using smart pending filters.
2. **Three-Table CRM Updates**:
   - **`Company`**: Updates `linkedInUrl`, `companyProfile` (if empty), `webEmail` (if empty), and appends a clean structured breakdown of all social links into `comments`.
   - **`Email`**: Inserts all newly discovered contact emails with `source = 'WEB'`, checking for role-based formats (`info@`, `contact@`, etc.) and deduplicating per company.
   - **`Activity`**: Logs an audit entry (`type = 'SOCIALS_SCRAPED'`) detailing discovered profiles and emails.
3. **Playwright Anti-Bot Stealth Crawler**:
   - Injects stealth script (`navigator.webdriver` removal, Chrome runtime mocking, WebGL fingerprinting).
   - Smooth lazy-scroll down each page to trigger footers where 90% of business social links reside.
   - Automatic Cloudflare / WAF hydration challenge handling (HTTP 202 wait).
   - Intelligent internal subpage ranking: crawls up to 4 internal pages (`/contact`, `/about`, `/team`, root).
4. **Flexible Automation & Cron Scheduling**:
   - **Linux Crontab Script** (`cron_job.sh`): Features `flock` locking to prevent overlapping runs.
   - **Built-in Daemon Mode**: `python main.py --daemon --interval-hours 6` for zero-config background execution.

---

## Quick Start

### 1. Install Dependencies
```bash
pip install -r requirements.txt
playwright install --with-deps chromium
```

### 2. Configure Environment
Create or edit `.env` in this directory (or use the CRM's `.env` in the parent directory):
```ini
DATABASE_URL="postgresql://postgres:postgres@127.0.0.1:5432/lead_crm?schema=public"
CONCURRENCY=4
TIMEOUT_SECONDS=20
```

### 3. Run the Enricher

```bash
# Standard run: enriches up to 50 pending leads from the database
python main.py --limit 50
```

---

## Common Usage Examples

### Test with SQLite Dev Database (Dry Run)
Test scraping without writing any changes to the database:
```bash
python main.py --db-url "sqlite:///../prisma/dev.db" --limit 5 --dry-run
```

### Enrich a Specific Company by ID
```bash
python main.py --company-id "cmvdtjxzz0005blptm3dilfd8"
```

### Re-scrape All Companies (including already enriched ones)
```bash
python main.py --all --limit 100
```

### Run as a Continuous Background Daemon (Every 6 Hours)
```bash
python main.py --daemon --interval-hours 6 --limit 50
```

### Save a Copy of Scraped Data to JSON
```bash
python main.py --limit 20 --export-json results.json
```

---

## Cron Job Automation

A production-ready script `cron_job.sh` is provided with automatic virtualenv detection and lockfile handling:

```bash
chmod +x cron_job.sh
```

### Crontab Setup (Linux)
```bash
crontab -e
```
Add:
```bash
0 */6 * * * /bin/bash /path/to/Social-Links-Scrapper/cron_job.sh >> /var/log/social_scraper_cron.log 2>&1
```

---

## CLI Options Reference

| Flag | Default | Description |
| :--- | :--- | :--- |
| `--db-url URL` | `.env` | PostgreSQL (`postgresql://...`) or SQLite (`sqlite:///...`) URL |
| `--limit N` | None (all) | Max companies to scrape in this execution |
| `--concurrency N` | `4` | Number of concurrent Playwright browser workers |
| `--timeout N` | `20` | Page navigation timeout in seconds |
| `--max-subpages N` | `4` | Max internal subpages (contact/about/team/root) to crawl per site |
| `--all` | `False` | Scrape all companies with websites, even if already enriched |
| `--company-id ID` | `None` | Target a single company record by its CUID |
| `--dry-run` | `False` | Scrape websites but do not write changes to database |
| `--daemon` | `False` | Run continuously in background, waking up periodically |
| `--interval-hours N` | `6.0` | Hours between cycles when running in daemon mode |
| `--checkpoint-file PATH`| `scraper_checkpoint.json` | File path to store batch progress for resuming |
| `--export-json PATH` | `None` | Save an export copy of all results to a JSON file |
| `--no-resume` | `False` | Ignore checkpoint and start fresh |
| `--no-headless` | `False` | Open browser visibly for debugging |
| `-v, --verbose` | `False` | Enable detailed debug logs |
