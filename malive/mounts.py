"""Unidades de Windows para los discos de datos y las carpetas de red del equipo.

Con el aislamiento, un programa de Windows solo ve su C:, las carpetas
compartidas (Documentos, Descargas) y, si se abre un fichero de otra carpeta,
esa carpeta como Y:. Aquí se buscan los montajes «de datos» del equipo (un NAS
por CIFS/NFS, un segundo disco, un USB en /run/media…) para darles letra fija.

Se descarta todo lo que es del sistema: la raíz, /boot, /var, /usr…, las
subvolúmenes del disco del sistema (en btrfs, /home, /var/log… salen como
montajes aparte del mismo dispositivo) y los sistemas de ficheros virtuales.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

NET_FS = {"cifs", "smb3", "smbfs", "nfs", "nfs4", "fuse.sshfs", "fuse.rclone", "davfs", "fuse.davfs2", "9p"}
DATA_FS = {"ext2", "ext3", "ext4", "btrfs", "xfs", "ntfs", "ntfs3", "fuseblk", "exfat", "vfat", "f2fs",
           "zfs", "bcachefs", "jfs", "reiserfs", "hfsplus", "udf", "iso9660"}
AUTOFS = "autofs"

SYSTEM_EXACT = {"/", "/home", "/root", "/srv", "/opt", "/usr", "/var", "/tmp", "/boot", "/efi", "/swap"}
SYSTEM_PREFIXES = ("/boot/", "/efi/", "/usr/", "/var/", "/proc/", "/sys/", "/dev/", "/tmp/", "/snap/",
                   "/nix/", "/gnu/", "/etc/", "/run/")
ALLOWED_RUN = "/run/media/"

# Letras para estos montajes, en orden de preferencia (M y N primero: «Media»,
# «NAS»). Se evitan C (sistema), D/E (carpetas compartidas), S y X (las usa
# Proton), Y (carpeta del fichero abierto) y Z.
LETTERS = "mnopqrtuvwlkjihgf"


def _unescape(field: str) -> str:
    return re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), field)


def parse_mountinfo(text: str) -> list[tuple[str, str, str]]:
    """[(punto de montaje, tipo, origen)] de /proc/self/mountinfo."""
    out: list[tuple[str, str, str]] = []
    for line in text.splitlines():
        if " - " not in line:
            continue
        left, right = line.split(" - ", 1)
        lf, rf = left.split(), right.split()
        if len(lf) < 5 or len(rf) < 2:
            continue
        out.append((_unescape(lf[4]), rf[0], _unescape(rf[1])))
    return out


def _is_system(mp: str) -> bool:
    if mp in SYSTEM_EXACT:
        return True
    if mp.startswith(ALLOWED_RUN):
        return False
    return mp.startswith(SYSTEM_PREFIXES)


def data_mounts(mountinfo: str | None = None, home: Path | None = None) -> list[Path]:
    """Montajes de datos del equipo, ordenados por ruta."""
    if mountinfo is None:
        try:
            mountinfo = Path("/proc/self/mountinfo").read_text(encoding="utf-8", errors="replace")
        except OSError:
            return []
    entries = parse_mountinfo(mountinfo)
    root_src = next((src for mp, _fs, src in reversed(entries) if mp == "/"), None)
    home = (home or Path.home()).resolve()
    chosen: dict[str, str] = {}
    for mp, fs, src in entries:
        if _is_system(mp) or Path(mp) == home:
            continue
        if fs in NET_FS or fs == AUTOFS:
            pass
        elif fs in DATA_FS:
            # Subvolúmenes o particiones bind del disco del sistema: no son «otro disco»
            if root_src and src == root_src:
                continue
        else:
            continue
        # autofs y el sistema real montado encima comparten punto: uno basta
        if mp not in chosen or chosen[mp] == AUTOFS:
            chosen[mp] = fs
    paths = sorted(Path(mp) for mp in chosen)
    # Si uno está dentro de otro, basta con el de fuera
    out: list[Path] = []
    for p in paths:
        if any(o in p.parents for o in out):
            continue
        out.append(p)
    return out


def assign_letters(mounts: list[Path], saved: dict[str, str], taken: set[str]) -> tuple[dict[str, str], bool]:
    """Letra fija para cada montaje: se recuerda la ya asignada (aunque el disco
    no esté conectado, para que no la coja otro) y a los nuevos se les da la
    primera libre. Devuelve ({ruta: letra}, si hay asignaciones nuevas)."""
    used = set(taken) | set(saved.values())
    out: dict[str, str] = {}
    changed = False
    for m in mounts:
        key = str(m)
        letter = saved.get(key)
        if letter is None:
            free = [c for c in LETTERS if c not in used]
            if not free:
                continue
            letter = free[0]
            used.add(letter)
            saved[key] = letter
            changed = True
        if letter in taken:
            continue
        out[key] = letter
    return out, changed


def accessible(p: Path) -> bool:
    try:
        return p.is_dir() and os.access(p, os.R_OK | os.X_OK)
    except OSError:
        return False
