import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FIX = Path(__file__).resolve().parent / "fixtures"
sys.path.insert(0, str(ROOT))


@pytest.fixture
def env(tmp_path, monkeypatch):
    """HOME y XDG aislados, sin GUI, umu-run falso y un GE-Proton falso instalado."""
    home = tmp_path / "home"
    for d in ("Documents", "Downloads", ".local/share", ".config", ".local/state"):
        (home / d).mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_DATA_HOME", str(home / ".local/share"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(home / ".local/state"))
    monkeypatch.setenv("MALIVE_NO_GUI", "1")
    # Plantillas: solo en sus propios tests (no lanzar procesos en segundo plano)
    monkeypatch.setenv("MALIVE_NO_TEMPLATES", "1")
    # Los tests comprueban mensajes en español; hay tests propios para el inglés
    monkeypatch.setenv("MALIVE_LANG", "es")
    from malive import i18n
    i18n.reset()
    # PATH mínimo, pero con el Python de los tests primero: el umu-run falso
    # (#!/usr/bin/env python3) necesita pylnk3, que puede no estar en el del sistema
    monkeypatch.setenv("PATH", f"{Path(sys.executable).parent}:/usr/bin:/bin")
    monkeypatch.setenv("MALIVE_UMU_RUN", str(ROOT / "tests/fake_umu_run.py"))
    monkeypatch.setenv("MALIVE_RUN_BIN", "/opt/test/malive-run")
    log = tmp_path / "umu.log"
    monkeypatch.setenv("FAKE_UMU_LOG", str(log))
    monkeypatch.setenv("FAKE_PAYLOAD", str(FIX / "hello.exe"))
    monkeypatch.setenv("FAKE_LINGER", "1")

    # Los montajes reales de la máquina de tests no deben colarse como unidades
    from malive import mounts
    monkeypatch.setattr(mounts, "data_mounts", lambda *a, **k: [])
    # Ni su hardware (gráficas, driver de NVIDIA)
    from malive import hardware, nvidia
    empty = tmp_path / "nohw"
    (empty / "sys").mkdir(parents=True)
    (empty / "proc").mkdir(parents=True)
    real_detect = hardware.detect
    monkeypatch.setattr(hardware, "detect",
                        lambda sys_root=empty / "sys", proc_root=empty / "proc", with_names=False:
                        real_detect(sys_root, proc_root, with_names))
    monkeypatch.setattr(nvidia, "gpu_present", lambda: False)
    from malive import paths
    gp = paths.proton_dir() / "GE-Proton10-1"
    gp.mkdir(parents=True)
    (gp / "proton").write_text("#!/bin/sh\n")
    (gp / "toolmanifest.vdf").write_text('"manifest" {}\n')

    class E:
        pass

    e = E()
    e.home = home
    e.tmp = tmp_path
    e.downloads = home / "Downloads"

    def calls():
        if not log.exists():
            return []
        return [json.loads(l) for l in log.read_text().splitlines() if l.strip()]

    e.calls = calls
    return e
