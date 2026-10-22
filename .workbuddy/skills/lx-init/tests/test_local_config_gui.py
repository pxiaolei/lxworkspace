"""Local-window save/cancel and credential handling, using fictional inputs only."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location("gui_config_writer_test", SCRIPTS / "write_local_config.py")
writer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(writer)


class LocalConfigGuiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        (self.root / "config").mkdir()
        (self.root / "config/fog_config.yaml.example").write_text(
            'lx_shujuku:\n  api:\n    username: ""\n    password: ""\nlx_haibao:\n  image_api:\n    kie:\n      api_key: ""\n', encoding="utf-8")
        (self.root / "config/lx-wenda.json.example").write_text('{"token":"", "base_url":"https://example.invalid"}\n', encoding="utf-8")
        self.values = {"username": "fictional-user", "password": '  fictional-"pass\\word  ', "image_key": "fictional-image-key", "wenda_token": "fictional-token"}

    def run_gui(self, values=None, error=None):
        out, err = io.StringIO(), io.StringIO()
        with patch.object(writer, "open_config_window", return_value=values, side_effect=error) as window, contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = writer.main(["--gui", "--root", str(self.root)])
        self.assertEqual(err.getvalue(), "")
        for value in self.values.values():
            self.assertNotIn(value, out.getvalue())
        return code, out.getvalue(), window

    def test_cancel_creates_no_config_or_temporary_credentials(self):
        before = {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        code, output, _ = self.run_gui()
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(output)["status"], "cancelled")
        self.assertEqual(before, {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob('*') if p.is_file()})

    def test_window_failure_is_redacted_and_writes_nothing(self):
        code, output, _ = self.run_gui(error=RuntimeError(self.values['password']))
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(output)["phase"], "open-window")
        self.assertFalse((self.root / "config/fog_config.yaml").exists())

    def test_save_preserves_special_characters_and_reads_back(self):
        code, output, _ = self.run_gui(self.values)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output)["readback"], "verified")
        import yaml
        fog = yaml.safe_load((self.root / "config/fog_config.yaml").read_text())
        self.assertEqual(fog['lx_shujuku']['api']['password'], self.values['password'])
        self.assertEqual(fog['lx_haibao']['image_api']['kie']['api_key'], self.values['image_key'])
        self.assertEqual(json.loads((self.root / "config/lx-wenda.json").read_text())['token'], self.values['wenda_token'])
        self.assertEqual(len(list((self.root / "config").iterdir())), 4)

    def test_existing_values_are_not_displayed_or_overwritten(self):
        target = self.root / 'config/fog_config.yaml'
        target.write_text((self.root / 'config/fog_config.yaml.example').read_text().replace('username: ""', 'username: "keep-user"').replace('password: ""', 'password: "keep-password"'))
        code, _, window = self.run_gui(self.values)
        self.assertEqual(code, 0)
        fields = window.call_args.args[1]
        self.assertEqual(fields[0]['status'], 'kept')
        self.assertNotIn('keep-password', repr(fields))
        saved = target.read_text()
        self.assertIn('password: "keep-password"', saved)
        self.assertNotIn(self.values['password'], saved)

    def test_fog_without_wenda_shows_only_three_fields(self):
        # Rename the fictional template out of the enabled path; no real files touched.
        (self.root / 'config/lx-wenda.json.example').rename(self.root / 'config/unused-example.json')
        values = dict(self.values, wenda_token='')
        code, _, window = self.run_gui(values)
        self.assertEqual(code, 0)
        self.assertEqual([f['name'] for f in window.call_args.args[1]], ['username', 'password', 'image_key'])
        self.assertFalse((self.root / 'config/lx-wenda.json').exists())

    def test_incomplete_input_does_not_write_either_file(self):
        code, _, _ = self.run_gui(dict(self.values, wenda_token=''))
        self.assertEqual(code, 2)
        self.assertFalse((self.root / 'config/fog_config.yaml').exists())
        self.assertFalse((self.root / 'config/lx-wenda.json').exists())

    def test_newline_input_is_rejected_without_writes(self):
        code, _, _ = self.run_gui(dict(self.values, password='fictional\npassword'))
        self.assertEqual(code, 2)
        self.assertFalse((self.root / 'config/fog_config.yaml').exists())

    def test_modified_readback_is_not_reported_as_saved(self):
        real_apply = writer.apply_local_config
        def corrupt(root, values):
            result = real_apply(root, values)
            (root / 'config/lx-wenda.json').write_text('{"token":"unexpected"}')
            return result
        with patch.object(writer, 'apply_local_config', side_effect=corrupt):
            code, output, _ = self.run_gui(self.values)
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(output)['phase'], 'readback')

    def test_invalid_target_stops_before_opening_window(self):
        (self.root / 'config/fog_config.yaml').symlink_to(self.root / 'outside.yaml')
        code, _, window = self.run_gui(self.values)
        self.assertEqual(code, 2)
        window.assert_not_called()

    def test_gui_and_file_import_are_mutually_exclusive(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(writer.main(['--gui', '--from-json', 'unused.json']), 2)

    def test_windows_crlf_and_existing_values_are_preserved(self):
        target = self.root / 'config/fog_config.yaml'
        original = (self.root / 'config/fog_config.yaml.example').read_bytes().replace(b'username: ""', b'username: "keep-user"').replace(b'\n', b'\r\n')
        target.write_bytes(original)
        wenda = self.root / 'config/lx-wenda.json'
        wenda.write_bytes(b'{"token":"keep-token"}\r\n')
        code, _, _ = self.run_gui(self.values)
        self.assertEqual(code, 0)
        result = target.read_bytes()
        self.assertIn(b'username: "keep-user"\r\n', result)
        self.assertNotIn(b'\n', result.replace(b'\r\n', b''))
        self.assertEqual(wenda.read_bytes(), b'{"token":"keep-token"}\r\n')

    def test_dispatcher_passes_gui_flag_to_host_python(self):
        import subprocess
        path = SCRIPTS.parents[3] / 'tools/run_skill.py'
        spec = importlib.util.spec_from_file_location('gui_dispatcher_test', path)
        dispatcher = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(dispatcher)
        with patch.object(dispatcher.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0)) as run:
            self.assertEqual(dispatcher.main(['init-config', '--gui']), 0)
        command = run.call_args.args[0]
        self.assertEqual(command[0], sys.executable)
        self.assertTrue(command[2].endswith('write_local_config.py'))
        self.assertEqual(command[3:], ['--gui'])


if __name__ == '__main__':
    unittest.main()
