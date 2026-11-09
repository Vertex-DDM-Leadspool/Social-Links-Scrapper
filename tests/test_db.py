"""Unit and integration tests for the Database Access Layer (db.py)."""

from pathlib import Path
import sqlite3
import sys
import unittest

sys.path.insert(0, str(Path(__file__).parent.parent.resolve()))

from db import CompanyLead, DatabaseClient, generate_cuid


class TestDatabaseLayer(unittest.TestCase):
    """Test suite verifying database schema compliance, queries, and updates."""

    def setUp(self):
        """Set up an in-memory SQLite database matching the Prisma schema."""
        self.db = DatabaseClient(database_url="sqlite:///:memory:")
        self.conn = self.db.connect()
        cur = self.conn.cursor()

        # Create Company table
        cur.execute("""
            CREATE TABLE "Company" (
                "id" TEXT PRIMARY KEY,
                "name" TEXT,
                "normalizedDomain" TEXT,
                "phone" TEXT,
                "address" TEXT,
                "city" TEXT,
                "state" TEXT,
                "website" TEXT,
                "linkedInUrl" TEXT,
                "campaign" TEXT,
                "admin" TEXT,
                "dc" TEXT,
                "companyProfile" TEXT,
                "ceoProfile" TEXT,
                "verifier" TEXT,
                "webEmail" TEXT,
                "linkedInEmail" TEXT,
                "comments" TEXT,
                "status" TEXT DEFAULT 'NEW',
                "verified" BOOLEAN DEFAULT 0,
                "ownerId" TEXT,
                "source" TEXT DEFAULT 'Manual',
                "archived" BOOLEAN DEFAULT 0,
                "createdAt" TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                "updatedAt" TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Create Email table
        cur.execute("""
            CREATE TABLE "Email" (
                "id" TEXT PRIMARY KEY,
                "companyId" TEXT NOT NULL,
                "contactId" TEXT,
                "address" TEXT NOT NULL,
                "source" TEXT DEFAULT 'WEB',
                "verificationStatus" TEXT DEFAULT 'unverified',
                "isRoleBased" BOOLEAN DEFAULT 0,
                "unsubscribed" BOOLEAN DEFAULT 0,
                "bounced" BOOLEAN DEFAULT 0,
                "archived" BOOLEAN DEFAULT 0,
                "createdAt" TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                "updatedAt" TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY ("companyId") REFERENCES "Company"("id")
            )
        """)

        # Create Activity table
        cur.execute("""
            CREATE TABLE "Activity" (
                "id" TEXT PRIMARY KEY,
                "companyId" TEXT NOT NULL,
                "type" TEXT NOT NULL,
                "body" TEXT NOT NULL,
                "actorId" TEXT,
                "createdAt" TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY ("companyId") REFERENCES "Company"("id")
            )
        """)

        # Insert sample test companies
        cur.execute("""
            INSERT INTO "Company" ("id", "name", "website", "linkedInUrl", "webEmail", "archived")
            VALUES ('comp_1', 'Alpha Corp', 'https://alphacorp.example.com', NULL, NULL, 0)
        """)
        cur.execute("""
            INSERT INTO "Company" ("id", "name", "website", "linkedInUrl", "webEmail", "comments", "archived")
            VALUES ('comp_2', 'Beta LLC', 'https://betallc.example.com', 'https://linkedin.com/company/betallc', 'info@betallc.example.com', '[Scraped Socials - 2026-11-01]', 0)
        """)
        cur.execute("""
            INSERT INTO "Company" ("id", "name", "website", "archived")
            VALUES ('comp_3', 'Archived Firm', 'https://archived.example.com', 1)
        """)
        cur.execute("""
            INSERT INTO "Company" ("id", "name", "website", "archived")
            VALUES ('comp_4', 'No Web Firm', NULL, 0)
        """)
        self.conn.commit()

    def tearDown(self):
        self.db.close()

    def test_cuid_generator(self):
        """Test CUID format and uniqueness."""
        ids = {generate_cuid() for _ in range(100)}
        self.assertEqual(len(ids), 100)
        for cuid in ids:
            self.assertTrue(cuid.startswith("c"))
            self.assertGreaterEqual(len(cuid), 20)

    def test_fetch_companies_pending_only(self):
        """Test that pending_only correctly filters to companies needing socials."""
        leads = self.db.fetch_companies_to_scrape(pending_only=True)
        self.assertEqual(len(leads), 1)
        self.assertEqual(leads[0].id, "comp_1")
        self.assertEqual(leads[0].website, "https://alphacorp.example.com")

    def test_fetch_companies_all(self):
        """Test fetching all active companies with valid websites."""
        leads = self.db.fetch_companies_to_scrape(pending_only=False)
        self.assertEqual(len(leads), 2)
        lead_ids = {l.id for l in leads}
        self.assertIn("comp_1", lead_ids)
        self.assertIn("comp_2", lead_ids)

    def test_fetch_single_company(self):
        """Test targeting a specific company ID."""
        leads = self.db.fetch_companies_to_scrape(company_id="comp_2")
        self.assertEqual(len(leads), 1)
        self.assertEqual(leads[0].id, "comp_2")

    def test_update_company_socials(self):
        """Test updating Company, Email, and Activity tables with scraped results."""
        socials = {
            "linkedin": "https://linkedin.com/company/alphacorp",
            "facebook": "https://facebook.com/AlphaCorpOfficial",
            "twitter": "https://x.com/alphacorp",
            "instagram": "https://instagram.com/alphacorp",
        }
        emails = ["contact@alphacorp.example.com", "sales@alphacorp.example.com"]

        res = self.db.update_company_socials(
            company_id="comp_1",
            socials_dict=socials,
            emails_list=emails,
            pages_crawled_count=3,
            dry_run=False,
        )

        self.assertTrue(res["updated"])
        self.assertEqual(res["emails_added"], 2)
        self.assertEqual(res["socials_count"], 4)

        cur = self.conn.cursor()

        # Check Company record updated
        cur.execute('SELECT "linkedInUrl", "companyProfile", "webEmail", comments FROM "Company" WHERE id = "comp_1"')
        row = cur.fetchone()
        self.assertEqual(row["linkedInUrl"], "https://linkedin.com/company/alphacorp")
        self.assertEqual(row["companyProfile"], "https://linkedin.com/company/alphacorp")
        self.assertEqual(row["webEmail"], "contact@alphacorp.example.com")
        self.assertIn("[Scraped Socials -", row["comments"])
        self.assertIn("Facebook: https://facebook.com/AlphaCorpOfficial", row["comments"])
        self.assertIn("Discovered Emails: contact@alphacorp.example.com", row["comments"])

        # Check Email table received both addresses
        cur.execute('SELECT address, "isRoleBased" FROM "Email" WHERE "companyId" = "comp_1"')
        email_rows = cur.fetchall()
        self.assertEqual(len(email_rows), 2)
        addresses = {r["address"] for r in email_rows}
        self.assertEqual(addresses, {"contact@alphacorp.example.com", "sales@alphacorp.example.com"})

        # Check Activity record logged
        cur.execute('SELECT type, body FROM "Activity" WHERE "companyId" = "comp_1"')
        activity_rows = cur.fetchall()
        self.assertEqual(len(activity_rows), 1)
        self.assertEqual(activity_rows[0]["type"], "SOCIALS_SCRAPED")
        self.assertIn("4 platform profiles found", activity_rows[0]["body"])

    def test_dry_run_does_not_modify_db(self):
        """Test that dry_run flag leaves database completely untouched."""
        socials = {"linkedin": "https://linkedin.com/company/alphacorp"}
        emails = ["info@alphacorp.example.com"]

        res = self.db.update_company_socials(
            company_id="comp_1",
            socials_dict=socials,
            emails_list=emails,
            dry_run=True,
        )

        self.assertTrue(res["dry_run"])
        cur = self.conn.cursor()
        cur.execute('SELECT "linkedInUrl" FROM "Company" WHERE id = "comp_1"')
        self.assertIsNone(cur.fetchone()["linkedInUrl"])
        cur.execute('SELECT COUNT(*) FROM "Email" WHERE "companyId" = "comp_1"')
        self.assertEqual(cur.fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
