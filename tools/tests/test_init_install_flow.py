"""Partner install wizard: runtime report, local config writes, and the venv hash gate."""
import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[2]
FOG_EXAMPLE = ROOT / "config/fog_config.yaml.example"
WENDA_EXAMPLE = ROOT / "config/lx-wenda.json.example"

SECRET_PASSWORD = "p@ss:word"
SECRET_IMAGE_KEY = "kie/key+1"
SECRET_TOKEN = "tok=en"
SECRET_KEPT_PASSWORD = "kept-password-ALPHA"
SECRET_KEPT_TOKEN = "kept-token-EPSILON"
SECRET_OTHER_PASSWORD = "OTHER-PASSWORD-GAMMA"
LEAKED_RUNNER_OUT = "LEAKED-SECRET-OUT"
LEAKED_RUNNER_ERR = "LEAKED-SECRET-ERR"


def load(name, relative):
    file = ROOT / relative
    spec = importlib.util.spec_from_file_location(name, file)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


init = load("partner_init_flow_init", ".workbuddy/skills/lx-init/scripts/init_workspace.py")
runtime = load("partner_init_flow_runtime", ".workbuddy/skills/lx-init/scripts/bootstrap_runtime.py")
config = load("partner_init_flow_config", ".workbuddy/skills/lx-init/scripts/write_local_config.py")
dispatcher = load("partner_init_flow_dispatcher", "tools/run_skill.py")


class Completed:
    def __init__(self, code, out="", err=""):
        self.returncode = code
        self.stdout = out
        self.stderr = err


