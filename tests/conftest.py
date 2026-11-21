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
    monkeypatch.setenv("PATH", "/usr/bin:/bin")  # sin xdg-user-dir del sistema raro
    monkeypatch.setenv("MALIVE_UMU_RUN", str(ROOT / "tests/fake_umu_run.py"))
    monkeypatch.setenv("MALIVE_RUN_BIN", "/opt/test/malive-run")
    log = tmp_path / "umu.log"
    monkeypatch.setenv("FAKE_UMU_LOG", str(log))
    monkeypatch.setenv("FAKE_PAYLOAD", str(FIX / "hello.exe"))
    monkeypatch.setenv("FAKE_LINGER", "1")

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
