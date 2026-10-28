"""Project dispatcher tests run copied code and fictional config only."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2 if Path(__file__).parent.name == "tests" else 1]


class WendaDispatcherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.skill = self.root / '.workbuddy/skills/lx-wenda'
        self.skill.mkdir(parents=True)
        (self.root / 'tools').mkdir()
        (self.root / 'config').mkdir()
        shutil.copy2(ROOT / 'tools/run_skill.py', self.root / 'tools/run_skill.py')
        for name in ['client.js', 'ask.js', 'server.js', 'selftest.js']:
            shutil.copy2(ROOT / '.workbuddy/skills/lx-wenda' / name, self.skill / name)
        (self.root / 'config/distribution-manifest.json').write_text(json.dumps({
            'version': 1, 'targets': {'FOG': {'repository': 'pxiaolei/FOG'},
                                    'lxworkspace': {'repository': 'pxiaolei/lxworkspace'}}}))
        (self.skill / 'config.json').write_text(json.dumps({'base': 'https://private.invalid', 'token': 'fictional'}))
        (self.root / 'config/lx-wenda.json').write_text(json.dumps({'base': 'https://decoy.invalid', 'token': 'fictional'}))

    def run_dispatch(self, args):
        env = dict(os.environ, MAXKB_BASE='https://poison.invalid', MAXKB_TOKEN='poison')
        return subprocess.run([sys.executable, '-B', str(self.root / 'tools/run_skill.py'), *args],
                              env=env, capture_output=True, text=True, timeout=10)

    def test_dispatcher_selects_private_file_instead_of_root_decoy(self):
        result = self.run_dispatch(['wenda', 'fixture question'])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['service'], 'https://private.invalid')
        self.assertNotIn('fictional', result.stdout)

    def test_project_dispatcher_cannot_activate_explicit_maintenance_config(self):
        result = self.run_dispatch(['wenda', 'fixture', '--config', str(self.root / 'config/lx-wenda.json')])
        self.assertNotEqual(result.returncode, 0)


if __name__ == '__main__':
    unittest.main()
