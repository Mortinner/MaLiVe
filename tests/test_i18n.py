import pytest

from malive import i18n


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    for var in ("MALIVE_LANG", "LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(i18n.session, "desktop_shell_env", lambda: None)
    i18n.reset()
    yield
    i18n.reset()


@pytest.mark.parametrize(
    "env, expected",
    [
        ({"LANG": "es_ES.UTF-8"}, "es"),
        ({"LANG": "es_MX.UTF-8"}, "es"),
        ({"LANG": "en_US.UTF-8"}, "en"),
        ({"LANG": "de_DE.UTF-8"}, "en"),
        ({"LANG": "fr_FR.UTF-8", "LANGUAGE": "es:fr"}, "es"),
        ({"LANG": "es_ES.UTF-8", "LANGUAGE": ""}, "es"),  # LANGUAGE vacío, como en Plasma
        ({"LANG": "es_ES.UTF-8", "LC_MESSAGES": "en_GB.UTF-8"}, "en"),
        ({"LANG": "C.UTF-8"}, "en"),
        ({}, "en"),
    ],
)
def test_language_detection(monkeypatch, env, expected):
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    assert i18n.lang() == expected


def test_falls_back_to_desktop_language(monkeypatch):
    # Lanzado desde el menú o el temporizador sin LANG: se usa el de Plasma
    monkeypatch.setattr(i18n.session, "desktop_shell_env", lambda: {"LANG": "es_ES.UTF-8"})
    assert i18n.lang() == "es"


def test_forced(monkeypatch):
    monkeypatch.setenv("LANG", "es_ES.UTF-8")
    monkeypatch.setenv("MALIVE_LANG", "en")
    assert i18n.T("hola", "hello") == "hello"


def test_english_messages_end_to_end(env, monkeypatch, capsys):
    """Un mensaje real del lanzador sale en inglés con el sistema en alemán."""
    from malive import runner

    monkeypatch.delenv("MALIVE_LANG", raising=False)
    monkeypatch.setenv("LANG", "de_DE.UTF-8")
    i18n.reset()
    assert runner.main(["/no/existe.exe"]) == 1
    assert "File not found" in capsys.readouterr().err
