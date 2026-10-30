#!/usr/bin/env python3
"""Production-Grade Playwright Social Media Crawler (Single-Script).

Crawls websites listed in an Excel workbook, follows internal links
(contact/about/team/root pages), bypasses anti-bot detection, and outputs a simple JSON mapping of websites to socials.
"""

import argparse
import asyncio
from dataclasses import dataclass, field
import json
import logging
import os
from pathlib import Path
import platform
import re
import sys
import tempfile
import time
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import parse_qsl, unquote, urlencode, urljoin, urlparse, urlunparse

import openpyxl
from playwright.async_api import Browser, BrowserContext, Page, async_playwright
from rich.console import Console
from rich.logging import RichHandler
from rich.panel import Panel
from rich.progress import BarColumn, MofNCompleteColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn, TimeRemainingColumn
from rich.table import Table

console = Console()
logger = logging.getLogger("social_crawler")

# ---------------------------------------------------------------------------
# Anti-Bot & Platform Configurations
# ---------------------------------------------------------------------------
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

# Anti-Bot stealth injection script executed before any page script
STEALTH_INJECT_JS = """
(() => {
    // 1. Hide navigator.webdriver
    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });

    // 2. Mock Chrome runtime object
    window.chrome = {
        runtime: {},
        app: {},
        csi: function(){},
        loadTimes: function(){}
    };

    // 3. Mock plugins list
    Object.defineProperty(navigator, 'plugins', {
        get: () => [1, 2, 3, 4, 5]
    });

    // 4. Mock languages
    Object.defineProperty(navigator, 'languages', {
        get: () => ['en-US', 'en']
    });

    // 5. Pass WebGL vendor check
    try {
        const getParameter = WebGLRenderingContext.prototype.getParameter;
        WebGLRenderingContext.prototype.getParameter = function(parameter) {
            if (parameter === 37445) return 'Intel Inc.';
            if (parameter === 37446) return 'Intel Iris OpenGL Engine';
            return getParameter.apply(this, arguments);
        };
    } catch(e) {}
})();
"""

# Tracked social platforms
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

PLATFORM_DOMAINS = {
    "facebook": {"facebook.com", "m.facebook.com", "fb.com", "fb.me"},
    "twitter": {"twitter.com", "x.com", "mobile.twitter.com"},
    "linkedin": {"linkedin.com"},
    "instagram": {"instagram.com"},
    "youtube": {"youtube.com", "youtu.be", "m.youtube.com"},
    "tiktok": {"tiktok.com"},
    "pinterest": {"pinterest.com", "pin.it"},
    "yelp": {"yelp.com"},
    "threads": {"threads.net"},
    "discord": {"discord.gg", "discord.com"},
    "telegram": {"t.me", "telegram.me"},
    "reddit": {"reddit.com"},
    "whatsapp": {"wa.me", "api.whatsapp.com", "chat.whatsapp.com"},
    "github": {"github.com"},
    "medium": {"medium.com"},
    "snapchat": {"snapchat.com"},
    "bluesky": {"bsky.app"},
}

TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "fbclid", "igshid", "gclid", "ref", "ref_src", "sender_device",
    "is_from_webapp", "locale", "si", "s", "t", "feature", "app"
}

IGNORED_PATH_PATTERNS = {
    "facebook": [
        r"^/?$", r"^/sharer(?:/sharer\.php)?", r"^/share\.php", r"^/dialog/",
        r"^/tr/?$", r"^/help(?:/.*)?$", r"^/policies(?:/.*)?$", r"^/privacy(?:/.*)?$",
        r"^/legal(?:/.*)?$", r"^/login(?:/.*)?$", r"^/recover(?:/.*)?$", r"^/hashtag(?:/.*)?$"
    ],
    "twitter": [
        r"^/?$", r"^/intent(?:/.*)?$", r"^/share(?:/.*)?$", r"^/privacy(?:/.*)?$",
        r"^/tos(?:/.*)?$", r"^/help(?:/.*)?$", r"^/i(?:/.*)?$", r"^/home(?:/.*)?$",
        r"^/explore(?:/.*)?$", r"^/login(?:/.*)?$"
    ],
    "linkedin": [
        r"^/?$", r"^/sharing(?:/.*)?$", r"^/shareArticle(?:/.*)?$", r"^/help(?:/.*)?$",
        r"^/legal(?:/.*)?$", r"^/pulse(?:/.*)?$", r"^/jobs(?:/.*)?$", r"^/login(?:/.*)?$"
    ],
    "instagram": [
        r"^/?$", r"^/legal(?:/.*)?$", r"^/about(?:/.*)?$", r"^/explore(?:/.*)?$",
        r"^/accounts(?:/.*)?$", r"^/developer(?:/.*)?$"
    ],
    "youtube": [
        r"^/?$", r"^/t/terms", r"^/t/privacy", r"^/about", r"^/feed(?:/.*)?$", r"^/premium"
    ],
    "tiktok": [
        r"^/?$", r"^/legal(?:/.*)?$", r"^/about(?:/.*)?$", r"^/explore(?:/.*)?$"
    ],
    "pinterest": [
        r"^/?$", r"^/pin/create(?:/.*)?$", r"^/pin/find(?:/.*)?$", r"^/about(?:/.*)?$", r"^/terms(?:/.*)?$"
    ],
    "yelp": [
        r"^/?$", r"^/search(?:/.*)?$", r"^/writeareview(?:/.*)?$", r"^/login(?:/.*)?$", r"^/signup(?:/.*)?$"
    ],
    "reddit": [
        r"^/?$", r"^/login(?:/.*)?$", r"^/register(?:/.*)?$", r"^/submit(?:/.*)?$"
    ],
    "github": [
        r"^/?$", r"^/features(?:/.*)?$", r"^/pricing(?:/.*)?$", r"^/login(?:/.*)?$", r"^/join(?:/.*)?$"
    ],
}

