# -*- coding: utf-8 -*-
"""Build 轻读.exe — keeps the Chinese exe name intact across locales."""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main() -> int:
    html = ROOT / "ui" / "index.html"
    icon = ROOT / "icon.ico"
    main_py = ROOT / "main.py"
    dist = ROOT / "dist"
    build = ROOT / "build"

    for p in (html, icon, main_py):
        if not p.is_file():
            print("missing", p)
            return 1

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--windowed",
        "--name",
        "轻读",
        "--icon",
        str(icon),
        "--add-data",
        f"{html}{os.pathsep}ui",
        "--distpath",
        str(dist),
        "--workpath",
        str(build),
        "--specpath",
        str(build),
        str(main_py),
    ]
    print("running:", " ".join(cmd))
    rc = subprocess.call(cmd, cwd=str(ROOT))
    print("exit", rc)
    if rc == 0:
        for p in dist.iterdir():
            print("dist:", repr(p.name), p.stat().st_size)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
