import tempfile
import unittest
from pathlib import Path

from PIL import Image
from _support import FIXTURE, require


def reviewed(row, **changes):
    return {'image_sha256': row['image_sha256'], 'viewed': True, 'reviewer': 'offline-test-fixture',
            'observed_text': row['target']['text'], 'unexpected_text': [],
            'checks': {key: True for key in ('text_exact', 'amounts_dates_exact', 'no_extra_copy', 'no_crop',
                                            'subject_consistent', 'layout_correct', 'transparency_correct')},
            'notes': 'Synthetic test data, not a real visual acceptance.', **changes}


class ValidationTests(unittest.TestCase):
    def setUp(self):
        self.api = require(self, 'validate_images')
        self.target = require(self, 'input_plan').build_plan(FIXTURE)['targets'][0]

    def test_strict_size_boundary(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)/'result.jpg'
            Image.new('RGB', (690, 160), 'blue').save(p)
            length = p.stat().st_size
            self.assertTrue(self.api.validate_file(p, {**self.target, 'max_bytes': length+1})['passed'])
            self.assertFalse(self.api.validate_file(p, {**self.target, 'max_bytes': length})['passed'])
            self.assertFalse(self.api.validate_file(p, {**self.target, 'max_bytes': length-1})['passed'])

    def test_decoder_format_dimensions_and_alpha(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)/'fake.jpg'
            Image.new('RGB', (100, 100), 'white').save(p, format='PNG')
            result = self.api.validate_file(p, self.target)
            self.assertFalse(result['passed'])
            self.assertIn('format', result['errors'])
            self.assertIn('dimensions', result['errors'])
            target = next(r for r in require(self, 'input_plan').build_plan(FIXTURE)['targets'] if r['type']=='popup')
            Image.new('RGBA', (534, 712), 'white').save(p, format='PNG')
            self.assertIn('transparency', self.api.validate_file(p, target)['errors'])

    def test_missing_review_never_passes(self):
        row = {'target': self.target, 'image_sha256': 'a'*64}
        self.assertFalse(self.api.validate_review(row, None)['passed'])
        self.assertEqual(self.api.validate_review(row, None)['status'], 'pending_review')

    def test_review_is_bound_to_hash_all_copy_and_all_checks(self):
        row = {'target': self.target, 'image_sha256': 'a'*64}
        self.assertTrue(self.api.validate_review(row, reviewed(row))['passed'])
        bad = [reviewed(row, viewed=False), reviewed(row, image_sha256='b'*64), reviewed(row, observed_text={}),
               reviewed(row, unexpected_text=['限量100名']), reviewed(row, checks={})]
        for review in bad:
            with self.subTest(review=review):
                self.assertFalse(self.api.validate_review(row, review)['passed'])

    def test_amount_date_and_crop_fail_independently(self):
        target = next(r for r in require(self, 'input_plan').build_plan(FIXTURE)['targets'] if r['type']=='splash')
        row = {'target': target, 'image_sha256': 'a'*64}
        for field, wrong in [('amounts', ['早鸟价 ¥500', '正式价 ¥100']), ('dates', ['4月30日-5月6日'])]:
            review = reviewed(row, observed_text={**target['text'], field: wrong})
            self.assertFalse(self.api.validate_review(row, review)['passed'])
        review = reviewed(row)
        review['checks']['no_crop'] = False
        self.assertFalse(self.api.validate_review(row, review)['passed'])
