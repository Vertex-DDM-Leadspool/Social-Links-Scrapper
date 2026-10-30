"""Unit tests for SocialExtractor."""

import unittest
from extractor import EmailExtractor, SocialExtractor


class TestSocialExtractor(unittest.TestCase):

    def test_identify_platform(self):
        cases = [
            ("https://facebook.com/mybusiness", "facebook"),
            ("https://www.facebook.com/pages/mybusiness", "facebook"),
            ("https://fb.com/mybusiness", "facebook"),
            ("https://twitter.com/mybusiness", "twitter"),
            ("https://x.com/mybusiness", "twitter"),
            ("https://www.linkedin.com/company/mybusiness", "linkedin"),
            ("https://linkedin.com/in/john-doe", "linkedin"),
            ("https://www.instagram.com/mybusiness/", "instagram"),
            ("https://youtube.com/@mybusiness", "youtube"),
            ("https://www.youtube.com/channel/UC12345", "youtube"),
            ("https://tiktok.com/@mybusiness", "tiktok"),
            ("https://www.pinterest.com/mybusiness", "pinterest"),
            ("https://www.yelp.com/biz/my-insurance-co", "yelp"),
            ("https://threads.net/@mybusiness", "threads"),
            ("https://discord.gg/invitecode", "discord"),
            ("https://t.me/mychannel", "telegram"),
            ("https://reddit.com/r/insurance", "reddit"),
            ("https://github.com/myorg", "github"),
            ("https://medium.com/@author", "medium"),
            ("https://snapchat.com/add/myuser", "snapchat"),
            ("https://bsky.app/profile/user.bsky.social", "bluesky"),
            ("https://google.com/search", None),
            ("https://example.com", None),
        ]
        for url, expected in cases:
            with self.subTest(url=url):
                self.assertEqual(SocialExtractor.identify_platform(url), expected)

    def test_tracking_params_removed(self):
        url = "https://facebook.com/mybusiness?utm_source=google&utm_medium=cpc&fbclid=IwAR123"
        cleaned = SocialExtractor.sanitize_url(url)
        self.assertEqual(cleaned, "https://facebook.com/mybusiness")

    def test_share_links_rejected(self):
        bad_links = [
            "https://www.facebook.com/sharer/sharer.php?u=https://example.com",
            "https://www.facebook.com/share.php?u=xyz",
            "https://twitter.com/intent/tweet?text=Check+this+out",
            "https://twitter.com/share?url=https://example.com",
            "https://www.linkedin.com/sharing/share-offsite/?url=https://example.com",
            "https://www.linkedin.com/shareArticle?mini=true",
            "https://www.pinterest.com/pin/create/button/?url=xyz",
        ]
        for link in bad_links:
            with self.subTest(link=link):
                platform = SocialExtractor.identify_platform(link)
                clean = SocialExtractor.sanitize_url(link)
                self.assertFalse(SocialExtractor.is_valid_profile_link(platform, clean))

    def test_root_platform_placeholders_rejected(self):
        placeholders = [
            "https://www.facebook.com",
            "https://www.facebook.com/",
            "https://twitter.com",
            "https://twitter.com/",
            "https://x.com/",
            "https://www.linkedin.com/",
            "https://www.instagram.com/",
            "https://pinterest.com/",
            "https://www.yelp.com/",
        ]
        for link in placeholders:
            with self.subTest(link=link):
                platform = SocialExtractor.identify_platform(link)
                clean = SocialExtractor.sanitize_url(link)
                self.assertFalse(SocialExtractor.is_valid_profile_link(platform, clean))

    def test_json_ld_extraction(self):
        json_sample = [
            """
            {
                "@context": "https://schema.org",
                "@type": "LocalBusiness",
                "name": "Acme Insurance",
                "sameAs": [
                    "https://www.facebook.com/AcmeInsurance",
                    "https://x.com/AcmeInsurance",
                    "https://www.linkedin.com/company/acme-insurance/"
                ]
            }
            """
        ]
        res = SocialExtractor.extract_from_json_ld(json_sample)
        self.assertIn("facebook", res)
        self.assertIn("twitter", res)
        self.assertIn("linkedin", res)
        self.assertEqual(res["facebook"], ["https://www.facebook.com/AcmeInsurance"])

    def test_meta_tag_extraction(self):
        metas = [
            {"name": "twitter:site", "content": "@AcmeInsure"},
            {"property": "article:publisher", "content": "https://www.facebook.com/AcmeInsure/"},
            {"property": "og:see_also", "content": "https://www.instagram.com/acmeinsure/"},
        ]
        res = SocialExtractor.extract_from_meta_tags(metas)
        self.assertIn("twitter", res)
        self.assertIn("facebook", res)
        self.assertIn("instagram", res)
        self.assertEqual(res["twitter"], ["https://x.com/AcmeInsure"])


