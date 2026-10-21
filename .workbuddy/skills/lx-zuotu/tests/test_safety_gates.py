import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from _support import FIXTURE, ROOT, require


class SafetyGateTests(unittest.TestCase):
    def setUp(self): self.cli = require(self, 'run_zuotu_batch')

    def test_unconfirmed_generation_rejects_before_path_or_provider_setup(self):
        with patch.object(self.cli, 'generate_batch', side_effect=AssertionError('generation forbidden')), patch.object(Path, 'mkdir', side_effect=AssertionError('write forbidden')), patch('requests.sessions.Session.request', side_effect=AssertionError('network forbidden')), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(self.cli.main(['--file', str(FIXTURE)]), 2)
            self.assertEqual(self.cli.main(['--sample-only','--file', str(FIXTURE)]), 2)
            self.assertEqual(self.cli.main(['--review-file','missing','--file',str(FIXTURE)]), 2)

    def test_check_and_dry_run_are_read_only_even_when_confirmed(self):
        original_open = Path.open
        def read_only(path, mode='r', *args, **kwargs):
            if any(char in mode for char in 'wax+'): raise AssertionError('write forbidden')
            return original_open(path, mode, *args, **kwargs)
        for args in (['--check'], ['--dry-run','--confirmed','--types','all','--file',str(FIXTURE)]):
            with self.subTest(args=args), patch.object(require(self,'kie_adapter'),'config_status',return_value={'provider':'kie','api_key_configured':True,'models_configured':True,'model_compatible':True}), patch.object(Path,'open',read_only), patch.object(Path,'mkdir',side_effect=AssertionError('mkdir forbidden')), patch('requests.sessions.Session.request',side_effect=AssertionError('network forbidden')), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(self.cli.main(args), 0)

    def test_install_without_confirmation_does_not_create_venv_or_run_pip(self):
        runtime=require(self,'check_runtime')
        with patch('subprocess.run',side_effect=AssertionError('subprocess forbidden')), patch.object(Path,'mkdir',side_effect=AssertionError('write forbidden')), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(runtime.main(['--install']), 2)

    def test_wrappers_disable_bytecode_before_import(self):
        for name in ('zuotu.sh','zuotu.cmd','check_runtime.sh','check_runtime.cmd'):
            p=ROOT/name
            self.assertTrue(p.is_file(), name+' missing')
            self.assertIn('PYTHONDONTWRITEBYTECODE',p.read_text())

    def test_input_conflict_and_changed_confirmation_write_nothing(self):
        plan=require(self,'input_plan').build_plan(FIXTURE)
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError): self.cli.generate_batch(plan,Path(tmp)/'new',confirmed=False)
            self.assertFalse((Path(tmp)/'new').exists())
