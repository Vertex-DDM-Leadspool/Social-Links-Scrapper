"""Command Line Interface for the Social Media Scraper."""

import argparse
import asyncio
import logging
from pathlib import Path
import sys

from rich.console import Console
from rich.logging import RichHandler
from rich.panel import Panel
from rich.progress import BarColumn, MofNCompleteColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn, TimeRemainingColumn
from rich.table import Table

from config import ScraperConfig
from excel_reader import ExcelReader, SiteRecord
from scraper import ProductionScraper
from storage import StorageManager

console = Console()


def setup_logging(verbose: bool = False):
    """Configure rich logging to console and detailed logging to file."""
    log_level = logging.DEBUG if verbose else logging.INFO

    # Root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)
    root_logger.handlers.clear()

    # File handler (debug level always)
    file_handler = logging.FileHandler("scraper.log", encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    file_handler.setFormatter(file_formatter)
    root_logger.addHandler(file_handler)

    # Console handler
    console_handler = RichHandler(console=console, show_time=True, show_path=False)
    console_handler.setLevel(log_level)
    root_logger.addHandler(console_handler)


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Production-grade Social Media Scraper using Playwright & Excel input.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    parser.add_argument(
        "--excel",
        type=str,
        default="US Insurance brokers Data (Responses).xlsx",
        help="Path to the Excel file containing website URLs.",
    )
    parser.add_argument(
        "--sheet",
        type=str,
        default=None,
        help="Specific sheet name to read from the Excel file (auto-detected if omitted).",
    )
    parser.add_argument(
        "--column",
        type=str,
        default=None,
        help="Website column name or 0-based index in the Excel sheet (auto-detected if omitted).",
    )
    parser.add_argument(
        "--output",
        type=str,
        default="socials_results.json",
        help="Path to save the output JSON file.",
    )
    parser.add_argument(
        "--jsonl",
        type=str,
        default="socials_results.jsonl",
        help="Path to save real-time streaming JSONL output.",
    )
    parser.add_argument(
        "--format",
        choices=["dict", "list", "detailed"],
        default="dict",
        help="JSON output format: 'dict' for {website: {platform: url}}, 'list' for {website: [urls]}, or 'detailed'.",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=8,
        help="Number of concurrent browser pages to run.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=20,
        help="Navigation timeout per page in seconds.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit number of websites to scrape (useful for test runs).",
    )
    parser.add_argument(
        "--start",
        type=int,
        default=0,
        help="Starting offset index in unique websites list.",
    )
    parser.add_argument(
        "--no-resume",
        action="store_true",
        help="Start scraping from scratch even if previous output JSON exists.",
    )
    parser.add_argument(
        "--no-fallback",
        action="store_true",
        help="Disable automatic fallback to root domain when subpage has no socials.",
    )
    parser.add_argument(
        "--no-contact",
        action="store_true",
        help="Disable automatic fallback to /contact or /about pages.",
    )
    parser.add_argument(
        "--no-headless",
        action="store_true",
        help="Run browser in visible mode (default is headless).",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose debug logging.",
    )

    return parser.parse_args()


async def main_async():
    """Main async execution entrypoint."""
    args = parse_args()
    setup_logging(args.verbose)

    config = ScraperConfig(
        excel_path=Path(args.excel),
        sheet_name=args.sheet,
        website_column=args.column,
        output_path=Path(args.output),
        jsonl_output_path=Path(args.jsonl) if args.jsonl else None,
        output_format=args.format,
        concurrency=args.concurrency,
        timeout_seconds=args.timeout,
        headless=not args.no_headless,
        check_root_fallback=not args.no_fallback,
        check_contact_fallback=not args.no_contact,
        resume=not args.no_resume,
        limit=args.limit,
        start_index=args.start,
    )

    # Banner
    console.print(
        Panel.fit(
            "[bold cyan]Playwright Social Media Scraper[/bold cyan]\n"
            f"[yellow]Excel Source:[/yellow] {config.excel_path}\n"
            f"[yellow]Output JSON:[/yellow]  {config.output_path} (Format: [green]{config.output_format}[/green])\n"
            f"[yellow]Concurrency:[/yellow]  {config.concurrency} workers | [yellow]Timeout:[/yellow] {config.timeout_seconds}s\n"
            f"[yellow]Resume Mode:[/yellow]  {config.resume} | [yellow]Root Fallback:[/yellow] {config.check_root_fallback}",
            title="[bold green]Social Scraper Initialized[/bold green]",
            border_style="cyan",
        )
    )

    # 1. Read Excel file
    try:
        reader = ExcelReader(
            file_path=config.excel_path,
            sheet_name=config.sheet_name,
            website_column=config.website_column,
        )
        records = reader.load_sites(limit=config.limit, start_index=config.start_index)
    except Exception as e:
        console.print(f"[bold red]Failed to read Excel file:[/bold red] {e}")
        sys.exit(1)

    if not records:
        console.print("[yellow]No website records found to scrape.[/yellow]")
        return

    # 2. Storage & Resume Setup
    storage = StorageManager(
        json_path=config.output_path,
        jsonl_path=config.jsonl_output_path,
        format_style=config.output_format,
    )

    skip_urls = set()
    if config.resume:
        skip_urls = storage.load_existing()

    to_process = [r for r in records if r.normalized_url not in skip_urls]

    console.print(
        f"[green]Total unique target sites:[/green] {len(records)} | "
        f"[blue]Already completed:[/blue] {len(skip_urls)} | "
        f"[bold yellow]Remaining to scrape:[/bold yellow] {len(to_process)}"
    )

    if not to_process:
        console.print("[bold green]All sites already scraped! Generating final report...[/bold green]")
        storage.save_full_json(len(records))
        display_summary(storage, len(records))
        return

    # 3. Execution with Live Progress
    scraper = ProductionScraper(config)

    progress = Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        TimeRemainingColumn(),
        console=console,
    )

    task_id = progress.add_task("[cyan]Scraping websites...", total=len(to_process))

    def on_record_done(record: SiteRecord, result: dict):
        # Key directly by the website URL from Excel
        storage.record_result(record.normalized_url, result)
        socials_count = result.get("total_socials", 0)
        emails_count = result.get("total_emails", 0)
        status = result.get("status")
        status_color = "green" if status == "success" else "red"
        short_url = record.normalized_url[:40] + ("..." if len(record.normalized_url) > 40 else "")

        progress.update(
            task_id,
            advance=1,
            description=f"[{status_color}]{short_url}[/{status_color}] ({socials_count} socials, {emails_count} emails)",
        )

    def on_batch_saved(completed_count: int):
        storage.save_full_json(len(records))
        logging.getLogger(__name__).debug(f"Progress checkpoint saved ({completed_count} sites).")

    try:
        with progress:
            await scraper.run(
                records=to_process,
                on_record_done=on_record_done,
                on_batch_saved=on_batch_saved,
                skip_urls=skip_urls,
            )
    except KeyboardInterrupt:
        console.print("\n[yellow]Scraping interrupted by user. Saving current progress...[/yellow]")
    finally:
        storage.save_full_json(len(records))

    display_summary(storage, len(records))


