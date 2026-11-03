#!/usr/bin/env python3
"""Local login window; default preview never requests credentials or creates a session."""
from pathlib import Path
import sys
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'lx-shenfenzheng/scripts'))
import honghu_common as common


def main(argv=None):
    return common.login_main(argv, Path(__file__).with_name('ticket_cache.json'))


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except ValueError as exc:
        print(str(exc))
        raise SystemExit(2)