class TestEmailExtractor(unittest.TestCase):

    def test_clean_email_valid(self):
        cases = [
            ("info@championrisk.com", "info@championrisk.com"),
            ("1412@enterinsurance.com", "1412@enterinsurance.com"),
            ("John.Doe@Agency.com", "john.doe@agency.com"),
            ("  support@agency.co.uk  ", "support@agency.co.uk"),
            ("<contact@agency.com>", "contact@agency.com"),
            ("(claims@agency.com)", "claims@agency.com"),
            ("help@agency.com.", "help@agency.com"),
        ]
        for inp, expected in cases:
            with self.subTest(inp=inp):
                self.assertEqual(EmailExtractor.clean_email(inp), expected)

    def test_clean_email_mailto(self):
        cases = [
            ("mailto:info@agency.com", "info@agency.com"),
            ("mailto:sales@agency.com?subject=Quote%20Request&body=Hello", "sales@agency.com"),
            ("mailto:contact%40agency.com", "contact@agency.com"),
            ("mailto: support@agency.com/ ", "support@agency.com"),
        ]
        for inp, expected in cases:
            with self.subTest(inp=inp):
                self.assertEqual(EmailExtractor.clean_email(inp), expected)

    def test_clean_email_rejected_false_positives(self):
        bad_candidates = [
            "image@2x.png",
            "logo@3x.jpg",
            "icon@2x.webp",
            "bg@2x.svg",
            "bundle@1.0.js",
            "styles@2.css",
            "user@example.com",
            "yourname@email.com",
            "info@yourdomain.com",
            "test@domain.com",
            "error@sentry.io",
            "widget@wixpress.com",
            "anything@cloudflare.com",
            "schema@schema.org",
            "bad@domain..com",
            ".bad@domain.com",
            "bad@domain.c",
            "not-an-email",
            "",
            None,
        ]
        for bad in bad_candidates:
            with self.subTest(bad=bad):
                self.assertIsNone(EmailExtractor.clean_email(bad))

    def test_extract_from_mailtos(self):
        mailtos = [
            "mailto:info@agency.com",
            "mailto:support@agency.com?subject=Help",
            "mailto:sales@agency.com,billing@agency.com",
        ]
        res = EmailExtractor.extract_from_mailtos(mailtos)
        self.assertEqual(res, {"info@agency.com", "support@agency.com", "sales@agency.com", "billing@agency.com"})

    def test_extract_from_text(self):
        text = """
        Contact us at info@myinsurance.com or call our office.
        For claims, reach out to claims@myinsurance.com.
        De-obfuscated: sales [at] myinsurance.com or quotes(at)myinsurance.com.
        Do not email dummy accounts like test@example.com or user@domain.com.
        Asset image@2x.png should be ignored.
        """
        res = EmailExtractor.extract_from_text(text)
        expected = {
            "info@myinsurance.com",
            "claims@myinsurance.com",
            "sales@myinsurance.com",
            "quotes@myinsurance.com",
        }
        self.assertEqual(res, expected)

    def test_extract_from_json_ld(self):
        json_sample = [
            """
            {
                "@context": "https://schema.org",
                "@type": "InsuranceAgency",
                "name": "Acme Insurance",
                "email": "contact@acmeinsurance.com",
                "contactPoint": {
                    "@type": "ContactPoint",
                    "email": "mailto:support@acmeinsurance.com"
                }
            }
            """
        ]
        res = EmailExtractor.extract_from_json_ld(json_sample)
        self.assertEqual(res, {"contact@acmeinsurance.com", "support@acmeinsurance.com"})

    def test_extract_from_meta_tags(self):
        metas = [
            {"name": "email", "content": "info@acmeinsurance.com"},
            {"property": "og:email", "content": "contact@acmeinsurance.com"},
        ]
        res = EmailExtractor.extract_from_meta_tags(metas)
        self.assertEqual(res, {"info@acmeinsurance.com", "contact@acmeinsurance.com"})

    def test_extract_from_html(self):
        html = """
        <html>
            <body>
                <a href="mailto:info@agency.com">Email Us</a>
                <div data-email="sales@agency.com">Contact Sales</div>
                <p>Support: support@agency.com</p>
                <img src="logo@2x.png" />
            </body>
        </html>
        """
        res = EmailExtractor.extract_from_html(html)
        self.assertEqual(res, {"info@agency.com", "sales@agency.com", "support@agency.com"})


if __name__ == "__main__":
    unittest.main()
