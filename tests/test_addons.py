"""Complementos (códecs, bibliotecas…) dentro de un programa ya instalado."""

import shutil
from datetime import datetime, timedelta, timezone

from conftest import FIX
from malive import addons, config, detect, integrate, paths, runner, ui


def _install_testapp(env):
    setup = env.downloads / "TestApp-2.1.0-x64-setup.exe"
    shutil.copy(FIX / "TestApp-2.1.0-x64-setup.exe", setup)
    assert runner.main([str(setup)]) == 0
    return config.all_apps()[0]


def _addon(env, name="LAVFilters-0.83-Installer.exe"):
    dest = env.downloads / name
    shutil.copy(FIX / "inno-fake-setup.exe", dest)
    return dest


def test_classify(env):
    assert addons.classify(detect.detect(_addon(env))).family == addons.MEDIA
    assert addons.classify(detect.detect(_addon(env, "VC_redist.x64.exe"))).family == addons.RUNTIME
    assert addons.classify(detect.detect(_addon(env, "windowsdesktop-runtime-8.0.11-win-x64.exe"))).family == addons.RUNTIME
    assert addons.classify(detect.detect(_addon(env, "inno-fake-setup.exe"))) is None


def test_unknown_installer_without_open_apps_is_a_new_program(env, monkeypatch):
    _install_testapp(env)
    asked = []
    monkeypatch.setattr(ui, "checklist", lambda *a, **k: asked.append(a) or None)
    monkeypatch.setattr(ui, "backend", lambda: "zenity")
    assert addons.ask(detect.detect(_addon(env, "otro-setup.exe"))) == []
    assert not asked


def test_single_player_gets_codec_without_asking(env, monkeypatch):
    app = _install_testapp(env)
    monkeypatch.setattr(addons, "is_media_app", lambda a: a.id == app.id)
    monkeypatch.setattr(ui, "backend", lambda: "zenity")
    asked = []
    monkeypatch.setattr(ui, "checklist", lambda *a, **k: asked.append(a) or None)
    lav = _addon(env)
    n_before = len(env.calls())
    assert runner.main([str(lav)]) == 0
    assert not asked
    # Se ejecutó en el entorno de la app, sin crear otra app ni accesos nuevos
    assert len(config.all_apps()) == 1
    assert len(env.calls()) == n_before + 1 and env.calls()[-1]["argv"] == [str(lav)]
    assert len(config.load_app(app.id).entries) == 1


def _fake_apps(env, monkeypatch, specs):
    """specs: (id, reproductor, abierto, last_used)"""
    apps = [config.App(id=i, name=i.title(), kind="installed", prefix=str(env.tmp / i),
                       proton="GE-Proton10-1", last_used=lu) for i, _m, _o, lu in specs]
    media = {i for i, m, _o, _l in specs if m}
    running = {str(env.tmp / i) for i, _m, o, _l in specs if o}
    monkeypatch.setattr(config, "all_apps", lambda: apps)
    monkeypatch.setattr(addons, "is_media_app", lambda a: a.id in media)
    monkeypatch.setattr(addons.pfx, "is_busy", lambda p: str(p) in running)
    monkeypatch.setattr(ui, "backend", lambda: "zenity")
    seen = {}
    monkeypatch.setattr(ui, "checklist", lambda m, items, title="": seen.setdefault("items", items) and None)
    return seen


def test_only_open_player_wins(env, monkeypatch):
    seen = _fake_apps(env, monkeypatch, [("potplayer", True, True, "2026-01-02T00:00:00+00:00"),
                                         ("mpc", True, False, "2026-01-03T00:00:00+00:00"),
                                         ("notepad", False, True, "")])
    assert addons.ask(detect.detect(_addon(env))) == ["potplayer"]
    assert "items" not in seen


def test_several_players_ask_with_last_used_checked(env, monkeypatch):
    seen = _fake_apps(env, monkeypatch, [("potplayer", True, False, "2026-01-02T00:00:00+00:00"),
                                         ("mpc", True, False, "2026-01-03T00:00:00+00:00"),
                                         ("notepad", False, True, "")])
    assert addons.ask(detect.detect(_addon(env))) is None  # el diálogo falso cancela
    items = seen["items"]
    assert items[0][0] == "mpc" and items[0][2] is True
    assert [i for i, _l, on in items if on] == ["mpc"]
    assert items[-1][0] == addons.NEW and items[-1][2] is False


def test_unknown_installer_with_open_app_asks_new_checked(env, monkeypatch):
    seen = _fake_apps(env, monkeypatch, [("notepad", False, True, "")])
    assert addons.ask(detect.detect(_addon(env, "gimp-setup.exe"))) is None
    assert [i for i, _l, on in seen["items"] if on] == [addons.NEW]


def test_runtime_with_one_open_app(env, monkeypatch):
    _fake_apps(env, monkeypatch, [("juego", False, True, ""), ("notepad", False, False, "")])
    assert addons.ask(detect.detect(_addon(env, "VC_redist.x64.exe"))) == ["juego"]


def test_cancel_choice_installs_nothing(env, monkeypatch):
    _install_testapp(env)
    monkeypatch.setattr(ui, "backend", lambda: "zenity")
    monkeypatch.setattr(ui, "checklist", lambda *a, **k: None)
    n = len(env.calls())
    assert runner.main([str(_addon(env))]) == 1
    assert len(env.calls()) == n


def test_no_gui_uses_suggestion(env, monkeypatch):
    app = _install_testapp(env)
    monkeypatch.setattr(addons, "is_media_app", lambda a: True)
    assert addons.ask(detect.detect(_addon(env))) == [app.id]


def test_choose_forces_question_and_into_skips_it(env, monkeypatch):
    app = _install_testapp(env)
    monkeypatch.setattr(ui, "backend", lambda: "zenity")
    asked = []
    monkeypatch.setattr(ui, "checklist", lambda m, items, title="": asked.append(items) or [app.id])
    plugin = _addon(env, "plugin-setup.exe")
    assert runner.main(["--choose", str(plugin)]) == 0
    assert asked and env.calls()[-1]["argv"] == [str(plugin)]
    assert len(config.all_apps()) == 1

    asked.clear()
    assert runner.main(["--into", app.id, str(plugin)]) == 0
    assert not asked and len(config.all_apps()) == 1


def test_recent_use_is_recorded(env):
    app = _install_testapp(env)
    assert runner.main(["--app", app.id, "--entry", app.entries[0].id]) == 0
    app = config.load_app(app.id)
    assert addons.used_recently(app)
    later = datetime.now(timezone.utc) + timedelta(hours=1)
    assert not addons.used_recently(app, now=later)


def test_into_handler_desktop(env):
    integrate.install_handler()
    text = (paths.applications_dir() / integrate.INTO_DESKTOP_ID).read_text()
    assert "--choose %f" in text and "NoDisplay=true" in text
    assert "Name[es]=MaLiVe: instalar dentro de un programa…" in text
    integrate.remove_handler()
    assert not (paths.applications_dir() / integrate.INTO_DESKTOP_ID).exists()
