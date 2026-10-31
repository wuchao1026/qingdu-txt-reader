# -*- coding: utf-8 -*-
import winreg
from pathlib import Path

preferred = Path("dist") / "轻读.exe"
print("exists", preferred.is_file(), preferred)
if not preferred.is_file():
    exes = [p for p in Path("dist").glob("*.exe")]
    print("fallbacks", exes)
    if not exes:
        raise SystemExit("no exe")
    preferred = exes[0]

exe = str(preferred.resolve())
cmd = f'"{exe}" "%1"'
print("cmd", cmd)
for path in [
    r"Software\Classes\MimoTxtReader.txt\shell\open\command",
    r"Software\Classes\txtfile\shell\open\command",
    r"Software\Classes\txtfilelegacy\shell\open\command",
]:
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, path) as k:
        winreg.SetValueEx(k, "", 0, winreg.REG_SZ, cmd)
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as k:
        print("ok", path, "=", winreg.QueryValueEx(k, "")[0])
