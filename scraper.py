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

try:
    import openpyxl
except ImportError:
    openpyxl = None

try:
    from playwright.async_api import Browser, BrowserContext, Page, async_playwright
except ImportError:
    Browser = BrowserContext = Page = Any
    async_playwright = None

try:
    from rich.console import Console
    from rich.logging import RichHandler
    from rich.panel import Panel
    from rich.progress import BarColumn, MofNCompleteColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn, TimeRemainingColumn
    from rich.table import Table
    console = Console()
except ImportError:
    class DummyConsole:
        def print(self, *a, **kw):
            clean_a = [re.sub(r"\[/?\w+.*?\]", "", str(x)) if isinstance(x, str) else str(x) for x in a]
            print(*clean_a)
    console = DummyConsole()
    RichHandler = logging.StreamHandler
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

COMMON_TLDS = (
    "com|org|net|edu|gov|io|co|ai|us|uk|ca|de|fr|it|es|au|biz|info|dev|app|tech|me|tv|cc|xyz|online|store|site"
)


def repair_website_url(raw: Optional[str]) -> Optional[str]:
    """Repairs common OCR and copy-paste typos in website URLs (e.g. missing dots after www or before com)."""
    if not raw or not isinstance(raw, str):
        return None
    val = raw.strip()
    if not val:
        return None

    # Check for invalid placeholders
    if val.lower() in INVALID_URL_VALUES:
        return None

    # Detect protocol
    proto_match = re.match(r"^(https?:\/\/|\/\/)", val, re.IGNORECASE)
    scheme = "https://"
    remainder = val
    if proto_match:
        matched = proto_match.group(1).lower()
        scheme = "https://" if matched == "//" else (matched if matched.endswith("://") else f"{matched}://")
        remainder = val[len(proto_match.group(1)):]

    # Fix missing dot after www (e.g. wwwgoogle.com -> www.google.com)
    remainder = re.sub(r"^www([a-zA-Z0-9-])", r"www.\1", remainder, flags=re.IGNORECASE)

    # Split host and path/query/fragment
    split_match = re.match(r"^([^/?#]+)(.*)$", remainder)
    if not split_match:
        return f"{scheme}{remainder}"

    host = split_match.group(1)
    rest = split_match.group(2) or ""

    # Fix missing dot before common TLD in host (e.g. googlecom -> google.com, www.googlecom -> www.google.com)
    tld_regex = rf"([^.])({COMMON_TLDS})$"
    host = re.sub(tld_regex, r"\1.\2", host, flags=re.IGNORECASE)

    # Clean double dots or trailing dots
    host = re.sub(r"\.{2,}", ".", host).strip(".")

    if not host or "." not in host:
        return f"{scheme}{host}{rest}" if host else None

    return f"{scheme}{host}{rest}"


def normalize_domain(raw: Optional[str]) -> Optional[str]:
    """Extract clean root domain from raw URL or domain string."""
    repaired = repair_website_url(raw)
    if not repaired:
        return None
    cleaned = re.sub(r"^https?:\/\/", "", repaired, flags=re.IGNORECASE).split("/")[0].split("?")[0].split("#")[0].strip().lower()
    if cleaned.startswith("www."):
        cleaned = cleaned[4:]
    return cleaned if "." in cleaned else None


