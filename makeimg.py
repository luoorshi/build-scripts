#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 buidl_tools.sh 的编译产物和已准备好的 rootfs 打成 TF 卡启动镜像。

只生成 build/<soc>/quark-n-<soc>-sdcard.img，不写入物理磁盘。
必须在 Linux 上执行（losetup / parted / mkfs / sudo）。

示例（仓库根目录）:
  python3 build-scripts/makeimg.py h5
  python3 build-scripts/makeimg.py h5 --rootfs ./tools/arm64_rootfs --size 2048M
"""

from __future__ import annotations

import argparse
import filecmp
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
BUILD_ROOT = REPO_ROOT / "build"
TOOLS_DIR = REPO_ROOT / "tools"
LINUX_DIR = REPO_ROOT / "linux"

# 与《H5制作镜像详细指南》一致：1MiB 之前留给 SPL/U-Boot，FAT 到 256MiB，其余给 ext4。
BOOT_END_MIB = 256
MIN_IMAGE_MIB = 2048
# 自动计算体积时，为模块和根分区空闲预留的空间。
MODULES_RESERVE_MIB = 256
ROOT_FREE_MIB = 512
# 用户显式指定 --size 时，只要求装得下内容，再留一小段余量。
EXPLICIT_SLACK_MIB = 64

SOC_INFO = {
    "h5": {
        "mkimage_arch": "arm64",
        "make_arch": "arm64",
        "cross": "aarch64-none-linux-gnu-",
        "toolchain_dir": "15.2.rel1-arm64",
        "uboot_name": "u-boot-sunxi-with-spl-h5.bin",
        "dtb_name": "sun50i-h5-quark-luoorshi.dtb",
        "kernel_file": "Image",
        "boot_cmd": "booti",
        "linux_image": Path("arch/arm64/boot/Image"),
        "linux_dtb": Path("arch/arm64/boot/dts/allwinner/sun50i-h5-quark-luoorshi.dtb"),
        "archives": (
            "rootfs-arm64.tar.gz",
            "arm64_rootfs.tar.gz",
            "arm64-rootfs.tar.gz",
        ),
        "dirs": ("arm64_rootfs",),
    },
    "h3": {
        "mkimage_arch": "arm",
        "make_arch": "arm",
        "cross": "arm-none-linux-gnueabihf-",
        "toolchain_dir": "15.2.rel1-arm",
        "uboot_name": "u-boot-sunxi-with-spl-h3.bin",
        "dtb_name": "sun8i-h3-quark-luoorshi.dtb",
        "kernel_file": "zImage",
        "boot_cmd": "bootz",
        "linux_image": Path("arch/arm/boot/zImage"),
        "linux_dtb": Path("arch/arm/boot/dts/allwinner/sun8i-h3-quark-luoorshi.dtb"),
        "archives": (
            "rootfs-arm32.tar.gz",
            "arm32_rootfs.tar.gz",
            "arm32-rootfs.tar.gz",
            "rootfs-armhf.tar.gz",
        ),
        "dirs": ("arm32_rootfs",),
    },
}

_LOG_FH = None


def log(msg: str) -> None:
    print(msg, flush=True)
    if _LOG_FH is not None:
        _LOG_FH.write(msg + "\n")
        _LOG_FH.flush()


def die(msg: str) -> None:
    text = f"错误: {msg}"
    print(text, file=sys.stderr, flush=True)
    if _LOG_FH is not None:
        _LOG_FH.write(text + "\n")
        _LOG_FH.flush()
    sys.exit(1)


def run(cmd: list[str]) -> None:
    shown = " ".join(_quote(str(part)) for part in cmd)
    log("+ " + shown)
    result = subprocess.run(cmd)
    if result.returncode != 0:
        die(f"命令失败（退出码 {result.returncode}）: {shown}")


def run_quiet(cmd: list[str]) -> int:
    result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return result.returncode


def _quote(text: str) -> str:
    if text == "" or any(ch.isspace() for ch in text):
        return "'" + text.replace("'", "'\"'\"'") + "'"
    return text


def ensure_linux() -> None:
    if not sys.platform.startswith("linux"):
        die(
            "本脚本必须在 Linux 上运行（需要 losetup、parted、mkfs、sudo）。"
            "请在编译用的 Linux 机器上执行，不要在 Windows 上执行。"
        )


def require_commands(names: list[str]) -> None:
    missing = [name for name in names if shutil.which(name) is None]
    if missing:
        die(
            "缺少命令: "
            + ", ".join(missing)
            + "。可安装: sudo apt install -y parted dosfstools e2fsprogs util-linux kmod coreutils tar"
        )


def open_log(soc: str):
    global _LOG_FH
    out_dir = BUILD_ROOT / soc
    out_dir.mkdir(parents=True, exist_ok=True)
    _LOG_FH = open(out_dir / "makeimg.log", "w", encoding="utf-8")


def soc_dir(soc: str) -> Path:
    return BUILD_ROOT / soc


def mount_boot(soc: str) -> str:
    return f"/mnt/makeimg-{soc}-boot"


def mount_root(soc: str) -> str:
    return f"/mnt/makeimg-{soc}-root"


def loop_record(soc: str) -> Path:
    return soc_dir(soc) / "makeimg.loop"


def image_path(soc: str) -> Path:
    return soc_dir(soc) / f"quark-n-{soc}-sdcard.img"


def partial_image_path(soc: str) -> Path:
    return soc_dir(soc) / f"quark-n-{soc}-sdcard.img.partial"


def parse_size_mib(text: str) -> int:
    """把 2048、2048M、2048MiB、2G 解析成 MiB。M/MB/MiB 都按 1024 进制，与 GNU dd 的 bs=1M 一致。"""
    raw = text.strip().replace(" ", "")
    if not raw:
        die("--size 不能为空")
    unit = ""
    number = raw
    for suffix, name in (
        ("MIB", "M"),
        ("GIB", "G"),
        ("TIB", "T"),
        ("KIB", "K"),
        ("MB", "M"),
        ("GB", "G"),
        ("TB", "T"),
        ("KB", "K"),
        ("M", "M"),
        ("G", "G"),
        ("T", "T"),
        ("K", "K"),
    ):
        if raw.upper().endswith(suffix):
            number = raw[: -len(suffix)]
            unit = name
            break
    if not number.isdigit():
        die(f"无法识别镜像大小: {text}。示例: 2048M、2G、4096")
    value = int(number)
    if unit == "" or unit == "M":
        mib = value
    elif unit == "G":
        mib = value * 1024
    elif unit == "T":
        mib = value * 1024 * 1024
    else:
        if value % 1024 != 0:
            die(f"大小 {text} 换算后不是整数 MiB")
        mib = value // 1024
    if mib < 512:
        die(f"镜像至少需要 512MiB，当前为 {mib}MiB。启动分区占用到 {BOOT_END_MIB}MiB。")
    return mib


def align_up(value: int, step: int) -> int:
    return ((value + step - 1) // step) * step


def resolve_user_path(text: str) -> Path:
    raw = Path(text)
    if raw.is_absolute():
        if not raw.exists():
            die(f"找不到 --rootfs: {raw}")
        return raw.resolve()
    from_cwd = (Path.cwd() / raw).resolve()
    from_repo = (REPO_ROOT / raw).resolve()
    if (Path.cwd() / raw).exists():
        return from_cwd
    if (REPO_ROOT / raw).exists():
        return from_repo
    die(f"找不到 --rootfs: {text}。已尝试 {Path.cwd() / raw} 和 {REPO_ROOT / raw}")


def is_archive(path: Path) -> bool:
    name = path.name.lower()
    return name.endswith(".tar.gz") or name.endswith(".tgz")


def looks_like_rootfs(path: Path) -> bool:
    if not path.is_dir():
        return False
    return any((path / name).exists() for name in ("bin", "sbin", "etc", "usr", "lib"))


def locate_rootfs_root(path: Path) -> Path | None:
    if looks_like_rootfs(path):
        return path
    if not path.is_dir():
        return None
    children = [child for child in path.iterdir() if child.is_dir() and not child.name.startswith(".")]
    if len(children) == 1 and looks_like_rootfs(children[0]):
        return children[0]
    return None


def require_rootfs_root(path: Path) -> Path:
    found = locate_rootfs_root(path)
    if found is None:
        die(f"目录不像根文件系统（缺少 bin/sbin/etc/usr/lib）: {path}")
    return found


def archive_signature(archive: Path) -> str:
    stat = archive.stat()
    return f"{archive.resolve()}\n{stat.st_size}\n{stat.st_mtime_ns}\n"


def extract_rootfs(soc: str, archive: Path) -> Path:
    """把 tar.gz 解压到 build/<soc>/rootfs。压缩包大小和时间未变时直接复用。"""
    cache = (soc_dir(soc) / "rootfs").resolve()
    parent = soc_dir(soc).resolve()
    if cache.parent != parent or cache.name != "rootfs":
        die(f"拒绝使用异常解压目录: {cache}")
    if cache.is_symlink():
        die(f"{cache} 是符号链接，拒绝删除或覆盖")

    stamp = soc_dir(soc) / "rootfs.stamp"
    signature = archive_signature(archive)
    existing = locate_rootfs_root(cache) if cache.is_dir() else None
    if stamp.is_file() and stamp.read_text(encoding="utf-8") == signature and existing is not None:
        log(f"rootfs 压缩包未变化，使用已解压目录: {existing}")
        return existing

    log(f"解压 rootfs: {archive} -> {cache}")
    if cache.exists():
        run(["sudo", "rm", "-rf", str(cache)])
    cache.mkdir(parents=True, exist_ok=True)
    run(["sudo", "tar", "-xzf", str(archive), "-C", str(cache)])
    found = require_rootfs_root(cache)
    stamp.write_text(signature, encoding="utf-8")
    log(f"rootfs 根目录: {found}")
    return found


def default_rootfs_source(soc: str) -> tuple[Path, str]:
    info = SOC_INFO[soc]
    for name in info["archives"]:
        candidate = TOOLS_DIR / name
        if candidate.is_file():
            return candidate, "archive"
    for name in info["dirs"]:
        candidate = TOOLS_DIR / name
        if locate_rootfs_root(candidate) is not None:
            return candidate, "dir"
    archive_list = "、".join(str(TOOLS_DIR / name) for name in info["archives"])
    dir_list = "、".join(str(TOOLS_DIR / name) for name in info["dirs"])
    die(
        "未指定 --rootfs，且 tools/ 下没有找到该芯片的根文件系统。\n"
        f"压缩包（按顺序，找到即用）: {archive_list}\n"
        f"或已解压目录: {dir_list}\n"
        "也可以显式传入目录或 tar.gz: --rootfs ./tools/arm64_rootfs"
    )


def prepare_rootfs(soc: str, rootfs_arg: str | None) -> tuple[Path, str]:
    if rootfs_arg:
        source = resolve_user_path(rootfs_arg)
        if source.is_file():
            if not is_archive(source):
                die(f"--rootfs 指向文件，但不是 .tar.gz 或 .tgz: {source}")
            return extract_rootfs(soc, source), f"archive:{source}"
        if source.is_dir():
            return require_rootfs_root(source), f"dir:{source}"
        die(f"--rootfs 不是目录也不是压缩包: {source}")

    source, kind = default_rootfs_source(soc)
    if kind == "archive":
        return extract_rootfs(soc, source), f"archive:{source}"
    return require_rootfs_root(source), f"dir:{source}"


def first_existing(paths: list[Path]) -> Path | None:
    for path in paths:
        if path.is_file() and path.stat().st_size > 0:
            return path
    return None


def require_min_size(path: Path, min_bytes: int, desc: str) -> None:
    size = path.stat().st_size
    if size < min_bytes:
        die(f"{desc} 过小（{size} 字节），可能不是有效文件: {path}")


def locate_uboot_and_dtb(soc: str) -> tuple[Path, Path]:
    info = SOC_INFO[soc]
    uboot = first_existing(
        [
            soc_dir(soc) / info["uboot_name"],
            REPO_ROOT / "u-boot" / "u-boot-sunxi-with-spl.bin",
        ]
    )
    dtb = first_existing(
        [
            soc_dir(soc) / info["dtb_name"],
            LINUX_DIR / info["linux_dtb"],
        ]
    )
    if uboot is None:
        die(f"缺少 U-Boot。请先执行 buidl_tools.sh {soc}，期望文件: {soc_dir(soc) / info['uboot_name']}")
    if dtb is None:
        die(f"缺少设备树。请先执行 buidl_tools.sh {soc}，期望文件: {soc_dir(soc) / info['dtb_name']}")
    require_min_size(uboot, 100 * 1024, "U-Boot")
    require_min_size(dtb, 1024, "设备树")
    return uboot, dtb


def build_h3_zimage() -> Path:
    """H3 的 U-Boot 只有 bootz。bootz 认的是 zImage，不认未压缩的 Image，也没有 booti。"""
    linux_zimage = LINUX_DIR / SOC_INFO["h3"]["linux_image"]
    archived = soc_dir("h3") / "zImage"
    arch = config_is_arm64(LINUX_DIR)

    if arch is False:
        gcc = cross_gcc("h3")
        if gcc is None:
            die(
                "H3 需要 zImage，但找不到 arm-none-linux-gnueabihf-gcc，无法生成。\n"
                "请先执行 buidl_tools.sh h3。"
            )
        log("======== 确认 H3 zImage 与当前内核一致（供 bootz 使用） ========")
        env = os.environ.copy()
        env["PATH"] = toolchain_path("h3")
        jobs = os.cpu_count() or 4
        cmd = [
            "make",
            "-C",
            str(LINUX_DIR),
            "ARCH=arm",
            "CROSS_COMPILE=arm-none-linux-gnueabihf-",
            f"-j{jobs}",
            "zImage",
        ]
        log("+ " + " ".join(cmd))
        result = subprocess.run(cmd, env=env)
        if result.returncode != 0:
            die(f"make zImage 失败（退出码 {result.returncode}）。H3 不能用 booti 启动未压缩 Image。")

    if linux_zimage.is_file() and linux_zimage.stat().st_size >= 1024 * 1024 and arch is not True:
        soc_dir("h3").mkdir(parents=True, exist_ok=True)
        if not archived.is_file() or not filecmp.cmp(linux_zimage, archived, shallow=False):
            shutil.copy2(linux_zimage, archived)
            log(f"已归档 zImage: {archived}")
        return archived

    if archived.is_file() and archived.stat().st_size >= 1024 * 1024:
        log(f"使用已归档的 zImage: {archived}")
        return archived

    die(
        "H3 需要 zImage。当前 U-Boot 是 32 位，Kconfig 里 booti 只给 ARM64，串口会出现 Unknown command 'booti'。\n"
        "请在内核树仍是 H3 配置时执行 buidl_tools.sh h3，或:\n"
        "  make -C linux ARCH=arm CROSS_COMPILE=arm-none-linux-gnueabihf- zImage\n"
        f"期望文件: {archived}"
    )


def locate_artifacts(soc: str) -> dict[str, Path]:
    info = SOC_INFO[soc]
    uboot, dtb = locate_uboot_and_dtb(soc)
    if soc == "h3":
        image = build_h3_zimage()
    else:
        image = first_existing(
            [
                soc_dir(soc) / info["kernel_file"],
                LINUX_DIR / info["linux_image"],
            ]
        )
        if image is None:
            die(f"缺少内核 Image。请先执行 buidl_tools.sh {soc}，期望文件: {soc_dir(soc) / 'Image'}")
    require_min_size(image, 1024 * 1024, "内核 " + info["kernel_file"])
    return {"uboot": uboot, "image": image, "dtb": dtb}


def find_mkimage() -> str:
    local = REPO_ROOT / "u-boot" / "tools" / "mkimage"
    if local.is_file() and os.access(local, os.X_OK):
        return str(local)
    found = shutil.which("mkimage")
    if found:
        return found
    die("未找到 mkimage。请先编译 U-Boot（生成 u-boot/tools/mkimage），或安装 u-boot-tools。")


def write_boot_script(soc: str) -> Path:
    """生成 boot.cmd / boot.scr。

    在指南原命令前写明 mmc 0 的分区变量。否则只有 distro boot 预先设置过
    devtype/devnum/distro_bootpart 时，load 才能找到 Image 和 dtb。
    """
    info = SOC_INFO[soc]
    dtb_name = info["dtb_name"]
    kernel_file = info["kernel_file"]
    boot_cmd = info["boot_cmd"]
    content = (
        'setenv bootargs "console=ttyS0,115200 root=/dev/mmcblk0p2 rootwait panic=10"\n'
        "setenv devtype mmc\n"
        "setenv devnum 0\n"
        "setenv distro_bootpart 1\n"
        "mmc dev 0\n"
        "load ${devtype} ${devnum}:${distro_bootpart} ${kernel_addr_r} " + kernel_file + "\n"
        "load ${devtype} ${devnum}:${distro_bootpart} ${fdt_addr_r} " + dtb_name + "\n"
        + boot_cmd + " ${kernel_addr_r} - ${fdt_addr_r}\n"
    )
    cmd_path = soc_dir(soc) / "boot.cmd"
    scr_path = soc_dir(soc) / "boot.scr"
    with cmd_path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(content)
    run([find_mkimage(), "-C", "none", "-A", info["mkimage_arch"], "-T", "script", "-d", str(cmd_path), str(scr_path)])
    if not scr_path.is_file() or scr_path.stat().st_size == 0:
        die(f"未生成 boot.scr: {scr_path}")
    log(f"已生成启动脚本: {scr_path}")
    return scr_path


def config_is_arm64(linux_dir: Path) -> bool | None:
    config = linux_dir / ".config"
    if not config.is_file():
        return None
    arm64 = False
    arm = False
    with config.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if line.startswith("CONFIG_ARM64=y"):
                arm64 = True
            elif line.startswith("CONFIG_ARM=y"):
                arm = True
    if arm64:
        return True
    if arm:
        return False
    return None


def decide_modules(soc: str, packed_image: Path, skip: bool) -> str:
    """决定是否执行 modules_install。

    H3 与 H5 共用 linux/。当前 .config 若是另一颗芯片，安装模块会把错误架构的 .ko 写进镜像。
    build/<soc>/Image 若和 linux 树里的 Image 内容不同，模块版本也会和实际启动的内核不一致。
    """
    if skip:
        log("已指定 --skip-modules，不安装内核模块。")
        return "skipped: --skip-modules"

    want64 = soc == "h5"
    arch = config_is_arm64(LINUX_DIR)
    if arch is None:
        log("警告: 未找到可用的 linux/.config，跳过模块安装。镜像里只有编进内核的驱动，以及 rootfs 里原有的模块。")
        return "skipped: no .config"
    if arch != want64:
        die(
            f"当前 linux/.config 是 {'arm64' if arch else 'arm32'}，与本次打包的 {soc} 不一致。"
            "H3/H5 共用同一棵内核树，不能把另一颗芯片的模块装进镜像。"
            f"请先执行 buidl_tools.sh {soc} 重新编译；若 rootfs 里已经有匹配模块，再加 --skip-modules。"
        )

    order = LINUX_DIR / "modules.order"
    if not order.is_file():
        log("警告: 未找到 linux/modules.order，跳过模块安装。请先让 buidl_tools.sh 完成 make modules。")
        return "skipped: no modules.order"

    linux_image = LINUX_DIR / SOC_INFO[soc]["linux_image"]
    if linux_image.is_file():
        if not filecmp.cmp(linux_image, packed_image, shallow=False):
            die(
                "将写入启动分区的内核与 linux 树中的内核内容不一致，装上去的模块会和实际启动的内核不匹配。\n"
                f"启动用: {packed_image}\n"
                f"内核树: {linux_image}\n"
                f"请重新执行 buidl_tools.sh {soc}，或在确认 rootfs 已含对应模块后加 --skip-modules。"
            )
    else:
        log(f"警告: linux 树中没有 {linux_image.name}，仍按当前内核树安装模块。若编译中断过，请先重新编译。")

    gcc = cross_gcc(soc)
    if gcc is None:
        die(
            f"需要安装模块，但找不到 {SOC_INFO[soc]['cross']}gcc。"
            "请先执行 buidl_tools.sh 解压工具链，或改用 --skip-modules。"
        )
    if shutil.which("depmod") is None:
        die("需要安装模块，但找不到 depmod（软件包 kmod）。")
    return "install"


def cross_gcc(soc: str) -> str | None:
    info = SOC_INFO[soc]
    name = info["cross"] + "gcc"
    local = TOOLS_DIR / info["toolchain_dir"] / "bin" / name
    if local.is_file() and os.access(local, os.X_OK):
        return str(local)
    return shutil.which(name)


def toolchain_path(soc: str) -> str:
    bindir = TOOLS_DIR / SOC_INFO[soc]["toolchain_dir"] / "bin"
    current = os.environ.get("PATH", "")
    if bindir.is_dir():
        return str(bindir) + os.pathsep + current
    return current


def install_modules(soc: str, root_mount: str) -> None:
    info = SOC_INFO[soc]
    log("======== 安装内核模块到镜像根分区 ========")
    run(
        [
            "sudo",
            "env",
            "PATH=" + toolchain_path(soc),
            "make",
            "-C",
            str(LINUX_DIR),
            "ARCH=" + info["make_arch"],
            "CROSS_COMPILE=" + info["cross"],
            "INSTALL_MOD_PATH=" + root_mount,
            "modules_install",
        ]
    )


def du_bytes(path: Path) -> int:
    result = subprocess.run(["du", "-sb", str(path)], capture_output=True, text=True)
    if result.returncode != 0:
        result = subprocess.run(["sudo", "du", "-sb", str(path)], capture_output=True, text=True)
    if result.returncode != 0:
        detail = (result.stderr or "").strip()
        die(f"无法统计 rootfs 大小: {path} {detail}")
    token = result.stdout.split()
    if not token:
        die(f"du 没有输出大小: {path}")
    return int(token[0])


def bytes_to_mib(size: int) -> int:
    return (size + 1024 * 1024 - 1) // (1024 * 1024)


def choose_image_mib(root_bytes: int, will_install_modules: bool, size_arg: str | None) -> int:
    root_mib = bytes_to_mib(root_bytes)
    modules_mib = MODULES_RESERVE_MIB if will_install_modules else 0
    if size_arg is None:
        total = BOOT_END_MIB + root_mib + modules_mib + ROOT_FREE_MIB
        image_mib = max(MIN_IMAGE_MIB, align_up(total, 128))
        log(
            f"未指定 --size，使用 {image_mib}MiB"
            f"（下限 {MIN_IMAGE_MIB}MiB；rootfs 约 {root_mib}MiB，模块预留 {modules_mib}MiB，根分区空闲 {ROOT_FREE_MIB}MiB）"
        )
        return image_mib

    image_mib = parse_size_mib(size_arg)
    usable = image_mib - BOOT_END_MIB
    need = root_mib + modules_mib + EXPLICIT_SLACK_MIB
    if usable < need:
        die(
            f"指定大小 {image_mib}MiB 不够。根分区可用 {usable}MiB，"
            f"rootfs 约 {root_mib}MiB，模块预留 {modules_mib}MiB，还需要 {EXPLICIT_SLACK_MIB}MiB 余量。"
            f"请至少使用 --size {BOOT_END_MIB + need}M，或不传 --size 让脚本自己放大。"
        )
    log(f"使用指定镜像大小: {image_mib}MiB（rootfs 约 {root_mib}MiB）")
    return image_mib


def cleanup_mounts(soc: str, loop: str | None) -> None:
    for point in (mount_root(soc), mount_boot(soc)):
        if run_quiet(["mountpoint", "-q", point]) == 0:
            run_quiet(["sudo", "umount", point])
            if run_quiet(["mountpoint", "-q", point]) == 0:
                run_quiet(["sudo", "umount", "-l", point])
    if loop:
        run_quiet(["sudo", "losetup", "-d", loop])
    record = loop_record(soc)
    if record.exists():
        record.unlink()


def wait_partitions(loop: str) -> tuple[str, str]:
    part1 = loop + "p1"
    part2 = loop + "p2"
    for _ in range(20):
        if os.path.exists(part1) and os.path.exists(part2):
            return part1, part2
        run_quiet(["sudo", "partprobe", loop])
        run_quiet(["sudo", "partx", "-u", loop])
        time.sleep(0.3)
    die(f"循环设备已绑定，但未出现分区节点: {part1} {part2}")


def copy_rootfs(source: Path, destination: str) -> None:
    log(f"复制 rootfs: {source} -> {destination}")
    run(["sudo", "cp", "-a", str(source) + "/.", destination + "/"])


def assert_rootfs_not_mounted(path: Path) -> None:
    """rootfs 目录里若还挂着 chroot 用的 proc/sys/dev，cp 会把宿主机目录卷进镜像。"""
    mounts = Path("/proc/mounts")
    if not mounts.is_file():
        return
    root = str(path.resolve())
    hits = []
    with mounts.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            parts = line.split()
            if len(parts) < 2:
                continue
            target = parts[1].replace("\\040", " ")
            if target == root or target.startswith(root + "/"):
                hits.append(target)
    if hits:
        die("rootfs 目录下仍有挂载点，请先卸载再打包:\n" + "\n".join(hits))


def ensure_disk_space(root_bytes: int) -> None:
    free = shutil.disk_usage(BUILD_ROOT).free
    need = root_bytes + 512 * 1024 * 1024
    if free < need:
        die(
            f"build/ 所在磁盘剩余空间不足。剩余约 {free // (1024 * 1024)}MiB，"
            f"至少需要约 {need // (1024 * 1024)}MiB 用来写入 rootfs。"
        )


def create_image(soc: str, artifacts: dict[str, Path], rootfs: Path, image_mib: int, module_action: str) -> None:
    img = partial_image_path(soc)
    info = SOC_INFO[soc]
    boot_scr = soc_dir(soc) / "boot.scr"
    if img.exists():
        img.unlink()
    log(f"======== 创建稀疏镜像 {img} （{image_mib}MiB） ========")
    log("成功结束后才会替换为 " + str(image_path(soc)))
    run(["truncate", "-s", f"{image_mib}M", str(img)])
    run(["sudo", "parted", "-s", str(img), "--", "mktable", "msdos"])
    run(["sudo", "parted", "-s", str(img), "--", "mkpart", "primary", "fat32", "1MiB", f"{BOOT_END_MIB}MiB"])
    run(["sudo", "parted", "-s", str(img), "--", "mkpart", "primary", "ext4", f"{BOOT_END_MIB}MiB", "100%"])

    log("+ sudo losetup --find --show --partscan " + str(img))
    result = subprocess.run(
        ["sudo", "losetup", "--find", "--show", "--partscan", str(img)],
        stdout=subprocess.PIPE,
        text=True,
    )
    if result.returncode != 0:
        die(f"losetup 失败（退出码 {result.returncode}）")
    loop = ""
    for line in (result.stdout or "").splitlines():
        line = line.strip()
        if line.startswith("/dev/"):
            loop = line
    if not loop:
        die(f"losetup 没有返回循环设备名: {result.stdout!r}")
    loop_record(soc).write_text(loop + "\n", encoding="utf-8")
    log(f"循环设备: {loop}")

    part1, part2 = wait_partitions(loop)
    run(["sudo", "mkfs.vfat", "-n", "BOOT", part1])
    run(["sudo", "mkfs.ext4", "-F", "-L", "rootfs", part2])

    boot_point = mount_boot(soc)
    root_point = mount_root(soc)
    run(["sudo", "mkdir", "-p", boot_point, root_point])
    run(["sudo", "mount", part1, boot_point])
    run(["sudo", "mount", part2, root_point])

    run(["sudo", "cp", "-f", str(artifacts["image"]), boot_point + "/" + info["kernel_file"]])
    run(["sudo", "cp", "-f", str(artifacts["dtb"]), boot_point + "/" + info["dtb_name"]])
    run(["sudo", "cp", "-f", str(boot_scr), boot_point + "/boot.scr"])

    copy_rootfs(rootfs, root_point)
    run(["sudo", "mkdir", "-p", *(root_point + "/" + name for name in ("proc", "sys", "dev", "run", "tmp"))])
    run(["sudo", "chmod", "1777", root_point + "/tmp"])

    if module_action == "install":
        install_modules(soc, root_point)

    run(["sudo", "sync"])
    run(["sudo", "umount", root_point])
    run(["sudo", "umount", boot_point])
    log("======== 写入 U-Boot（偏移 8KiB） ========")
    run(["sudo", "dd", f"if={artifacts['uboot']}", f"of={loop}", "bs=1k", "seek=8", "conv=notrunc"])
    run(["sudo", "sync"])
    run(["sudo", "losetup", "-d", loop])
    if loop_record(soc).exists():
        loop_record(soc).unlink()


def write_manifest(soc: str, artifacts: dict[str, Path], rootfs: Path, rootfs_origin: str, image_mib: int, module_action: str) -> None:
    img = image_path(soc)
    lines = [
        f"soc={soc}",
        f"image={img}",
        f"image_mib={image_mib}",
        f"uboot={artifacts['uboot']}",
        f"kernel={artifacts['image']}",
        f"dtb={artifacts['dtb']}",
        f"dtb_boot_name={SOC_INFO[soc]['dtb_name']}",
        f"rootfs={rootfs}",
        f"rootfs_origin={rootfs_origin}",
        f"modules={module_action}",
        'bootargs=console=ttyS0,115200 root=/dev/mmcblk0p2 rootwait panic=10',
        "",
    ]
    path = soc_dir(soc) / "makeimg-manifest.txt"
    path.write_text("\n".join(lines), encoding="utf-8")
    log(f"清单: {path}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="把 H3/H5 编译产物和 rootfs 打成 TF 卡启动镜像。不写物理磁盘。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例:\n"
            "  python3 build-scripts/makeimg.py h5\n"
            "  python3 build-scripts/makeimg.py h5 --rootfs ./tools/arm64_rootfs --size 2048M\n"
            "  python3 build-scripts/makeimg.py h3 --rootfs ./tools/rootfs-arm32.tar.gz\n"
            "\n"
            "未指定 --rootfs 时，H5 优先使用 tools/rootfs-arm64.tar.gz，H3 优先使用 tools/rootfs-arm32.tar.gz。\n"
            "未指定 --size 时，至少 2048MiB，rootfs 更大时会自动加大。"
        ),
    )
    parser.add_argument("soc", choices=("h3", "h5"), help="目标芯片，必填")
    parser.add_argument(
        "--rootfs",
        default=None,
        help="根文件系统目录，或 .tar.gz/.tgz。相对路径先相对当前目录，再相对仓库根。省略则按芯片在 tools/ 中查找",
    )
    parser.add_argument(
        "--size",
        default=None,
        help="镜像大小，例如 2048M、2G。省略时至少 2048MiB，并按 rootfs 体积自动加大",
    )
    parser.add_argument(
        "--skip-modules",
        action="store_true",
        help="不执行 modules_install。默认会在内核树架构匹配且已编译模块时，把模块装进镜像",
    )
    return parser.parse_args()


def main() -> None:
    ensure_linux()
    args = parse_args()
    soc = args.soc
    require_commands(
        [
            "sudo",
            "parted",
            "mkfs.vfat",
            "mkfs.ext4",
            "losetup",
            "truncate",
            "dd",
            "tar",
            "du",
            "cp",
            "mount",
            "umount",
            "mountpoint",
            "sync",
            "make",
        ]
    )
    open_log(soc)

    stale = loop_record(soc).read_text(encoding="utf-8").strip() if loop_record(soc).is_file() else None
    cleanup_mounts(soc, stale or None)

    log(f"======== 打包 {soc} 启动镜像 ========")
    artifacts = locate_artifacts(soc)
    rootfs, origin = prepare_rootfs(soc, args.rootfs)
    assert_rootfs_not_mounted(rootfs)
    boot_scr = write_boot_script(soc)
    module_action = decide_modules(soc, artifacts["image"], args.skip_modules)
    root_bytes = du_bytes(rootfs)
    image_mib = choose_image_mib(root_bytes, module_action == "install", args.size)
    ensure_disk_space(root_bytes)

    log(f"U-Boot: {artifacts['uboot']}")
    log(f"内核:   {artifacts['image']} -> 启动分区文件名 {SOC_INFO[soc]['kernel_file']}，启动命令 {SOC_INFO[soc]['boot_cmd']}")
    log(f"DTB:    {artifacts['dtb']} -> 启动分区文件名 {SOC_INFO[soc]['dtb_name']}")
    log(f"boot:   {boot_scr}")
    log(f"rootfs: {rootfs} ({origin})")
    log(f"modules: {module_action}")

    partial = partial_image_path(soc)
    image_started = False
    success = False
    loop = None
    try:
        image_started = True
        create_image(soc, artifacts, rootfs, image_mib, module_action)
        os.replace(partial, image_path(soc))
        success = True
    finally:
        if not success:
            if loop_record(soc).is_file():
                loop = loop_record(soc).read_text(encoding="utf-8").strip() or None
            cleanup_mounts(soc, loop)
            if image_started and partial.exists():
                log(f"打包未完成，删除不完整镜像: {partial}")
                partial.unlink()

    final_image = image_path(soc)
    write_manifest(soc, artifacts, rootfs, origin, image_mib, module_action)
    log("======== 镜像已生成 ========")
    log(f"输出: {final_image}")
    log("本脚本不写 TF 卡。确认目标盘符后，在 Linux 上手工烧录，例如:")
    log(f"  sudo dd if={final_image} of=/dev/sdX bs=4M status=progress conv=fsync")
    log("把 /dev/sdX 换成 lsblk 里的 TF 卡整盘，不要写成某个分区。")


if __name__ == "__main__":
    main()
