#!/usr/bin/env python3
"""Compatibility entry for existing partner commands."""
import runpy
from pathlib import Path
if __name__ == "__main__":
    runpy.run_path(str(Path(__file__).with_name("update_fog.py")), run_name="__main__")
