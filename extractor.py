"""Social media link and email extractor, normalizer, and validator."""

import json
import logging
import re
from typing import Any, Dict, List, Optional, Set
from urllib.parse import parse_qsl, unquote, urlencode, urlparse, urlunparse

logger = logging.getLogger(__name__)

# Query parameters to remove during URL sanitization
TRACKING_PARAMS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "fbclid",
    "igshid",
    "gclid",
    "ref",
    "ref_src",
    "sender_device",
    "is_from_webapp",
    "locale",
    "si",
    "s",
    "t",
    "feature",
    "app",
}

# Paths that indicate sharing actions, policies, or generic non-profile pages
IGNORED_PATH_PATTERNS = {
    "facebook": [
        r"^/?$",
        r"^/sharer(?:/sharer\.php)?",
        r"^/share\.php",
        r"^/dialog/",
        r"^/tr/?$",
        r"^/help(?:/.*)?$",
        r"^/policies(?:/.*)?$",
        r"^/privacy(?:/.*)?$",
        r"^/legal(?:/.*)?$",
        r"^/login(?:/.*)?$",
        r"^/recover(?:/.*)?$",
        r"^/hashtag(?:/.*)?$",
        r"^/campaign(?:/.*)?$",
        r"^/plugins(?:/.*)?$",
    ],
    "twitter": [
        r"^/?$",
        r"^/intent(?:/.*)?$",
        r"^/share(?:/.*)?$",
        r"^/privacy(?:/.*)?$",
        r"^/tos(?:/.*)?$",
        r"^/help(?:/.*)?$",
        r"^/i(?:/.*)?$",
        r"^/home(?:/.*)?$",
        r"^/explore(?:/.*)?$",
        r"^/login(?:/.*)?$",
    ],
    "linkedin": [
        r"^/?$",
        r"^/sharing(?:/.*)?$",
        r"^/shareArticle(?:/.*)?$",
        r"^/help(?:/.*)?$",
        r"^/legal(?:/.*)?$",
        r"^/pulse(?:/.*)?$",
        r"^/jobs(?:/.*)?$",
        r"^/login(?:/.*)?$",
    ],
    "instagram": [
        r"^/?$",
        r"^/legal(?:/.*)?$",
        r"^/about(?:/.*)?$",
        r"^/explore(?:/.*)?$",
        r"^/accounts(?:/.*)?$",
        r"^/developer(?:/.*)?$",
    ],
    "youtube": [
        r"^/?$",
        r"^/t/terms",
        r"^/t/privacy",
        r"^/about",
        r"^/feed(?:/.*)?$",
        r"^/premium",
    ],
    "tiktok": [
        r"^/?$",
        r"^/legal(?:/.*)?$",
        r"^/about(?:/.*)?$",
        r"^/explore(?:/.*)?$",
    ],
    "pinterest": [
        r"^/?$",
        r"^/pin/create(?:/.*)?$",
        r"^/pin/find(?:/.*)?$",
        r"^/about(?:/.*)?$",
        r"^/terms(?:/.*)?$",
    ],
    "yelp": [
        r"^/?$",
        r"^/search(?:/.*)?$",
        r"^/writeareview(?:/.*)?$",
        r"^/login(?:/.*)?$",
        r"^/signup(?:/.*)?$",
    ],
    "reddit": [
        r"^/?$",
        r"^/login(?:/.*)?$",
        r"^/register(?:/.*)?$",
        r"^/submit(?:/.*)?$",
    ],
    "github": [
        r"^/?$",
        r"^/features(?:/.*)?$",
        r"^/pricing(?:/.*)?$",
        r"^/login(?:/.*)?$",
        r"^/join(?:/.*)?$",
    ],
}

# Domain mapping for platforms
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


