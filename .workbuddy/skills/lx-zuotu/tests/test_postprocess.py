import random
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw
from _support import FIXTURE, require


class PostprocessTests(unittest.TestCase):
    def setUp(self):
        self.api = require(self, 'postprocess')
        plan = require(self, 'input_plan').build_plan(FIXTURE)
        self.jpg = plan['targets'][0]
        self.png = next(r for r in plan['targets'] if r['type'] == 'popup')

    def test_oversized_jpg_compresses_without_lowering_dimensions(self):
        with tempfile.TemporaryDirectory() as tmp:
            src, out = Path(tmp)/'source.jpg', Path(tmp)/'result.jpg'
            data = random.Random(81).randbytes(690*160*3)
            Image.frombytes('RGB', (690, 160), data).save(src, quality=100, subsampling=0)
            self.assertGreater(src.stat().st_size, self.jpg['max_bytes'])
            result = self.api.process_image(src, out, self.jpg)
            self.assertLess(out.stat().st_size, self.jpg['max_bytes'])
            self.assertGreaterEqual(result['quality'], 60)
            with Image.open(out) as im: self.assertEqual(im.size, (690, 160))

    def test_impossible_limit_fails_and_does_not_write_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            src, out = Path(tmp)/'source.png', Path(tmp)/'result.jpg'
            Image.new('RGB', (1000, 1000), 'blue').save(src)
            with self.assertRaises(ValueError):
                self.api.process_image(src, out, {**self.jpg, 'max_bytes': 100})
            self.assertFalse(out.exists())

    def test_large_png_preserves_real_transparency_and_removes_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            src, out = Path(tmp)/'source.png', Path(tmp)/'result.png'
            im = Image.new('RGBA', (1068, 1424), (0, 0, 0, 0))
            ImageDraw.Draw(im).rounded_rectangle((40, 40, 1028, 1384), radius=80, fill=(60, 120, 230, 255))
            im.save(src, compress_level=0, exif=Image.Exif())
            self.assertGreater(src.stat().st_size, self.png['max_bytes'])
            self.api.process_image(src, out, self.png)
            self.assertLess(out.stat().st_size, self.png['max_bytes'])
            with Image.open(out) as image:
                self.assertEqual(image.size, (534, 712))
                self.assertEqual(image.convert('RGBA').getchannel('A').getextrema(), (0, 255))
                self.assertFalse(image.getexif())

    def test_opaque_popup_is_rejected_without_fake_cutout(self):
        with tempfile.TemporaryDirectory() as tmp:
            src, out = Path(tmp)/'source.png', Path(tmp)/'result.png'
            Image.new('RGB', (534, 712), 'white').save(src)
            with self.assertRaises(ValueError): self.api.process_image(src, out, self.png)
            self.assertFalse(out.exists())

    def test_no_overwrite_and_crop_is_recorded(self):
        with tempfile.TemporaryDirectory() as tmp:
            src, out = Path(tmp)/'source.png', Path(tmp)/'result.jpg'
            Image.new('RGB', (1000, 1000), 'blue').save(src)
            result = self.api.process_image(src, out, self.jpg)
            self.assertNotEqual(result['crop_box'], [0, 0, 1000, 1000])
            before = out.read_bytes()
            with self.assertRaises(FileExistsError): self.api.process_image(src, out, self.jpg)
            self.assertEqual(before, out.read_bytes())
