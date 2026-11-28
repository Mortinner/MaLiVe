import pytest
from pathlib import Path

from conftest import FIX
from malive import detect


@pytest.mark.parametrize(
    "name, kind, subtype, product",
    [
        ("hello.exe", "app", None, "Hola Mundo"),
        ("TestApp-2.1.0-x64-setup.exe", "installer", "nsis", "Test App"),
        ("prueba.msi", "msi", "msi", None),  # nombre depende de msitools
        ("inno-fake-setup.exe", "installer", "inno", "Hola Mundo"),
        ("dos.exe", "dos", None, None),
    ],
)
def test_fixtures(name, kind, subtype, product):
    d = detect.detect(FIX / name)
    assert d.kind == kind
    assert d.subtype == subtype
    if product:
        assert d.name == product


def test_msi_name_or_filename():
    d = detect.detect(FIX / "prueba.msi")
    assert d.name in ("Prueba MSI", "prueba")


def test_setup_name_hint(tmp_path):
    f = tmp_path / "MiPrograma_Setup_v3.exe"
    f.write_bytes((FIX / "hello.exe").read_bytes())
    d = detect.detect(f)
    assert d.kind == "installer" and d.subtype == "generic"


def test_not_windows(tmp_path):
    f = tmp_path / "x.exe"
    f.write_bytes(b"\x7fELF" + b"\0" * 100)
    assert detect.detect(f).kind == "unsupported"


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("npp.8.6.2.Installer.x64.exe", "npp"),
        ("Foo Bar Setup 1.2.exe", "Foo Bar"),
        ("GIMP-2.10.38-setup-1.exe", "GIMP"),
        ("vlc-3.0.21-win64.exe", "vlc"),
        ("setup.exe", "setup"),
    ],
)
def test_clean_name(raw, expected):
    assert detect.clean_name(raw) == expected


def test_generic_setup_uses_folder_name(tmp_path):
    d = tmp_path / "Office 2010"
    d.mkdir()
    f = d / "setup.exe"
    f.write_bytes(b"MZ" + b"\0" * 200)  # sin cabecera PE: el nombre no depende de ella
    assert detect.name_from_path(f) == "Office"


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("PuTTY release 0.85 (64-bit)", "PuTTY"),
        ("7-Zip 24.08 (x64 edition)", "7-Zip"),
        ("Notepad++ (64-bit x64)", "Notepad++"),
        ("VLC media player", "VLC media player"),
        ("Microsoft Office 2010", "Microsoft Office 2010"),
        ("KeePass Password Safe 2.57.1", "KeePass Password Safe"),
        ("Audacity 3.7.1 Setup", "Audacity"),
        ("Format Factory", "Format Factory"),
        ("FormatFactory v5.18.0", "FormatFactory"),
    ],
)
def test_clean_product_name(raw, expected):
    assert detect.clean_product_name(raw) == expected


def test_generic_windows_product_name_falls_back_to_file_name():
    from malive import detect
    v = {"ProductName": "Microsoft® Windows® Operating System", "FileDescription": "Windows® installer"}
    assert detect._product_name(v, Path("/d/Rockstar-Games-Launcher.exe")) == "Rockstar Games Launcher"
