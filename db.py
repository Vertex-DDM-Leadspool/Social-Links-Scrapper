"""Database Access Layer for LeadsPool CRM.

Connects to PostgreSQL (production / local) or SQLite (development fallback).
Reads the schema, pulls companies needing social/email enrichment, and
updates the records back into the database.
"""

from dataclasses import dataclass
import json
import logging
import os
from pathlib import Path
import re
import secrets
import time
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import parse_qs, urlparse

logger = logging.getLogger(__name__)

# Role-based email prefixes
ROLE_EMAIL_PREFIXES = {
    "info", "contact", "support", "sales", "help", "admin", "office",
    "billing", "service", "inquiries", "general", "marketing", "team"
}


def generate_cuid() -> str:
    """Generate a clean CUID-compatible unique identifier for Prisma models."""
    timestamp = hex(int(time.time() * 1000))[2:]
    random_part = secrets.token_hex(8)
    return f"c{timestamp[:8]}{random_part}"[:25]


@dataclass
class CompanyLead:
    """Represents a Company record fetched from the database."""
    id: str
    name: Optional[str]
    website: str
    normalized_domain: Optional[str]
    linkedin_url: Optional[str]
    facebook_url: Optional[str]
    company_profile: Optional[str]
    web_email: Optional[str]
    comments: Optional[str]
    status: Optional[str] = "NEW"


def parse_env_file(filepath: Path) -> Dict[str, str]:
    """Parse key-value pairs from a .env file, ignoring comments and stripping quotes."""
    env_vars: Dict[str, str] = {}
    if not filepath.exists() or not filepath.is_file():
        return env_vars
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, val = line.split("=", 1)
                    key = key.strip()
                    val = val.strip().strip('"').strip("'")
                    env_vars[key] = val
    except Exception as e:
        logger.debug(f"Could not read {filepath}: {e}")
    return env_vars


def load_env_database_url() -> Optional[str]:
    """Pull database credentials from .env file or system environment."""
    # 0. Try loading via python-dotenv if installed
    try:
        from dotenv import load_dotenv
        for candidate in [
            Path.cwd() / ".env",
            Path(__file__).resolve().parent / ".env",
            Path(__file__).resolve().parent.parent / ".env",
        ]:
            if candidate.exists() and candidate.is_file():
                load_dotenv(candidate)
                break
    except ImportError:
        pass

    # 1. Direct environment variable
    db_url = os.getenv("DATABASE_URL")
    if db_url:
        return db_url

    # 2. Search candidate .env file locations
    candidate_paths = [
        Path.cwd() / ".env",
        Path(__file__).resolve().parent / ".env",
        Path(__file__).resolve().parent.parent / ".env",
        Path("../LeadsPool-Outreach/.env"),
        Path("../.env"),
    ]

    for env_path in candidate_paths:
        if env_path.exists() and env_path.is_file():
            vars_dict = parse_env_file(env_path)

            # Export to os.environ so other modules can use them
            for k, v in vars_dict.items():
                if k not in os.environ:
                    os.environ[k] = v

            # Check for DATABASE_URL
            if "DATABASE_URL" in vars_dict and vars_dict["DATABASE_URL"]:
                logger.info(f"Loaded DATABASE_URL from: {env_path.resolve()}")
                return vars_dict["DATABASE_URL"]

            # Or assemble from individual PostgreSQL credentials
            user = vars_dict.get("POSTGRES_USER") or vars_dict.get("DB_USER")
            dbname = vars_dict.get("POSTGRES_DB") or vars_dict.get("DB_NAME")
            if user and dbname:
                password = vars_dict.get("POSTGRES_PASSWORD") or vars_dict.get("DB_PASSWORD") or ""
                host = vars_dict.get("POSTGRES_HOST") or vars_dict.get("DB_HOST") or "127.0.0.1"
                port = vars_dict.get("POSTGRES_PORT") or vars_dict.get("DB_PORT") or "5432"
                schema = vars_dict.get("POSTGRES_SCHEMA") or "public"
                assembled_url = f"postgresql://{user}:{password}@{host}:{port}/{dbname}?schema={schema}"
                logger.info(f"Assembled DATABASE_URL from components in: {env_path.resolve()}")
                return assembled_url

    # 3. Check if individual variables exist in system environment
    user = os.getenv("POSTGRES_USER") or os.getenv("DB_USER")
    dbname = os.getenv("POSTGRES_DB") or os.getenv("DB_NAME")
    if user and dbname:
        password = os.getenv("POSTGRES_PASSWORD") or os.getenv("DB_PASSWORD") or ""
        host = os.getenv("POSTGRES_HOST") or os.getenv("DB_HOST") or "127.0.0.1"
        port = os.getenv("POSTGRES_PORT") or os.getenv("DB_PORT") or "5432"
        schema = os.getenv("POSTGRES_SCHEMA") or "public"
        return f"postgresql://{user}:{password}@{host}:{port}/{dbname}?schema={schema}"

    # 4. Check for sqlite dev.db fallback in local or parent directories
    for dev_path in [
        Path("dev.db"),
        Path(__file__).resolve().parent / "dev.db",
        Path(__file__).resolve().parent.parent / "prisma" / "dev.db",
        Path("../LeadsPool-Outreach/prisma/dev.db"),
    ]:
        if dev_path.exists() and dev_path.is_file():
            logger.info(f"Found local dev.db fallback at: {dev_path.resolve()}")
            return f"sqlite:///{dev_path.resolve()}"

    return None


