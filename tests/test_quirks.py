"""Ajustes por programa: conocidos (quirks) y propios de cada app."""

import shutil

from conftest import FIX
from malive import cli, config, runner


def _app(env):
    setup = env.downloads / "TestApp-2.1.0-x64-setup.exe"
    shutil.copy(FIX / "TestApp-2.1.0-x64-setup.exe", setup)
    runner.main([str(setup)])
    return config.all_apps()[0]


def test_known_quirk_for_format_factory(env, monkeypatch):
    from malive import prefix as pfx
    app = _app(env)
    e = {}
    pfx.apply_app_settings(e, app, r"/x/drive_c/Program Files/FormatFactory5.22.0.0/FormatFactory.exe")
    assert e["WINEDLLOVERRIDES"] == "sapi=d"
    e = {"WINEDLLOVERRIDES": "sapi=b"}  # lo que pone el usuario a mano, gana (va al final)
    pfx.apply_app_settings(e, app, "FormatFactory.exe")
    assert e["WINEDLLOVERRIDES"] == "sapi=d;sapi=b"
    e = {}
    pfx.apply_app_settings(e, app, "hello.exe")
    assert "WINEDLLOVERRIDES" not in e


def test_app_settings_reach_umu(env, capsys):
    app = _app(env)
    assert cli.main(["dll", app.id, "d3d9=n,b"]) == 0
    assert cli.main(["env", app.id, "PROTON_ENABLE_NVAPI=1"]) == 0
    app = config.load_app(app.id)
    assert app.dll_overrides == {"d3d9": "n,b"} and app.env == {"PROTON_ENABLE_NVAPI": "1"}
    assert runner.main(["--app", app.id, "--entry", app.entries[0].id]) == 0
    call = env.calls()[-1]
    assert call["dlls"] == "d3d9=n,b" and call["nvapi"] == "1"

    assert cli.main(["dll", app.id, "--remove", "d3d9"]) == 0
    assert config.load_app(app.id).dll_overrides == {}
    out = capsys.readouterr().out
    assert "d3d9=n,b" in out


def test_bad_dll_mode(env):
    app = _app(env)
    import pytest
    with pytest.raises(SystemExit):
        cli.main(["dll", app.id, "sapi=off"])


def test_app_quirk_covers_installer(env):
    """El instalador abre el programa al terminar: el ajuste debe ir ya en él."""
    from malive import prefix as pfx
    app = config.App(id="format-factory", name="Format Factory", kind="installed",
                     prefix=str(env.tmp / "ff"), proton="GE-Proton10-1")
    e = {}
    pfx.apply_app_settings(e, app, "/home/u/Downloads/FFSetup5.22.0.0.exe")
    assert e["WINEDLLOVERRIDES"] == "sapi=d"
    e = {}
    pfx.apply_app_settings(e, app, "msiexec")
    assert e["WINEDLLOVERRIDES"] == "sapi=d"
