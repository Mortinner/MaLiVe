"""Plantillas de contenedor: Template_Mono.mlv + Template_Mono_to_use.mlv."""

import shutil

import pytest

from conftest import FIX
from malive import cli, config, runner, templates
from malive import prefix as pfx

PROTON = "GE-Proton10-1"


@pytest.fixture
def tpl(env, monkeypatch):
    monkeypatch.delenv("MALIVE_NO_TEMPLATES", raising=False)
    monkeypatch.setenv("MALIVE_TEMPLATES_SYNC", "1")
    return env


def _install(env):
    setup = env.downloads / "TestApp-2.1.0-x64-setup.exe"
    shutil.copy(FIX / "TestApp-2.1.0-x64-setup.exe", setup)
    assert runner.main([str(setup)]) == 0
    return config.all_apps()[0]


def test_build_creates_template_and_spare(tpl):
    assert cli.main(["template", "build"]) == 0
    base, spare = templates.base_path(PROTON), templates.spare_path(PROTON)
    assert base.name == "Template_Mono.mlv" and spare.name == "Template_Mono_to_use.mlv"
    assert templates.base_ready(PROTON) and templates.spare_ready(PROTON)
    assert pfx.is_created(base) and pfx.is_created(spare)
    # Se instalaron los componentes comunes
    assert any(c["argv"][:2] == ["winetricks", "-q"] and "vcrun2022" in c["argv"] for c in tpl.calls())


def test_new_app_takes_spare_instantly_and_spare_is_refilled(tpl):
    templates.build(PROTON)
    (templates.spare_path(PROTON) / "drive_c" / "marca-repuesto").write_text("x")
    n = len(tpl.calls())
    app = _install(tpl)
    # Ni «umu-run ""» ni winetricks: solo el instalador del programa
    new_calls = tpl.calls()[n:]
    assert [c["argv"] for c in new_calls] == [[str(tpl.downloads / "TestApp-2.1.0-x64-setup.exe")]]
    assert (app.prefix_path / "drive_c" / "marca-repuesto").exists()  # era el repuesto
    assert not (app.prefix_path / templates.MARKER).exists()
    # Z: apunta a la carpeta vacía del contenedor nuevo, no de la plantilla
    assert pfx.drives(app.prefix_path)["z"] == (app.prefix_path / pfx.EMPTY_DIR).resolve()
    # Hay un repuesto nuevo para el siguiente
    assert templates.spare_ready(PROTON)
    assert not (templates.spare_path(PROTON) / "drive_c" / "marca-repuesto").exists()


def test_without_spare_copies_template(tpl):
    templates.build(PROTON)
    shutil.rmtree(templates.spare_path(PROTON))
    n = len(tpl.calls())
    app = _install(tpl)
    assert pfx.is_created(app.prefix_path)
    assert [c["argv"][0] for c in tpl.calls()[n:]] == [str(tpl.downloads / "TestApp-2.1.0-x64-setup.exe")]
    assert templates.spare_ready(PROTON)


def test_without_template_creates_from_scratch_and_builds_template(tpl):
    app = _install(tpl)
    assert pfx.is_created(app.prefix_path)
    assert templates.base_ready(PROTON)  # pedido para la próxima vez


def test_rebuild_invalidates_old_spare(tpl):
    templates.build(PROTON)
    old = templates._marker(templates.spare_path(PROTON))["built"]
    templates.build(PROTON, force=True)
    assert templates._marker(templates.spare_path(PROTON))["built"] != old
    assert templates.spare_ready(PROTON)


def test_disabled_in_other_tests(env):
    assert templates.take(PROTON, env.tmp / "x") is None
