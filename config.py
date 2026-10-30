"""Configuration settings for the Social Media Scraper."""

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
    """Configuration options for the scraper execution."""
    excel_path: Path = Path("US Insurance brokers Data (Responses).xlsx")
    sheet_name: Optional[str] = None
    website_column: Optional[str] = None
    output_path: Path = Path("socials_results.json")
    jsonl_output_path: Optional[Path] = Path("socials_results.jsonl")
    output_format: str = "dict"  # "dict", "list", or "detailed"

    # Concurrency and timing
    concurrency: int = 8
    timeout_seconds: int = 20
    navigation_wait_until: str = "domcontentloaded"  # "load", "domcontentloaded", "networkidle"
    page_settle_wait_ms: int = 1500                  # Standard wait for client JS rendering
    waf_challenge_wait_ms: int = 3500                # Wait when 202 / WAF challenge is encountered
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
    save_interval: int = 10                # Save progress to JSON every N items
    resume: bool = True                    # Resume from previous output if exists

    # Limits
    limit: Optional[int] = None            # Max websites to scrape
    start_index: int = 0                   # Offset to start from
