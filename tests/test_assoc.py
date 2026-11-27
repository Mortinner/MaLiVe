from malive import assoc

REG = r'''WINE REGISTRY Version 2
;; All keys relative to \\Machine

[Software\\Classes\\.txt] 1700000000
#time=1da
@="Notepad++_file"
"Content Type"="text/plain"

[Software\\Classes\\.nfo] 1700000000
@="Notepad++_file"

[Software\\Classes\\Notepad++_file\\shell\\open\\command] 1700000000
@="\"C:\\Program Files\\Notepad++\\notepad++.exe\" \"%1\""

[Software\\Classes\\.log\\OpenWithProgids] 1700000000
"Notepad++_file"=""

[Software\\Classes\\Applications\\notepad++.exe\\SupportedTypes] 1700000000
".ini"=""

[Software\\Classes\\*\\shell\\Edit with Notepad++\\command] 1700000000
@="\"C:\\Program Files\\Notepad++\\notepad++.exe\" \"%1\""

[Software\\Classes\\.doc] 1700000000
@="Word.Document"

[Software\\Classes\\Word.Document\\shell\\open\\command] 1700000000
@="\"C:\\Program Files\\Office\\winword.exe\" \"%1\""

[Software\\Wine\\Something] 1700000000
@="ignored"
'''


def test_extensions_from_registry(tmp_path):
    (tmp_path / "system.reg").write_text(REG)
    classes = assoc.prefix_classes(tmp_path)
    exts = assoc.extensions_for(classes, r"C:\Program Files\Notepad++\notepad++.exe")
    # .txt/.nfo por ProgID, .log por OpenWithProgids, .ini por SupportedTypes;
    # «*» (todos los ficheros) y .doc (otro programa) no
    assert exts == {".txt", ".nfo", ".log", ".ini"}


def test_user_reg_overrides_machine(tmp_path):
    (tmp_path / "system.reg").write_text(REG)
    (tmp_path / "user.reg").write_text(
        'WINE REGISTRY Version 2\n\n[Software\\\\Classes\\\\.txt] 1700000000\n@="OtroPrograma"\n'
    )
    exts = assoc.extensions_for(assoc.prefix_classes(tmp_path), r"C:\Program Files\Notepad++\notepad++.exe")
    assert ".txt" not in exts and ".nfo" in exts


def test_mimetypes_with_system_globs(tmp_path, monkeypatch):
    (tmp_path / "system.reg").write_text(REG)
    mime = tmp_path / "share" / "mime"
    mime.mkdir(parents=True)
    (mime / "globs2").write_text(
        "# comentario\n50:text/plain:*.txt\n50:text/x-nfo:*.nfo\n50:text/x-log:*.log\n"
        "50:text/x-ini:*.ini\n50:application/msword:*.doc\n50:application/octet-stream:*.bin\n"
    )
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "nada"))
    monkeypatch.setenv("XDG_DATA_DIRS", str(tmp_path / "share"))
    mimes = assoc.mimetypes_for(tmp_path, r"C:\Program Files\Notepad++\notepad++.exe")
    assert mimes == ["text/plain", "text/x-ini", "text/x-log", "text/x-nfo"]


def test_no_registry_no_mimetypes(tmp_path):
    assert assoc.mimetypes_for(tmp_path, r"C:\x\y.exe") == []
    assert assoc.mimetypes_for(tmp_path, None) == []


def test_unescape_unicode():
    # Wine escribe 4 cifras cuando le sigue un carácter hexadecimal: «ña» → \x00f1a
    assert assoc._unescape(r'C:\\Archivos\\Espa\x00f1a') == "C:\\Archivos\\España"
    assert assoc._unescape(r'Espa\xf1') == "Españ"


def test_categories_for():
    from malive.assoc import categories_for
    assert categories_for(["audio/mpeg", "video/x-matroska"]) == "AudioVideo;Video;Player;"
    assert categories_for(["audio/flac"]) == "AudioVideo;Audio;Player;"
    assert categories_for(["image/png"]) == "Graphics;Viewer;"
    assert categories_for(["application/x-7z-compressed", "application/zip"]) == "Utility;Archiving;Compression;"
    assert categories_for(["text/plain"]) == "Utility;TextEditor;"
    assert categories_for([]) == "Utility;"
    assert categories_for(["application/x-keepass2"]) == "Utility;"
