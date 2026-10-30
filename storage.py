"""Storage manager for saving scraper results to simple JSON and JSONL atomically."""

from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import tempfile
from typing import Any, Dict, List, Optional, Set

from config import SOCIAL_PLATFORMS

logger = logging.getLogger(__name__)


class StorageManager:
    """Handles thread-safe/atomic persistence of scraping results."""

    def __init__(
        self,
        json_path: Path,
        jsonl_path: Optional[Path] = None,
        format_style: str = "dict",
    ):
        self.json_path = Path(json_path)
        self.jsonl_path = Path(jsonl_path) if jsonl_path else None
        self.format_style = format_style
        self.results: Dict[str, Dict[str, Any]] = {}
        self.platforms_counter: Dict[str, int] = {p: 0 for p in SOCIAL_PLATFORMS}
        self.total_emails_counter: int = 0

    def load_existing(self) -> Set[str]:
        """Load already completed site URLs from the existing JSON output file."""
        if not self.json_path.exists():
            return set()

        try:
            with open(self.json_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            already_done = set()

            # Handle both simple structure {"site": socials} and nested {"sites": {...}}
            if "sites" in data and isinstance(data["sites"], dict):
                sites_data = data["sites"]
                already_done = set(sites_data.keys())
                for url, record in sites_data.items():
                    self.results[url] = record
                    socials = record.get("socials", {})
                    for platform, links in socials.items():
                        if links:
                            self.platforms_counter[platform] = self.platforms_counter.get(platform, 0) + len(links)
                    emails = record.get("emails", [])
                    self.total_emails_counter += len(emails)
            else:
                already_done = set(data.keys())
                for url, val in data.items():
                    if isinstance(val, dict):
                        # Simple dict format
                        emails_val = val.get("emails")
                        emails_list: List[str] = []
                        if emails_val:
                            emails_list = [emails_val] if isinstance(emails_val, str) else list(emails_val)
                            self.total_emails_counter += len(emails_list)

                        socials = {
                            p: [links] if isinstance(links, str) else links
                            for p, links in val.items()
                            if p != "emails"
                        }
                        all_socials = [l for p, links in socials.items() for l in links]

                        self.results[url] = {
                            "status": "success",
                            "emails": emails_list,
                            "socials": socials,
                            "all_socials": all_socials,
                            "total_socials": len(all_socials),
                            "total_emails": len(emails_list),
                        }
                    elif isinstance(val, list):
                        # Simple list format
                        emails_from_list = [
                            l.replace("mailto:", "") for l in val if isinstance(l, str) and l.startswith("mailto:")
                        ]
                        self.total_emails_counter += len(emails_from_list)
                        social_links = [
                            l for l in val if isinstance(l, str) and not l.startswith("mailto:")
                        ]
                        self.results[url] = {
                            "status": "success",
                            "emails": emails_from_list,
                            "socials": {},
                            "all_socials": social_links,
                            "total_socials": len(social_links),
                            "total_emails": len(emails_from_list),
                        }

            logger.info(f"Resumed from {self.json_path}: loaded {len(already_done)} existing websites.")
            return already_done
        except Exception as e:
            logger.warning(f"Could not read existing JSON file {self.json_path} ({e}). Starting fresh.")
            return set()

    def record_result(self, site_url: str, site_data: Dict[str, Any]) -> None:
        """Store a single site result in memory and append to jsonl."""
        self.results[site_url] = site_data

        # Update platforms counter
        socials = site_data.get("socials", {})
        for platform, links in socials.items():
            if links:
                self.platforms_counter[platform] = self.platforms_counter.get(platform, 0) + len(links)

        # Update emails counter
        emails = site_data.get("emails", [])
        self.total_emails_counter += len(emails)

        # Append to JSONL stream
        if self.jsonl_path:
            try:
                # Format simple jsonl record
                if self.format_style == "list":
                    all_items = list(site_data.get("all_socials", []))
                    if emails:
                        for e in emails:
                            mailto_link = f"mailto:{e}"
                            if mailto_link not in all_items:
                                all_items.append(mailto_link)
                    line_obj = {site_url: sorted(all_items)}
                elif self.format_style == "dict":
                    clean_dict = {}
                    if emails:
                        clean_dict["emails"] = emails[0] if len(emails) == 1 else emails
                    for p, links in socials.items():
                        if links:
                            clean_dict[p] = links[0] if len(links) == 1 else links
                    line_obj = {site_url: clean_dict}
                else:
                    line_obj = {"website": site_url, **site_data}

                with open(self.jsonl_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(line_obj, ensure_ascii=False) + "\n")
            except Exception as e:
                logger.error(f"Error writing to JSONL stream {self.jsonl_path}: {e}")

    def save_full_json(self, total_sites_count: int = 0) -> None:
        """Atomically persist the results structure to the main JSON file."""
        if self.format_style == "list":
            # Simple list: {"website": ["https://...", "mailto:..."]}
            output_payload = {}
            for url, data in self.results.items():
                all_items = list(data.get("all_socials", []))
                emails = data.get("emails", [])
                if emails:
                    for e in emails:
                        mailto_link = f"mailto:{e}"
                        if mailto_link not in all_items:
                            all_items.append(mailto_link)
                output_payload[url] = sorted(all_items)
        elif self.format_style == "dict":
            # Simple dictionary: {"website": {"emails": "...", "facebook": "https://...", "twitter": "..."}}
            output_payload = {}
            for url, data in self.results.items():
                socials = data.get("socials", {})
                emails = data.get("emails", [])
                clean_dict = {}
                if emails:
                    clean_dict["emails"] = emails[0] if len(emails) == 1 else emails
                for platform, links in socials.items():
                    if links:
                        clean_dict[platform] = links[0] if len(links) == 1 else links
                output_payload[url] = clean_dict
        else:
            # Detailed format with metadata wrapper
            success_count = sum(1 for r in self.results.values() if r.get("status") == "success")
            failed_count = sum(1 for r in self.results.values() if r.get("status") != "success")
            sites_with_emails = sum(1 for r in self.results.values() if len(r.get("emails", [])) > 0)
            output_payload = {
                "metadata": {
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                    "total_target_sites": total_sites_count or len(self.results),
                    "scraped_count": len(self.results),
                    "success_count": success_count,
                    "failed_count": failed_count,
                    "total_emails_found": self.total_emails_counter,
                    "sites_with_emails": sites_with_emails,
                    "platforms_summary": {k: v for k, v in self.platforms_counter.items() if v > 0},
                },
                "sites": self.results,
            }

        # Atomic write via temporary file
        parent_dir = self.json_path.parent
        parent_dir.mkdir(parents=True, exist_ok=True)

        try:
            with tempfile.NamedTemporaryFile("w", dir=parent_dir, delete=False, encoding="utf-8") as tmp:
                json.dump(output_payload, tmp, indent=2, ensure_ascii=False)
                temp_name = tmp.name

            # Atomically replace destination file
            os.replace(temp_name, self.json_path)
        except Exception as e:
            logger.error(f"Failed to write results JSON atomically: {e}")
            if "temp_name" in locals() and os.path.exists(temp_name):
                try:
                    os.remove(temp_name)
                except OSError:
                    pass
            raise
