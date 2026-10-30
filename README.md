# Production-Grade Playwright Social Media & Email Crawler (Single Script)

A self-contained, production-grade crawler in a **single script** ([`scraper.py`](file:///home/saad/Desktop/Diadalus/Social-Scraper/scraper.py)). It reads website URLs from an Excel workbook, acts as an intelligent crawler following internal pages (contact, about, team, root homepage), bypasses anti-bot defenses, and outputs a simple JSON mapping of websites to socials and contact emails.

---

## Simple JSON Structure

The JSON directly maps the website to its socials and extracted emails:

```json
{
  "https://www.championrisk.com/": {
    "emails": "info@championrisk.com",
    "facebook": "https://facebook.com/ChampionRisk",
    "twitter": "https://twitter.com/champrisk",
    "linkedin": [
      "http://linkedin.com/in/mark-raby-27439112",
      "https://linkedin.com/company/champion-risk-&-insurance-services"
    ]
  },
  "https://www.enterinsurance.com/": {
    "emails": "1412@enterinsurance.com",
    "facebook": "https://facebook.com/enterinsurance",
    "instagram": [
      "https://instagram.com/enterinsurancefla",
      "https://instagram.com/enterinsurancehealth"
    ],
    "whatsapp": "https://wa.me/18134056150"
  },
  "https://adrianasinsurance.com/en/insurance/auto-insurance-8th-St": {
    "facebook": [
      "https://facebook.com/AdrianasInsurance",
      "https://facebook.com/AdrianasInsuranceServices",
      "https://facebook.com/adrianasinsurance"
    ],
    "instagram": "https://instagram.com/adrianasinsurance",
    "youtube": [
      "https://youtube.com/@AdrianasInsurance",
      "https://youtube.com/@adrianasinsurance",
      "https://youtube.com/@adrianasinsuranceservices"
    ],
    "tiktok": [
      "https://tiktok.com/@adrianas_insurance",
      "https://tiktok.com/@adrianasinsurance"
    ],
    "yelp": "https://yelp.com/biz/adrianas-insurance-baldwin-park",
    "linkedin": "https://linkedin.com/company/adrianas-insurance"
  },
  "https://site-with-no-socials.com/": {}
}
```

Or as a flat list with `--format list`:
```json
{
  "https://www.championrisk.com/": [
    "https://facebook.com/ChampionRisk",
    "https://linkedin.com/company/champion-risk-&-insurance-services",
    "https://twitter.com/champrisk",
    "mailto:info@championrisk.com"
  ]
}
```

---

## How It Works as an Anti-Bot Crawler

1. **Anti-Bot Stealth**:
   - Injects stealth script before page loads (`Object.defineProperty(navigator, 'webdriver', { get: () => undefined })`).
   - Mocks `window.chrome.runtime`, real plugins, WebGL vendor, and browser languages.
   - Disables Blink automation flags (`--disable-blink-features=AutomationControlled`).
   - Uses OS-matched Chrome User-Agent and `Sec-Ch-Ua` headers.
   - Automatically handles WAF/Cloudflare (HTTP 202) hydration challenges.

2. **Smooth Lazy-Scroll Trigger**:
   - 90% of business websites place social links in footers that only load when the user scrolls down.
   - The crawler automatically scrolls to the bottom of each page to trigger lazy-loaded footers, iframes, and dynamic social widgets.

3. **Intelligent Internal Crawling**:
   - If socials are missing or partial on the entry page, it discovers and scores internal links on the same domain.
   - High-priority pages crawled automatically:
     - **Contact Pages**: `/contact`, `/contact-us`, `/reach-us`, `/get-in-touch`
     - **About Pages**: `/about`, `/about-us`, `/who-we-are`, `/our-story`
     - **Team Pages**: `/team`, `/our-team`, `/leadership`, `/agents`
     - **Root Domain Homepage**: If the Excel URL was a deep subpage (e.g. `/en/insurance/auto-8th-st`), it automatically visits the root homepage.
   - Aggregates all social profiles found across all subpages for that website.

4. **Self-Contained Single Script**:
   - Everything (Excel reader, anti-bot stealth, multi-page crawler, extraction, storage, and progress UI) is contained in **[`scraper.py`](file:///home/saad/Desktop/Diadalus/Social-Scraper/scraper.py)**.

5. **Atomic Storage & Auto-Resume**:
   - Periodically saves every 10 sites and on exit via temporary file replacement (`os.replace`), preventing corrupted JSON files.
   - Resume mode: if stopped and re-run, it immediately skips already crawled websites.

---

## How to Run

### 1. Default Run (Full Excel Dataset, 8 Workers)
```bash
python3 scraper.py
```

### 2. Quick Test Run (e.g., first 10 websites)
```bash
python3 scraper.py --limit 10
```

### 3. Flat List Format
```bash
python3 scraper.py --format list
```

### 4. Custom Subpage Crawl Depth & Concurrency
```bash
python3 scraper.py --concurrency 10 --max-subpages 5 --timeout 15
```

---

## CLI Options

| Flag | Default | Description |
| :--- | :--- | :--- |
| `--excel PATH` | `US Insurance brokers Data (Responses).xlsx` | Path to Excel file |
| `--output PATH` | `socials_results.json` | Path to save output JSON |
| `--format {dict,list}` | `dict` | `dict` (categorized) or `list` (flat array) |
| `--concurrency N` | `8` | Number of concurrent browser workers |
| `--timeout N` | `20` | Timeout per page in seconds |
| `--max-subpages N` | `4` | Max internal subpages (contact/about/team/root) to crawl per site |
| `--limit N` | `None` (all) | Limit number of websites to crawl |
| `--no-resume` | `False` | Start from scratch instead of resuming |
| `--no-headless` | `False` | Run browser visibly for debugging |
| `-v, --verbose` | `False` | Enable debug logs in console |