# Subpage priority keywords for internal crawler navigation
CRAWL_KEYWORDS_PRIORITY = [
    # Top priority: Contact pages
    (r"/(?:contact|contact-us|contactus|contact_us|reach-us|get-in-touch)", 10),
    # Secondary: About & Company profiles
    (r"/(?:about|about-us|aboutus|about_us|who-we-are|our-story)", 8),
    # Tertiary: Team / Leadership
    (r"/(?:team|our-team|leadership|staff|agents|brokers)", 6),
    # Quaternary: Connect / Social / Community
    (r"/(?:connect|social|community|follow-us|links)", 5),
    # Locations
    (r"/(?:locations|offices|branches)", 4),
]


# ---------------------------------------------------------------------------
# Social Link Extractor & Sanitizer
# ---------------------------------------------------------------------------
class SocialExtractor:
    """Extracts, cleans, categorizes, and validates social media links."""

    @staticmethod
    def identify_platform(url: str) -> Optional[str]:
        if not url or not isinstance(url, str):
            return None
        clean = url.strip().lower()
        if not clean.startswith(("http://", "https://", "//")):
            clean = "https://" + clean.lstrip("/")
        try:
            parsed = urlparse(clean)
            host = parsed.netloc.lower()
            if host.startswith("www."):
                host = host[4:]
            host = host.split(":")[0]
        except Exception:
            return None

        for platform, domains in PLATFORM_DOMAINS.items():
            if host in domains or any(host.endswith("." + d) for d in domains):
                return platform
        return None

    @classmethod
    def sanitize_url(cls, url: str) -> Optional[str]:
        if not url or not isinstance(url, str):
            return None
        url = url.strip()
        if url.startswith("//"):
            url = "https:" + url
        elif not url.startswith(("http://", "https://")):
            url = "https://" + url

        try:
            parsed = urlparse(url)
            scheme = parsed.scheme.lower()
            if scheme not in ("http", "https"):
                return None
            netloc = parsed.netloc.lower()
            if netloc.startswith("www."):
                netloc = netloc[4:]

            path = re.sub(r"/+", "/", parsed.path)
            if len(path) > 1 and path.endswith("/"):
                path = path[:-1]

            query_items = parse_qsl(parsed.query, keep_blank_values=False)
            filtered_query = [(k, v) for k, v in query_items if k.lower() not in TRACKING_PARAMS]
            new_query = urlencode(filtered_query)
            return urlunparse((scheme, netloc, path, "", new_query, ""))
        except Exception:
            return None

    @classmethod
    def is_valid_profile_link(cls, platform: str, url: str) -> bool:
        try:
            parsed = urlparse(url)
            path = parsed.path.strip()
            query = parsed.query.strip()
        except Exception:
            return False

        patterns = IGNORED_PATH_PATTERNS.get(platform, [])
        for pattern in patterns:
            if re.match(pattern, path, re.IGNORECASE):
                if platform == "facebook" and path == "/profile.php" and "id=" in query:
                    return True
                return False

        if not path or path == "/":
            return bool(query)

        if platform == "yelp" and not path.startswith("/biz/"):
            return False
        if platform == "tiktok" and not path.startswith("/@"):
            return False
        if platform == "whatsapp" and "api.whatsapp.com" in parsed.netloc and "phone=" not in query:
            return False
        if platform == "snapchat" and not path.startswith("/add/"):
            return False
        if platform == "bluesky" and not path.startswith("/profile/"):
            return False

        return True

    @classmethod
    def extract_from_hrefs(cls, hrefs: List[str]) -> Dict[str, List[str]]:
        results: Dict[str, Set[str]] = {p: set() for p in PLATFORM_DOMAINS}
        for raw in hrefs:
            platform = cls.identify_platform(raw)
            if not platform:
                continue
            clean = cls.sanitize_url(raw)
            if clean and cls.is_valid_profile_link(platform, clean):
                results[platform].add(clean)
        return {k: sorted(list(v)) for k, v in results.items() if v}

    @classmethod
    def extract_from_json_ld(cls, json_ld_strings: List[str]) -> Dict[str, List[str]]:
        results: Dict[str, Set[str]] = {p: set() for p in PLATFORM_DOMAINS}

        def process_value(val: Any) -> None:
            if isinstance(val, str):
                platform = cls.identify_platform(val)
                if platform:
                    clean = cls.sanitize_url(val)
                    if clean and cls.is_valid_profile_link(platform, clean):
                        results[platform].add(clean)
            elif isinstance(val, list):
                for item in val:
                    process_value(item)
            elif isinstance(val, dict):
                for k, v in val.items():
                    if k in ("sameAs", "url", "publisher", "member", "author"):
                        process_value(v)
                    elif isinstance(v, (dict, list)):
                        process_value(v)

        for raw_str in json_ld_strings:
            try:
                data = json.loads(raw_str)
                process_value(data)
            except Exception:
                for match in re.findall(r"https?://[^\s\"\'<>]+", raw_str or ""):
                    platform = cls.identify_platform(match)
                    if platform:
                        clean = cls.sanitize_url(match)
                        if clean and cls.is_valid_profile_link(platform, clean):
                            results[platform].add(clean)

        return {k: sorted(list(v)) for k, v in results.items() if v}

    @classmethod
    def extract_from_meta_tags(cls, meta_tags: List[Dict[str, str]]) -> Dict[str, List[str]]:
        results: Dict[str, Set[str]] = {p: set() for p in PLATFORM_DOMAINS}
        for meta in meta_tags:
            prop = (meta.get("property") or meta.get("name") or "").strip().lower()
            content = (meta.get("content") or "").strip()
            if not content:
                continue

            if content.startswith(("http://", "https://", "//")):
                platform = cls.identify_platform(content)
                if platform:
                    clean = cls.sanitize_url(content)
                    if clean and cls.is_valid_profile_link(platform, clean):
                        results[platform].add(clean)
            elif prop in ("twitter:site", "twitter:creator") and content.startswith("@"):
                handle = content[1:].strip()
                if handle and re.match(r"^[A-Za-z0-9_]{1,15}$", handle):
                    results["twitter"].add(f"https://x.com/{handle}")

        return {k: sorted(list(v)) for k, v in results.items() if v}

    @classmethod
    def merge_social_dicts(cls, *dicts: Dict[str, List[str]]) -> Dict[str, List[str]]:
        merged: Dict[str, Set[str]] = {}
        for d in dicts:
            for platform, links in d.items():
                if platform not in merged:
                    merged[platform] = set()
                merged[platform].update(links)
        return {k: sorted(list(v)) for k, v in merged.items() if v}


