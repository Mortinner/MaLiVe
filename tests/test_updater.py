"""Actualizador contra un servidor HTTP local que imita las releases de GitHub."""

import hashlib
import io
import json
import shutil
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from conftest import ROOT
from malive import config, updater


def make_zip(version: str, broken: bool = False, packages=None, cli_fails: bool = False) -> bytes:
    buf = io.BytesIO()
    root = f"malive-{version}/"
    with zipfile.ZipFile(buf, "w") as zf:
        for f in sorted((ROOT / "malive").rglob("*.py")):
            rel = f.relative_to(ROOT)
            data = f.read_text()
            if rel.name == "__init__.py" and rel.parent.name == "malive":
                data = f'__version__ = "{version}"\n' + ("this is not python\n" if broken else "")
            if cli_fails and rel.name == "cli.py":
                data = "def main(argv=None):\n    return 1\n"
            zf.writestr(root + str(rel), data)
        info = zipfile.ZipInfo(root + "install.sh")
        info.external_attr = 0o755 << 16
        zf.writestr(info, "#!/bin/bash\necho instalador\n")
        zf.writestr(root + "release.json", json.dumps({"version": version, "packages": packages or []}))
    return buf.getvalue()


@pytest.fixture
def gh(env, monkeypatch):
    files: dict[str, bytes] = {}

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            body = files.get(self.path)
            self.send_response(200 if body is not None else 404)
            self.end_headers()
            if body is not None:
                self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    base = f"http://127.0.0.1:{srv.server_port}"
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    def publish(version, zip_bytes=None, bad_sum=False):
        z = zip_bytes or make_zip(version)
        name = f"malive-{version}.zip"
        files[f"/dl/{name}"] = z
        digest = hashlib.sha256(z).hexdigest() if not bad_sum else "0" * 64
        files[f"/dl/{name}.sha256"] = f"{digest}\n".encode()
        files["/latest"] = json.dumps({
            "tag_name": f"v{version}", "html_url": f"{base}/rel", "body": "## Novedades\n- cosas",
            "assets": [{"name": name, "browser_download_url": f"{base}/dl/{name}"},
                       {"name": name + ".sha256", "browser_download_url": f"{base}/dl/{name}.sha256"}],
        }).encode()

    monkeypatch.setattr(updater, "API_LATEST", f"{base}/latest")
    monkeypatch.setattr(updater, "missing_packages", lambda manifest: list(manifest.get("packages", [])))
    # instalación actual, como la deja install.sh
    shutil.copytree(ROOT / "malive", updater.lib_dir() / "malive",
                    ignore=shutil.ignore_patterns("__pycache__"))
    updater.write_wrappers(updater.lib_dir(), updater.bin_dir())
    yield publish
    srv.shutdown()


def test_update_installs_and_keeps_previous(env, gh):
    gh("9.9.9")
    res = updater.check_and_update(force=True)
    assert res.status == "updated" and res.version == "9.9.9"
    assert updater.installed_version() == "9.9.9"
    assert (updater.lib_dir() / "malive.prev" / "__init__.py").is_file()
    assert (updater.bin_dir() / "malive").read_text().count(str(updater.lib_dir())) == 1
    # y el comando instalado funciona con la versión nueva
    import subprocess
    out = subprocess.run([str(updater.bin_dir() / "malive"), "--version"], capture_output=True, text=True)
    assert out.stdout.strip() == "malive 9.9.9"


def test_up_to_date(env, gh):
    gh("0.0.1")
    assert updater.check_and_update(force=True).status == "up-to-date"


def test_bad_checksum_changes_nothing(env, gh):
    before = updater.installed_version()
    gh("9.9.9", bad_sum=True)
    with pytest.raises(updater.UpdateError, match="SHA-256"):
        updater.check_and_update(force=True)
    assert updater.installed_version() == before


def test_broken_release_is_not_installed(env, gh):
    before = updater.installed_version()
    gh("9.9.9", zip_bytes=make_zip("9.9.9", broken=True))
    with pytest.raises(updater.UpdateError, match="no arranca"):
        updater.check_and_update(force=True)
    assert updater.installed_version() == before
    assert not (updater.lib_dir() / ".staging").exists()


def test_needs_packages_stages_installer(env, gh, tmp_path):
    before = updater.installed_version()
    gh("9.9.9", zip_bytes=make_zip("9.9.9", packages=["paquete-nuevo"]))
    res = updater.check_and_update(force=True, stage_dir=tmp_path / "staged")
    assert res.status == "needs-packages" and res.missing == ["paquete-nuevo"]
    assert (res.staged / "install.sh").stat().st_mode & 0o111  # ejecutable
    assert updater.installed_version() == before


def test_disabled_and_throttled(env, gh):
    gh("9.9.9")
    cfg = config.load_global()
    cfg["auto_update"] = False
    config.save_global(cfg)
    assert updater.check_and_update().status == "disabled"
    cfg["auto_update"] = True
    config.save_global(cfg)
    assert updater.check_and_update().status == "updated"
    gh("9.9.10")
    assert updater.check_and_update().status == "skipped"  # ya se comprobó hoy


def test_rejects_zip_with_path_traversal(env, gh):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("malive-9.9.9/../../evil.sh", "x")
    gh("9.9.9", zip_bytes=buf.getvalue())
    with pytest.raises(updater.UpdateError, match="inesperado"):
        updater.check_and_update(force=True)


def test_versions():
    assert updater.parse_version("v0.1.10") > updater.parse_version("0.1.9")
    with pytest.raises(updater.UpdateError):
        updater.parse_version("latest")


def test_release_metadata_consistent():
    from malive import __version__

    manifest = json.loads((ROOT / "release.json").read_text())
    assert manifest["version"] == __version__
    assert f"## {__version__}\n" in (ROOT / "CHANGELOG.md").read_text()
    install = (ROOT / "install.sh").read_text()
    for pkg in manifest["packages"]:
        assert f"need {pkg} " in install
    for group in manifest["any_of"]:
        for pkg in group:
            assert f"need {pkg} " in install


def test_rollback_when_new_version_fails_after_install(env, gh):
    before = updater.installed_version()
    gh("9.9.9", zip_bytes=make_zip("9.9.9", cli_fails=True))
    with pytest.raises(updater.UpdateError, match="se mantiene la versión anterior"):
        updater.check_and_update(force=True)
    assert updater.installed_version() == before
    import subprocess
    out = subprocess.run([str(updater.bin_dir() / "malive"), "--version"], capture_output=True, text=True)
    assert out.stdout.strip() == f"malive {before}"
