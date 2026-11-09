"""Configuration settings for the Social Media Scraper and CRM Database Enricher."""

from dataclasses import dataclass
from pathlib import Path
import platform
from typing import Optional, Set

# Detect operating system to provide consistent user-agent and Sec-Ch-Ua headers
IS_LINUX = platform.system().lower() == "linux"

if IS_LINUX:
    DEFAULT_USER_AGENT = (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/130.0.0.0 Safari/537.36"
    )
    DEFAULT_SEC_CH_UA_PLATFORM = '"Linux"'
else:
    DEFAULT_USER_AGENT = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/130.0.0.0 Safari/537.36"
    )
    DEFAULT_SEC_CH_UA_PLATFORM = '"Windows"'

# Known social media platforms to track
SOCIAL_PLATFORMS: Set[str] = {
    "facebook",
    "instagram",
    "twitter",
    "linkedin",
    "youtube",
    "tiktok",
    "pinterest",
    "yelp",
    "threads",
    "discord",
    "telegram",
    "reddit",
    "whatsapp",
    "github",
    "medium",
    "snapchat",
    "bluesky",
}


@dataclass
class ScraperConfig:
    """Configuration options for the scraper and database enrichment execution."""
    # Database settings
    database_url: Optional[str] = None
    pending_only: bool = True               # Only scrape companies missing LinkedIn/email/socials
    company_id: Optional[str] = None       # Target specific company ID
    dry_run: bool = False                  # Do not write changes to database

    # File exports & checkpoints
    checkpoint_path: Path = Path("scraper_checkpoint.json")
    export_json: Optional[Path] = None     # Optional JSON export path
    output_format: str = "dict"            # "dict" or "list"

    # Crawler concurrency & timing
    concurrency: int = 4                   # Number of concurrent browser pages
    timeout_seconds: int = 15              # Page navigation timeout
    max_subpages: int = 3                  # Internal subpages (contact/about/team/root)
    navigation_wait_until: str = "domcontentloaded"
    page_settle_wait_ms: int = 800
    waf_challenge_wait_ms: int = 2000
    max_retries: int = 1

    # Browser behavior
    headless: bool = True
    user_agent: str = DEFAULT_USER_AGENT
    sec_ch_ua_platform: str = DEFAULT_SEC_CH_UA_PLATFORM
    viewport_width: int = 1280
    viewport_height: int = 800

    # Scraper features
    check_root_fallback: bool = True       # If subpage has 0 socials, check root domain
    check_contact_fallback: bool = True    # If root has 0 socials, check contact/about page
    save_interval: int = 10                # Checkpoint save interval
    resume: bool = True                    # Resume from previous output if exists

    # Limits & Scheduling
    limit: Optional[int] = None            # Max websites to scrape
    start_index: int = 0                   # Offset to start from
    daemon: bool = False                   # Run as a continuous background daemon
    interval_hours: float = 6.0            # Daemon recurrence interval in hours