# ---------------------------------------------------------------------------
# Email Extractor & Sanitizer
# ---------------------------------------------------------------------------
IGNORED_EMAIL_DOMAINS = {
    "example.com",
    "example.org",
    "example.net",
    "domain.com",
    "yourdomain.com",
    "yoursite.com",
    "mysite.com",
    "company.com",
    "email.com",
    "test.com",
    "sample.com",
    "website.com",
    "sentry.io",
    "wixpress.com",
    "cloudflare.com",
    "googleapis.com",
    "schema.org",
    "github.com",
    "w3.org",
    "wordpress.org",
    "gravatar.com",
}

NON_EMAIL_EXTENSIONS = {
    "png", "jpg", "jpeg", "gif", "webp", "svg", "bmp", "tiff", "ico",
    "css", "js", "woff", "woff2", "ttf", "eot", "mp4", "mp3", "pdf", "zip", "gz"
}

EMAIL_REGEX = re.compile(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+(?:\.[a-zA-Z0-9-]+)+$")


class EmailExtractor:
    """Extracts, cleans, normalizes, and validates email addresses from web pages."""

    @staticmethod
    def clean_email(raw: str) -> Optional[str]:
        """Normalize, sanitize, and validate a candidate email string."""
        if not raw or not isinstance(raw, str):
            return None

        # Decode URL encoding (e.g. %40 -> @)
        try:
            raw = unquote(raw.strip())
        except Exception:
            raw = raw.strip()

        # Remove mailto: prefix if present
        if raw.lower().startswith("mailto:"):
            raw = raw[7:].strip()

        # Strip query parameters or URL fragments (?subject=..., #...)
        raw = raw.split("?")[0].split("#")[0].strip()

        # Strip surrounding punctuation and brackets
        raw = raw.strip(" \t\n\r'\"<>()[]{},;:./\\")

        if not raw or "@" not in raw:
            return None

        email = raw.lower()

        # Validate against standard email regex
        if not EMAIL_REGEX.match(email):
            return None

        local, domain = email.rsplit("@", 1)

        # Validate local part length and format
        if not local or len(local) > 64 or local.startswith(".") or local.endswith(".") or ".." in local:
            return None

        # Validate domain length and format
        if not domain or len(domain) > 255 or domain.startswith(".") or domain.endswith(".") or ".." in domain:
            return None

        parts = domain.split(".")
        if len(parts) < 2:
            return None

        tld = parts[-1]
        # TLD must be alphabetic and at least 2 characters long
        if len(tld) < 2 or not tld.isalpha() or tld in NON_EMAIL_EXTENSIONS:
            return None

        # Reject common template, dummy, or error-tracking domains
        if domain in IGNORED_EMAIL_DOMAINS or any(domain == d or domain.endswith("." + d) for d in IGNORED_EMAIL_DOMAINS):
            return None

        return email

    @classmethod
    def extract_from_mailtos(cls, mailto_list: List[str]) -> Set[str]:
        """Extract valid emails from anchor mailto links."""
        results: Set[str] = set()
        for raw in mailto_list:
            if not raw or not isinstance(raw, str):
                continue
            cleaned_raw = raw.strip()
            if cleaned_raw.lower().startswith("mailto:"):
                cleaned_raw = cleaned_raw[7:].strip()
            cleaned_raw = cleaned_raw.split("?")[0].split("#")[0]
            for part in re.split(r"[,;]", cleaned_raw):
                cleaned = cls.clean_email(part)
                if cleaned:
                    results.add(cleaned)
        return results

    @classmethod
    def extract_from_text(cls, text: str) -> Set[str]:
        """Extract emails from visible text with de-obfuscation."""
        if not text or not isinstance(text, str):
            return set()

        # De-obfuscate bracketed/punctuated at and dot patterns
        deobf = re.sub(r"\s*(?:\[at\]|\(at\)|\{at\}|\[@\]|\(@\))\s*", "@", text, flags=re.IGNORECASE)
        deobf = re.sub(r"\s*(?:\[dot\]|\(dot\)|\{dot\})\s*", ".", deobf, flags=re.IGNORECASE)

        candidates = re.findall(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+(?:\.[a-zA-Z0-9-]+)+", deobf)
        results: Set[str] = set()
        for c in candidates:
            cleaned = cls.clean_email(c)
            if cleaned:
                results.add(cleaned)
        return results

    @classmethod
    def extract_from_json_ld(cls, json_ld_strings: List[str]) -> Set[str]:
        """Extract emails from Schema.org JSON-LD scripts."""
        results: Set[str] = set()

        def process_val(val: Any) -> None:
            if isinstance(val, str):
                if "@" in val or val.lower().startswith("mailto:"):
                    cleaned = cls.clean_email(val)
                    if cleaned:
                        results.add(cleaned)
            elif isinstance(val, list):
                for item in val:
                    process_val(item)
            elif isinstance(val, dict):
                for k, v in val.items():
                    k_lower = k.lower()
                    if k_lower in ("email", "contactpoint", "author", "publisher"):
                        process_val(v)
                    elif isinstance(v, (dict, list)):
                        process_val(v)

        for raw_str in json_ld_strings:
            if not raw_str or not isinstance(raw_str, str):
                continue
            try:
                data = json.loads(raw_str)
                process_val(data)
            except Exception:
                for match in re.findall(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+(?:\.[a-zA-Z0-9-]+)+", raw_str):
                    cleaned = cls.clean_email(match)
                    if cleaned:
                        results.add(cleaned)

        return results

    @classmethod
    def extract_from_meta_tags(cls, meta_tags: List[Dict[str, str]]) -> Set[str]:
        """Extract emails from HTML meta tags."""
        results: Set[str] = set()
        for meta in meta_tags:
            prop = (meta.get("property") or meta.get("name") or "").strip().lower()
            content = (meta.get("content") or "").strip()
            if not content:
                continue
            if "email" in prop or "contact" in prop or "@" in content:
                cleaned = cls.clean_email(content)
                if cleaned:
                    results.add(cleaned)
        return results

    @classmethod
    def extract_from_html(cls, html: str) -> Set[str]:
        """Fallback search across raw HTML for mailto links and email attributes."""
        results: Set[str] = set()
        if not html:
            return results

        # 1. Search mailto: links in HTML attributes
        for m in re.findall(r'href=["\']mailto:([^"\'<>]+)["\']', html, re.IGNORECASE):
            for part in re.split(r"[,;]", m):
                cleaned = cls.clean_email(part)
                if cleaned:
                    results.add(cleaned)

        # 2. Search data-email, data-mailto attributes
        for m in re.findall(r'data-(?:email|mailto)=["\']([^"\'<>]+)["\']', html, re.IGNORECASE):
            cleaned = cls.clean_email(m)
            if cleaned:
                results.add(cleaned)

        # 3. Text regex across HTML
        for m in re.findall(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+(?:\.[a-zA-Z0-9-]+)+", html):
            cleaned = cls.clean_email(m)
            if cleaned:
                results.add(cleaned)

        return results

    @classmethod
    def merge_email_sets(cls, *sets: Set[str]) -> List[str]:
        """Merge multiple email sets and return a sorted list."""
        merged: Set[str] = set()
        for s in sets:
            if s:
                merged.update(s)
        return sorted(list(merged))

    @classmethod
    def extract_all(
        cls,
        mailto_list: Optional[List[str]] = None,
        text: Optional[str] = None,
        json_ld_strings: Optional[List[str]] = None,
        meta_tags: Optional[List[Dict[str, str]]] = None,
        html: Optional[str] = None,
    ) -> List[str]:
        """Convenience method to extract and merge emails from all available page sources."""
        s1 = cls.extract_from_mailtos(mailto_list or [])
        s2 = cls.extract_from_text(text or "")
        s3 = cls.extract_from_json_ld(json_ld_strings or [])
        s4 = cls.extract_from_meta_tags(meta_tags or [])
        s5 = cls.extract_from_html(html or "") if html else set()
        return cls.merge_email_sets(s1, s2, s3, s4, s5)



# ---------------------------------------------------------------------------
# Excel Reader & Validator
# ---------------------------------------------------------------------------
DOMAIN_REGEX = re.compile(
    r"^(?:https?:\/\/)?(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}(?::\d+)?(?:\/.*)?$",
    re.IGNORECASE,
)
STREET_KEYWORDS = {" rd", " st", " ave", " blvd", " dr", " ln", " way", " ct", " ste", " suite", " unit", " fl ", " floor", " pkwy", " hwy"}
INVALID_URL_VALUES = {"n/a", "na", "none", "null", "-", "--", "no website", "unknown", "not available"}


@dataclass
class SiteTarget:
    original_url: str
    normalized_url: str
    domain: str
    row_number: int


def load_sites_from_excel(
    file_path: Path,
    sheet_name: Optional[str] = None,
    column_name: Optional[str] = None,
    limit: Optional[int] = None,
    start_index: int = 0,
) -> List[SiteTarget]:
    """Reads Excel file, auto-detects website column, skips invalid rows, and deduplicates."""
    if not file_path.exists():
        raise FileNotFoundError(f"Excel file not found at: {file_path}")

    logger.info(f"Loading Excel file: {file_path}")
    wb = openpyxl.load_workbook(file_path, read_only=True, data_only=True)

    sheet = wb[sheet_name] if sheet_name and sheet_name in wb.sheetnames else wb.active
    rows_iter = sheet.iter_rows(values_only=True)

    try:
        headers = list(next(rows_iter))
    except StopIteration:
        wb.close()
        return []

    # Find Website column
    col_idx = None
    if column_name:
        for idx, h in enumerate(headers):
            if h and str(h).strip().lower() == column_name.strip().lower():
                col_idx = idx
                break

    if col_idx is None:
        for idx, h in enumerate(headers):
            if h and any(k in str(h).strip().lower() for k in ["website", "site", "url", "web"]):
                col_idx = idx
                break

    if col_idx is None:
        col_idx = 6  # Standard fallback

    logger.info(f"Using website column index {col_idx} ('{headers[col_idx]}')")

    unique_sites: Dict[str, SiteTarget] = {}

    for row_num, row in enumerate(rows_iter, start=2):
        if col_idx >= len(row) or row[col_idx] is None:
            continue
        raw = str(row[col_idx]).strip()
        if not raw or raw.lower() in INVALID_URL_VALUES:
            continue
        # Skip phone numbers
        if re.match(r"^\+?[\d\s\-\(\)\.]{7,}$", raw):
            continue
        # Skip street addresses
        if any(k in raw.lower() for k in STREET_KEYWORDS) and not raw.startswith(("http://", "https://")):
            continue
        if not DOMAIN_REGEX.match(raw):
            continue

        # Normalize
        clean_url = raw
        if clean_url.startswith("//"):
            clean_url = "https:" + clean_url
        elif not clean_url.startswith(("http://", "https://")):
            clean_url = "https://" + clean_url

        parsed = urlparse(clean_url)
        domain = parsed.netloc.lower()
        if domain.startswith("www."):
            domain = domain[4:]

        if clean_url not in unique_sites:
            unique_sites[clean_url] = SiteTarget(
                original_url=raw,
                normalized_url=clean_url,
                domain=domain,
                row_number=row_num,
            )

    wb.close()
    records = list(unique_sites.values())
    logger.info(f"Found {len(records)} unique valid websites from Excel.")

    if start_index > 0:
        records = records[start_index:]
    if limit is not None and limit > 0:
        records = records[:limit]

    return records


# ---------------------------------------------------------------------------
# Crawler & Extraction Engine
# ---------------------------------------------------------------------------
DOM_EXTRACTOR_SCRIPT = """
() => {
    // 1. Anchor links
    const allAnchors = Array.from(document.querySelectorAll('a[href]'));
    const hrefs = [];
    const mailtoHrefs = [];

    for (const a of allAnchors) {
        const href = a.href || a.getAttribute('href') || '';
        if (!href) continue;
        const lower = href.trim().toLowerCase();
        if (lower.startsWith('mailto:')) {
            mailtoHrefs.push(href.trim());
        } else if (!lower.startsWith('javascript:') && !lower.startsWith('tel:')) {
            hrefs.push(href.trim());
        }
    }

    // 2. Schema.org JSON-LD
    const jsonLds = Array.from(document.querySelectorAll('script[type="application/ld+json"]'))
        .map(s => s.textContent || '')
        .filter(t => t.trim().length > 0);

    // 3. Meta tags
    const metaTags = Array.from(document.querySelectorAll('meta'))
        .map(m => ({
            name: m.getAttribute('name') || '',
            property: m.getAttribute('property') || '',
            content: m.getAttribute('content') || ''
        }))
        .filter(m => m.content.trim().length > 0);

    // 4. Visible body text
    const pageText = document.body ? (document.body.innerText || '') : '';

    return {
        hrefs: Array.from(new Set(hrefs)),
        mailtoHrefs: Array.from(new Set(mailtoHrefs)),
        jsonLds,
        metaTags,
        pageText
    };
}
"""


class SiteCrawler:
    """Crawls a website and relevant subpages to discover all social links and emails."""

    def __init__(
        self,
        max_subpages_per_site: int = 4,
        timeout_seconds: int = 20,
    ):
        self.max_subpages = max_subpages_per_site
        self.timeout_ms = timeout_seconds * 1000

    def rank_internal_subpages(self, hrefs: List[str], base_url: str) -> List[str]:
        """Discover same-domain subpages and rank by likelihood of having socials and contact emails."""
        parsed_base = urlparse(base_url)
        base_domain = parsed_base.netloc.lower().replace("www.", "")

        scored_links: List[Tuple[str, int]] = []
        seen = {base_url}

        # Also add root domain to queue if starting URL was a subpage
        if parsed_base.path and parsed_base.path not in ("/", ""):
            root_url = f"{parsed_base.scheme}://{parsed_base.netloc}/"
            scored_links.append((root_url, 9))
            seen.add(root_url)

        for href in hrefs:
            try:
                full_url = urljoin(base_url, href)
                parsed = urlparse(full_url)
                link_domain = parsed.netloc.lower().replace("www.", "")

                # Must be on the same domain
                if link_domain != base_domain and not link_domain.endswith("." + base_domain):
                    continue

                clean = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
                if clean in seen:
                    continue

                # Skip non-HTML assets
                path_lower = parsed.path.lower()
                if path_lower.endswith((".pdf", ".jpg", ".png", ".webp", ".zip", ".mp4", ".css", ".js")):
                    continue

                # Calculate score based on priority keywords
                score = 0
                for pattern, weight in CRAWL_KEYWORDS_PRIORITY:
                    if re.search(pattern, path_lower):
                        score = max(score, weight)

                if score > 0:
                    scored_links.append((full_url, score))
                    seen.add(clean)
            except Exception:
                continue

        # Sort descending by priority score
        scored_links.sort(key=lambda x: -x[1])
        return [url for url, _ in scored_links[:self.max_subpages]]

    async def crawl_single_page(self, page: Page, url: str) -> Tuple[Dict[str, List[str]], Set[str], List[str]]:
        """Navigate to a page with anti-bot handling, scroll down, and extract socials and emails."""
        try:
            res = await page.goto(url, timeout=self.timeout_ms, wait_until="domcontentloaded")
            status = res.status if res else None

            # Handle WAF challenge (HTTP 202) or blank dynamic rendering
            title = await page.title()
            if status in (202, 403) or not title:
                await page.wait_for_timeout(3500)

            # Scroll smoothly to bottom to trigger lazy-loaded footers and social widgets
            await page.evaluate("""
                window.scrollTo({
                    top: document.body.scrollHeight,
                    behavior: 'smooth'
                });
            """)
            await page.wait_for_timeout(1000)

            # In-page JS extraction
            data = await page.evaluate(DOM_EXTRACTOR_SCRIPT)
            raw_hrefs = data.get("hrefs", [])
            mailto_hrefs = data.get("mailtoHrefs", [])
            json_lds = data.get("jsonLds", [])
            meta_tags = data.get("metaTags", [])
            page_text = data.get("pageText", "")

            # Socials extraction
            href_socials = SocialExtractor.extract_from_hrefs(raw_hrefs)
            json_socials = SocialExtractor.extract_from_json_ld(json_lds)
            meta_socials = SocialExtractor.extract_from_meta_tags(meta_tags)
            merged_socials = SocialExtractor.merge_social_dicts(href_socials, json_socials, meta_socials)

            # Email extraction
            mailto_emails = EmailExtractor.extract_from_mailtos(mailto_hrefs)
            json_emails = EmailExtractor.extract_from_json_ld(json_lds)
            meta_emails = EmailExtractor.extract_from_meta_tags(meta_tags)
            text_emails = EmailExtractor.extract_from_text(page_text)

            # Fallback regex search on HTML source
            try:
                page_html = await page.content()
                html_emails = EmailExtractor.extract_from_html(page_html)
            except Exception:
                html_emails = set()

            merged_emails = set(EmailExtractor.merge_email_sets(
                mailto_emails, json_emails, meta_emails, text_emails, html_emails
            ))

            return merged_socials, merged_emails, raw_hrefs

        except Exception as e:
            logger.debug(f"Error crawling {url}: {e}")
            return {}, set(), []

    async def crawl_site(self, target: SiteTarget, browser: Browser) -> Dict[str, Any]:
        """Crawl the entry website and relevant internal subpages to gather all socials and emails."""
        # Create dedicated incognito context per site
        context = await browser.new_context(
            user_agent=DEFAULT_USER_AGENT,
            viewport={"width": 1280, "height": 800},
            ignore_https_errors=True,
            java_script_enabled=True,
            extra_http_headers={
                "Accept-Language": "en-US,en;q=0.9",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                "Sec-Ch-Ua": '"Chromium";v="130", "Google Chrome";v="130", "Not?A_Brand";v="99"',
                "Sec-Ch-Ua-Mobile": "?0",
                "Sec-Ch-Ua-Platform": DEFAULT_SEC_CH_UA_PLATFORM,
                "Upgrade-Insecure-Requests": "1",
            },
        )
        # Inject anti-bot stealth script on every frame creation
        await context.add_init_script(STEALTH_INJECT_JS)

        page = await context.new_page()
        all_socials_dict: Dict[str, List[str]] = {}
        all_emails_set: Set[str] = set()
        crawled_pages = [target.normalized_url]

        try:
            # 1. Crawl initial entry URL
            socials, emails, hrefs = await self.crawl_single_page(page, target.normalized_url)
            all_socials_dict = SocialExtractor.merge_social_dicts(all_socials_dict, socials)
            all_emails_set.update(emails)

            # 2. Discover and rank candidate internal subpages (contact, about, team, root)
            candidate_subpages = self.rank_internal_subpages(hrefs, target.normalized_url)

            # 3. Follow subpages if more socials/emails might exist
            for sub_url in candidate_subpages:
                if sub_url not in crawled_pages:
                    crawled_pages.append(sub_url)
                    sub_socials, sub_emails, _ = await self.crawl_single_page(page, sub_url)
                    all_socials_dict = SocialExtractor.merge_social_dicts(all_socials_dict, sub_socials)
                    all_emails_set.update(sub_emails)

            sorted_emails = sorted(list(all_emails_set))

            # Flat list of all links
            flat_list = sorted(list({link for links in all_socials_dict.values() for link in links}))

            # Clean output dictionary with single-string values when length is 1
            clean_dict = {}
            if sorted_emails:
                clean_dict["emails"] = sorted_emails[0] if len(sorted_emails) == 1 else sorted_emails

            for platform, links in all_socials_dict.items():
                if links:
                    clean_dict[platform] = links[0] if len(links) == 1 else links

            return {
                "website": target.original_url,
                "emails": sorted_emails,
                "socials_dict": clean_dict,
                "socials_list": flat_list,
                "total_socials": len(flat_list),
                "total_emails": len(sorted_emails),
                "pages_crawled": crawled_pages,
            }

        except Exception as e:
            logger.warning(f"Failed to crawl {target.normalized_url}: {e}")
            return {
                "website": target.original_url,
                "emails": [],
                "socials_dict": {},
                "socials_list": [],
                "total_socials": 0,
                "total_emails": 0,
                "pages_crawled": crawled_pages,
            }
        finally:
            await page.close()
            await context.close()


# ---------------------------------------------------------------------------
# Storage & Persistence (Simple JSON)
# ---------------------------------------------------------------------------
class SimpleStorage:
    """Manages atomic saving directly to the simple JSON format with resume support."""

    def __init__(self, output_path: Path, format_style: str = "dict"):
        self.output_path = Path(output_path)
        self.format_style = format_style
        self.data: Dict[str, Any] = {}
        self.platform_counts: Dict[str, int] = {p: 0 for p in SOCIAL_PLATFORMS}
        self.total_emails: int = 0

    def load_existing(self) -> Set[str]:
        """Load already processed websites from output file."""
        if not self.output_path.exists():
            return set()
        try:
            with open(self.output_path, "r", encoding="utf-8") as f:
                content = json.load(f)

            if isinstance(content, dict):
                # Handle old nested structure if present
                if "sites" in content and isinstance(content["sites"], dict):
                    self.data = content["sites"]
                else:
                    self.data = content

                for val in self.data.values():
                    if isinstance(val, dict):
                        emails_val = val.get("emails")
                        if emails_val:
                            cnt = 1 if isinstance(emails_val, str) else len(emails_val)
                            self.total_emails += cnt
                        for p, links in val.items():
                            if p != "emails" and p in self.platform_counts:
                                cnt = 1 if isinstance(links, str) else len(links)
                                self.platform_counts[p] += cnt
                    elif isinstance(val, list):
                        for item in val:
                            if isinstance(item, str) and item.startswith("mailto:"):
                                self.total_emails += 1

                return set(self.data.keys())
        except Exception as e:
            logger.warning(f"Could not load existing file ({e}). Starting fresh.")
        return set()

    def record(self, result: Dict[str, Any]) -> None:
        """Record crawler result."""
        site_key = result["website"]
        if self.format_style == "list":
            all_links = list(result.get("socials_list", []))
            for email in result.get("emails", []):
                mailto_link = f"mailto:{email}"
                if mailto_link not in all_links:
                    all_links.append(mailto_link)
            self.data[site_key] = sorted(all_links)
        else:
            self.data[site_key] = result["socials_dict"]

        # Track stats
        for platform, links in result.get("socials_dict", {}).items():
            if platform == "emails":
                cnt = 1 if isinstance(links, str) else len(links)
                self.total_emails += cnt
            else:
                cnt = 1 if isinstance(links, str) else len(links)
                self.platform_counts[platform] = self.platform_counts.get(platform, 0) + cnt

    def save(self) -> None:
        """Atomically persist data to destination file."""
        parent_dir = self.output_path.parent
        parent_dir.mkdir(parents=True, exist_ok=True)
        try:
            with tempfile.NamedTemporaryFile("w", dir=parent_dir, delete=False, encoding="utf-8") as tmp:
                json.dump(self.data, tmp, indent=2, ensure_ascii=False)
                temp_name = tmp.name
            os.replace(temp_name, self.output_path)
        except Exception as e:
            logger.error(f"Error saving JSON atomically: {e}")
            if "temp_name" in locals() and os.path.exists(temp_name):
                try:
                    os.remove(temp_name)
                except OSError:
                    pass


# ---------------------------------------------------------------------------
# CLI & Execution Runner
# ---------------------------------------------------------------------------
def setup_logging(verbose: bool = False):
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    root_logger.handlers.clear()

    # File log
    fh = logging.FileHandler("scraper.log", encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    root_logger.addHandler(fh)

    # Console log
    ch = RichHandler(console=console, show_time=True, show_path=False)
    ch.setLevel(logging.DEBUG if verbose else logging.INFO)
    root_logger.addHandler(ch)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Production-grade Social Media Crawler using Playwright & Excel input.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--excel",
        type=str,
        default="US Insurance brokers Data (Responses).xlsx",
        help="Path to Excel file.",
    )
    parser.add_argument(
        "--output",
        type=str,
        default="socials_results.json",
        help="Path to save output JSON.",
    )
    parser.add_argument(
        "--format",
        choices=["dict", "list"],
        default="dict",
        help="Output JSON format: 'dict' for {website: {platform: url}} or 'list' for {website: [urls]}.",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=8,
        help="Number of concurrent browser pages to crawl with.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=20,
        help="Page timeout in seconds.",
    )
    parser.add_argument(
        "--max-subpages",
        type=int,
        default=4,
        help="Maximum internal subpages (contact/about/team/root) to crawl per website.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit number of websites to crawl.",
    )
    parser.add_argument(
        "--no-resume",
        action="store_true",
        help="Start from scratch ignoring previous results.",
    )
    parser.add_argument(
        "--no-headless",
        action="store_true",
        help="Run browser visibly for debugging.",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose debug logging.",
    )
    return parser.parse_args()


async def run_crawler():
    args = parse_args()
    setup_logging(args.verbose)

    excel_path = Path(args.excel)
    output_path = Path(args.output)

    console.print(
        Panel.fit(
            "[bold cyan]Playwright Social Media Deep Crawler[/bold cyan]\n"
            f"[yellow]Excel File:[/yellow]    {excel_path}\n"
            f"[yellow]Output JSON:[/yellow]   {output_path} (Format: [green]{args.format}[/green])\n"
            f"[yellow]Concurrency:[/yellow]   {args.concurrency} workers | [yellow]Timeout:[/yellow] {args.timeout}s\n"
            f"[yellow]Subpage Depth:[/yellow] Up to {args.max_subpages} pages per site (Contact / About / Team / Root)\n"
            f"[yellow]Anti-Bot:[/yellow]      Stealth JS Injection + Lazy-scroll trigger + WAF resolution",
            title="[bold green]Crawler Initialized[/bold green]",
            border_style="cyan",
        )
    )

    # 1. Load Excel
    try:
        targets = load_sites_from_excel(excel_path, limit=args.limit)
    except Exception as e:
        console.print(f"[bold red]Failed to load Excel file:[/bold red] {e}")
        sys.exit(1)

    if not targets:
        console.print("[yellow]No websites found to crawl.[/yellow]")
        return

    # 2. Storage & Resume
    storage = SimpleStorage(output_path, format_style=args.format)
    skip_urls = set() if args.no_resume else storage.load_existing()
    pending = [t for t in targets if t.original_url not in skip_urls and t.normalized_url not in skip_urls]

    console.print(
        f"[green]Total target websites:[/green] {len(targets)} | "
        f"[blue]Already completed:[/blue] {len(skip_urls)} | "
        f"[bold yellow]Remaining to crawl:[/bold yellow] {len(pending)}"
    )

    if not pending:
        console.print("[bold green]All websites already crawled! Displaying summary:[/bold green]")
        display_summary(storage, len(targets))
        return

    # 3. Crawler Execution
    crawler = SiteCrawler(
        max_subpages_per_site=args.max_subpages,
        timeout_seconds=args.timeout,
    )

    queue: asyncio.Queue = asyncio.Queue()
    for t in pending:
        await queue.put(t)

    progress = Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        TimeRemainingColumn(),
        console=console,
    )

    task_id = progress.add_task("[cyan]Crawling websites...", total=len(pending))

    # Anti-bot browser launch arguments
    launch_args = [
        "--no-sandbox",
        "--disable-setuid-sandbox",
        "--disable-dev-shm-usage",
        "--disable-blink-features=AutomationControlled",
        "--disable-infobars",
        "--window-position=0,0",
        "--ignore-certificate-errors",
    ]

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=not args.no_headless,
            args=launch_args,
        )

        completed_count = 0

        async def worker():
            nonlocal completed_count
            while not queue.empty():
                try:
                    target: SiteTarget = await queue.get()
                except asyncio.QueueEmpty:
                    break

                try:
                    res = await crawler.crawl_site(target, browser)
                    storage.record(res)
                    completed_count += 1

                    # Periodic atomic save every 10 sites
                    if completed_count % 10 == 0:
                        storage.save()

                    # Progress update
                    found_socials = res.get("total_socials", 0)
                    found_emails = res.get("total_emails", 0)
                    pages_count = len(res.get("pages_crawled", []))
                    short_url = target.original_url[:35] + ("..." if len(target.original_url) > 35 else "")
                    progress.update(
                        task_id,
                        advance=1,
                        description=f"[cyan]{short_url}[/cyan] ({found_socials} socials, {found_emails} emails across {pages_count} pages)",
                    )
                except Exception as e:
                    logger.error(f"Worker error on {target.original_url}: {e}")
                finally:
                    queue.task_done()

        # Launch workers pool
        workers = [asyncio.create_task(worker()) for _ in range(args.concurrency)]
        try:
            with progress:
                await asyncio.gather(*workers)
        except KeyboardInterrupt:
            console.print("\n[yellow]Crawl interrupted by user. Saving current results...[/yellow]")
        finally:
            storage.save()
            await browser.close()

    display_summary(storage, len(targets))


def display_summary(storage: SimpleStorage, total_count: int):
    table = Table(title="[bold green]Crawl Results Summary[/bold green]", border_style="cyan")
    table.add_column("Metric", style="bold cyan")
    table.add_column("Value", style="bold white")

    with_socials = sum(
        1 for val in storage.data.values()
        if (isinstance(val, dict) and any(k != "emails" for k in val))
        or (isinstance(val, list) and any(not str(x).startswith("mailto:") for x in val))
    )
    with_emails = sum(
        1 for val in storage.data.values()
        if (isinstance(val, dict) and "emails" in val)
        or (isinstance(val, list) and any(str(x).startswith("mailto:") for x in val))
    )
    total_socials = sum(storage.platform_counts.values())

    table.add_row("Total Websites Targeted", str(total_count))
    table.add_row("Total Websites Crawled", str(len(storage.data)))
    table.add_row("Websites with Socials", f"[green]{with_socials}[/green]")
    table.add_row("Websites with Emails", f"[green]{with_emails}[/green]")
    table.add_row("Total Social Profiles Found", f"[bold yellow]{total_socials}[/bold yellow]")
    table.add_row("Total Emails Found", f"[bold yellow]{storage.total_emails}[/bold yellow]")
    table.add_row("Output JSON File", str(storage.output_path))
    console.print(table)

    if any(storage.platform_counts.values()):
        p_table = Table(title="[bold magenta]Profiles Discovered per Platform[/bold magenta]", border_style="magenta")
        p_table.add_column("Platform", style="cyan")
        p_table.add_column("Profiles", style="green")
        for plat, cnt in sorted(storage.platform_counts.items(), key=lambda x: -x[1]):
            if cnt > 0:
                p_table.add_row(plat.capitalize(), str(cnt))
        console.print(p_table)


# ---------------------------------------------------------------------------
# Production Scraper API Compatibility Class
# ---------------------------------------------------------------------------
class ProductionScraper:
    """Multi-page crawler orchestrator providing compatibility with the modular CLI and programmatic API."""

    def __init__(self, config: Any):
        self.config = config
        self.crawler = SiteCrawler(
            max_subpages_per_site=getattr(config, "max_subpages", 4),
            timeout_seconds=getattr(config, "timeout_seconds", 20),
        )

    async def run(
        self,
        records: List[Any],
        on_record_done: Optional[Any] = None,
        on_batch_saved: Optional[Any] = None,
        skip_urls: Optional[Set[str]] = None,
    ) -> None:
        skip_urls = skip_urls or set()
        pending = [
            r for r in records
            if getattr(r, "normalized_url", None) not in skip_urls
            and getattr(r, "original_url", None) not in skip_urls
        ]
        if not pending:
            return

        queue: asyncio.Queue = asyncio.Queue()
        for r in pending:
            await queue.put(r)

        launch_args = [
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-dev-shm-usage",
            "--disable-blink-features=AutomationControlled",
            "--disable-infobars",
            "--window-position=0,0",
            "--ignore-certificate-errors",
        ]

        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=getattr(self.config, "headless", True),
                args=launch_args,
            )
            completed_count = 0

            async def worker():
                nonlocal completed_count
                while not queue.empty():
                    try:
                        record = await queue.get()
                    except asyncio.QueueEmpty:
                        break

                    try:
                        target = SiteTarget(
                            original_url=record.original_url,
                            normalized_url=record.normalized_url,
                            domain=record.domain,
                            row_number=record.row_numbers[0] if getattr(record, "row_numbers", None) else 0,
                        )
                        result = await self.crawler.crawl_site(target, browser)
                        completed_count += 1

                        if on_record_done:
                            on_record_done(record, {
                                "status": "success",
                                "emails": result.get("emails", []),
                                "socials": {
                                    p: [l] if isinstance(l, str) else l
                                    for p, l in result.get("socials_dict", {}).items()
                                    if p != "emails"
                                },
                                "all_socials": result.get("socials_list", []),
                                "total_socials": result.get("total_socials", 0),
                                "total_emails": result.get("total_emails", 0),
                                "pages_crawled": result.get("pages_crawled", []),
                            })

                        save_interval = getattr(self.config, "save_interval", 10)
                        if on_batch_saved and completed_count % save_interval == 0:
                            on_batch_saved(completed_count)
                    except Exception as e:
                        logger.error(f"Error crawling {getattr(record, 'original_url', record)}: {e}")
                        if on_record_done:
                            on_record_done(record, {
                                "status": "failed",
                                "error": str(e),
                                "emails": [],
                                "socials": {},
                                "all_socials": [],
                                "total_socials": 0,
                                "total_emails": 0,
                            })
                    finally:
                        queue.task_done()

            concurrency = getattr(self.config, "concurrency", 8)
            workers = [asyncio.create_task(worker()) for _ in range(concurrency)]
            try:
                await asyncio.gather(*workers)
            finally:
                await browser.close()


def main():
    try:
        asyncio.run(run_crawler())
    except KeyboardInterrupt:
        console.print("[yellow]Crawler stopped.[/yellow]")
        sys.exit(0)


if __name__ == "__main__":
    main()
