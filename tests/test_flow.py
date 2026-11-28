"""Flujo completo con umu-run falso: instalar, lanzar, reutilizar, cancelar..."""

import os
import shutil
from pathlib import Path

import pylnk3

from conftest import FIX
from malive import config, paths, prefix as pfx, runner, ui


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
    # Cada programa con su clase de ventana (su botón en la barra de tareas):
    # el Wine de Proton usa «steam_app_<SteamAppId>», y umu toma el id de GAMEID
    assert "StartupWMClass=steam_app_malive_test_app_test_app" in desktop
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
    assert f"Exec=/opt/test/malive-run --uninstall {app.id} --entry {app.entries[0].id}" in text

    # el usuario dice que no
    monkeypatch.setattr(ui, "question", lambda *a, **k: False)
    assert runner.main(["--uninstall", app.id]) == 1
    assert desktop.exists() and app.prefix_path.exists()

    # y luego que sí
    monkeypatch.setattr(ui, "question", lambda *a, **k: True)
    assert runner.main(["--uninstall", app.id]) == 0
    assert not desktop.exists() and not app.prefix_path.exists() and config.all_apps() == []
    assert not list(paths.icons_dir().glob(f"{app.id}-*"))  # nada fuera del contenedor

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
    # Reconstrucción completa (la incremental no redibuja el menú de Plasma),
    # y otras dos programadas con systemd-run unos segundos después
    assert calls == [f"update-desktop-database {apps}", "kbuildsycoca6 --noincremental",
                     "systemd-run --user", "systemd-run --user"]


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


def test_delayed_rebuilds_use_systemd_timer_with_desktop_env(env, monkeypatch):
    from malive import shortcuts

    monkeypatch.setattr(shortcuts.shutil, "which", lambda c: f"/usr/bin/{c}")
    cmds = []

    class R:
        returncode = 0
        stderr = ""

    monkeypatch.setattr(shortcuts.subprocess, "run", lambda cmd, **kw: (cmds.append(cmd), R())[1])
    env_desk = {"LANG": "es_ES.UTF-8", "XDG_MENU_PREFIX": "plasma-", "PATH": "/usr/bin", "SECRET": "x"}
    shortcuts.schedule_delayed_rebuilds("/usr/bin/kbuildsycoca6", env_desk, delays=(5, 30))
    assert len(cmds) == 2
    first = cmds[0]
    assert first[:3] == ["/usr/bin/systemd-run", "--user", "--quiet"]
    assert "--on-active=5s" in first and "--on-active=30s" in cmds[1]
    assert "--setenv=XDG_MENU_PREFIX=plasma-" in first and "--setenv=LANG=es_ES.UTF-8" in first
    assert not any("SECRET" in a for a in first)  # solo las variables necesarias
    assert first[-2:] == ["/usr/bin/kbuildsycoca6", "--noincremental"]


def _fake_mime_db(env):
    mime = env.home / ".local/share/mime"
    mime.mkdir(parents=True, exist_ok=True)
    (mime / "globs2").write_text("50:text/plain:*.txt\n")


def test_installed_app_gets_mimetype_and_opens_files(env):
    _fake_mime_db(env)
    setup = copy_fixture(env, "TestApp-2.1.0-x64-setup.exe")
    runner.main([str(setup)])
    app = config.all_apps()[0]
    entry = app.entries[0]
    text = Path(entry.desktop).read_text()
    assert f"--app {app.id} --entry {entry.id} %F" in text
    assert "MimeType=text/plain;" in text
    assert "Categories=Utility;TextEditor;" in text

    # «Abrir con» un fichero de Descargas (compartida como E:)
    doc = env.downloads / "nota.txt"
    doc.write_text("hola")
    assert runner.main(["--app", app.id, "--entry", entry.id, str(doc)]) == 0
    assert env.calls()[-1]["argv"] == [entry.exe, "E:\\nota.txt"]

    # Un fichero fuera de las carpetas compartidas: su carpeta se monta como Y:
    other = env.tmp / "usb"
    other.mkdir()
    (other / "otra.txt").write_text("x")
    assert runner.main(["--app", app.id, "--entry", entry.id, str(other / "otra.txt")]) == 0
    last = env.calls()[-1]
    assert last["argv"] == [entry.exe, "Y:\\otra.txt"] and last["drives"]["y:"] == str(other)
    # …y esa carpeta (fuera de $HOME, p. ej. un NAS) se expone al contenedor de Proton
    assert str(other.resolve()) in last["pv_rw"].split(":")
    # Las carpetas de dentro de $HOME no hace falta exponerlas
    assert runner.main(["--app", app.id, "--entry", entry.id, str(doc)]) == 0
    assert env.calls()[-1]["pv_rw"] == ""


