#!/usr/bin/env python3
"""Unified Main Entrypoint for Social Media Scraper & CRM Database Enricher.

Pulls companies from the CRM database (PostgreSQL / SQLite), crawls their
websites for social links & contact emails using Playwright with anti-bot
stealth, updates records back into the database, and supports standalone runs,
daemon mode, and external cron execution.
"""

import argparse
import asyncio
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import re
import sys
import time
from typing import Any, Dict, List, Optional, Set

try:
    from rich.console import Console
    from rich.logging import RichHandler
    from rich.panel import Panel
    from rich.progress import BarColumn, MofNCompleteColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn, TimeRemainingColumn
    from rich.table import Table
    HAS_RICH = True
except ImportError:
    HAS_RICH = False

    class Console:
        def print(self, *args, **kwargs):
            clean_args = []
            for a in args:
                if isinstance(a, str):
                    # Strip rich formatting tags like [bold red]...[/bold red]
                    clean_args.append(re.sub(r"\[/?\w+.*?\]", "", a))
                else:
                    clean_args.append(str(a))
            print(*clean_args)

    class Panel:
        @staticmethod
        def fit(text, title=None, border_style=None):
            return f"\n--- {title or 'Info'} ---\n{text}\n-------------------"
        def __init__(self, text, title=None, border_style=None):
            self.text = text
            self.title = title
        def __str__(self):
            return f"\n--- {self.title or 'Info'} ---\n{self.text}\n-------------------"

    class Table:
        def __init__(self, title=None, border_style=None):
            self.title = title
            self.columns = []
            self.rows = []
        def add_column(self, name, **kwargs):
            self.columns.append(name)
        def add_row(self, *vals):
            self.rows.append(vals)
        def __str__(self):
            lines = [f"\n=== {self.title or 'Table'} ==="]
            lines.append(" | ".join(self.columns))
            lines.append("-" * 40)
            for r in self.rows:
                lines.append(" | ".join(re.sub(r"\[/?\w+.*?\]", "", str(v)) for v in r))
            return "\n".join(lines)

    RichHandler = logging.StreamHandler

from config import SOCIAL_PLATFORMS, ScraperConfig
from db import CompanyLead, DatabaseClient, load_env_database_url
from scraper import SiteCrawler, SiteTarget

console = Console()
logger = logging.getLogger("social_scraper")


def setup_logging(verbose: bool = False):
    """Configure rich console logging and detailed rotating file logging."""
    log_level = logging.DEBUG if verbose else logging.INFO

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)
    root_logger.handlers.clear()

    # File log
    log_file = Path("scraper.log")
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    root_logger.addHandler(file_handler)

    # Console log
    console_handler = RichHandler(console=console, show_time=True, show_path=False)
    console_handler.setLevel(log_level)
    root_logger.addHandler(console_handler)


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Production-Grade Social Media Scraper & CRM Database Enricher.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    # Database options
    parser.add_argument(
        "--db-url",
        type=str,
        default=None,
        help="Database URL (PostgreSQL 'postgresql://...' or SQLite 'sqlite:///...'). Defaults to DATABASE_URL in .env.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Scrape all companies with a website, including those already enriched.",
    )
    parser.add_argument(
        "--company-id",
        type=str,
        default=None,
        help="Target a specific Company ID to scrape and enrich.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Scrape websites but do not write any changes back to the database.",
    )

    # Scraper performance & crawler depth
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximum number of companies to scrape in this execution.",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=4,
        help="Number of concurrent browser pages to crawl with.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=20,
        help="Navigation timeout in seconds per page.",
    )
    parser.add_argument(
        "--max-subpages",
        type=int,
        default=4,
        help="Maximum internal subpages (contact/about/team/root) to crawl per website.",
    )

    # Checkpoint and export
    parser.add_argument(
        "--no-resume",
        action="store_true",
        help="Ignore checkpoint and re-process all matching companies.",
    )
    parser.add_argument(
        "--checkpoint-file",
        type=str,
        default="scraper_checkpoint.json",
        help="File path to store batch progress for resuming interrupted runs.",
    )
    parser.add_argument(
        "--export-json",
        type=str,
        default=None,
        help="Optional path to save an export copy of all scraped results to a JSON file.",
    )

    # Daemon and scheduling
    parser.add_argument(
        "--daemon",
        action="store_true",
        help="Run continuously in the background, waking up periodically to scrape new leads.",
    )
    parser.add_argument(
        "--interval-hours",
        type=float,
        default=6.0,
        help="Interval in hours between execution runs in daemon mode.",
    )

    # Browser & Debugging
    parser.add_argument(
        "--no-headless",
        action="store_true",
        help="Launch browser visibly for debugging.",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable detailed debug logging.",
    )

    return parser.parse_args()


