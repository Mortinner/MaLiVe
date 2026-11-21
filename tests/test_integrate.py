from malive import integrate, paths


def test_mimeapps_roundtrip(env):
    mf = paths.mimeapps_file()
    original = (
        "[Default Applications]\n"
        "text/plain=org.kde.kate.desktop;\n"
        "application/x-ms-dos-executable=wine.desktop;\n"
        "\n"
        "[Added Associations]\n"
        "application/x-msdownload=wine.desktop;\n"
    )
    mf.write_text(original)
    integrate.install_handler()
    text = mf.read_text()
    assert "text/plain=org.kde.kate.desktop;" in text
    assert "application/x-ms-dos-executable=malive.desktop;" in text
    assert "application/x-msdownload=malive.desktop;wine.desktop;" in text
    assert integrate.is_integrated()
    handler = (paths.applications_dir() / "malive.desktop").read_text()
    assert "Exec=/opt/test/malive-run %f" in handler
    assert "application/x-msi;" in handler

    integrate.remove_handler()
    text = mf.read_text()
    assert "application/x-ms-dos-executable=wine.desktop;" in text
    assert "malive.desktop" not in text
    assert "text/plain=org.kde.kate.desktop;" in text
    assert "application/x-msdownload=wine.desktop;" in text
    assert not integrate.is_integrated()


def test_mimeapps_created_from_scratch(env):
    integrate.install_handler()
    text = paths.mimeapps_file().read_text()
    assert text.startswith("[Default Applications]")
    integrate.remove_handler()
    assert "malive" not in paths.mimeapps_file().read_text()