def test_web_installer_detected_as_app_still_gets_menu_entries(env):
    """Lanzadores tipo «aw_launcher.exe»: no parecen instaladores, pero
    descargan e instalan el juego. Sus accesos deben llegar al menú."""
    launcher = env.downloads / "aw_launcher.exe"
    shutil.copy(FIX / "hello.exe", launcher)
    assert runner.main([str(launcher)]) == 0
    apps = config.all_apps()
    assert len(apps) == 1 and apps[0].kind == "portable"
    names = sorted(e.name for e in apps[0].entries)
    assert names == ["Game", "Game Launcher"]
    by_name = {e.name: e for e in apps[0].entries}
    # En C:\\Games → sección Juegos; el otro no tiene pistas de juego
    assert "Categories=Game;" in Path(by_name["Game Launcher"].desktop).read_text()
    assert "Categories=Utility;" in Path(by_name["Game"].desktop).read_text()
    assert Path(apps[0].entries[0].desktop).is_file()
    # Volver a ejecutar el lanzador reutiliza el mismo contenedor
    assert runner.main([str(launcher)]) == 0
    assert len(config.all_apps()) == 1 and len(config.load_app(apps[0].id).entries) == 2


def test_desktop_defaults_and_no_stray_drives(env):
    """Sin Xalia ni unidad S:, y con aislamiento no quedan letras que vean $HOME."""
    setup = copy_fixture(env, "TestApp-2.1.0-x64-setup.exe")
    assert runner.main([str(setup)]) == 0
    app = config.all_apps()[0]
    dd = app.prefix_path / "dosdevices"
    (dd / "x:").symlink_to(Path.home())   # como deja winetricks
    (dd / "s:").symlink_to("/home")       # como deja Proton
    assert runner.main(["--app", app.id, "--entry", app.entries[0].id]) == 0
    call = env.calls()[-1]
    assert call["xalia"] == "0" and call["gamedrive"] == "0"
    assert "x:" not in call["drives"] and "s:" not in call["drives"]
    assert "d:" in call["drives"] and "c:" in call["drives"]


def test_looks_like_game(tmp_path):
    from malive.shortcuts import looks_like_game
    d = tmp_path / "Program Files" / "Something"
    d.mkdir(parents=True)
    exe = d / "app.exe"
    exe.write_bytes(b"MZ")
    assert not looks_like_game(exe)
    (d / "UnityPlayer.dll").write_bytes(b"MZ")
    assert looks_like_game(exe)
    assert looks_like_game(tmp_path / "drive_c/Games/Launcher.exe")
    assert looks_like_game(tmp_path / "x/MyGame-Win64-Shipping.exe")


def test_icon_found_through_paths_in_app_logs(env, monkeypatch):
    """El .exe del lanzador no trae icono; el juego (y su GameIcon.ico) está
    donde eligió el usuario, y el lanzador lo anota en su registro."""
    from malive import mounts, shortcuts
    data = env.tmp / "DataLinux"
    game = data / "Games" / "Armored Warfare MALIVE"
    game.mkdir(parents=True)
    (game / "GameIcon.ico").write_bytes(b"\x00\x00\x01\x00" + b"\x00" * 60)
    monkeypatch.setattr(mounts, "data_mounts", lambda *a, **k: [data])
    monkeypatch.setattr(shortcuts, "extract_icon",
                        lambda src, dest, index=0: str(src) if Path(src).suffix == ".ico" else None)
    launcher = env.downloads / "aw_launcher.exe"
    shutil.copy(FIX / "hello.exe", launcher)
    assert runner.main([str(launcher)]) == 0
    app = config.all_apps()[0]
    entry = next(e for e in app.entries if e.name == "Game Launcher")
    assert entry.icon is None  # todavía no hay juego descargado
    # El lanzador descarga el juego en M: y lo anota en su log (JSON escapado)
    logs = app.prefix_path / "drive_c/users/steamuser/AppData/Local/Launcher/Logs"
    logs.mkdir(parents=True)
    (logs / "app.log").write_text('return value \'{"APP":"M:\\\\Games\\\\Armored Warfare MALIVE","success":true}\'\n')
    # El siguiente arranque reintenta los iconos que faltan
    assert runner.main(["--app", app.id, "--entry", entry.id]) == 0
    entry = next(e for e in config.load_app(app.id).entries if e.name == "Game Launcher")
    assert entry.icon == str(game / "GameIcon.ico")
    assert f"Icon={entry.icon}" in Path(entry.desktop).read_text()


