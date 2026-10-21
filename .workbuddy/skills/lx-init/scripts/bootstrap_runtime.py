#!/usr/bin/env python3
"""Install missing Python, Node.js, or Git and report present, newly_installed, and failed."""
import json
import re
import shutil
import subprocess
import sys

TOOLS = ("python", "node", "git")
LABELS = {"python": "Python（>=3.10）", "node": "Node.js", "git": "Git"}
WINGET_IDS = {
    "python": "Python.Python.3.12",
    "node": "OpenJS.NodeJS.LTS",
    "git": "Git.Git",
}
BREW_FORMULAS = {"python": "python@3.12", "node": "node", "git": "git"}
OFFICIAL_URLS = {
    "python": {
        "darwin": "https://www.python.org/downloads/macos/",
        "win32": "https://www.python.org/downloads/windows/",
    },
    "node": {
        "darwin": "https://nodejs.org/en/download",
        "win32": "https://nodejs.org/en/download",
    },
    "git": {
        "darwin": "https://git-scm.com/download/mac",
        "win32": "https://git-scm.com/download/win",
    },
}
_VERSION = re.compile(r"[0-9][0-9A-Za-z._+-]{0,31}\Z")
_PYTHON_PROBE = (
    "import sys; print('%d.%d.%d' % sys.version_info[:3]); "
    "raise SystemExit(0 if sys.version_info >= (3, 10) else 1)"
)


def _version(value):
    if isinstance(value, str) and _VERSION.fullmatch(value.strip()):
        return value.strip()
    return None


def _found(value):
    return isinstance(value, str) and bool(value.strip())


def plan_command(tool, platform, which):
    """Return (argv, error). argv is None when this platform cannot install the tool."""
    label = LABELS[tool]
    if platform == "win32":
        winget = which("winget")
        if not winget:
            return None, "未找到 winget。请先安装微软「应用安装程序」，然后重试；不会改用其它安装器。"
        return [
            winget, "install", "--id", WINGET_IDS[tool], "-e",
            "--accept-package-agreements", "--accept-source-agreements",
        ], None
    if platform == "darwin":
        brew = which("brew")
        if not brew:
            return None, "未找到 Homebrew。请用官方安装包安装%s：%s" % (label, OFFICIAL_URLS[tool]["darwin"])
        return [brew, "install", BREW_FORMULAS[tool]], None
    return None, "当前系统不能自动安装%s。自动补齐只支持 Windows 的 winget，以及已安装 Homebrew 的 Mac。" % label


def _run(command, runner):
    try:
        result = runner(command)
    except OSError:
        return False, "无法执行安装命令。"
    code = getattr(result, "returncode", None)
    if code == 0:
        return True, ""
    if isinstance(code, int):
        return False, "安装命令未能完成（退出码 %s）。" % code
    return False, "安装命令未能完成。"


def _detail(state, probe_value, reason=None, via=None):
    detail = {"state": state}
    version = _version(probe_value)
    if version:
        detail["version"] = version
    if via:
        detail["via"] = via
    if reason:
        detail["reason"] = reason
    return detail


def bootstrap(*, platform, probes, which, runner, check_only=False):
    """Detect Python >=3.10, Node.js, and Git. Install only the missing ones unless check_only."""
    present = []
    newly_installed = []
    failed = []
    planned = []
    details = {}
    channel = "winget" if platform == "win32" else "brew" if platform == "darwin" else ""
    for tool in TOOLS:
        current = probes(tool)
        if _found(current):
            present.append(tool)
            details[tool] = _detail("present", current)
            continue
        if check_only:
            planned.append(tool)
            details[tool] = _detail("planned", None)
            continue
        command, error = plan_command(tool, platform, which)
        if error or not command:
            failed.append(tool)
            details[tool] = _detail("failed", None, reason=error or "无法执行安装命令。")
            continue
        installed, reason = _run(command, runner)
        if installed:
            current = probes(tool)
            if _found(current):
                newly_installed.append(tool)
                details[tool] = _detail("newly_installed", current, via=channel)
                continue
            reason = "安装命令已结束，但当前环境仍找不到%s。请新开终端后重试。" % LABELS[tool]
        failed.append(tool)
        details[tool] = _detail("failed", None, reason=reason)
    return {
        "present": present,
        "newly_installed": newly_installed,
        "failed": failed,
        "planned": planned,
        "ok": not failed and not planned,
        "details": details,
    }


def probe_python():
    if sys.version_info >= (3, 10):
        return "%d.%d.%d" % sys.version_info[:3]
    for command in (["python3"], ["python"], ["py", "-3"]):
        if not shutil.which(command[0]):
            continue
        try:
            result = subprocess.run(command + ["-c", _PYTHON_PROBE], capture_output=True, text=True)
        except OSError:
            continue
        if result.returncode == 0:
            lines = [line.strip() for line in (result.stdout or "").splitlines() if line.strip()]
            if lines:
                return lines[-1]
    return None


def probe_node():
    return "available" if shutil.which("node") else None


def probe_git():
    return "available" if shutil.which("git") else None


def _live_probe(name):
    return {"python": probe_python, "node": probe_node, "git": probe_git}[name]()


def _live_runner(command):
    return subprocess.run(command, capture_output=True, text=True)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv not in ([], ["--check-only"]):
        print("只接受 --check-only")
        return 2
    report = bootstrap(
        platform=sys.platform,
        probes=_live_probe,
        which=shutil.which,
        runner=_live_runner,
        check_only=argv == ["--check-only"],
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