class SocialExtractor:
    """Extracts, cleans, and categorizes social media links."""

    @staticmethod
    def identify_platform(url: str) -> Optional[str]:
        """Identify which social platform a URL belongs to, if any."""
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
            # Remove port if present
            host = host.split(":")[0]
        except Exception:
            return None

        for platform, domains in PLATFORM_DOMAINS.items():
            if host in domains or any(host.endswith("." + d) for d in domains):
                return platform

        return None

    @classmethod
    def sanitize_url(cls, url: str) -> Optional[str]:
        """Normalize URL, remove tracking query params and fragments."""
        if not url or not isinstance(url, str):
            return None

        url = url.strip()
        # Handle protocol-relative URLs
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
            # Normalize www for consistent matching
            if netloc.startswith("www."):
                netloc_clean = netloc[4:]
            else:
                netloc_clean = netloc

            path = parsed.path
            # Remove redundant slashes
            path = re.sub(r"/+", "/", path)
            # Remove trailing slash unless path is just '/'
            if len(path) > 1 and path.endswith("/"):
                path = path[:-1]

            # Filter query parameters
            query_items = parse_qsl(parsed.query, keep_blank_values=False)
            filtered_query = []
            for k, v in query_items:
                k_lower = k.lower()
                if k_lower not in TRACKING_PARAMS:
                    filtered_query.append((k, v))

            new_query = urlencode(filtered_query)
            clean_url = urlunparse((scheme, netloc, path, "", new_query, ""))
            return clean_url
        except Exception:
            return None

    @classmethod
    def is_valid_profile_link(cls, platform: str, url: str) -> bool:
        """Verify that the link points to an actual profile/business page,

        not a share button, login page, or root homepage.
        """
        try:
            parsed = urlparse(url)
            path = parsed.path.strip()
            query = parsed.query.strip()
        except Exception:
            return False

        # Check ignored path patterns for this platform
        patterns = IGNORED_PATH_PATTERNS.get(platform, [])
        for pattern in patterns:
            if re.match(pattern, path, re.IGNORECASE):
                # Special exception for facebook profile.php?id=...
                if platform == "facebook" and path == "/profile.php" and "id=" in query:
                    return True
                return False

        # Must have a non-empty path (more than just "/")
        if not path or path == "/":
            # If there's a query like profile.php?id=123, allow it
            if not query:
                return False

        # Specific platform validations
        if platform == "yelp":
            # Yelp businesses always live under /biz/
            if not path.startswith("/biz/"):
                return False

        if platform == "tiktok":
            # TikTok profiles usually start with /@
            if not path.startswith("/@"):
                return False

        if platform == "whatsapp":
            # If api.whatsapp.com/send, must have phone number, not just text
            if "api.whatsapp.com" in parsed.netloc and "phone=" not in query:
                return False

        if platform == "snapchat":
            if not path.startswith("/add/"):
                return False

        if platform == "bluesky":
            if not path.startswith("/profile/"):
                return False

        return True

    @classmethod
    def extract_from_hrefs(cls, hrefs: List[str]) -> Dict[str, List[str]]:
        """Process a list of raw href links and return categorized social links."""
        results: Dict[str, Set[str]] = {p: set() for p in PLATFORM_DOMAINS}

        for raw in hrefs:
            if not raw or not isinstance(raw, str):
                continue

            platform = cls.identify_platform(raw)
            if not platform:
                continue

            clean = cls.sanitize_url(raw)
            if not clean:
                continue

            if cls.is_valid_profile_link(platform, clean):
                results[platform].add(clean)

        return {k: sorted(list(v)) for k, v in results.items() if v}

    @classmethod
    def extract_from_json_ld(cls, json_ld_strings: List[str]) -> Dict[str, List[str]]:
        """Extract social profile links from Schema.org JSON-LD scripts

        (e.g., sameAs, publisher, contactPoint).
        """
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
                    # High priority keys
                    if k in ("sameAs", "url", "publisher", "member", "author"):
                        process_value(v)
                    elif isinstance(v, (dict, list)):
                        process_value(v)

        for raw_str in json_ld_strings:
            if not raw_str or not isinstance(raw_str, str):
                continue
            try:
                data = json.loads(raw_str)
                process_value(data)
            except Exception:
                # Sometimes JSON-LD is concatenated or has minor syntax errors
                # Fall back to regex link searching within the string
                for match in re.findall(r"https?://[^\s\"\'<>]+", raw_str):
                    platform = cls.identify_platform(match)
                    if platform:
                        clean = cls.sanitize_url(match)
                        if clean and cls.is_valid_profile_link(platform, clean):
                            results[platform].add(clean)

        return {k: sorted(list(v)) for k, v in results.items() if v}

    @classmethod
    def extract_from_meta_tags(cls, meta_tags: List[Dict[str, str]]) -> Dict[str, List[str]]:
        """Extract socials from OpenGraph / Twitter meta tags

        e.g., article:publisher, og:see_also, twitter:site.
        """
        results: Dict[str, Set[str]] = {p: set() for p in PLATFORM_DOMAINS}

        for meta in meta_tags:
            prop = meta.get("property") or meta.get("name") or ""
            content = meta.get("content") or ""
            prop = prop.strip().lower()
            content = content.strip()

            if not content:
                continue

            # Handle direct URLs
            if content.startswith(("http://", "https://", "//")):
                platform = cls.identify_platform(content)
                if platform:
                    clean = cls.sanitize_url(content)
                    if clean and cls.is_valid_profile_link(platform, clean):
                        results[platform].add(clean)

            # Handle twitter:site or twitter:creator containing handle e.g. @CareSpotHealth
            elif prop in ("twitter:site", "twitter:creator") and content.startswith("@"):
                handle = content[1:].strip()
                if handle and re.match(r"^[A-Za-z0-9_]{1,15}$", handle):
                    twitter_url = f"https://x.com/{handle}"
                    results["twitter"].add(twitter_url)

        return {k: sorted(list(v)) for k, v in results.items() if v}

    @classmethod
    def extract_from_raw_html(cls, html: str) -> Dict[str, List[str]]:
        """Fallback regex search across raw HTML to capture embedded/data-attributes."""
        results: Dict[str, Set[str]] = {p: set() for p in PLATFORM_DOMAINS}
        if not html:
            return {}

        matches = re.findall(r"(?:href|data-href|data-url|content)=[\"'](https?://[^\s\"\'<>]+)[\"']", html, re.IGNORECASE)
        for m in matches:
            platform = cls.identify_platform(m)
            if platform:
                clean = cls.sanitize_url(m)
                if clean and cls.is_valid_profile_link(platform, clean):
                    results[platform].add(clean)

        return {k: sorted(list(v)) for k, v in results.items() if v}

    @classmethod
    def merge_social_dicts(cls, *dicts: Dict[str, List[str]]) -> Dict[str, List[str]]:
        """Merge multiple platform dictionaries and remove duplicates."""
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