@dataclass
class SiteTarget:
    original_url: str
    normalized_url: str
    domain: str
    row_number: int = 0


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
        max_subpages_per_site: int = 3,
        timeout_seconds: int = 15,
    ):
        self.max_subpages = max_subpages_per_site
        self.timeout_seconds = timeout_seconds
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

    async def crawl_single_page(
        self,
        page: Page,
        url: str,
        timeout_ms: Optional[int] = None,
        log_fn: Optional[Any] = None,
        log_prefix: str = "",
    ) -> Tuple[Dict[str, List[str]], Set[str], List[str]]:
        """Navigate to a page with anti-bot handling, scroll down, and extract socials and emails."""
        t_ms = timeout_ms or self.timeout_ms
        try:
            if log_fn:
                log_fn(f"{log_prefix} [dim]→ Fetching:[/] [blue]{url}[/] [dim]({t_ms//1000}s timeout)[/dim]")

            res = await page.goto(url, timeout=t_ms, wait_until="domcontentloaded")
            status = res.status if res else 200

            # Handle WAF challenge (HTTP 202) or blank dynamic rendering
            try:
                title = await page.title()
            except Exception:
                title = ""

            if status in (202, 403) or not title:
                if log_fn:
                    log_fn(f"{log_prefix} [dim]  Waiting for challenge/render...[/dim]")
                await page.wait_for_timeout(1500)

            # Scroll smoothly to bottom to trigger lazy-loaded footers and social widgets
            await page.evaluate("""
                try {
                    window.scrollTo({
                        top: document.body.scrollHeight,
                        behavior: 'smooth'
                    });
                } catch(e) {}
            """)
            await page.wait_for_timeout(600)

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
            err_line = str(e).split("\n")[0]
            if "Timeout" in err_line:
                err_line = f"Navigation timed out ({t_ms//1000}s)"
            if log_fn:
                log_fn(f"{log_prefix} [yellow]  ⚠ Could not load {url}: {err_line}[/yellow]")
            logger.debug(f"Error crawling {url}: {e}")
            return {}, set(), []

    async def crawl_site(
        self,
        target: SiteTarget,
        browser: Browser,
        log_fn: Optional[Any] = None,
        log_prefix: str = "",
    ) -> Dict[str, Any]:
        """Crawl the entry website and relevant internal subpages to gather all socials and emails."""
        # Ensure scheme is present and repair any malformed URLs missing dots
        repaired = repair_website_url(target.normalized_url or target.original_url)
        url = repaired or (target.normalized_url or "").strip()
        if not url.startswith("http://") and not url.startswith("https://"):
            url = f"https://{url}"
        target.normalized_url = url

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
            if log_fn:
                log_fn(f"{log_prefix} [cyan]🌐 Navigating to entry page:[/] [bold]{target.normalized_url}[/]")

            socials, emails, hrefs = await self.crawl_single_page(
                page, target.normalized_url, timeout_ms=self.timeout_ms, log_fn=log_fn, log_prefix=log_prefix
            )
            all_socials_dict = SocialExtractor.merge_social_dicts(all_socials_dict, socials)
            all_emails_set.update(emails)

            if log_fn:
                entry_socials = [k.capitalize() for k in socials if socials[k]]
                log_fn(
                    f"{log_prefix} [dim]✓ Entry page parsed:[/] "
                    f"[green]{len(entry_socials)} socials ({', '.join(entry_socials) or 'none'})[/], "
                    f"[yellow]{len(emails)} emails[/]"
                )

            # 2. Discover and rank candidate internal subpages (contact, about, team, root)
            candidate_subpages = self.rank_internal_subpages(hrefs, target.normalized_url)
            if candidate_subpages and log_fn:
                paths = [urlparse(u).path or '/' for u in candidate_subpages]
                log_fn(f"{log_prefix} [dim]🔗 Discovered {len(candidate_subpages)} internal subpages to check: {paths}[/dim]")

            # 3. Follow subpages if more socials/emails might exist
            for idx, sub_url in enumerate(candidate_subpages, start=1):
                if sub_url not in crawled_pages:
                    crawled_pages.append(sub_url)
                    sub_path = urlparse(sub_url).path or sub_url
                    if log_fn:
                        log_fn(f"{log_prefix} [dim]  └─ [{idx}/{len(candidate_subpages)}] Checking subpage: {sub_path}...[/dim]")
                    sub_socials, sub_emails, _ = await self.crawl_single_page(
                        page, sub_url, timeout_ms=min(self.timeout_ms, 10000), log_fn=log_fn, log_prefix=log_prefix
                    )
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
            if log_fn:
                log_fn(f"{log_prefix} [yellow]⚠ Failed to crawl {target.normalized_url}: {e}[/yellow]")
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
            try:
                await page.close()
            except Exception:
                pass
            try:
                await context.close()
            except Exception:
                pass


if __name__ == "__main__":
    from main import main
    main()
