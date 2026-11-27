"""Discos de datos y carpetas de red como unidades de Windows."""

from pathlib import Path

from malive import config, mounts, prefix as pfx, runner

# Equipo de Marcos (df -h): btrfs con subvolúmenes, /dev/sdb en ~/DataLinux y
# un NAS CIFS con autofs en ~/DataNas
MOUNTINFO = """\
22 1 0:30 /@ / rw,relatime shared:1 - btrfs /dev/nvme0n1p4 rw,subvol=/@
23 22 0:5 / /dev rw shared:2 - devtmpfs devtmpfs rw
24 22 0:22 / /proc rw shared:5 - proc proc rw
30 22 0:30 /@root /root rw shared:10 - btrfs /dev/nvme0n1p4 rw
31 22 0:30 /@srv /srv rw shared:11 - btrfs /dev/nvme0n1p4 rw
32 22 0:30 /@home /home rw shared:12 - btrfs /dev/nvme0n1p4 rw
33 22 0:30 /@cache /var/cache rw shared:13 - btrfs /dev/nvme0n1p4 rw
34 22 0:30 /@log /var/log rw shared:14 - btrfs /dev/nvme0n1p4 rw
35 22 259:3 / /boot rw shared:15 - vfat /dev/nvme0n1p3 rw
36 22 0:40 / /tmp rw shared:16 - tmpfs tmpfs rw
37 22 0:41 / /run rw shared:17 - tmpfs tmpfs rw
38 37 0:42 / /run/user/1000 rw shared:18 - tmpfs tmpfs rw
39 32 8:16 / /home/morti/DataLinux rw shared:19 - btrfs /dev/sdb rw
40 32 0:50 / /home/morti/DataNas rw shared:20 - autofs systemd-1 rw,fd=112
41 40 0:51 / /home/morti/DataNas rw shared:21 - cifs //192.168.1.6/data2 rw,vers=3.1.1
42 22 0:52 / /proc/sys/fs/binfmt_misc rw shared:22 - autofs systemd-1 rw
43 37 8:33 / /run/media/morti/USB\\040DISK rw shared:23 - exfat /dev/sdc1 rw
44 22 8:17 / /mnt/backup rw shared:24 - ext4 /dev/sdd1 rw
45 44 8:18 / /mnt/backup/sub rw shared:25 - ext4 /dev/sdd2 rw
"""


def test_data_mounts_real_layout():
    found = mounts.data_mounts(MOUNTINFO, home=Path("/home/morti"))
    assert found == [Path("/home/morti/DataLinux"), Path("/home/morti/DataNas"),
                     Path("/mnt/backup"), Path("/run/media/morti/USB DISK")]


def test_letters_are_stable():
    saved: dict[str, str] = {}
    first, changed = mounts.assign_letters([Path("/a"), Path("/b")], saved, {"c", "d", "e", "z"})
    assert first == {"/a": "m", "/b": "n"} and changed
    # Un disco nuevo no mueve a los demás; uno desconectado conserva su letra
    second, changed = mounts.assign_letters([Path("/b"), Path("/0new")], saved, {"c", "d", "e", "z"})
    assert second == {"/b": "n", "/0new": "o"} and changed
    third, changed = mounts.assign_letters([Path("/a")], saved, {"c", "d", "e", "z"})
    assert third == {"/a": "m"} and not changed


def test_launch_gets_mount_drives(env, monkeypatch):
    nas = env.tmp / "nas"
    (nas / "Pelis").mkdir(parents=True)
    film = nas / "Pelis" / "Yakuza (1974).mkv"
    film.write_text("x")
    monkeypatch.setattr(mounts, "data_mounts", lambda *a, **k: [nas])
    from test_flow import copy_fixture
    runner.main([str(copy_fixture(env, "TestApp-2.1.0-x64-setup.exe"))])
    app = config.all_apps()[0]
    entry = app.entries[0]
    assert runner.main(["--app", app.id, "--entry", entry.id, str(film)]) == 0
    call = env.calls()[-1]
    assert call["drives"]["m:"] == str(nas)
    # El fichero se abre por su unidad fija, sin Y:
    assert call["argv"] == [entry.exe, "M:\\Pelis\\Yakuza (1974).mkv"]
    assert "y:" not in call["drives"]
    # Fuera de $HOME: se expone al contenedor
    assert str(nas.resolve()) in call["pv_rw"].split(":")
    assert config.load_global()["drive_letters"] == {str(nas): "m"}

    # Desactivado: vuelve a ser Y:
    cfg = config.load_global()
    cfg["host_drives"] = False
    config.save_global(cfg)
    assert runner.main(["--app", app.id, "--entry", entry.id, str(film)]) == 0
    call = env.calls()[-1]
    assert "m:" not in call["drives"] and call["argv"][-1] == "Y:\\Yakuza (1974).mkv"
