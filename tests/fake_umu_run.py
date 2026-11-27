#!/usr/bin/env python3
"""umu-run falso para los tests: imita lo que hacen umu + Proton en el prefijo
(estructura, unidades c:/z:, accesos .lnk de un instalador) sin Wine."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pylnk3

args = sys.argv[1:]
if os.environ.get("FAKE_UMU_DELAY"):  # para capturas de pantalla del progreso
    import time as _t
    _t.sleep(float(os.environ["FAKE_UMU_DELAY"]))
prefix = Path(os.environ["WINEPREFIX"])
assert os.environ.get("PROTONPATH"), "PROTONPATH no definido"
assert os.environ.get("GAMEID"), "GAMEID no definido"

# --- umu setup_pfx + Proton setup_prefix ---
prefix.mkdir(parents=True, exist_ok=True)
if not (prefix / "pfx").exists():
    (prefix / "pfx").symlink_to(".")
c = prefix / "drive_c"
for d in ["windows/system32", "Program Files", "Program Files (x86)",
          "users/steamuser/Desktop", "users/steamuser/AppData/Roaming/Microsoft/Windows/Start Menu/Programs",
          "ProgramData/Microsoft/Windows/Start Menu/Programs"]:
    (c / d).mkdir(parents=True, exist_ok=True)
(c / "windows/system32/notepad.exe").write_bytes(b"MZ stock")
dd = prefix / "dosdevices"
dd.mkdir(exist_ok=True)
if not os.path.lexists(dd / "c:"):
    (dd / "c:").symlink_to("../drive_c")
if not os.path.lexists(dd / "z:"):  # igual que Proton: solo si no existe
    (dd / "z:").symlink_to("/")

with open(os.environ["FAKE_UMU_LOG"], "a") as f:
    f.write(json.dumps({
        "argv": args, "verb": os.environ.get("PROTON_VERB"), "cwd": os.getcwd(),
        "protonpath": os.environ["PROTONPATH"],
        "pv_rw": os.environ.get("PRESSURE_VESSEL_FILESYSTEMS_RW", ""),
        "drives": {e.name: os.readlink(e) for e in dd.iterdir() if e.is_symlink()},
    }) + "\n")

if args == [""]:
    sys.exit(0)

payload = Path(os.environ["FAKE_PAYLOAD"])


def linger():
    env = dict(os.environ, WINEPREFIX=str(prefix))
    subprocess.Popen(["sleep", os.environ.get("FAKE_LINGER", "3")], env=env, start_new_session=True)


if os.environ.get("FAKE_CANCEL") == "1":
    sys.exit(1)

if args[0] == "msiexec":
    assert args[1] == "/i" and args[2][1] == ":", args
    dest = c / "Program Files/PruebaMSI"
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copy(payload, dest / "hello.exe")
    sys.exit(0)

exe = Path(args[0])
data = exe.read_bytes() if exe.is_file() else b""
if b"NullsoftInst" in data:
    dest = c / "Program Files/TestApp"
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copy(payload, dest / "hello.exe")
    shutil.copy(payload, dest / "uninstall.exe")
    sm = c / "users/steamuser/AppData/Roaming/Microsoft/Windows/Start Menu/Programs/Test App"
    sm.mkdir(parents=True, exist_ok=True)
    target = r"C:\Program Files\TestApp\hello.exe"
    pylnk3.for_file(target, str(sm / "Test App.lnk"), work_dir=r"C:\Program Files\TestApp")
    pylnk3.for_file(r"C:\Program Files\TestApp\uninstall.exe", str(sm / "Uninstall Test App.lnk"))
    # duplicado en el escritorio: no debe crear un segundo .desktop
    pylnk3.for_file(target, str(c / "users/steamuser/Desktop/Test App.lnk"))
    # Asociación .txt → hello.exe, como haría el instalador en el registro
    with open(prefix / "system.reg", "a") as reg:
        reg.write('\n[Software\\\\Classes\\\\.txt] 1700000000\n@="TestApp.txt"\n'
                  '\n[Software\\\\Classes\\\\TestApp.txt\\\\shell\\\\open\\\\command] 1700000000\n'
                  '@="\\"C:\\\\Program Files\\\\TestApp\\\\hello.exe\\" \\"%1\\""\n')
    linger()
    # «Ejecutar Test App» al terminar: el contenedor no acaba hasta cerrarla
    import time
    time.sleep(float(os.environ.get("FAKE_APP_RUNS", "0")))
    sys.exit(0)

sys.exit(int(os.environ.get("FAKE_RC", "0")))
