"""Unit tests for StorageManager."""

import json
from pathlib import Path
import tempfile
import unittest

from scraper import SimpleStorage
from storage import StorageManager


class TestStorageManager(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.json_path = Path(self.temp_dir.name) / "test_socials.json"
        self.jsonl_path = Path(self.temp_dir.name) / "test_socials.jsonl"

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_record_and_save_simple_dict_structure(self):
        storage = StorageManager(self.json_path, self.jsonl_path, format_style="dict")
        sample_data = {
            "status": "success",
            "socials": {
                "facebook": ["https://facebook.com/example"],
                "linkedin": ["https://linkedin.com/company/example"]
            },
            "all_socials": [
                "https://facebook.com/example",
                "https://linkedin.com/company/example"
            ],
            "total_socials": 2
        }

        storage.record_result("https://www.example.com", sample_data)
        storage.save_full_json()

        self.assertTrue(self.json_path.exists())
        with open(self.json_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        # Verify simple structure: website is top-level key, socials are value
        self.assertIn("https://www.example.com", data)
        self.assertNotIn("metadata", data)
        self.assertNotIn("sites", data)
        self.assertEqual(data["https://www.example.com"]["facebook"], "https://facebook.com/example")
        self.assertEqual(data["https://www.example.com"]["linkedin"], "https://linkedin.com/company/example")

    def test_record_and_save_simple_list_structure(self):
        storage = StorageManager(self.json_path, format_style="list")
        sample_data = {
            "status": "success",
            "socials": {"twitter": ["https://x.com/sample"]},
            "all_socials": ["https://x.com/sample"],
            "total_socials": 1
        }
        storage.record_result("https://www.sample.com", sample_data)
        storage.save_full_json()

        with open(self.json_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        self.assertIn("https://www.sample.com", data)
        self.assertEqual(data["https://www.sample.com"], ["https://x.com/sample"])

    def test_resume_loading_simple(self):
        storage1 = StorageManager(self.json_path, format_style="dict")
        storage1.record_result("https://site1.com", {"status": "success", "socials": {"twitter": ["https://x.com/s1"]}})
        storage1.save_full_json()

        storage2 = StorageManager(self.json_path, format_style="dict")
        completed = storage2.load_existing()

        self.assertIn("https://site1.com", completed)
        self.assertEqual(len(completed), 1)

    def test_record_and_save_dict_with_emails(self):
        storage = StorageManager(self.json_path, self.jsonl_path, format_style="dict")
        sample_data = {
            "status": "success",
            "emails": ["info@championrisk.com", "sales@championrisk.com"],
            "socials": {
                "facebook": ["https://facebook.com/ChampionRisk"],
                "twitter": ["https://twitter.com/champrisk"]
            },
            "all_socials": [
                "https://facebook.com/ChampionRisk",
                "https://twitter.com/champrisk"
            ],
            "total_socials": 2,
            "total_emails": 2,
        }

        storage.record_result("https://www.championrisk.com", sample_data)
        storage.save_full_json()

        self.assertTrue(self.json_path.exists())
        with open(self.json_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        site_entry = data["https://www.championrisk.com"]
        self.assertIn("emails", site_entry)
        self.assertEqual(site_entry["emails"], ["info@championrisk.com", "sales@championrisk.com"])
        self.assertEqual(site_entry["facebook"], "https://facebook.com/ChampionRisk")
        self.assertEqual(site_entry["twitter"], "https://twitter.com/champrisk")

    def test_record_and_save_list_with_emails(self):
        storage = StorageManager(self.json_path, format_style="list")
        sample_data = {
            "status": "success",
            "emails": ["info@sample.com"],
            "socials": {"twitter": ["https://x.com/sample"]},
            "all_socials": ["https://x.com/sample"],
            "total_socials": 1,
            "total_emails": 1,
        }
        storage.record_result("https://www.sample.com", sample_data)
        storage.save_full_json()

        with open(self.json_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        self.assertIn("https://www.sample.com", data)
        self.assertIn("https://x.com/sample", data["https://www.sample.com"])
        self.assertIn("mailto:info@sample.com", data["https://www.sample.com"])

    def test_resume_loading_with_emails(self):
        storage1 = StorageManager(self.json_path, format_style="dict")
        storage1.record_result(
            "https://site1.com",
            {
                "status": "success",
                "emails": ["contact@site1.com"],
                "socials": {"twitter": ["https://x.com/s1"]},
            },
        )
        storage1.save_full_json()

        storage2 = StorageManager(self.json_path, format_style="dict")
        completed = storage2.load_existing()

        self.assertIn("https://site1.com", completed)
        self.assertEqual(storage2.total_emails_counter, 1)

    def test_simple_storage_from_scraper_records_emails(self):
        storage = SimpleStorage(self.json_path, format_style="dict")
        res = {
            "website": "https://www.enterinsurance.com/",
            "emails": ["1412@enterinsurance.com"],
            "socials_dict": {
                "emails": "1412@enterinsurance.com",
                "facebook": "https://facebook.com/enterinsurance",
            },
            "socials_list": ["https://facebook.com/enterinsurance"],
            "total_socials": 1,
            "total_emails": 1,
            "pages_crawled": ["https://www.enterinsurance.com/"],
        }
        storage.record(res)
        storage.save()

        self.assertEqual(storage.total_emails, 1)
        self.assertTrue(self.json_path.exists())

        with open(self.json_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        self.assertIn("https://www.enterinsurance.com/", data)
        self.assertEqual(data["https://www.enterinsurance.com/"]["emails"], "1412@enterinsurance.com")
        self.assertEqual(data["https://www.enterinsurance.com/"]["facebook"], "https://facebook.com/enterinsurance")


if __name__ == "__main__":
    unittest.main()
