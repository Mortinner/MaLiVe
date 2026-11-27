"""Soporte de NVIDIA (nvidia-libs de SveSop) dentro de los entornos."""

import io
import json
import shutil
import tarfile
from pathlib import Path

import pytest

from conftest import FIX
from malive import cli, config, nvidia, runner


def _fake_libs(root: Path) -> Path:
    d = root / "v9.9.9"
    for n in nvidia.DLLS:
        (d / "x64").mkdir(parents=True, exist_ok=True)
        (d / "x64" / f"{n}.dll").write_bytes(b"\x7fELF " + n.encode())
    return d


def _app(env):
    setup = env.downloads / "TestApp-2.1.0-x64-setup.exe"
    shutil.copy(FIX / "TestApp-2.1.0-x64-setup.exe", setup)
    runner.main([str(setup)])
    return config.all_apps()[0]


@pytest.fixture
def gpu(monkeypatch):
    monkeypatch.setattr(nvidia, "gpu_present", lambda: True)

    def fake_download(on_progress=None):
        d = _fake_libs(nvidia.libs_root())
        nvidia._set_current(d.name)
        return d
    monkeypatch.setattr(nvidia, "download", fake_download)


def test_off_by_default(env, gpu):
    app = _app(env)
    assert runner.main(["--app", app.id, "--entry", app.entries[0].id]) == 0
    call = env.calls()[-1]
    assert "nvcuda" not in call["dlls"] and call["nvapi"] == ""
    assert nvidia.installed() is None  # ni siquiera se descarga


def test_on_links_dlls_and_sets_overrides(env, gpu):
    app = _app(env)
    assert cli.main(["nvidia", app.id, "on"]) == 0
    assert runner.main(["--app", app.id, "--entry", app.entries[0].id]) == 0
    call = env.calls()[-1]
    libs = nvidia.installed()
    assert libs is not None
    assert call["nvcuda"] == str(libs / "x64" / "nvcuda.dll")
    assert "nvcuda=n" in call["dlls"] and "nvencodeapi64=n" in call["dlls"]
    assert call["nvapi"] == "1"
    s32 = app.prefix_path / "drive_c/windows/system32"
    assert (s32 / f"nvcuda.dll{nvidia.BACKUP_SUFFIX}").read_bytes() == b"MZ proton nvcuda"

    # Desactivar devuelve la DLL de Proton
    assert cli.main(["nvidia", app.id, "off"]) == 0
    assert (s32 / "nvcuda.dll").read_bytes() == b"MZ proton nvcuda"
    assert not (s32 / f"nvcuda.dll{nvidia.BACKUP_SUFFIX}").exists()
    assert runner.main(["--app", app.id, "--entry", app.entries[0].id]) == 0
    assert "nvcuda" not in env.calls()[-1]["dlls"]


def test_no_gpu_no_effect(env, monkeypatch):
    monkeypatch.setattr(nvidia, "gpu_present", lambda: False)
    app = _app(env)
    cli.main(["nvidia", app.id, "on"])
    assert runner.main(["--app", app.id, "--entry", app.entries[0].id]) == 0
    assert "nvcuda" not in env.calls()[-1]["dlls"]


def test_download_failure_does_not_block(env, monkeypatch):
    monkeypatch.setattr(nvidia, "gpu_present", lambda: True)

    def boom(on_progress=None):
        raise nvidia.NvidiaError("sin red")
    monkeypatch.setattr(nvidia, "download", boom)
    app = _app(env)
    cli.main(["nvidia", app.id, "on"])
    assert runner.main(["--app", app.id, "--entry", app.entries[0].id]) == 0
    assert "nvcuda" not in env.calls()[-1]["dlls"]


def test_global_default(env, gpu):
    app = _app(env)
    assert cli.main(["set", "nvidia", "on"]) == 0
    assert runner.main(["--app", app.id, "--entry", app.entries[0].id]) == 0
    assert "nvcuda=n" in env.calls()[-1]["dlls"]


def test_download_real_flow(env, monkeypatch):
    """download() con la API de GitHub simulada: elige el paquete, verifica y extrae."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:xz") as tf:
        for n in nvidia.DLLS:
            data = b"ELF " + n.encode()
            ti = tarfile.TarInfo(f"nvidia-libs-v1.0.5/x64/{n}.dll")
            ti.size = len(data)
            tf.addfile(ti, io.BytesIO(data))
    blob = buf.getvalue()
    import hashlib
    api = {"tag_name": "v1.0.5", "assets": [
        {"name": "nvcuda-32bit.tar.xz", "browser_download_url": "https://x/32"},
        {"name": "nvidia-libs-v1.0.5.tar.xz", "browser_download_url": "https://x/main",
         "digest": "sha256:" + hashlib.sha256(blob).hexdigest()},
    ]}

    class Resp(io.BytesIO):
        headers = {}

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_get(url, timeout=30):
        return Resp(json.dumps(api).encode() if url == nvidia.GITHUB_LATEST else blob)
    monkeypatch.setattr(nvidia, "_http_get", fake_get)
    d = nvidia.download()
    assert d.name == "v1.0.5" and nvidia.installed() == d

    # Suma mal → se descarta
    api["tag_name"] = "v1.0.6"
    api["assets"][1]["digest"] = "sha256:" + "0" * 64
    with pytest.raises(nvidia.NvidiaError):
        nvidia.download()
    assert nvidia.installed() == d
