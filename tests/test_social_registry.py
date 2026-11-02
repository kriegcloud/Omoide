import unittest

from app.services.social_registry import normalize_social_url, PLATFORMS, brand_metadata


class SocialRegistryTests(unittest.TestCase):
    def test_every_platform(self):
        for platform in PLATFORMS:
            with self.subTest(platform=platform['id']):
                result = normalize_social_url(platform['example'])
                self.assertEqual(result['platform'], platform['id'])
                self.assertTrue(result['handle'])

    def test_tracking_alias_and_handle_defaults(self):
        self.assertEqual(normalize_social_url(' HTTP://WWW.Twitter.com/Someone/?utm_source=x&si=1#bio')['normalized_url'], 'https://x.com/Someone')
        self.assertEqual(normalize_social_url('@person')['url'], 'https://instagram.com/person')
        self.assertEqual(normalize_social_url('@alice@social.example')['url'], 'https://social.example/@alice')
        self.assertEqual(normalize_social_url('https://social.example/@alice')['platform'], 'mastodon')
        self.assertEqual(normalize_social_url('example.com/a/?q=yes&utm_campaign=x')['url'], 'https://example.com/a?q=yes')

    def test_invalid_urls(self):
        for value in ('', 'javascript:alert(1)', 'file:///tmp/a', 'https://user:pass@example.com', 'https://example.com:bad/a', 'just a handle'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_social_url(value)

    def test_brand_domains_respect_public_and_hosted_boundaries(self):
        for url, domain in [('blog.example.co.uk/me', 'example.co.uk'),
                            ('site.example.com/me', 'example.com'),
                            ('person.github.io/me', 'person.github.io'),
                            ('nested.person.github.io/me', 'person.github.io')]:
            self.assertEqual(normalize_social_url(url)['brand_domain'], domain)

    def test_mastodon_brand_is_derived_from_detected_profile(self):
        self.assertEqual(brand_metadata('social.example', 'mastodon')['simple_icons_slug'], 'mastodon')
        self.assertIsNone(brand_metadata('social.example')['simple_icons_slug'])

    def test_linkedin_uses_site_fallback_when_catalogue_has_no_icon(self):
        self.assertIsNone(brand_metadata('linkedin.com')['simple_icons_slug'])

class CompletePublicSuffixTests(unittest.TestCase):
    def test_wildcards_and_exceptions(self):
        from app.services.social_registry import registrable_domain
        self.assertEqual(registrable_domain('blog.a.b.ck'), 'a.b.ck')
        self.assertEqual(registrable_domain('blog.www.ck'), 'www.ck')
        self.assertEqual(registrable_domain('blog.city.kawasaki.jp'), 'city.kawasaki.jp')
        self.assertEqual(registrable_domain('blog.foo.kawasaki.jp'), 'blog.foo.kawasaki.jp')

    def test_explicit_http_personal_site_and_nondefault_ports(self):
        from app.services.social_registry import normalize_social_url
        self.assertEqual(normalize_social_url('http://example.com:80/me/')['url'], 'http://example.com/me')
        self.assertEqual(normalize_social_url('http://example.com:443/me/')['url'], 'http://example.com:443/me')
        self.assertEqual(normalize_social_url('example.com/me/')['url'], 'https://example.com/me')