class RuntimeReportTest(unittest.TestCase):
    def test_check_only_reports_present_and_planned_without_installing(self):
        calls = []

        def probes(name):
            return {"python": "3.12.3", "node": None, "git": "2.40.0"}[name]

        def runner(command):
            calls.append(command)
            raise AssertionError("check-only must not install")

        report = runtime.bootstrap(platform="win32", probes=probes, which=lambda name: "winget", runner=runner, check_only=True)
        self.assertEqual(report["present"], ["python", "git"])
        self.assertEqual(report["newly_installed"], [])
        self.assertEqual(report["failed"], [])
        self.assertEqual(report["planned"], ["node"])
        self.assertFalse(report["ok"])
        self.assertEqual(calls, [])

    def test_windows_install_uses_winget_and_marks_only_new_tools(self):
        state = {"python": "3.12.3", "node": None, "git": None}
        calls = []

        def probes(name):
            return state[name]

        def which(name):
            return r"C:\Windows\winget.exe" if name == "winget" else None

        def runner(command):
            calls.append(list(command))
            tool = "node" if "OpenJS.NodeJS.LTS" in command else "git"
            state[tool] = "installed"
            return Completed(0)

        report = runtime.bootstrap(platform="win32", probes=probes, which=which, runner=runner, check_only=False)
        self.assertEqual(report["present"], ["python"])
        self.assertEqual(report["newly_installed"], ["node", "git"])
        self.assertEqual(report["failed"], [])
        self.assertTrue(report["ok"])
        self.assertEqual(calls[0][0], r"C:\Windows\winget.exe")
        self.assertIn("OpenJS.NodeJS.LTS", calls[0])
        self.assertIn("Git.Git", calls[1])
        self.assertNotIn("Python.Python.3.12", calls[0] + calls[1])

    def test_darwin_without_brew_fails_with_official_url_and_no_command(self):
        calls = []

        def probes(name):
            return "3.12.3" if name == "python" else None

        report = runtime.bootstrap(
            platform="darwin",
            probes=probes,
            which=lambda name: None,
            runner=lambda command: calls.append(command),
            check_only=False,
        )
        self.assertEqual(report["present"], ["python"])
        self.assertEqual(report["newly_installed"], [])
        self.assertEqual(report["failed"], ["node", "git"])
        self.assertIn("https://nodejs.org/en/download", report["details"]["node"]["reason"])
        self.assertIn("https://git-scm.com/download/mac", report["details"]["git"]["reason"])
        self.assertEqual(calls, [])
        blob = json.dumps(report, ensure_ascii=False)
        self.assertNotIn(LEAKED_RUNNER_OUT, blob)

    def test_linux_does_not_invent_an_installer(self):
        calls = []

        def probes(name):
            return None if name == "node" else "ok"

        report = runtime.bootstrap(
            platform="linux",
            probes=probes,
            which=lambda name: "/usr/bin/" + name,
            runner=lambda command: calls.append(command),
            check_only=False,
        )
        self.assertEqual(report["failed"], ["node"])
        self.assertIn("winget", report["details"]["node"]["reason"])
        self.assertEqual(calls, [])

    def test_failed_install_omits_runner_output(self):
        def probes(name):
            return None if name == "git" else "ok"

        def runner(command):
            raise OSError(LEAKED_RUNNER_ERR)

        report = runtime.bootstrap(
            platform="darwin",
            probes=probes,
            which=lambda name: "/opt/homebrew/bin/brew" if name == "brew" else None,
            runner=runner,
            check_only=False,
        )
        self.assertEqual(report["failed"], ["git"])
        self.assertNotIn("newly_installed", report["present"])
        blob = json.dumps(report, ensure_ascii=False)
        self.assertNotIn(LEAKED_RUNNER_OUT, blob)
        self.assertNotIn(LEAKED_RUNNER_ERR, blob)

        def runner_output(command):
            return Completed(17, LEAKED_RUNNER_OUT, LEAKED_RUNNER_ERR)

        report = runtime.bootstrap(
            platform="win32",
            probes=probes,
            which=lambda name: "winget" if name == "winget" else None,
            runner=runner_output,
            check_only=False,
        )
        blob = json.dumps(report, ensure_ascii=False)
        self.assertNotIn(LEAKED_RUNNER_OUT, blob)
        self.assertNotIn(LEAKED_RUNNER_ERR, blob)
        self.assertIn("17", report["details"]["git"]["reason"])

    def test_success_without_reprobe_is_failed_not_newly_installed(self):
        def runner(command):
            return Completed(0, LEAKED_RUNNER_OUT, "")

        report = runtime.bootstrap(
            platform="win32",
            probes=lambda name: None if name == "node" else "ok",
            which=lambda name: "winget" if name == "winget" else None,
            runner=runner,
            check_only=False,
        )
        self.assertEqual(report["newly_installed"], [])
        self.assertEqual(report["failed"], ["node"])
        self.assertNotIn(LEAKED_RUNNER_OUT, json.dumps(report))

    def test_check_only_cli_uses_real_tools_without_subprocess(self):
        out = io.StringIO()
        with patch.object(runtime.subprocess, "run", side_effect=AssertionError("install")), contextlib.redirect_stdout(out):
            code = runtime.main(["--check-only"])
        report = json.loads(out.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(report["present"], ["python", "node", "git"])
        self.assertEqual(report["newly_installed"], [])
        self.assertEqual(report["failed"], [])

    def test_cli_rejects_unknown_args_without_echoing_them(self):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = runtime.main(["--password", SECRET_PASSWORD])
        self.assertEqual(code, 2)
        self.assertNotIn(SECRET_PASSWORD, out.getvalue() + err.getvalue())


class LocalConfigWriteTest(unittest.TestCase):
    def copy_examples(self, root):
        config_dir = Path(root) / "config"
        config_dir.mkdir()
        (config_dir / "fog_config.yaml.example").write_bytes(FOG_EXAMPLE.read_bytes())
        (config_dir / "lx-wenda.json.example").write_bytes(WENDA_EXAMPLE.read_bytes())
        (config_dir / "distribution.json").write_text(json.dumps({"version": 1, "kind": "lxworkspace", "repository": "pxiaolei/lxworkspace", "skills": ["lx-wenda"]}), encoding="utf-8")
        return config_dir

    def run_config(self, args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = config.main(args)
        return code, out.getvalue(), err.getvalue()

    def test_existing_formal_files_keep_nonempty_secrets_and_stdout_is_redacted(self):
        fog_before_repo = FOG_EXAMPLE.read_bytes()
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = self.copy_examples(tmp)
            fog = config_dir / "fog_config.yaml"
            fog.write_text(
                "\n".join([
                    "# local note DO-NOT-TOUCH",
                    "version: 1",
                    "lx_shujuku:",
                    "  api:",
                    '    base_url: "http://example.invalid"',
                    '    username: "" # 本地备注',
                    f'    password: "{SECRET_KEPT_PASSWORD}"',
                    "lx_haibao:",
                    "  image_api:",
                    "    kie:",
                    '      api_key: ""',
                    'extra_local: "keep-me"',
                    "",
                ]),
                encoding="utf-8",
            )
            wenda = config_dir / "lx-wenda.json"
            original_wenda = json.dumps(
                {"base": "http://example.invalid", "token": SECRET_KEPT_TOKEN, "timeout_ms": 120000, "note": "local"},
                ensure_ascii=False,
                indent=2,
            ) + "\n"
            wenda.write_text(original_wenda, encoding="utf-8")
            form = Path(tmp) / "form.json"
            form.write_text(json.dumps({
                "username": "user-one",
                "password": SECRET_OTHER_PASSWORD,
                "image_key": SECRET_IMAGE_KEY,
                "wenda_token": SECRET_TOKEN,
            }), encoding="utf-8")
            code, out, err = self.run_config(["--from-json", str(form), "--root", tmp])
            text = fog.read_text(encoding="utf-8")
            self.assertEqual(code, 0, out + err)
            self.assertIn('username: "user-one" # 本地备注', text)
            self.assertIn(f'password: "{SECRET_KEPT_PASSWORD}"', text)
            self.assertNotIn(SECRET_OTHER_PASSWORD, text)
            self.assertIn(f'api_key: "{SECRET_IMAGE_KEY}"', text)
            self.assertIn("# local note DO-NOT-TOUCH", text)
            self.assertIn('extra_local: "keep-me"', text)
            self.assertEqual(wenda.read_text(encoding="utf-8"), original_wenda)
            report = json.loads(out)
            self.assertEqual(report["fog_config.yaml"]["fields"]["password"], "kept")
            self.assertEqual(report["fog_config.yaml"]["fields"]["username"], "written")
            self.assertEqual(report["fog_config.yaml"]["fields"]["image_key"], "written")
            self.assertEqual(report["lx-wenda.json"]["fields"]["token"], "kept")
            blob = out + err
            for secret in (SECRET_PASSWORD, SECRET_IMAGE_KEY, SECRET_TOKEN, SECRET_KEPT_PASSWORD, SECRET_KEPT_TOKEN, SECRET_OTHER_PASSWORD, "user-one"):
                self.assertNotIn(secret, blob)
        self.assertEqual(FOG_EXAMPLE.read_bytes(), fog_before_repo)

    def test_missing_formal_files_are_created_from_examples_without_echoing_secrets(self):
        before = {path: path.read_bytes() for path in (FOG_EXAMPLE, WENDA_EXAMPLE)}
        with tempfile.TemporaryDirectory() as tmp:
            self.copy_examples(tmp)
            form = Path(tmp) / "form.json"
            form.write_text(json.dumps({
                "username": "user-one",
                "password": SECRET_PASSWORD,
                "image_key": SECRET_IMAGE_KEY,
                "wenda_token": SECRET_TOKEN,
            }), encoding="utf-8")
            code, out, err = self.run_config(["--from-json", str(form), "--root", tmp])
            self.assertEqual(code, 0, out + err)
            fog = (Path(tmp) / "config/fog_config.yaml").read_text(encoding="utf-8")
            wenda = json.loads((Path(tmp) / "config/lx-wenda.json").read_text(encoding="utf-8"))
            self.assertIn('username: "user-one"', fog)
            self.assertIn(f'password: "{SECRET_PASSWORD}"', fog)
            self.assertIn(f'api_key: "{SECRET_IMAGE_KEY}"', fog)
            self.assertIn("http://datareporting.sfczhushou.com", fog)
            self.assertEqual(wenda["token"], SECRET_TOKEN)
            self.assertEqual(wenda["base"], "http://maxkb.sfczhushou.com")
            blob = out + err
            for secret in ("user-one", SECRET_PASSWORD, SECRET_IMAGE_KEY, SECRET_TOKEN):
                self.assertNotIn(secret, blob)
            report = json.loads(out)
            self.assertEqual(report["fog_config.yaml"]["action"], "created")
            self.assertEqual(report["lx-wenda.json"]["action"], "created")
            self.assertIn("已写入", report["summary"])
        for path, content in before.items():
            self.assertEqual(path.read_bytes(), content)

    def test_env_file_fills_only_empty_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = self.copy_examples(tmp)
            fog = config_dir / "fog_config.yaml"
            fog.write_text(FOG_EXAMPLE.read_text(encoding="utf-8"), encoding="utf-8")
            env_file = Path(tmp) / "form.env"
            env_file.write_text(f"username=user-one\npassword={SECRET_PASSWORD}\n", encoding="utf-8")
            code, out, err = self.run_config(["--env-file", str(env_file), "--root", tmp])
            self.assertEqual(code, 0, out + err)
            text = fog.read_text(encoding="utf-8")
            self.assertIn('username: "user-one"', text)
            self.assertIn(f'password: "{SECRET_PASSWORD}"', text)
            self.assertIn('api_key: ""', text)
            self.assertFalse((config_dir / "lx-wenda.json").exists())
            report = json.loads(out)
            self.assertEqual(report["fog_config.yaml"]["fields"]["image_key"], "missing")
            self.assertEqual(report["lx-wenda.json"]["fields"]["token"], "missing")
            self.assertNotIn(SECRET_PASSWORD, out + err)
            self.assertNotIn("user-one", out + err)

    def test_existing_file_does_not_gain_missing_structure(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = self.copy_examples(tmp)
            fog = config_dir / "fog_config.yaml"
            original = "lx_shujuku:\n  api:\n    username: \"\"\n    password: \"\"\n"
            fog.write_text(original, encoding="utf-8")
            form = Path(tmp) / "form.json"
            form.write_text(json.dumps({"username": "user-one", "image_key": SECRET_IMAGE_KEY}), encoding="utf-8")
            code, out, err = self.run_config(["--from-json", str(form), "--root", tmp])
            self.assertEqual(code, 0, out + err)
            text = fog.read_text(encoding="utf-8")
            self.assertNotIn("api_key", text)
            self.assertNotIn(SECRET_IMAGE_KEY, text)
            self.assertIn('username: "user-one"', text)
            report = json.loads(out)
            self.assertEqual(report["fog_config.yaml"]["fields"]["image_key"], "blocked")
            self.assertNotIn(SECRET_IMAGE_KEY, out + err)
            self.assertNotIn("user-one", out + err)

    def test_inline_form_invalid_json_and_symlink_do_not_echo_or_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = self.copy_examples(tmp)
            outside = Path(tmp) / "outside.yaml"
            outside.write_text("untouched: true\n", encoding="utf-8")
            link = config_dir / "fog_config.yaml"
            link.symlink_to(outside)
            form = Path(tmp) / "form.json"
            form.write_text(json.dumps({"username": "user-one", "password": SECRET_PASSWORD}), encoding="utf-8")
            code, out, err = self.run_config(["--from-json", str(form), "--root", tmp])
            self.assertNotEqual(code, 0)
            self.assertEqual(outside.read_text(encoding="utf-8"), "untouched: true\n")
            self.assertNotIn(SECRET_PASSWORD, out + err)
            self.assertNotIn("user-one", out + err)
            link.unlink()

            secret_name = Path(tmp) / SECRET_TOKEN
            code, out, err = self.run_config(["--from-json", str(secret_name), "--root", tmp])
            self.assertNotEqual(code, 0)
            self.assertNotIn(SECRET_TOKEN, out + err)
            self.assertFalse((config_dir / "fog_config.yaml").exists())

            code, out, err = self.run_config(["--from-json", '{"password": "' + SECRET_PASSWORD + '"}', "--root", tmp])
            self.assertNotEqual(code, 0)
            self.assertNotIn(SECRET_PASSWORD, out + err)
            self.assertFalse((config_dir / "fog_config.yaml").exists())

            broken = Path(tmp) / "broken.json"
            broken.write_text('{"password": "' + SECRET_PASSWORD + '"', encoding="utf-8")
            code, out, err = self.run_config(["--from-json", str(broken), "--root", tmp])
            self.assertNotEqual(code, 0)
            self.assertNotIn(SECRET_PASSWORD, out + err)

            code, out, err = self.run_config(["--password", SECRET_PASSWORD, "--root", tmp])
            self.assertNotEqual(code, 0)
            self.assertNotIn(SECRET_PASSWORD, out + err)

    def test_filled_file_is_not_rewritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = self.copy_examples(tmp)
            fog = config_dir / "fog_config.yaml"
            fog.write_text(
                'lx_shujuku:\n  api:\n    username: "user-one"\n    password: "kept-password-ALPHA"\n'
                'lx_haibao:\n  image_api:\n    kie:\n      api_key: "kie/key+1"\n',
                encoding="utf-8",
            )
            wenda = config_dir / "lx-wenda.json"
            wenda.write_text('{"base": "http://maxkb.sfczhushou.com", "token": "tok=en"}\n', encoding="utf-8")
            before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in (fog, wenda)}
            form = Path(tmp) / "form.json"
            form.write_text(json.dumps({
                "username": "other-user",
                "password": SECRET_OTHER_PASSWORD,
                "image_key": "other-key",
                "wenda_token": "other-token",
            }), encoding="utf-8")
            code, out, err = self.run_config(["--from-json", str(form), "--root", tmp])
            self.assertEqual(code, 0, out + err)
            for path, (content, mtime) in before.items():
                self.assertEqual(path.read_bytes(), content)
                self.assertEqual(path.stat().st_mtime_ns, mtime)
            for secret in ("other-user", SECRET_OTHER_PASSWORD, "other-key", "other-token", "user-one", SECRET_KEPT_PASSWORD, SECRET_IMAGE_KEY, SECRET_TOKEN):
                self.assertNotIn(secret, out + err)


class InitHashGateTest(unittest.TestCase):
    def test_stale_hash_and_missing_runtime_do_not_install_project_deps(self):
        with patch.object(init.subprocess, "run", side_effect=AssertionError("installer called")), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(init.main(["--install"]), 0)
            with self.assertRaises(SystemExit):
                init.main(["--install", "--confirmed", "--expected-preview-hash", "stale"])

        def which(name):
            return None if name == "node" else "/usr/bin/" + name

        err = io.StringIO()
        with patch.object(init.shutil, "which", side_effect=which), patch.object(init.subprocess, "run", side_effect=AssertionError("pip")):
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
                expected = hashlib.sha256(json.dumps(init.build_plan(), sort_keys=True).encode()).hexdigest()
                with self.assertRaises(SystemExit):
                    init.main(["--install", "--confirmed", "--expected-preview-hash", expected])
        text = err.getvalue()
        self.assertIn("init-runtime", text)
        self.assertNotIn("不能自动全局安装", text)


class DispatcherRouteTest(unittest.TestCase):
    def test_init_helpers_use_the_current_interpreter(self):
        process = MagicMock(returncode=0)
        launcher = "/tmp/launcher-python"
        with patch.object(dispatcher.sys, "executable", launcher), patch.object(dispatcher.Path, "is_file", return_value=True), patch.object(dispatcher.subprocess, "run", return_value=process) as run:
            self.assertEqual(dispatcher.main(["init-runtime", "--check-only"]), 0)
            self.assertEqual(dispatcher.main(["init-config", "--from-json", "form.json"]), 0)
            self.assertEqual(dispatcher.main(["init"]), 0)
        commands = [call.args[0] for call in run.call_args_list]
        self.assertTrue(commands[0][2].endswith("bootstrap_runtime.py"))
        self.assertEqual(commands[0][0], launcher)
        self.assertIn("--check-only", commands[0])
        self.assertTrue(commands[1][2].endswith("write_local_config.py"))
        self.assertEqual(commands[1][0], launcher)
        self.assertEqual(commands[1][-2:], ["--from-json", "form.json"])
        self.assertTrue(commands[2][2].endswith("init_workspace.py"))
        self.assertNotEqual(commands[2][0], launcher)
        self.assertIn(".venv", commands[2][0].replace("\\", "/"))
        for command in commands:
            self.assertNotIn(SECRET_PASSWORD, " ".join(command))


if __name__ == "__main__":
    unittest.main()
