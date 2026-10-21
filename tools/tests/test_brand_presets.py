import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
from brand_presets import PREFIX, load_manifest, parse_manifest, verify_files


class PublicBrandTest(unittest.TestCase):
    def test_checkout_preserves_exact_bytes_for_windows_hash_checks(self):
        self.assertIn("* -text", (ROOT / ".gitattributes").read_text().splitlines())

    def test_all_presets_are_complete_and_match_shared_hashes(self):
        value = load_manifest(ROOT)
        self.assertEqual(len(value["brands"]), 10)
        self.assertEqual(len(value["files"]), 31)
        self.assertEqual(verify_files(ROOT, value), [])
        lock = json.loads((ROOT / "config/source-lock.json").read_text())
        for path, digest in value["files"].items():
            self.assertEqual(lock["files"][path], digest)

    def test_manifest_cannot_allow_private_or_arbitrary_paths(self):
        original = load_manifest(ROOT)
        for path in ("config/fog_config.yaml", "../logo.png", PREFIX + "brand-assets/halo/extra.png", PREFIX + "brands/custom.yaml"):
            value = json.loads(json.dumps(original))
            value["files"][path] = "0" * 64
            with self.assertRaises(ValueError):
                parse_manifest(json.dumps(value))
        for brands in (["../halo"], ["halo", "halo"]):
            value = dict(original, brands=brands)
            with self.assertRaises(ValueError):
                parse_manifest(json.dumps(value))


if __name__ == "__main__":
    unittest.main()
