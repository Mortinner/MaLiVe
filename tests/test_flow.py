"""Flujo completo con umu-run falso: instalar, lanzar, reutilizar, cancelar..."""

import os
import shutil
from pathlib import Path

import pylnk3

from conftest import FIX
from malive import config, paths, prefix as pfx, runner


def copy_fixture(env, name, dest_name=None):
    dest = env.downloads / (dest_name or name)
    shutil.copy(FIX / name, dest)
    return dest


def test_install_nsis(env):
    setup = copy_fixture(env, "TestApp-2.1.0-x64-setup.exe")
    assert runner.main([str(setup)]) == 0

    apps = config.all_apps()
    assert len(apps) == 1
    app = apps[0]
    assert app.id == "test-app" and app.name == "Test App" and app.kind == "installed"
    assert app.proton == "GE-Proton10-1"

    # un único acceso: sin el desinstalador y sin duplicar el del escritorio
    assert [e.name for e in app.entries] == ["Test App"]
    entry = app.entries[0]
    assert entry.exe.endswith("drive_c/Program Files/TestApp/hello.exe")
    assert entry.workdir and entry.workdir.endswith("Program Files/TestApp")

    desktop = Path(entry.desktop).read_text()
    assert "Exec=/opt/test/malive-run --app test-app --entry test-app" in desktop
    assert "StartupWMClass=hello.exe" in desktop
    if shutil.which("icotool"):
        assert entry.icon and entry.icon.endswith(".png") and Path(entry.icon).is_file()

    calls = env.calls()
    assert calls[0]["argv"] == [""]  # creación del prefijo
    install_call = calls[-1]
    assert install_call["argv"] == [str(setup)]
    assert install_call["cwd"] == str(env.downloads)
    assert install_call["protonpath"].endswith("GE-Proton10-1")
    # aislamiento: Z: a carpeta vacía, Documentos/Descargas compartidas, sin Y: (Descargas ya cubierta)
    drives = install_call["drives"]
    assert drives["z:"] == str(app.prefix_path / pfx.EMPTY_DIR)
    assert set(drives.values()) >= {str(env.home / "Documents"), str(env.home / "Downloads")}
    assert "y:" not in drives
    # tras esperar a que termine el proceso rezagado, ya no hay nada vivo
    assert not pfx.is_busy(app.prefix_path)


def test_source_drive_when_outside_shared(env):
    other = env.tmp / "usb"
    other.mkdir()
    setup = other / "TestApp-setup.exe"
    shutil.copy(FIX / "TestApp-2.1.0-x64-setup.exe", setup)
    assert runner.main([str(setup)]) == 0
    assert env.calls()[-1]["drives"]["y:"] == str(other)


def test_launch_entry_and_reinstall_reuses_prefix(env):
    setup = copy_fixture(env, "TestApp-2.1.0-x64-setup.exe")
    runner.main([str(setup)])
    app = config.all_apps()[0]
    entry = app.entries[0]

    assert runner.main(["--app", app.id, "--entry", entry.id]) == 0
    last = env.calls()[-1]
    assert last["argv"] == [entry.exe]
    assert last["cwd"] == entry.workdir
    assert last["verb"] == "waitforexitandrun"

    # reinstalar: mismo prefijo, sin entradas duplicadas
    runner.main([str(setup)])
    apps = config.all_apps()
    assert len(apps) == 1 and len(apps[0].entries) == 1


def test_isolation_off(env):
    cfg = config.load_global()
    cfg["isolate"] = False
    config.save_global(cfg)
    setup = copy_fixture(env, "TestApp-2.1.0-x64-setup.exe")
    runner.main([str(setup)])
    drives = env.calls()[-1]["drives"]
    assert drives["z:"] == "/"
    assert set(drives) == {"c:", "z:"}


def test_msi_without_shortcuts_falls_back_to_exe(env):
    msi = copy_fixture(env, "prueba.msi")
    assert runner.main([str(msi)]) == 0
    app = config.all_apps()[0]
    call = env.calls()[-1]
    assert call["argv"][0] == "msiexec" and call["argv"][1] == "/i"
    assert call["argv"][2].upper().endswith("\\PRUEBA.MSI")
    assert "DISABLEADVTSHORTCUTS=1" in call["argv"]
    assert len(app.entries) == 1 and app.entries[0].exe.endswith("PruebaMSI/hello.exe")


