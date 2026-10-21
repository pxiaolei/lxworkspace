import hashlib
import unittest

import yaml
from PIL import Image
from _support import ROOT


class ReferenceAssetsTests(unittest.TestCase):
    def test_fourteen_static_references_have_source_and_exact_hashes(self):
        base=ROOT/'assets/references'
        index=base/'index.yaml'
        self.assertTrue(index.is_file())
        rows=yaml.safe_load(index.read_text())['references']
        self.assertEqual(len(rows),14)
        self.assertEqual({r['set'] for r in rows},{'mayday','newyear69'})
        paths=set()
        for row in rows:
            self.assertNotIn(row['path'],paths); paths.add(row['path'])
            path=base/row['path']
            self.assertTrue(path.resolve().is_relative_to(base.resolve()))
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),row['sha256'])
            self.assertTrue(row['source_archive'].endswith('.zip'))
            self.assertEqual(len(row['archive_sha256']),64)
            with Image.open(path) as image:
                self.assertEqual(image.size,(row['width'],row['height']))
                self.assertIn(image.format,{'JPEG','PNG'})
                self.assertEqual(getattr(image,'n_frames',1),1)