def test_icon_from_uninstall_registry_and_leveldb(env, monkeypatch):
    from malive import mounts, shortcuts
    data = env.tmp / "DataLinux"
    a = data / "Games" / "A"
    b = data / "Games" / "B"
    for d in (a, b):
        d.mkdir(parents=True)
    (a / "GameIcon.ico").write_bytes(b"\x00\x00\x01\x00")
    (b / "b.ico").write_bytes(b"\x00\x00\x01\x00")
    monkeypatch.setattr(mounts, "data_mounts", lambda *a, **k: [data])
    launcher = env.downloads / "aw_launcher.exe"
    shutil.copy(FIX / "hello.exe", launcher)
    assert runner.main([str(launcher)]) == 0
    app = config.all_apps()[0]
    # Registro de Windows: «Desinstalar programas» con DisplayIcon
    with open(app.prefix_path / "system.reg", "a") as reg:
        reg.write('\n[Software\\\\Microsoft\\\\Windows\\\\CurrentVersion\\\\Uninstall\\\\Game A] 1700000000\n'
                  '"DisplayIcon"="M:\\\\Games\\\\A\\\\GameIcon.ico,0"\n')
    assert a / "GameIcon.ico" in shortcuts.icons_from_app_data(app.prefix_path)
    # Ajustes de un lanzador Chromium (Local Storage/leveldb, binario)
    ldb = app.prefix_path / "drive_c/users/steamuser/AppData/Local/L/Local Storage/leveldb"
    ldb.mkdir(parents=True)
    (ldb / "000005.ldb").write_bytes(b"\x00\x01installPath\x00M:\\\\Games\\\\B\x00\xff")
    assert b / "b.ico" in shortcuts.icons_from_app_data(app.prefix_path)


def test_refresh_also_finds_missing_icons(env, monkeypatch):
    from malive import cli, mounts, shortcuts
    data = env.tmp / "DataLinux"
    game = data / "Games" / "G"
    game.mkdir(parents=True)
    (game / "GameIcon.ico").write_bytes(b"\x00\x00\x01\x00")
    monkeypatch.setattr(mounts, "data_mounts", lambda *a, **k: [data])
    monkeypatch.setattr(shortcuts, "extract_icon",
                        lambda src, dest, index=0: str(src) if Path(src).suffix == ".ico" else None)
    launcher = env.downloads / "aw_launcher.exe"
    shutil.copy(FIX / "hello.exe", launcher)
    assert runner.main([str(launcher)]) == 0
    app = config.all_apps()[0]
    ldb = app.prefix_path / "drive_c/users/steamuser/AppData/Local/L/Default/Local Storage/leveldb"
    ldb.mkdir(parents=True)
    (ldb / "000003.log").write_bytes(b"\x01installPath\x00M:\\\\Games\\\\G\x00")
    assert cli.main(["refresh"]) == 0
    entry = next(e for e in config.load_app(app.id).entries if e.name == "Game Launcher")
    assert entry.icon == str(game / "GameIcon.ico")


def test_icons_live_inside_the_container(env, monkeypatch):
    from malive import shortcuts
    monkeypatch.setattr(shortcuts, "extract_icon",
                        lambda src, dest, index=0: (dest.parent.mkdir(parents=True, exist_ok=True),
                                                    dest.with_suffix(".png").write_bytes(b"png"),
                                                    str(dest.with_suffix(".png")))[2])
    setup = copy_fixture(env, "TestApp-2.1.0-x64-setup.exe")
    assert runner.main([str(setup)]) == 0
    app = config.all_apps()[0]
    entry = app.entries[0]
    assert entry.icon == str(app.prefix_path / "drive_c" / "icons" / f"{entry.id}.png")
    # Iconos de versiones anteriores se mudan a C:\icons al refrescar
    legacy = paths.icons_dir() / f"{app.id}-{entry.id}.png"
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_bytes(b"old")
    Path(entry.icon).unlink()
    entry.icon = str(legacy)
    app.save()
    from malive import cli
    assert cli.main(["refresh"]) == 0
    entry = config.load_app(app.id).entries[0]
    assert entry.icon == str(app.prefix_path / "drive_c" / "icons" / f"{entry.id}.png")
    assert not legacy.exists() and Path(entry.icon).read_bytes() == b"old"


def test_known_game_launchers_and_user_category(tmp_path, env):
    from malive.shortcuts import looks_like_game
    bn = tmp_path / "drive_c/Program Files (x86)/Battle.net/Battle.net Launcher.exe"
    assert looks_like_game(bn)
    assert looks_like_game(tmp_path / "x/EpicGamesLauncher.exe")
    assert not looks_like_game(tmp_path / "drive_c/Program Files/Notepad++/notepad++.exe")
    # El usuario manda: malive category
    from malive import cli
    setup = copy_fixture(env, "TestApp-2.1.0-x64-setup.exe")
    assert runner.main([str(setup)]) == 0
    app = config.all_apps()[0]
    assert cli.main(["category", app.id, "juegos"]) == 0
    text = Path(config.load_app(app.id).entries[0].desktop).read_text()
    assert "Categories=Game;" in text
    assert cli.main(["refresh"]) == 0  # se respeta al rehacer los accesos
    assert "Categories=Game;" in Path(config.load_app(app.id).entries[0].desktop).read_text()
    assert cli.main(["category", app.id, "auto"]) == 0
    assert "Categories=Game;" not in Path(config.load_app(app.id).entries[0].desktop).read_text()