def test_cancelled_install_removes_new_prefix(env, monkeypatch):
    monkeypatch.setenv("FAKE_CANCEL", "1")
    setup = copy_fixture(env, "TestApp-2.1.0-x64-setup.exe")
    runner.main([str(setup)])
    assert config.all_apps() == []
    assert not any(paths.prefixes_dir().iterdir())


def test_portable_reuses_prefix_per_folder(env):
    folder = env.tmp / "portables" / "hola"
    folder.mkdir(parents=True)
    exe = folder / "hola.exe"
    shutil.copy(FIX / "hello.exe", exe)
    assert runner.main([str(exe), "--flag"]) == 0
    assert runner.main([str(exe)]) == 0
    apps = config.all_apps()
    assert len(apps) == 1 and apps[0].kind == "portable" and apps[0].name == "Hola Mundo"
    last = env.calls()[-1]
    assert last["argv"] == [str(exe)] and last["cwd"] == str(folder)
    assert env.calls()[-2]["argv"] == [str(exe), "--flag"]
    assert last["drives"]["y:"] == str(folder)


def test_exe_and_lnk_inside_prefix(env):
    setup = copy_fixture(env, "TestApp-2.1.0-x64-setup.exe")
    runner.main([str(setup)])
    app = config.all_apps()[0]
    inside = Path(app.entries[0].exe)
    assert runner.main([str(inside)]) == 0
    assert env.calls()[-1]["argv"] == [str(inside)]

    link = app.prefix_path / "drive_c/users/steamuser/Desktop/Test App.lnk"
    assert runner.main([str(link)]) == 0
    assert env.calls()[-1]["argv"] == [str(inside)]
    assert len(config.all_apps()) == 1


def test_foreign_prefix_rejected(env):
    foreign = env.tmp / "lutris-prefix/drive_c/Program Files/X"
    foreign.mkdir(parents=True)
    exe = foreign / "x.exe"
    shutil.copy(FIX / "hello.exe", exe)
    n = len(env.calls())
    assert runner.main([str(exe)]) == 1
    assert len(env.calls()) == n


def test_quick_failure_returns_code(env, monkeypatch):
    exe = copy_fixture(env, "hello.exe", "roto.exe")
    monkeypatch.setenv("FAKE_RC", "3")
    assert runner.main([str(exe)]) == 3


def test_dos_rejected(env):
    exe = copy_fixture(env, "dos.exe")
    assert runner.main([str(exe)]) == 1
    assert env.calls() == []


def test_missing_entry_exe(env):
    setup = copy_fixture(env, "TestApp-2.1.0-x64-setup.exe")
    runner.main([str(setup)])
    app = config.all_apps()[0]
    os.unlink(app.entries[0].exe)
    assert runner.main(["--app", app.id, "--entry", app.entries[0].id]) == 1


def test_windows_path_mapping(env):
    setup = copy_fixture(env, "TestApp-2.1.0-x64-setup.exe")
    runner.main([str(setup)])
    app = config.all_apps()[0]
    p = app.prefix_path
    assert pfx.windows_to_unix(p, r"c:\PROGRAM FILES\testapp\HELLO.EXE") == p / "drive_c/Program Files/TestApp/hello.exe"
    assert pfx.windows_to_unix(p, r"%ProgramFiles%\TestApp\hello.exe") == p / "drive_c/Program Files/TestApp/hello.exe"
    assert pfx.unix_to_windows(p, p / "drive_c/Program Files/TestApp/hello.exe") == r"C:\Program Files\TestApp\hello.exe"
    assert pfx.unix_to_windows(p, env.downloads / "a b.txt").endswith(r":\a b.txt")


def test_cli_list_and_remove(env, capsys):
    from malive import cli

    setup = copy_fixture(env, "TestApp-2.1.0-x64-setup.exe")
    runner.main([str(setup)])
    assert cli.main(["list"]) == 0
    assert "Test App" in capsys.readouterr().out
    app = config.all_apps()[0]
    desktop = Path(app.entries[0].desktop)
    assert cli.main(["remove", app.id, "--yes"]) == 0
    assert not desktop.exists() and not app.prefix_path.exists() and config.all_apps() == []


