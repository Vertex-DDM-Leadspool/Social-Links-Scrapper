"""Excel file reader and URL preprocessor."""

from dataclasses import dataclass, field
import logging
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import urlparse

import openpyxl

logger = logging.getLogger(__name__)

# Common names for website columns
WEBSITE_COLUMN_ALIASES = [
    "website",
    "web",
    "site",
    "url",
    "web address",
    "company website",
    "business website",
    "homepage",
]

# Words that indicate a non-URL entry
INVALID_URL_VALUES = {
    "n/a",
    "na",
    "none",
    "null",
    "-",
    "--",
    "no website",
    "unknown",
    "not available",
    "pending",
}

# Regex to check if a string looks like a legitimate domain or URL
DOMAIN_REGEX = re.compile(
    r"^(?:https?:\/\/)?(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}(?::\d+)?(?:\/.*)?$",
    re.IGNORECASE,
)

# Address keywords to quickly reject shifted address rows
STREET_KEYWORDS = {
    " rd", " st", " ave", " blvd", " dr", " ln", " way", " ct", " ste", " suite", " unit", " fl ", " floor", " pkwy", " hwy"
}


@dataclass
class SiteRecord:
    """Represents a unique website to scrape along with its metadata."""
    original_url: str
    normalized_url: str
    domain: str
    row_numbers: List[int] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


class ExcelReader:
    """Reads website URLs and metadata from Excel workbooks."""

    def __init__(
        self,
        file_path: Path,
        sheet_name: Optional[str] = None,
        website_column: Optional[str] = None,
    ):
        self.file_path = Path(file_path)
        self.sheet_name = sheet_name
        self.website_column = website_column

        if not self.file_path.exists():
            raise FileNotFoundError(f"Excel file not found at: {self.file_path}")

    @staticmethod
    def is_valid_url_string(raw: str) -> bool:
        """Check if a raw cell value represents a valid URL/domain."""
        if not raw or not isinstance(raw, str):
            return False

        clean = raw.strip()
        if not clean or clean.lower() in INVALID_URL_VALUES:
            return False

        # Reject phone numbers
        if re.match(r"^\+?[\d\s\-\(\)\.]{7,}$", clean):
            return False

        # Reject street addresses
        clean_lower = clean.lower()
        if any(keyword in clean_lower for keyword in STREET_KEYWORDS) and not clean.startswith(("http://", "https://")):
            return False

        # Must match domain pattern
        if not DOMAIN_REGEX.match(clean):
            return False

        return True

    @staticmethod
    def normalize_website_url(raw: str) -> Tuple[str, str]:
        """Normalize URL by adding https:// if scheme is missing and return (normalized_url, domain)."""
        clean = raw.strip()
        if clean.startswith("//"):
            clean = "https:" + clean
        elif not clean.startswith(("http://", "https://")):
            clean = "https://" + clean

        parsed = urlparse(clean)
        domain = parsed.netloc.lower()
        if domain.startswith("www."):
            domain = domain[4:]

        return clean, domain

    def find_website_column_index(self, headers: List[Any], rows: List[List[Any]]) -> int:
        """Locate the website column index by header name or by scanning sample rows."""
        # 1. If user supplied an explicit column name or index
        if self.website_column:
            # Check if integer index was passed
            if self.website_column.isdigit():
                idx = int(self.website_column)
                if 0 <= idx < len(headers):
                    return idx
            # Match against header names
            target = self.website_column.strip().lower()
            for idx, h in enumerate(headers):
                if h and str(h).strip().lower() == target:
                    return idx

        # 2. Match headers against common aliases
        for idx, h in enumerate(headers):
            if h is None:
                continue
            h_str = str(h).strip().lower()
            if h_str in WEBSITE_COLUMN_ALIASES or any(alias in h_str for alias in WEBSITE_COLUMN_ALIASES):
                logger.info(f"Detected website column '{headers[idx]}' at column index {idx}")
                return idx

        # 3. Fallback: inspect sample row data to see which column contains URLs
        scores = [0] * len(headers)
        for row in rows[:50]:
            for col_idx, val in enumerate(row):
                if val and self.is_valid_url_string(str(val)):
                    scores[col_idx] += 1

        best_idx = max(range(len(scores)), key=lambda i: scores[i])
        if scores[best_idx] > 0:
            logger.info(f"Inferred website column at index {best_idx} by content analysis")
            return best_idx

        raise ValueError(f"Could not identify a website column in {self.file_path}. Please specify --column.")

    def load_sites(self, limit: Optional[int] = None, start_index: int = 0) -> List[SiteRecord]:
        """Load, validate, deduplicate, and return website records from the Excel file."""
        logger.info(f"Opening Excel workbook: {self.file_path}")
        wb = openpyxl.load_workbook(self.file_path, read_only=True, data_only=True)

        # Select sheet
        if self.sheet_name and self.sheet_name in wb.sheetnames:
            sheet = wb[self.sheet_name]
        else:
            # Prefer 'Form Responses' or sheet with most rows
            candidates = [s for s in wb.sheetnames if "response" in s.lower() or "form" in s.lower() or "data" in s.lower()]
            sheet_title = candidates[0] if candidates else wb.sheetnames[0]
            sheet = wb[sheet_title]
            logger.info(f"Using sheet: '{sheet_title}'")

        row_iterator = sheet.iter_rows(values_only=True)
        try:
            headers = list(next(row_iterator))
        except StopIteration:
            wb.close()
            return []

        # Find columns
        # First gather sample rows to aid column detection if headers are ambiguous
        sample_rows = []
        all_remaining_rows = []
        for r in row_iterator:
            all_remaining_rows.append(list(r))
            if len(sample_rows) < 30:
                sample_rows.append(list(r))

        website_col_idx = self.find_website_column_index(headers, sample_rows)

        # Map header names for metadata extraction
        header_names = [str(h).strip() if h is not None else f"Column_{i}" for i, h in enumerate(headers)]

        # Group and deduplicate sites
        sites_map: Dict[str, SiteRecord] = {}
        total_rows_processed = 0
        skipped_empty = 0
        skipped_invalid = 0

        for row_idx, row in enumerate(all_remaining_rows, start=2):
            total_rows_processed += 1
            if website_col_idx >= len(row) or row[website_col_idx] is None:
                skipped_empty += 1
                continue

            raw_val = str(row[website_col_idx]).strip()
            if not raw_val or raw_val.lower() in INVALID_URL_VALUES:
                skipped_empty += 1
                continue

            if not self.is_valid_url_string(raw_val):
                skipped_invalid += 1
                continue

            normalized_url, domain = self.normalize_website_url(raw_val)

            # Build metadata dict from row
            row_meta = {}
            for col_i, col_name in enumerate(header_names):
                if col_i < len(row) and row[col_i] is not None:
                    row_meta[col_name] = str(row[col_i]).strip()

            if normalized_url not in sites_map:
                sites_map[normalized_url] = SiteRecord(
                    original_url=raw_val,
                    normalized_url=normalized_url,
                    domain=domain,
                    row_numbers=[row_idx],
                    metadata=row_meta,
                )
            else:
                sites_map[normalized_url].row_numbers.append(row_idx)

        wb.close()

        records = list(sites_map.values())
        logger.info(
            f"Excel parsing complete: {total_rows_processed} data rows, "
            f"{skipped_empty} empty/NA, {skipped_invalid} invalid/address rows. "
            f"Found {len(records)} unique valid websites."
        )

        # Apply offset and limit if specified
        if start_index > 0:
            records = records[start_index:]
        if limit is not None and limit > 0:
            records = records[:limit]

        return records