class DatabaseClient:
    """Database connection and query manager for PostgreSQL and SQLite."""

    def __init__(self, database_url: Optional[str] = None):
        self.raw_url = database_url or load_env_database_url()
        if not self.raw_url:
            raise ValueError(
                "DATABASE_URL not found. Provide --db-url, set the DATABASE_URL environment "
                "variable, or configure a .env file."
            )
        self.is_sqlite = (
            self.raw_url.startswith("sqlite:")
            or self.raw_url.startswith("file:")
            or self.raw_url.endswith(".db")
        )
        self.conn = None
        self._has_socials_col: Optional[bool] = None
        self._has_facebook_col: Optional[bool] = None

    def connect(self):
        """Establish connection to PostgreSQL or SQLite."""
        if self.conn:
            return self.conn

        if self.is_sqlite:
            import sqlite3
            db_path = self.raw_url.replace("sqlite:///", "").replace("sqlite://", "").replace("file:", "")
            logger.info(f"Connecting to SQLite database: {db_path}")
            self.conn = sqlite3.connect(db_path)
            self.conn.row_factory = sqlite3.Row
            return self.conn

        # PostgreSQL connection
        try:
            import psycopg2
            from psycopg2.extras import RealDictCursor
        except ImportError:
            raise ImportError(
                "psycopg2 is required for PostgreSQL. Install via: pip install psycopg2-binary"
            )

        parsed = urlparse(self.raw_url)
        db_name = parsed.path.lstrip("/").split("?")[0]
        schema = "public"

        # Check for query parameters like ?schema=public
        if parsed.query:
            qs = parse_qs(parsed.query)
            if "schema" in qs:
                schema = qs["schema"][0]

        connect_kwargs: Dict[str, Any] = {
            "dbname": db_name,
            "user": parsed.username or "postgres",
            "password": parsed.password or "",
            "host": parsed.hostname or "localhost",
            "port": parsed.port or 5432,
            "cursor_factory": RealDictCursor,
        }

        # Handle sslmode if passed
        if parsed.query:
            qs = parse_qs(parsed.query)
            if "sslmode" in qs:
                connect_kwargs["sslmode"] = qs["sslmode"][0]

        logger.info(f"Connecting to PostgreSQL: {connect_kwargs['host']}:{connect_kwargs['port']}/{db_name}")
        self.conn = psycopg2.connect(**connect_kwargs)
        self.conn.autocommit = False

        # Set search path to schema
        with self.conn.cursor() as cur:
            cur.execute(f'SET search_path TO "{schema}", public;')
        self.conn.commit()

        return self.conn

    def close(self):
        """Close database connection."""
        if self.conn:
            try:
                self.conn.close()
            except Exception:
                pass
            self.conn = None

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.conn:
            if exc_type:
                self.conn.rollback()
            else:
                self.conn.commit()
            self.close()

    def check_has_socials_column(self) -> bool:
        """Check if 'socials' column exists on the Company table."""
        if self._has_socials_col is not None:
            return self._has_socials_col

        self.connect()
        cur = self.conn.cursor()
        try:
            if self.is_sqlite:
                cur.execute('PRAGMA table_info("Company")')
                cols = [row["name"] if hasattr(row, "__getitem__") and not isinstance(row, tuple) else row[1] for row in cur.fetchall()]
                self._has_socials_col = "socials" in cols
            else:
                cur.execute("""
                    SELECT column_name 
                    FROM information_schema.columns 
                    WHERE LOWER(table_name) = 'company' AND LOWER(column_name) = 'socials'
                """)
                self._has_socials_col = cur.fetchone() is not None
        except Exception as e:
            logger.debug(f"Could not check for 'socials' column: {e}")
            self._has_socials_col = False
        finally:
            cur.close()

        return self._has_socials_col

    def check_has_facebook_column(self) -> bool:
        """Check if 'facebookUrl' column exists on the Company table."""
        if self._has_facebook_col is not None:
            return self._has_facebook_col

        self.connect()
        cur = self.conn.cursor()
        try:
            if self.is_sqlite:
                cur.execute('PRAGMA table_info("Company")')
                cols = [row["name"] if hasattr(row, "__getitem__") and not isinstance(row, tuple) else row[1] for row in cur.fetchall()]
                self._has_facebook_col = "facebookUrl" in cols
            else:
                cur.execute("""
                    SELECT column_name 
                    FROM information_schema.columns 
                    WHERE LOWER(table_name) = 'company' AND LOWER(column_name) = 'facebookurl'
                """)
                self._has_facebook_col = cur.fetchone() is not None
        except Exception as e:
            logger.debug(f"Could not check for 'facebookUrl' column: {e}")
            self._has_facebook_col = False
        finally:
            cur.close()

        return self._has_facebook_col

    def fetch_companies_to_scrape(
        self,
        limit: Optional[int] = None,
        pending_only: bool = True,
        company_id: Optional[str] = None,
    ) -> List[CompanyLead]:
        """Fetch companies that have valid websites from the Company table.
        
        Args:
            limit: Maximum rows to return.
            pending_only: If True, only returns rows missing LinkedIn or webEmail,
                          or lacking scraped social comments.
            company_id: Specific Company ID to target.
        """
        self.connect()
        cur = self.conn.cursor()

        conditions = [
            'website IS NOT NULL',
            "TRIM(website) != ''",
            "LOWER(TRIM(website)) NOT IN ('n/a', 'na', 'none', 'null', '-', '--', 'no website', 'unknown', 'not available', 'nil')",
            'archived = false',
        ]
        params: List[Any] = []

        ph = '?' if self.is_sqlite else '%s'

        if company_id:
            conditions.append(f'id = {ph}')
            params.append(company_id)
        elif pending_only:
            # Pending criteria: not yet scraped by crawler (marked in comments)
            conditions.append(f'(comments IS NULL OR comments NOT LIKE {ph})')
            params.append('%Scraped Socials%')

        where_clause = " AND ".join(conditions)
        limit_clause = f" LIMIT {int(limit)}" if limit and limit > 0 else ""

        has_fb = self.check_has_facebook_column()
        fb_col_sql = '"facebookUrl"' if has_fb else 'NULL as "facebookUrl"'

        query = f"""
            SELECT id, name, website, "normalizedDomain", "linkedInUrl", {fb_col_sql}, "companyProfile", "webEmail", comments, status
            FROM "Company"
            WHERE {where_clause}
            ORDER BY "createdAt" ASC
            {limit_clause}
        """

        try:
            if params:
                cur.execute(query, params)
            else:
                cur.execute(query)
            rows = cur.fetchall()
            results = []
            for r in rows:
                get_val = lambda key: r[key] if hasattr(r, "__getitem__") and key in r else (r.get(key) if hasattr(r, "get") else None)
                results.append(
                    CompanyLead(
                        id=get_val("id"),
                        name=get_val("name"),
                        website=str(get_val("website") or "").strip(),
                        normalized_domain=get_val("normalizedDomain"),
                        linkedin_url=get_val("linkedInUrl"),
                        facebook_url=get_val("facebookUrl"),
                        company_profile=get_val("companyProfile"),
                        web_email=get_val("webEmail"),
                        comments=get_val("comments"),
                        status=get_val("status"),
                    )
                )
            return results
        finally:
            cur.close()

    def update_company_socials(
        self,
        company_id: str,
        socials_dict: Dict[str, Any],
        emails_list: List[str],
        pages_crawled_count: int = 1,
        repaired_website: Optional[str] = None,
        repaired_domain: Optional[str] = None,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """Update Company, Email, and Activity tables with scraped socials.
        
        Returns a dictionary summarizing changes made.
        """
        if dry_run:
            return {
                "company_id": company_id,
                "updated": True,
                "emails_added": len(emails_list),
                "socials_count": len([k for k in socials_dict if k != "emails"]),
                "repaired_website": repaired_website,
                "dry_run": True,
            }

        self.connect()
        cur = self.conn.cursor()
        has_socials_col = self.check_has_socials_column()
        has_fb_col = self.check_has_facebook_column()

        # Extract LinkedIn URL if discovered
        found_linkedin = None
        raw_li = socials_dict.get("linkedin")
        if raw_li:
            found_linkedin = raw_li[0] if isinstance(raw_li, list) else raw_li

        # Extract Facebook URL if discovered
        found_facebook = None
        raw_fb = socials_dict.get("facebook")
        if raw_fb:
            found_facebook = raw_fb[0] if isinstance(raw_fb, list) else raw_fb

        # Extract first web email if discovered
        found_email = None
        if emails_list:
            found_email = emails_list[0]
        elif "emails" in socials_dict:
            e_val = socials_dict["emails"]
            found_email = e_val[0] if isinstance(e_val, list) else e_val

        # Format social channels breakdown for comments
        social_platforms_found = {k: v for k, v in socials_dict.items() if k != "emails" and v}
        date_str = time.strftime("%Y-%m-%d %H:%M")
        
        # Build structured comment section
        summary_lines = [f"[Scraped Socials - {date_str}]"]
        if not social_platforms_found and not emails_list:
            summary_lines.append("- No public social profiles or emails detected on website.")
        else:
            for p, links in sorted(social_platforms_found.items()):
                link_str = ", ".join(links) if isinstance(links, list) else str(links)
                summary_lines.append(f"- {p.capitalize()}: {link_str}")
            if emails_list:
                summary_lines.append(f"- Discovered Emails: {', '.join(emails_list)}")
        new_comment_block = "\n".join(summary_lines)

        try:
            # 1. Fetch current record comments and fields
            ph = "?" if self.is_sqlite else "%s"
            fetch_cols = '"linkedInUrl", "companyProfile", "facebookUrl", "webEmail", comments' if has_fb_col else '"linkedInUrl", "companyProfile", "webEmail", comments'
            cur.execute(
                f'SELECT {fetch_cols} FROM "Company" WHERE id = {ph}',
                [company_id],
            )
            curr = cur.fetchone()
            curr_linkedin = curr["linkedInUrl"] if curr else None
            curr_profile = curr["companyProfile"] if curr else None
            curr_facebook = curr["facebookUrl"] if curr and has_fb_col and "facebookUrl" in curr else None
            curr_email = curr["webEmail"] if curr else None
            curr_comments = curr["comments"] if curr else None

            # Merge comments
            if curr_comments:
                # If old scraped socials block exists, replace or append
                if "[Scraped Socials" in curr_comments:
                    updated_comments = re.sub(
                        r"\[Scraped Socials.*?(\n\n|$)",
                        new_comment_block + "\n\n",
                        curr_comments,
                        flags=re.DOTALL,
                    ).strip()
                else:
                    updated_comments = f"{curr_comments.strip()}\n\n{new_comment_block}"
            else:
                updated_comments = new_comment_block

            # Decide updated LinkedIn, Facebook, and Email
            final_linkedin = curr_linkedin or found_linkedin
            final_profile = curr_profile or found_linkedin
            final_facebook = curr_facebook or found_facebook
            final_email = curr_email or found_email

            # 2. Update Company table
            update_fields = [
                f'"linkedInUrl" = {ph}',
                f'"companyProfile" = {ph}',
                f'"webEmail" = {ph}',
                f'comments = {ph}',
                f'"updatedAt" = CURRENT_TIMESTAMP',
            ]
            update_params = [final_linkedin, final_profile, final_email, updated_comments]

            if has_fb_col and final_facebook:
                update_fields.append(f'"facebookUrl" = {ph}')
                update_params.append(final_facebook)

            if has_socials_col:
                socials_json = json.dumps(social_platforms_found)
                update_fields.append(f'socials = {ph}')
                update_params.append(socials_json)

            if repaired_website:
                update_fields.append(f'website = {ph}')
                update_params.append(repaired_website)

            if repaired_domain:
                update_fields.append(f'"normalizedDomain" = {ph}')
                update_params.append(repaired_domain)

            update_params.append(company_id)
            set_clause = ", ".join(update_fields)

            cur.execute(f'UPDATE "Company" SET {set_clause} WHERE id = {ph}', update_params)

            # 3. Insert newly discovered emails into Email table
            emails_added_count = 0
            for email in emails_list:
                cleaned_email = email.strip().lower()
                cur.execute(
                    f'SELECT id FROM "Email" WHERE "companyId" = {ph} AND LOWER(address) = LOWER({ph})',
                    [company_id, cleaned_email],
                )
                existing = cur.fetchone()
                if not existing:
                    email_id = generate_cuid()
                    local_part = cleaned_email.split("@")[0]
                    is_role = any(local_part == prefix or local_part.startswith(prefix + ".") for prefix in ROLE_EMAIL_PREFIXES)

                    cur.execute(
                        f"""
                        INSERT INTO "Email" (
                            id, "companyId", address, source, "verificationStatus",
                            "isRoleBased", unsubscribed, bounced, archived,
                            "createdAt", "updatedAt"
                        )
                        VALUES ({ph}, {ph}, {ph}, 'WEB', 'unverified', {ph}, false, false, false, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                        """,
                        [email_id, company_id, cleaned_email, is_role],
                    )
                    emails_added_count += 1

            # 4. Insert audit Activity entry
            activity_id = generate_cuid()
            socials_count = len(social_platforms_found)
            activity_body = (
                f"Automated crawler enriched company socials: {socials_count} platform profiles found "
                f"({', '.join(social_platforms_found.keys()) or 'none'}), "
                f"{len(emails_list)} emails discovered across {pages_crawled_count} pages."
            )

            cur.execute(
                f"""
                INSERT INTO "Activity" (id, "companyId", type, body, "actorId", "createdAt")
                VALUES ({ph}, {ph}, 'SOCIALS_SCRAPED', {ph}, NULL, CURRENT_TIMESTAMP)
                """,
                [activity_id, company_id, activity_body],
            )

            self.conn.commit()

            return {
                "company_id": company_id,
                "updated": True,
                "linkedin": final_linkedin,
                "facebook": final_facebook,
                "web_email": final_email,
                "emails_added": emails_added_count,
                "socials_count": socials_count,
            }

        except Exception as e:
            self.conn.rollback()
            logger.error(f"Failed to update database for company {company_id}: {e}")
            raise
        finally:
            cur.close()

    def get_database_stats(self) -> Dict[str, int]:
        """Return counts of companies, websites, and enrichment status."""
        self.connect()
        cur = self.conn.cursor()
        try:
            cur.execute('SELECT COUNT(*) as cnt FROM "Company" WHERE archived = false')
            total = cur.fetchone()[0 if self.is_sqlite else "cnt"]

            cur.execute('SELECT COUNT(*) as cnt FROM "Company" WHERE website IS NOT NULL AND TRIM(website) != \'\' AND archived = false')
            with_website = cur.fetchone()[0 if self.is_sqlite else "cnt"]

            cur.execute('SELECT COUNT(*) as cnt FROM "Company" WHERE "linkedInUrl" IS NOT NULL AND archived = false')
            with_linkedin = cur.fetchone()[0 if self.is_sqlite else "cnt"]

            cur.execute('SELECT COUNT(*) as cnt FROM "Company" WHERE "webEmail" IS NOT NULL AND archived = false')
            with_email = cur.fetchone()[0 if self.is_sqlite else "cnt"]

            cur.execute('SELECT COUNT(*) as cnt FROM "Company" WHERE comments LIKE \'%Scraped Socials%\' AND archived = false')
            scraped = cur.fetchone()[0 if self.is_sqlite else "cnt"]

            cur.execute('SELECT COUNT(*) as cnt FROM "Email" WHERE archived = false')
            total_emails = cur.fetchone()[0 if self.is_sqlite else "cnt"]

            return {
                "total_companies": total,
                "with_website": with_website,
                "with_linkedin": with_linkedin,
                "with_web_email": with_email,
                "scraped_count": scraped,
                "total_emails_in_db": total_emails,
            }
        finally:
            cur.close()