def test_cli_shortcuts_rescan(env):
    from malive import cli

    setup = copy_fixture(env, "TestApp-2.1.0-x64-setup.exe")
    runner.main([str(setup)])
    app = config.all_apps()[0]
    # un programa nuevo con acceso directo que apareció más tarde
    extra = app.prefix_path / "drive_c/Program Files/TestApp/editor.exe"
    shutil.copy(FIX / "hello.exe", extra)
    sm = app.prefix_path / "drive_c/ProgramData/Microsoft/Windows/Start Menu/Programs"
    pylnk3.for_file(r"C:\Program Files\TestApp\editor.exe", str(sm / "Editor.lnk"))
    assert cli.main(["shortcuts", app.id]) == 0
    assert sorted(e.name for e in config.load_app(app.id).entries) == ["Editor", "Test App"]


def test_desktop_exec_quoting(env, monkeypatch):
    from malive import integrate

    monkeypatch.setenv("MALIVE_RUN_BIN", "/home/Mi Usuario/.local/bin/malive-run")
    setup = copy_fixture(env, "TestApp-2.1.0-x64-setup.exe")
    runner.main([str(setup)])
    text = Path(config.all_apps()[0].entries[0].desktop).read_text()
    assert 'Exec="/home/Mi Usuario/.local/bin/malive-run" --app test-app --entry test-app' in text
    integrate.install_handler()
    assert 'Exec="/home/Mi Usuario/.local/bin/malive-run" %f' in (paths.applications_dir() / "malive.desktop").read_text()


def test_console_app_gets_a_window(env):
    exe = copy_fixture(env, "consola.exe")
    assert runner.main([str(exe), "-v"]) == 0
    assert env.calls()[-1]["argv"] == ["start", "/wait", "/unix", str(exe), "-v"]


def test_uninstall_from_menu_action(env, monkeypatch):
    from malive import ui

    setup = copy_fixture(env, "TestApp-2.1.0-x64-setup.exe")
    runner.main([str(setup)])
    app = config.all_apps()[0]
    desktop = Path(app.entries[0].desktop)
    text = desktop.read_text()
    assert "Actions=malive-uninstall;" in text
    assert "[Desktop Action malive-uninstall]" in text
    assert "Name=Uninstall Test App" in text and "Name[es]=Desinstalar Test App" in text
    assert f"Exec=/opt/test/malive-run --uninstall {app.id}" in text

    # el usuario dice que no
    monkeypatch.setattr(ui, "question", lambda *a, **k: False)
    assert runner.main(["--uninstall", app.id]) == 1
    assert desktop.exists() and app.prefix_path.exists()

    # y luego que sí
    monkeypatch.setattr(ui, "question", lambda *a, **k: True)
    assert runner.main(["--uninstall", app.id]) == 0
    assert not desktop.exists() and not app.prefix_path.exists() and config.all_apps() == []
    assert not list(paths.icons_dir().glob(f"{app.id}-*"))

    # ya desinstalada: aviso, sin traza
    assert runner.main(["--uninstall", app.id]) == 1


def test_refresh_rewrites_old_desktop_files(env):
    from malive import cli

    setup = copy_fixture(env, "TestApp-2.1.0-x64-setup.exe")
    runner.main([str(setup)])
    desktop = Path(config.all_apps()[0].entries[0].desktop)
    desktop.write_text("[Desktop Entry]\nName=viejo\n")  # como los de la 0.1.2
    assert cli.main(["refresh"]) == 0
    assert "Actions=malive-uninstall;" in desktop.read_text()


def test_new_version_reuses_prefix_of_old_name(env):
    # app instalada con una versión anterior que guardó el nombre con versión
    old = config.App(id="putty-release-0-85-64-bit", name="PuTTY release 0.85 (64-bit)",
                     kind="installed", prefix="/x", proton="GE-Proton10-1")
    old.save()
    assert config.find_installed_by_name("PuTTY") is not None
    assert config.find_installed_by_name("PuTTY release 0.86 (64-bit)").id == old.id
    assert config.find_installed_by_name("PuTTYgen") is None