class CheckpointManager:
    """Manages saving and loading completed company IDs to allow auto-resuming."""

    def __init__(self, filepath: Path):
        self.filepath = Path(filepath)
        self.completed_ids: Set[str] = set()

    def load(self) -> Set[str]:
        if not self.filepath.exists():
            return set()
        try:
            with open(self.filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    self.completed_ids = set(data)
                elif isinstance(data, dict):
                    self.completed_ids = set(data.keys())
            return self.completed_ids
        except Exception as e:
            logger.debug(f"Could not load checkpoint: {e}")
            return set()

    def record(self, company_id: str):
        self.completed_ids.add(company_id)

    def save(self):
        try:
            with open(self.filepath, "w", encoding="utf-8") as f:
                json.dump(list(self.completed_ids), f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save checkpoint: {e}")

    def clear(self):
        if self.filepath.exists():
            try:
                self.filepath.unlink()
            except Exception:
                pass
        self.completed_ids.clear()


async def scrape_and_enrich_batch(
    config: ScraperConfig,
    checkpoint: CheckpointManager,
) -> Dict[str, Any]:
    """Execute a single scraping and database enrichment cycle."""
    # 1. Connect to Database
    try:
        db = DatabaseClient(database_url=config.database_url)
        db_type = "SQLite" if db.is_sqlite else "PostgreSQL"
        stats = db.get_database_stats()
    except Exception as e:
        console.print(f"[bold red]Database connection failed:[/bold red] {e}")
        console.print("[yellow]Tip:[/yellow] Set DATABASE_URL in your .env or pass --db-url.")
        return {"status": "error", "message": str(e)}

    # Display database info
    console.print(
        Panel.fit(
            f"[bold cyan]LeadsPool CRM Social Enricher[/bold cyan]\n"
            f"[yellow]Engine:[/yellow]          Playwright Stealth Anti-Bot Crawler\n"
            f"[yellow]Database:[/yellow]        {db_type} ({'Dry Run' if config.dry_run else 'Production Write'})\n"
            f"[yellow]Total Companies:[/yellow] {stats['total_companies']} | [green]With Websites:[/green] {stats['with_website']}\n"
            f"[yellow]Enriched Socials:[/yellow]{stats['scraped_count']} | [blue]With LinkedIn:[/blue] {stats['with_linkedin']} | [blue]With Web Email:[/blue] {stats['with_web_email']}\n"
            f"[yellow]Concurrency:[/yellow]     {config.concurrency} workers | [yellow]Timeout:[/yellow] {config.timeout_seconds}s | [yellow]Subpages:[/yellow] {config.max_subpages}",
            title="[bold green]Social Scraper Pipeline Initialized[/bold green]",
            border_style="cyan",
        )
    )

    # 2. Fetch target companies from database
    try:
        leads: List[CompanyLead] = db.fetch_companies_to_scrape(
            limit=config.limit,
            pending_only=config.pending_only,
            company_id=config.company_id,
        )
    except Exception as e:
        console.print(f"[bold red]Failed to fetch companies from database:[/bold red] {e}")
        return {"status": "error", "message": str(e)}

    if not leads:
        console.print("[bold green]All matching companies are already enriched! Nothing to scrape.[/bold green]")
        return {"status": "success", "processed": 0, "socials_found": 0, "emails_found": 0}

    # 3. Filter already completed from checkpoint
    completed_ids = checkpoint.load() if config.resume else set()
    to_process = [lead for lead in leads if lead.id not in completed_ids]

    console.print(
        f"[green]Matching companies in DB:[/green] {len(leads)} | "
        f"[blue]Already done in checkpoint:[/blue] {len(completed_ids)} | "
        f"[bold yellow]Remaining to scrape:[/bold yellow] {len(to_process)}"
    )

    if not to_process:
        console.print("[bold green]All targeted leads completed in current checkpoint.[/bold green]")
        return {"status": "success", "processed": 0, "socials_found": 0, "emails_found": 0}

    # 4. Set up crawler & queue
    from playwright.async_api import async_playwright

    crawler = SiteCrawler(
        max_subpages_per_site=config.max_subpages,
        timeout_seconds=config.timeout_seconds,
    )

    total_to_process = len(to_process)
    queue: asyncio.Queue = asyncio.Queue()
    for idx, lead in enumerate(to_process, start=1):
        await queue.put((idx, lead))

    console.print(
        f"\n[bold green]► Starting Enrichment Batch:[/] [cyan]{total_to_process} companies queued[/] | "
        f"[yellow]Concurrency: {config.concurrency} workers[/] | [yellow]Timeout: {config.timeout_seconds}s/page[/]\n"
    )
    sys.stdout.flush()

    # Anti-bot browser arguments
    launch_args = [
        "--no-sandbox",
        "--disable-setuid-sandbox",
        "--disable-dev-shm-usage",
        "--disable-blink-features=AutomationControlled",
        "--disable-infobars",
        "--window-position=0,0",
        "--ignore-certificate-errors",
    ]

    all_results: Dict[str, Any] = {}
    total_socials_counter: Dict[str, int] = {p: 0 for p in SOCIAL_PLATFORMS}
    total_emails_added: int = 0
    successful_updates: int = 0
    completed_count: int = 0
    completed_lock = asyncio.Lock()

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=config.headless,
                args=launch_args,
            )

            async def worker(worker_id: int):
                nonlocal successful_updates, total_emails_added, completed_count
                while not queue.empty():
                    try:
                        lead_idx, lead = await queue.get()
                    except asyncio.QueueEmpty:
                        break

                    prefix = f"[bold cyan][{lead_idx}/{total_to_process}][/bold cyan]"
                    lead_name = (lead.name or lead.website or "Untitled Lead").strip()
                    
                    raw_url = (lead.website or "").strip()
                    if not raw_url.startswith("http://") and not raw_url.startswith("https://"):
                        target_url = f"https://{raw_url}"
                    else:
                        target_url = raw_url

                    start_t = time.time()
                    time_str = datetime.now().strftime("%H:%M:%S")
                    console.print(
                        f"\n{prefix} [dim][{time_str}][/dim] [bold white]🚀 Processing:[/] [bold yellow]{lead_name}[/] "
                        f"[dim]({target_url})[/dim] [dim][Worker #{worker_id}][/dim]"
                    )
                    sys.stdout.flush()

                    try:
                        target = SiteTarget(
                            original_url=lead.website,
                            normalized_url=target_url,
                            domain=lead.normalized_domain or "",
                            row_number=lead_idx,
                        )

                        # Crawl site and subpages with safe overall timeout guard (max 35 seconds per site)
                        max_site_timeout = max(35, config.timeout_seconds + (config.max_subpages * 10) + 5)
                        try:
                            crawl_res = await asyncio.wait_for(
                                crawler.crawl_site(target, browser, log_fn=console.print, log_prefix=prefix),
                                timeout=max_site_timeout,
                            )
                        except asyncio.TimeoutError:
                            console.print(
                                f"{prefix} [bold red]⏱ Global site timeout ({max_site_timeout}s limit reached)[/] "
                                f"for {target_url} — skipping to next lead"
                            )
                            crawl_res = {
                                "website": target_url,
                                "emails": [],
                                "socials_dict": {},
                                "socials_list": [],
                                "total_socials": 0,
                                "total_emails": 0,
                                "pages_crawled": [target_url],
                            }

                        socials_dict = crawl_res.get("socials_dict", {})
                        emails_list = crawl_res.get("emails", [])
                        crawled_pages = crawl_res.get("pages_crawled", [])

                        # Save to database
                        console.print(f"{prefix} [dim]💾 Saving to PostgreSQL database...[/dim]")
                        db_res = db.update_company_socials(
                            company_id=lead.id,
                            socials_dict=socials_dict,
                            emails_list=emails_list,
                            pages_crawled_count=len(crawled_pages),
                            dry_run=config.dry_run,
                        )

                        # Record stats
                        successful_updates += 1
                        total_emails_added += db_res.get("emails_added", 0)
                        for platform, links in socials_dict.items():
                            if platform != "emails" and platform in total_socials_counter:
                                cnt = 1 if isinstance(links, str) else len(links)
                                total_socials_counter[platform] += cnt

                        # Store export payload
                        all_results[lead.id] = {
                            "company_name": lead.name,
                            "website": lead.website,
                            "socials": socials_dict,
                            "emails": emails_list,
                            "db_update": db_res,
                        }

                        # Checkpoint
                        checkpoint.record(lead.id)
                        if successful_updates % config.save_interval == 0:
                            checkpoint.save()

                        elapsed = time.time() - start_t
                        async with completed_lock:
                            completed_count += 1
                            percent = int((completed_count / total_to_process) * 100)
                            remaining = total_to_process - completed_count

                        found_cnt = crawl_res.get("total_socials", 0)
                        found_emails = len(emails_list)
                        platforms_found = [k.capitalize() for k in socials_dict.keys() if k != "emails"]

                        console.print(
                            f"{prefix} [bold green]✅ Finished in {elapsed:.1f}s[/] | "
                            f"Found: [green]{found_cnt} socials ({', '.join(platforms_found) or 'none'})[/], [yellow]{found_emails} emails[/] | "
                            f"[bold white]Progress: {completed_count}/{total_to_process} ({percent}%) — {remaining} remaining[/]"
                        )
                        sys.stdout.flush()

                    except Exception as e:
                        logger.error(f"Error processing lead {lead.id} ({lead.website}): {e}")
                        console.print(f"{prefix} [bold red]❌ Error on {lead_name}:[/] {e}")
                        async with completed_lock:
                            completed_count += 1
                    finally:
                        queue.task_done()

            workers = [asyncio.create_task(worker(w_id)) for w_id in range(1, config.concurrency + 1)]
            await asyncio.gather(*workers)

            await browser.close()

    except Exception as e:
        console.print(f"[bold red]Crawler error:[/bold red] {e}")
        logger.exception("Crawler execution failed")
    finally:
        checkpoint.save()
        db.close()

    # 5. Optional JSON export
    if config.export_json:
        try:
            export_path = Path(config.export_json)
            export_path.parent.mkdir(parents=True, exist_ok=True)
            with open(export_path, "w", encoding="utf-8") as f:
                json.dump(all_results, f, indent=2, ensure_ascii=False)
            console.print(f"[green]Exported results to:[/green] {export_path}")
        except Exception as e:
            logger.error(f"Could not export JSON: {e}")

    # 6. Display Summary Table
    display_enrichment_summary(
        total_targeted=len(to_process),
        successful_updates=successful_updates,
        total_emails_added=total_emails_added,
        socials_counter=total_socials_counter,
        is_dry_run=config.dry_run,
    )

    return {
        "status": "success",
        "processed": successful_updates,
        "socials_found": sum(total_socials_counter.values()),
        "emails_added": total_emails_added,
    }


def display_enrichment_summary(
    total_targeted: int,
    successful_updates: int,
    total_emails_added: int,
    socials_counter: Dict[str, int],
    is_dry_run: bool,
):
    """Render a clean summary table of the completed enrichment run."""
    table = Table(
        title="[bold green]CRM Social Enrichment Job Complete[/bold green]",
        border_style="cyan",
    )
    table.add_column("Metric", style="bold cyan")
    table.add_column("Value", style="bold white")

    total_socials_found = sum(socials_counter.values())

    table.add_row("Total Companies Targeted", str(total_targeted))
    table.add_row("Successfully Scraped & Enriched", f"[green]{successful_updates}[/green]")
    table.add_row("New Emails Inserted into DB", f"[yellow]{total_emails_added}[/yellow]")
    table.add_row("Total Social Profiles Discovered", f"[bold green]{total_socials_found}[/bold green]")
    table.add_row("Write Mode", "[yellow]Dry Run (No DB writes)[/yellow]" if is_dry_run else "[green]Production Database Committed[/green]")

    console.print(table)

    # Social platforms breakdown
    if any(socials_counter.values()):
        p_table = Table(title="[bold magenta]Discovered Social Profiles by Platform[/bold magenta]", border_style="magenta")
        p_table.add_column("Platform", style="cyan")
        p_table.add_column("Profiles Found", style="green")

        for platform, count in sorted(socials_counter.items(), key=lambda x: -x[1]):
            if count > 0:
                p_table.add_row(platform.capitalize(), str(count))

        console.print(p_table)


async def main_async():
    """Main CLI entrypoint with support for both single execution and daemon mode."""
    args = parse_args()
    setup_logging(args.verbose)

    config = ScraperConfig(
        database_url=args.db_url,
        pending_only=not args.all,
        company_id=args.company_id,
        dry_run=args.dry_run,
        limit=args.limit,
        concurrency=args.concurrency,
        timeout_seconds=args.timeout,
        max_subpages=args.max_subpages,
        resume=not args.no_resume,
        checkpoint_path=Path(args.checkpoint_file),
        export_json=Path(args.export_json) if args.export_json else None,
        headless=not args.no_headless,
        daemon=args.daemon,
        interval_hours=args.interval_hours,
    )

    checkpoint = CheckpointManager(config.checkpoint_path)

    if not config.daemon:
        # Standard one-off execution (ideal for cron jobs)
        await scrape_and_enrich_batch(config, checkpoint)
    else:
        # Continuous daemon mode
        console.print(
            Panel(
                f"[bold green]Starting Social Scraper Daemon[/bold green]\n"
                f"Will wake up every [cyan]{config.interval_hours}[/cyan] hours to scrape newly added CRM companies.\n"
                f"Press [bold red]Ctrl+C[/bold red] to stop.",
                border_style="green",
            )
        )
        while True:
            console.print(f"\n[cyan]-- Running Enrichment Cycle at {datetime.now(timezone.utc).isoformat()} --[/cyan]")
            await scrape_and_enrich_batch(config, checkpoint)
            sleep_seconds = int(config.interval_hours * 3600)
            console.print(f"[dim]Sleeping for {config.interval_hours} hours ({sleep_seconds}s)...[/dim]")
            try:
                await asyncio.sleep(sleep_seconds)
            except asyncio.CancelledError:
                break


def main():
    """Main script runner."""
    try:
        asyncio.run(main_async())
    except KeyboardInterrupt:
        console.print("\n[yellow]Execution halted by user.[/yellow]")
        sys.exit(0)


if __name__ == "__main__":
    main()