def display_summary(storage: StorageManager, total_target_sites: int):
    """Render a summary table with stats of the scraping results."""
    table = Table(title="[bold green]Scraping Job Summary[/bold green]", border_style="cyan")
    table.add_column("Metric", style="bold cyan")
    table.add_column("Value", style="bold white")

    success_count = sum(1 for r in storage.results.values() if r.get("status") == "success")
    failed_count = sum(1 for r in storage.results.values() if r.get("status") != "success")
    sites_with_socials = sum(1 for r in storage.results.values() if r.get("total_socials", 0) > 0)
    sites_with_emails = sum(1 for r in storage.results.values() if len(r.get("emails", [])) > 0)
    total_socials_found = sum(storage.platforms_counter.values())

    table.add_row("Total Target Sites", str(total_target_sites))
    table.add_row("Total Sites Processed", str(len(storage.results)))
    table.add_row("Sites with Socials Found", f"[green]{sites_with_socials}[/green]")
    table.add_row("Sites with Emails Found", f"[green]{sites_with_emails}[/green]")
    table.add_row("Failed / Unreachable Sites", f"[red]{failed_count}[/red]")
    table.add_row("Total Social Profiles Extracted", f"[bold yellow]{total_socials_found}[/bold yellow]")
    table.add_row("Total Emails Extracted", f"[bold yellow]{storage.total_emails_counter}[/bold yellow]")
    table.add_row("Output JSON File", str(storage.json_path))

    console.print(table)

    # Social platforms breakdown table
    p_table = Table(title="[bold magenta]Social Platforms Breakdown[/bold magenta]", border_style="magenta")
    p_table.add_column("Platform", style="cyan")
    p_table.add_column("Profiles Found", style="green")

    for platform, count in sorted(storage.platforms_counter.items(), key=lambda x: -x[1]):
        if count > 0:
            p_table.add_row(platform.capitalize(), str(count))

    if any(storage.platforms_counter.values()):
        console.print(p_table)


def main():
    """Main CLI entrypoint."""
    try:
        asyncio.run(main_async())
    except KeyboardInterrupt:
        console.print("[yellow]Scraper terminated.[/yellow]")
        sys.exit(0)


if __name__ == "__main__":
    main()
