"""Detección del equipo y configuración común."""

from pathlib import Path

from malive import config, hardware, runner


def _card(sys_root: Path, n: int, vendor: str, driver: str, pci: str, boot: bool):
    devs = sys_root / "devices" / pci
    (devs).mkdir(parents=True, exist_ok=True)
    (devs / "vendor").write_text(vendor + "\n")
    (devs / "boot_vga").write_text("1\n" if boot else "0\n")
    drv = sys_root / "bus" / "pci" / "drivers" / driver
    drv.mkdir(parents=True, exist_ok=True)
    if not (devs / "driver").exists():
        (devs / "driver").symlink_to(drv)
    card = sys_root / "class" / "drm" / f"card{n}"
    card.mkdir(parents=True)
    (card / "device").symlink_to(devs)
    (sys_root / "class" / "drm" / f"card{n}-HDMI-A-1").mkdir()


def _proc(root: Path, nvidia: bool):
    root.mkdir(parents=True, exist_ok=True)
    (root / "cpuinfo").write_text("processor : 0\nmodel name : Intel(R) Xeon(R) CPU E5-2696 v3 @ 2.30GHz\n")
    (root / "meminfo").write_text("MemTotal:       131900000 kB\n")
    if nvidia:
        (root / "driver/nvidia").mkdir(parents=True)
        (root / "driver/nvidia/version").write_text(
            "NVRM version: NVIDIA UNIX Open Kernel Module for x86_64  580.95.05  Release Build\n")


def test_desktop_single_nvidia(tmp_path):
    sysr, procr = tmp_path / "sys", tmp_path / "proc"
    _card(sysr, 0, "0x10de", "nvidia", "0000:03:00.0", True)
    _proc(procr, True)
    hw = hardware.detect(sysr, procr, with_names=False)
    assert hw.cpu.startswith("Intel(R) Xeon") and hw.ram_gb > 120
    assert len(hw.gpus) == 1 and hw.nvidia is not None and hw.nvidia_driver == "580.95.05"
    assert hardware.profile_env(hw) == {"PROTON_ENABLE_NVAPI": "1"}
    text = "\n".join(hardware.summary(hw))
    assert "CUDA" in text and "580.95.05" in text


def test_laptop_intel_plus_nvidia_uses_dedicated(tmp_path):
    sysr, procr = tmp_path / "sys", tmp_path / "proc"
    _card(sysr, 0, "0x8086", "i915", "0000:00:02.0", True)
    _card(sysr, 1, "0x10de", "nvidia", "0000:01:00.0", False)
    _proc(procr, True)
    env = hardware.profile_env(hardware.detect(sysr, procr, with_names=False))
    assert env["__NV_PRIME_RENDER_OFFLOAD"] == "1" and env["__VK_LAYER_NV_optimus"] == "NVIDIA_only"


def test_laptop_amd_apu_plus_radeon(tmp_path):
    sysr, procr = tmp_path / "sys", tmp_path / "proc"
    _card(sysr, 0, "0x1002", "amdgpu", "0000:05:00.0", True)
    _card(sysr, 1, "0x1002", "amdgpu", "0000:03:00.0", False)
    _proc(procr, False)
    hw = hardware.detect(sysr, procr, with_names=False)
    assert hw.nvidia is None
    assert hardware.profile_env(hw) == {"DRI_PRIME": "1"}


def test_amd_desktop_needs_nothing(tmp_path):
    sysr, procr = tmp_path / "sys", tmp_path / "proc"
    _card(sysr, 0, "0x1002", "amdgpu", "0000:03:00.0", True)
    _proc(procr, False)
    assert hardware.profile_env(hardware.detect(sysr, procr, with_names=False)) == {}


def test_profile_reaches_every_program(env, monkeypatch):
    """La configuración común llega a cualquier programa sin ajustes propios."""
    monkeypatch.setattr(hardware, "profile_env", lambda hw: {"DRI_PRIME": "1"})
    import shutil
    from conftest import FIX
    setup = env.downloads / "TestApp-2.1.0-x64-setup.exe"
    shutil.copy(FIX / "TestApp-2.1.0-x64-setup.exe", setup)
    runner.main([str(setup)])
    app = config.all_apps()[0]
    assert runner.main(["--app", app.id, "--entry", app.entries[0].id]) == 0
    assert env.calls()[-1]["dri_prime"] == "1"


def test_save_global_keeps_only_changes(env):
    cfg = config.load_global()
    cfg["host_drives"] = False
    config.save_global(cfg)
    import json
    from malive import paths
    saved = json.loads(paths.config_file().read_text())
    assert saved == {"host_drives": False}