def test_refresh_menus_waits_for_kbuildsycoca(env, monkeypatch):
    from malive import shortcuts

    calls = []
    monkeypatch.setattr(shortcuts.shutil, "which", lambda c: f"/usr/bin/{c}")

    class R:
        returncode = 0
        stderr = ""

    def fake_run(cmd, **kw):
        calls.append(" ".join([cmd[0].rsplit("/", 1)[-1], *cmd[1:2]]))
        assert "timeout" in kw  # síncrono, no Popen en segundo plano
        return R()

    monkeypatch.setattr(shortcuts.subprocess, "run", fake_run)
    shortcuts.refresh_menus()
    apps = str(paths.applications_dir())
    # Reconstrucción completa: la incremental no redibuja el menú de Plasma
    assert calls == [f"update-desktop-database {apps}", "kbuildsycoca6 --noincremental"]


def test_shortcut_appears_while_launched_app_is_still_open(env, monkeypatch):
    """Instalador con «Ejecutar X al terminar» marcado (lo típico): el acceso
    debe aparecer en el menú sin esperar a que el usuario cierre el programa."""
    import threading
    import time

    monkeypatch.setenv("FAKE_APP_RUNS", "12")
    setup = copy_fixture(env, "TestApp-2.1.0-x64-setup.exe")
    result = {}
    t = threading.Thread(target=lambda: result.setdefault("rc", runner.main([str(setup)])))
    t.start()
    desktop = paths.applications_dir() / "malive-test-app-test-app.desktop"
    deadline = time.monotonic() + 9
    while not desktop.exists() and time.monotonic() < deadline:
        time.sleep(0.2)
    assert desktop.exists(), "el acceso no apareció mientras el programa seguía abierto"
    assert t.is_alive()  # el «programa» sigue abierto
    t.join(30)
    assert result["rc"] == 0
    apps = config.all_apps()
    assert len(apps) == 1 and [e.name for e in apps[0].entries] == ["Test App"]  # sin duplicados


def test_old_entries_with_null_argument_still_launch(env):
    setup = copy_fixture(env, "TestApp-2.1.0-x64-setup.exe")
    runner.main([str(setup)])
    app = config.all_apps()[0]
    app.entries[0].args = ["\x00"]  # como quedó guardado en la 0.1.4
    app.save()
    assert runner.main(["--app", app.id, "--entry", app.entries[0].id]) == 0
    assert env.calls()[-1]["argv"] == [app.entries[0].exe]


def test_menu_rebuild_uses_desktop_language(env, monkeypatch):
    """Desde el menú o el temporizador MaLiVe puede no tener LANG; kbuildsycoca6
    debe usar el idioma de Plasma para reconstruir la base de datos que Plasma lee."""
    from malive import shortcuts

    monkeypatch.delenv("LANG", raising=False)
    monkeypatch.delenv("LANGUAGE", raising=False)
    monkeypatch.delenv("XDG_MENU_PREFIX", raising=False)
    monkeypatch.setattr(shortcuts, "desktop_shell_env",
                        lambda: {"LANG": "es_ES.UTF-8", "LANGUAGE": "", "XDG_DATA_DIRS": "/usr/share",
                                 "XDG_MENU_PREFIX": "plasma-"})
    monkeypatch.setattr(shortcuts.shutil, "which", lambda c: f"/usr/bin/{c}")
    seen = []

    class R:
        returncode = 0
        stderr = ""

    def fake_run(cmd, **kw):
        seen.append((cmd[0].rsplit("/", 1)[-1], kw["env"].get("LANG"), kw["env"].get("XDG_DATA_DIRS"),
                     kw["env"].get("XDG_MENU_PREFIX"), "PATH" in kw["env"]))
        return R()

    monkeypatch.setattr(shortcuts.subprocess, "run", fake_run)
    shortcuts.refresh_menus()
    # El entorno de Plasma entero: idioma, carpetas y, sobre todo, XDG_MENU_PREFIX
    assert ("kbuildsycoca6", "es_ES.UTF-8", "/usr/share", "plasma-", True) in seen


def test_desktop_shell_env_reads_own_process(env, monkeypatch):
    from malive import shortcuts

    import os
    me = open(f"/proc/{os.getpid()}/comm").read().strip()
    found = shortcuts.desktop_shell_env(names=(me,))
    # /proc/<pid>/environ es el entorno con el que arrancó el proceso
    assert found and "PATH" in found
    assert shortcuts.desktop_shell_env(names=("no-existe-este-proceso",)) is None
