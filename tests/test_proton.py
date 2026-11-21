"""Descarga de GE-Proton contra un servidor HTTP local que imita GitHub."""

import hashlib
import io
import json
import tarfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from malive import config, proton


def make_tarball(name: str) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for fname, content in (("proton", b"#!/bin/sh\n"), ("toolmanifest.vdf", b'"m" {}\n')):
            info = tarfile.TarInfo(f"{name}/{fname}")
            info.size = len(content)
            tf.addfile(info, io.BytesIO(content))
    return buf.getvalue()


@pytest.fixture
def github(monkeypatch):
    files: dict[str, bytes] = {}

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            body = files.get(self.path)
            if body is None:
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    base = f"http://127.0.0.1:{srv.server_port}"
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    def publish(name: str, corrupt: bool = False):
        tgz = make_tarball(name)
        digest = hashlib.sha512(tgz).hexdigest()
        files[f"/dl/{name}.tar.gz"] = tgz if not corrupt else tgz[:-10] + b"0" * 10
        files[f"/dl/{name}.sha512sum"] = f"{digest}  {name}.tar.gz\n".encode()
        release = {"tag_name": name, "assets": [
            {"name": f"{name}-aarch64.tar.gz", "browser_download_url": f"{base}/nope"},
            {"name": f"{name}.tar.gz", "browser_download_url": f"{base}/dl/{name}.tar.gz"},
            {"name": f"{name}.sha512sum", "browser_download_url": f"{base}/dl/{name}.sha512sum"},
        ]}
        files["/latest"] = json.dumps(release).encode()
        files[f"/tags/{name}"] = files["/latest"]

    monkeypatch.setattr(proton, "GITHUB_LATEST", f"{base}/latest")
    monkeypatch.setattr(proton, "GITHUB_API", base)
    yield publish
    srv.shutdown()


def test_update_and_cleanup(env, github):
    github("GE-Proton11-11")
    name, new = proton.update()
    assert (name, new) == ("GE-Proton11-11", True)
    assert proton.installed() == ["GE-Proton10-1", "GE-Proton11-11"]
    assert proton.update() == ("GE-Proton11-11", False)
    # GE-Proton10-1 sin uso y no es la más nueva -> se borra
    assert proton.cleanup(keep_unused=1) == ["GE-Proton10-1"]


def test_cleanup_keeps_pinned(env, github):
    github("GE-Proton11-11")
    proton.update()
    config.App(id="x", name="X", kind="installed", prefix="/tmp/x", proton="GE-Proton10-1").save()
    assert proton.cleanup(keep_unused=0) == ["GE-Proton11-11"]
    assert proton.installed() == ["GE-Proton10-1"]


def test_bad_checksum_rejected(env, github):
    github("GE-Proton11-12", corrupt=True)
    with pytest.raises(proton.ProtonError, match="SHA-512"):
        proton.update()
    assert "GE-Proton11-12" not in proton.installed()


def test_release_by_name(env, github):
    github("GE-Proton11-11")
    assert proton.release_by_name("GE-Proton11-11").tarball_name == "GE-Proton11-11.tar.gz"
    with pytest.raises(proton.ProtonError):
        proton.release_by_name("../../etc")