def test_two_web_installers_in_same_folder_get_separate_containers(env):
    """Rockstar se coló en el contenedor de Armored Warfare: ambos lanzadores
    estaban en la misma carpeta de Descargas."""
    a = env.downloads / "aw_launcher.exe"
    b = env.downloads / "rockstar.exe"
    shutil.copy(FIX / "hello.exe", a)
    shutil.copy(FIX / "hello.exe", b)
    assert runner.main([str(a)]) == 0
    assert runner.main([str(b)]) == 0
    apps = config.all_apps()
    assert len(apps) == 2 and apps[0].prefix != apps[1].prefix
    # Volver a ejecutar el primero reutiliza su contenedor
    assert runner.main([str(a)]) == 0
    assert len(config.all_apps()) == 2


def test_unlist_one_entry(env):
    from malive import cli
    launcher = env.downloads / "aw_launcher.exe"
    shutil.copy(FIX / "hello.exe", launcher)
    assert runner.main([str(launcher)]) == 0
    app = config.all_apps()[0]
    entry = next(e for e in app.entries if e.name == "Game")
    assert cli.main(["unlist", app.id, "Game"]) == 0
    app = config.load_app(app.id)
    assert [e.name for e in app.entries] == ["Game Launcher"]
    assert not Path(entry.desktop).exists()


def test_uninstall_one_program_keeps_the_others_and_container(env, monkeypatch):
    """Como en Windows: desinstalar Diablo IV no se lleva Battle.net; el
    contenedor solo se borra con el último programa."""
    monkeypatch.setattr(ui, "question", lambda *a, **k: True)
    launcher = env.downloads / "aw_launcher.exe"
    shutil.copy(FIX / "hello.exe", launcher)
    assert runner.main([str(launcher)]) == 0
    app = config.all_apps()[0]
    game = next(e for e in app.entries if e.name == "Game")
    keep = next(e for e in app.entries if e.name == "Game Launcher")
    assert f"--uninstall {app.id} --entry {game.id}" in Path(game.desktop).read_text()
    assert "Name[es]=Desinstalar Game" in Path(game.desktop).read_text()

    assert runner.main(["--uninstall", app.id, "--entry", game.id]) == 0
    # Se usó su desinstalador (registrado en «Agregar o quitar programas»)
    assert env.calls()[-1]["argv"][0].endswith("Program Files/Game/uninst.exe")
    assert not Path(game.exe).exists() and not Path(game.desktop).exists()
    app = config.load_app(app.id)
    assert [e.name for e in app.entries] == ["Game Launcher"] and app.prefix_path.exists()
    assert Path(keep.desktop).exists()

    # El último programa se lleva el contenedor
    assert runner.main(["--uninstall", app.id, "--entry", keep.id]) == 0
    assert config.all_apps() == [] and not app.prefix_path.exists()


def test_split_uninstall_commands():
    from malive.uninstallers import split_command
    assert split_command('"C:\\Program Files (x86)\\Battle.net\\Battle.net Uninstaller.exe" --lang=enUS '
                         '--uid=fenris --displayname="Diablo IV"') == (
        "C:\\Program Files (x86)\\Battle.net\\Battle.net Uninstaller.exe",
        ["--lang=enUS", "--uid=fenris", "--displayname=Diablo IV"])
    assert split_command("MsiExec.exe /X{1234-ABCD}") == ("MsiExec.exe", ["/X{1234-ABCD}"])
    assert split_command("C:\\Games\\x\\unins000.exe /SILENT") == ("C:\\Games\\x\\unins000.exe", ["/SILENT"])


def test_each_program_gets_its_own_window_class(env):
    """Antes todos usaban GAMEID=umu-default → ventanas «steam_app_default» y
    un solo botón en la barra de tareas para todos los programas."""
    launcher = env.downloads / "aw_launcher.exe"
    shutil.copy(FIX / "hello.exe", launcher)
    assert runner.main([str(launcher)]) == 0
    app = config.all_apps()[0]
    ids = set()
    for e in app.entries:
        assert runner.main(["--app", app.id, "--entry", e.id]) == 0
        gameid = env.calls()[-1]["gameid"]
        assert gameid == f"umu-{pfx.window_id(app, e.id)}"
        assert f"StartupWMClass=steam_app_{gameid[4:]}" in Path(e.desktop).read_text()
        ids.add(gameid)
    assert len(ids) == len(app.entries) > 1
