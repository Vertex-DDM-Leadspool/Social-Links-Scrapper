"""Unit tests for ExcelReader."""

import unittest
from excel_reader import ExcelReader


class TestExcelReader(unittest.TestCase):

    def test_is_valid_url_string(self):
        valid = [
            "https://www.enterinsurance.com/",
            "http://example.com/subpage",
            "www.example.com",
            "greatflorida.com",
            "championrisk.com/en/about",
        ]
        for v in valid:
            with self.subTest(val=v):
                self.assertTrue(ExcelReader.is_valid_url_string(v))

    def test_invalid_strings_rejected(self):
        invalid = [
            "N/A",
            "n/a",
            "None",
            "-",
            "",
            "4382 L B McLeod Rd, Orlando, FL 32811, United States",
            "15251 NE 18th Ave #9, North Miami Beach, FL 33162, United States",
            "+17146369092",
            "(800) 555-0199",
            "Orlando, FL",
        ]
        for inv in invalid:
            with self.subTest(val=inv):
                self.assertFalse(ExcelReader.is_valid_url_string(inv))

    def test_normalize_website_url(self):
        url, domain = ExcelReader.normalize_website_url("www.example.com")
        self.assertEqual(url, "https://www.example.com")
        self.assertEqual(domain, "example.com")

        url2, domain2 = ExcelReader.normalize_website_url("http://sub.example.com/path?a=1")
        self.assertEqual(url2, "http://sub.example.com/path?a=1")
        self.assertEqual(domain2, "sub.example.com")


if __name__ == "__main__":
    unittest.main()
