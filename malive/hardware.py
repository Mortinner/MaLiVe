"""Hardware del equipo y la configuración única que se deduce de él.

Idea de diseño (Marcos, 2026-11-27): como en Windows, todos los programas
tienen acceso a todo el hardware y cada uno usa lo que necesita. MaLiVe mira el
equipo y aplica la misma configuración a todos los entornos; nada por programa.

Qué ya da Proton sin ajustes: todos los hilos de la CPU, toda la RAM, sonido,
red, mandos y la gráfica para dibujar (Direct3D 9–12 → Vulkan con DXVK y
VKD3D), sea NVIDIA, AMD o Intel. Lo que se añade según el equipo:
  - NVIDIA con driver propietario: CUDA, NVENC/NVDEC y OptiX (nvidia-libs) y
    NVAPI (DLSS, Reflex, detección de la tarjeta).
  - Equipos con dos gráficas (portátiles): usar la dedicada, no la integrada.

La detección lee /sys y /proc (milisegundos), así que se hace en cada
lanzamiento: si cambias de tarjeta, la configuración cambia sola.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .i18n import T

VENDORS = {"0x10de": "nvidia", "0x1002": "amd", "0x8086": "intel"}


@dataclass
class GPU:
    vendor: str  # nvidia | amd | intel | other
    pci: str  # 0000:01:00.0
    driver: str  # nvidia, amdgpu, i915, xe, nouveau…
    boot_vga: bool
    name: str = ""

    @property
    def proprietary_nvidia(self) -> bool:
        return self.vendor == "nvidia" and self.driver == "nvidia"


@dataclass
class Hardware:
    cpu: str = ""
    threads: int = 0
    ram_gb: float = 0.0
    gpus: list[GPU] = field(default_factory=list)
    nvidia_driver: str | None = None
    opencl: list[str] = field(default_factory=list)  # ficheros .icd

    @property
    def nvidia(self) -> GPU | None:
        return next((g for g in self.gpus if g.proprietary_nvidia), None)

    @property
    def hybrid(self) -> bool:
        return len(self.gpus) > 1


def _read(p: Path) -> str:
    try:
        return p.read_text(errors="replace").strip()
    except OSError:
        return ""


def _gpu_names() -> dict[str, str]:
    """Nombres comerciales con lspci, si está (pciutils)."""
    if not shutil.which("lspci"):
        return {}
    try:
        out = subprocess.run(["lspci", "-D", "-mm"], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.TimeoutExpired):
        return {}
    names = {}
    for line in out.splitlines():
        parts = re.findall(r'"([^"]*)"', line)
        slot = line.split(" ", 1)[0]
        if len(parts) >= 3 and re.search(r"VGA|3D|Display", parts[0]):
            names[slot] = f"{parts[1]} {parts[2]}".replace("Corporation", "").replace("  ", " ").strip()
    return names


def detect_gpus(sys_root: Path = Path("/sys"), with_names: bool = True) -> list[GPU]:
    gpus: dict[str, GPU] = {}
    names = _gpu_names() if with_names and sys_root == Path("/sys") else {}
    drm = sys_root / "class" / "drm"
    if not drm.is_dir():
        return []
    for card in sorted(drm.glob("card[0-9]*")):
        if "-" in card.name:  # conectores (card0-HDMI-A-1)
            continue
        dev = card / "device"
        vendor_id = _read(dev / "vendor").lower()
        try:
            pci = Path(os.path.realpath(dev)).name
            driver = Path(os.path.realpath(dev / "driver")).name if (dev / "driver").exists() else ""
        except OSError:
            continue
        if pci in gpus:
            continue
        gpus[pci] = GPU(vendor=VENDORS.get(vendor_id, "other"), pci=pci, driver=driver,
                        boot_vga=_read(dev / "boot_vga") == "1", name=names.get(pci, ""))
    return list(gpus.values())


def detect(sys_root: Path = Path("/sys"), proc_root: Path = Path("/proc"), with_names: bool = True) -> Hardware:
    hw = Hardware(threads=os.cpu_count() or 0)
    m = re.search(r"^model name\s*:\s*(.+)$", _read(proc_root / "cpuinfo"), re.M)
    hw.cpu = re.sub(r"\s+", " ", m.group(1)).strip() if m else ""
    m = re.search(r"^MemTotal:\s*(\d+)", _read(proc_root / "meminfo"), re.M)
    hw.ram_gb = round(int(m.group(1)) / 1024 / 1024, 1) if m else 0.0
    hw.gpus = detect_gpus(sys_root, with_names)
    text = _read(proc_root / "driver" / "nvidia" / "version")
    m = re.search(r"Kernel Module(?: for [\w-]+)?\s+(\d+\.\d+(?:\.\d+)?)", text)
    hw.nvidia_driver = m.group(1) if m else None
    vendors = Path("/etc/OpenCL/vendors")
    if sys_root == Path("/sys") and vendors.is_dir():
        hw.opencl = sorted(p.name for p in vendors.glob("*.icd"))
    return hw


def profile_env(hw: Hardware) -> dict[str, str]:
    """Variables que se ponen a todos los programas según el equipo."""
    env: dict[str, str] = {}
    if hw.nvidia is not None:
        env["PROTON_ENABLE_NVAPI"] = "1"
    if hw.hybrid:
        dedicated = [g for g in hw.gpus if not g.boot_vga] or [g for g in hw.gpus if g.vendor != "intel"]
        target = dedicated[0] if dedicated else None
        if target is not None and target.proprietary_nvidia:
            env.update(__NV_PRIME_RENDER_OFFLOAD="1", __GLX_VENDOR_LIBRARY_NAME="nvidia",
                       __VK_LAYER_NV_optimus="NVIDIA_only")
        elif target is not None and target.vendor == "amd":
            env["DRI_PRIME"] = "1"
    return env


def summary(hw: Hardware) -> list[str]:
    """Líneas para mostrar al usuario (instalador, `malive hardware`)."""
    lines = [T(f"Procesador: {hw.cpu or '?'} ({hw.threads} hilos)", f"Processor: {hw.cpu or '?'} ({hw.threads} threads)"),
             T(f"Memoria: {hw.ram_gb:g} GB", f"Memory: {hw.ram_gb:g} GB")]
    for g in hw.gpus or []:
        label = g.name or g.vendor.upper()
        extra = f"driver {g.driver}" + (f" {hw.nvidia_driver}" if g.proprietary_nvidia and hw.nvidia_driver else "")
        lines.append(T(f"Gráfica: {label} ({extra})", f"Graphics: {label} ({extra})"))
    if not hw.gpus:
        lines.append(T("Gráfica: no detectada", "Graphics: not detected"))
    caps = [T("gráficos Direct3D 9–12 por Vulkan", "Direct3D 9–12 graphics via Vulkan")]
    if hw.nvidia is not None:
        caps.append("CUDA, NVENC/NVDEC, OptiX, NVAPI (DLSS)")
    if hw.opencl:
        caps.append("OpenCL")
    if hw.hybrid and profile_env(hw):
        caps.append(T("se usa la gráfica dedicada", "the dedicated GPU is used"))
    lines.append(T("Para todos los programas: ", "For every program: ") + ", ".join(caps))
    return lines
