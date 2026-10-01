#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""H3/H5 统一编译入口。命令行带 img 时，在同一次运行里打包 TF 卡镜像。

不带参数或 --help 会打印 H3 与 H5 的可复制命令。
不带 img 时只编译，结束后提示打包命令，以及单独执行 makeimg.py 的命令。
打包逻辑从 makeimg.py 移植到本文件，运行时不执行 makeimg.py。
rootfs 与镜像大小使用 makeimg.py 的默认规则。
工具链 PATH 只存在于本 Python 进程及其子进程。
"""

from __future__ import annotations

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

LFS_POINTER_PREFIX = "version https://git-lfs.github.com/spec/v1"
ARCHIVE_MIN_BYTES = 100000

BOOT_END_MIB = 256
MIN_IMAGE_MIB = 2048
MODULES_RESERVE_MIB = 256
ROOT_FREE_MIB = 512
EXPLICIT_SLACK_MIB = 64

STAGED_FW_REL = Path("lib/firmware/rtlwifi/rtl8723bu_nic.bin")
RTL_OBJ = LINUX_DIR / "drivers/net/wireless/realtek/rtl8xxxu/8723b.o"
RTL_FW_SRC = LINUX_DIR / "drivers/net/wireless/realtek/rtl8xxxu/firmware/rtl8723bu_nic.bin"

OR1K_DIR = TOOLS_DIR / "or1k-linux-musl-7.2.0"
OR1K_ARCHIVE = TOOLS_DIR / "or1k-linux-musl-7.2.0-20180317.tar.gz"
OR1K_GCC_NAME = "or1k-linux-musl-gcc"

SOC_INFO = {
    "h5": {
        "mkimage_arch": "arm64",
        "make_arch": "arm64",
        "cross": "aarch64-none-linux-gnu-",
        "toolchain_dir": "15.2.rel1-arm64",
        "toolchain_archive": "arm-gnu-toolchain-15.2.rel1-x86_64-aarch64-none-linux-gnu.tar.xz",
        "uboot_defconfig": "quark-luoorshi-h5_defconfig",
        "kernel_defconfig": "quark-luoorshi-h5_defconfig",
        "uboot_name": "u-boot-sunxi-with-spl-h5.bin",
        "dtb_name": "sun50i-h5-quark-luoorshi.dtb",
        "kernel_file": "Image",
        "boot_cmd": "booti",
        "linux_image": Path("arch/arm64/boot/Image"),
        "linux_dtb": Path("arch/arm64/boot/dts/allwinner/sun50i-h5-quark-luoorshi.dtb"),
        "kernel_targets": ["Image", "dtbs", "modules"],
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
        "toolchain_archive": "arm-gnu-toolchain-15.2.rel1-x86_64-arm-none-linux-gnueabihf.tar.xz",
        "uboot_defconfig": "quark-luoorshi-h3_defconfig",
        "kernel_defconfig": "quark-luoorshi-h3_defconfig",
        "uboot_name": "u-boot-sunxi-with-spl-h3.bin",
        "dtb_name": "sun8i-h3-quark-luoorshi.dtb",
        "kernel_file": "zImage",
        "boot_cmd": "bootz",
        "linux_image": Path("arch/arm/boot/zImage"),
        "linux_dtb": Path("arch/arm/boot/dts/allwinner/sun8i-h3-quark-luoorshi.dtb"),
        "kernel_targets": ["Image", "zImage", "dtbs", "modules"],
        "archives": (
            "rootfs-arm32.tar.gz",
            "arm32_rootfs.tar.gz",
            "arm32-rootfs.tar.gz",
            "rootfs-armhf.tar.gz",
        ),
        "dirs": ("arm32_rootfs",),
    },
}

USAGE = """\
H3 与 H5 都可以编译。不带参数、--help 或 -h 只打印下面这些命令，不开始编译。

H5 只编译:
  python3 build-scripts/buidl_tools.py h5
  python3 build-scripts/buidl_tools.py h5 debug

H5 编译并打包:
  python3 build-scripts/buidl_tools.py h5 img
  python3 build-scripts/buidl_tools.py h5 debug img

H3 只编译:
  python3 build-scripts/buidl_tools.py h3
  python3 build-scripts/buidl_tools.py h3 debug

H3 编译并打包:
  python3 build-scripts/buidl_tools.py h3 img
  python3 build-scripts/buidl_tools.py h3 debug img

单独打包，不重新编译:
  python3 build-scripts/makeimg.py h5
  python3 build-scripts/makeimg.py h3

说明:
  debug  可省略。写上则相关 make 增加 V=1。可以写在 img 前面或后面。
  img    可省略。写上则编译成功后在本脚本内打包。不写则只编译，结束时会再提示打包命令。
  debug 与 img 不能重复，也不能写其它参数。
"""

_LOG_FH = None


def log(msg: str) -> None:
    print(msg, flush=True)
    if _LOG_FH is not None:
        _LOG_FH.write(msg + "\n")
        _LOG_FH.flush()


def die(msg: str) -> None:
    text = "错误: " + msg
    print(text, file=sys.stderr, flush=True)
    if _LOG_FH is not None:
        _LOG_FH.write(text + "\n")
        _LOG_FH.flush()
    sys.exit(1)


def _quote(text: str) -> str:
    if text == "" or any(ch.isspace() for ch in text):
        return "'" + text.replace("'", "'\"'\"'") + "'"
    return text


def run(cmd: list[str], cwd: Path | None = None, fail: str | None = None) -> None:
    shown = " ".join(_quote(str(part)) for part in cmd)
    log("+ " + shown)
    result = subprocess.run(cmd, cwd=None if cwd is None else str(cwd))
    if result.returncode != 0:
        die(fail or f"命令失败（退出码 {result.returncode}）: {shown}")


def run_quiet(cmd: list[str]) -> int:
    result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return result.returncode


def run_tee(cmd: list[str], log_path: Path, cwd: Path) -> int:
    """把子进程的标准输出和标准错误同时写到终端和日志。返回子进程退出码。"""
    shown = " ".join(_quote(str(part)) for part in cmd)
    log("+ " + shown)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        handle = log_path.open("wb")
    except OSError as exc:
        die(f"无法写入日志 {log_path}: {exc}")
    with handle:
        proc = subprocess.Popen(
            cmd,
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        assert proc.stdout is not None
        while True:
            chunk = proc.stdout.read(4096)
            if not chunk:
                break
            sys.stdout.buffer.write(chunk)
            sys.stdout.buffer.flush()
            handle.write(chunk)
        return proc.wait()


def ensure_linux() -> None:
    if not sys.platform.startswith("linux"):
        die("本脚本必须在 Linux 上运行。请在编译机上执行，不要在 Windows 上执行。")


def require_commands(names: list[str]) -> None:
    missing = [name for name in names if shutil.which(name) is None]
    if missing:
        die(
            "缺少命令: "
            + ", ".join(missing)
            + "。可安装: sudo apt install -y parted dosfstools e2fsprogs util-linux kmod coreutils tar"
        )


def open_log(soc: str) -> None:
    global _LOG_FH
    out_dir = BUILD_ROOT / soc
    out_dir.mkdir(parents=True, exist_ok=True)
    _LOG_FH = open(out_dir / "makeimg.log", "w", encoding="utf-8")


def parse_cli(argv: list[str]) -> tuple[str, bool, bool]:
    if not argv or argv[0] in ("-h", "--help"):
        print(USAGE, end="")
        sys.exit(0)
    soc = argv[0]
    if soc not in ("h3", "h5"):
        print(USAGE, end="", file=sys.stderr)
        die(f"缺少或无效参数: 需要 h3 或 h5，收到 {soc}")
    flags = argv[1:]
    unknown = [item for item in flags if item not in ("debug", "img")]
    if unknown:
        print(USAGE, end="", file=sys.stderr)
        die("不能识别的参数: " + " ".join(unknown))
    if len(flags) != len(set(flags)):
        die("debug 和 img 不能重复")
    return soc, "debug" in flags, "img" in flags


def warn_missing_host_tools() -> None:
    missing = [name for name in ("gcc", "bison", "flex") if shutil.which(name) is None]
    if missing:
        log("=========================================")
        log("警告：系统可能缺少编译工具: " + " ".join(missing))
        log("建议: sudo apt install -y build-essential bison flex")
        log("=========================================")


def detect_jobs() -> str:
    jobs = os.environ.get("JOBS")
    if jobs:
        return jobs
    count = os.cpu_count()
    return str(count if count else 4)


def make_verbose_args() -> list[str]:
    if os.environ.get("MAKE_VERBOSE"):
        return ["V=1"]
    return []


def prepend_path(bindir: Path) -> None:
    text = str(bindir)
    parts = os.environ.get("PATH", "").split(os.pathsep)
    if text not in parts:
        os.environ["PATH"] = text + os.pathsep + os.environ.get("PATH", "")


def first_line(path: Path) -> str:
    with path.open("rb") as handle:
        raw = handle.readline(512)
    return raw.decode("utf-8", errors="replace").rstrip("\r\n")


def archive_needs_git_lfs_pull(archive: Path, min_bytes: int = ARCHIVE_MIN_BYTES) -> bool:
    if not archive.is_file():
        return True
    if first_line(archive) == LFS_POINTER_PREFIX:
        return True
    return archive.stat().st_size < min_bytes


def require_real_toolchain_archive(archive: Path, desc: str, min_bytes: int = ARCHIVE_MIN_BYTES) -> None:
    if not archive.is_file():
        die(f"未找到 {desc}: {archive}")
    size = archive.stat().st_size
    line = first_line(archive)
    if size < min_bytes:
        if line == LFS_POINTER_PREFIX:
            die(
                f"{desc} 仍为 Git LFS 指针（{size} 字节）。请在仓库根执行: git lfs pull\n"
                f"或按 tools/README.md 将完整压缩包放到: {archive}"
            )
        die(f"{desc} 文件过小（{size} 字节），可能损坏或未完整下载: {archive}")


def git_lfs_available() -> bool:
    return shutil.which("git") is not None and run_quiet(["git", "lfs", "version"]) == 0


def ensure_git_lfs_cli() -> None:
    if git_lfs_available():
        return
    log("未检测到 Git LFS 客户端，尝试安装 git-lfs ...")
    if shutil.which("apt-get"):
        run(["sudo", "apt-get", "update", "-qq"], fail="通过 apt 安装 git-lfs 失败，请手动执行: sudo apt install -y git-lfs")
        run(["sudo", "apt-get", "install", "-y", "git-lfs"], fail="通过 apt 安装 git-lfs 失败，请手动执行: sudo apt install -y git-lfs")
    elif shutil.which("dnf"):
        run(["sudo", "dnf", "install", "-y", "git-lfs"], fail="通过 dnf 安装 git-lfs 失败，请手动: sudo dnf install -y git-lfs")
    elif shutil.which("yum"):
        run(["sudo", "yum", "install", "-y", "git-lfs"], fail="通过 yum 安装 git-lfs 失败，请手动: sudo yum install -y git-lfs")
    elif shutil.which("pacman"):
        run(["sudo", "pacman", "-S", "--noconfirm", "git-lfs"], fail="通过 pacman 安装 git-lfs 失败，请手动: sudo pacman -S git-lfs")
    else:
        die("未找到 git-lfs，且无法自动安装（无 apt-get/dnf/yum/pacman）。请自行安装 git-lfs 后重试。")
    if not git_lfs_available():
        die("安装后 git lfs 仍不可用，请检查 PATH 与安装日志")
    result = subprocess.run(["git", "lfs", "version"], capture_output=True, text=True)
    first = ""
    for line in (result.stdout or "").splitlines():
        if line.strip():
            first = line.strip()
            break
    log("git-lfs 已可用: " + (first or "git lfs"))


def ensure_tools_git_lfs_archives(soc: str) -> None:
    names = [SOC_INFO[soc]["toolchain_archive"]]
    if soc == "h5":
        names.append(OR1K_ARCHIVE.name)
    archives = [TOOLS_DIR / name for name in names]
    if not any(archive_needs_git_lfs_pull(path) for path in archives):
        return
    if os.environ.get("LOIS_SKIP_GIT_LFS"):
        die("tools 工具链包未就绪（LFS 指针或过小），但已设置 LOIS_SKIP_GIT_LFS，跳过自动拉取。请自行放入完整压缩包或取消该变量。")
    log("======== 检测到 tools 工具链包为 Git LFS 指针或未完整，将安装/使用 git-lfs 并拉取 ========")
    if run_quiet(["git", "-C", str(REPO_ROOT), "rev-parse", "--is-inside-work-tree"]) != 0:
        die(f"无法自动拉取：{REPO_ROOT} 不是 git 工作区。请将真实压缩包放入 tools/，或使用含 LFS 的 git clone。")
    ensure_git_lfs_cli()
    run(["git", "lfs", "install"], cwd=REPO_ROOT, fail="git lfs pull 失败。请检查网络、LFS 远端与凭证。")
    run(["git", "lfs", "pull"], cwd=REPO_ROOT, fail="git lfs pull 失败。请检查网络、LFS 远端与凭证。")
    for path in archives:
        if archive_needs_git_lfs_pull(path):
            die(f"git lfs pull 后仍异常（仍为指针或过小）: {path}")
    log("======== tools 大文件已通过 Git LFS 就绪 ========")


def ensure_archived_toolchain(soc: str) -> None:
    info = SOC_INFO[soc]
    dest = TOOLS_DIR / info["toolchain_dir"]
    gcc_name = info["cross"] + "gcc"
    gcc_local = dest / "bin" / gcc_name
    archive = TOOLS_DIR / info["toolchain_archive"]
    desc = "AArch64 工具链压缩包" if soc == "h5" else "ARM32 工具链压缩包"
    if gcc_local.is_file() and os.access(gcc_local, os.X_OK):
        prepend_path(dest / "bin")
        return
    if shutil.which(gcc_name):
        return
    require_real_toolchain_archive(archive, desc)
    log(f"正在解压 {desc} 到 {dest} ...")
    dest.mkdir(parents=True, exist_ok=True)
    run(
        ["tar", "-xJf", str(archive), "-C", str(dest), "--strip-components=1"],
        fail=f"{desc} 解压失败（请确认 tar 支持 -J/xz，且压缩包完整）",
    )
    if not (gcc_local.is_file() and os.access(gcc_local, os.X_OK)):
        die(f"解压后未找到: {gcc_local}")
    prepend_path(dest / "bin")


def find_or1k_gcc(root: Path) -> Path | None:
    """等价于 find root -maxdepth 4 -type f -name or1k-linux-musl-gcc，取第一个可执行文件。"""
    for dirpath, dirnames, filenames in os.walk(root):
        rel = Path(dirpath).relative_to(root)
        depth = 0 if str(rel) == "." else len(rel.parts)
        if depth >= 4:
            dirnames[:] = []
            continue
        if OR1K_GCC_NAME not in filenames:
            continue
        candidate = Path(dirpath) / OR1K_GCC_NAME
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    return None


def clear_children(path: Path) -> None:
    for child in path.iterdir():
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        else:
            child.unlink()


def ensure_or1k_toolchain() -> None:
    gcc_local = OR1K_DIR / "bin" / OR1K_GCC_NAME
    if gcc_local.is_file() and os.access(gcc_local, os.X_OK):
        prepend_path(OR1K_DIR / "bin")
        return
    if shutil.which(OR1K_GCC_NAME):
        return
    require_real_toolchain_archive(OR1K_ARCHIVE, "or1k 工具链压缩包")
    log(f"正在解压 or1k 工具链到 {OR1K_DIR} ...")
    OR1K_DIR.mkdir(parents=True, exist_ok=True)
    shown = " ".join(
        [
            "tar",
            "-xzf",
            str(OR1K_ARCHIVE),
            "-C",
            str(OR1K_DIR),
            "--strip-components=1",
        ]
    )
    log("+ " + shown)
    stripped = subprocess.run(
        ["tar", "-xzf", str(OR1K_ARCHIVE), "-C", str(OR1K_DIR), "--strip-components=1"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if stripped.returncode != 0:
        clear_children(OR1K_DIR)
        run(["tar", "-xzf", str(OR1K_ARCHIVE), "-C", str(OR1K_DIR)], fail="or1k 工具链解压失败")
        if not (gcc_local.is_file() and os.access(gcc_local, os.X_OK)):
            found = find_or1k_gcc(OR1K_DIR)
            if found is None:
                die("解压后未找到 or1k-linux-musl-gcc")
            prepend_path(found.parent)
            return
    if not (gcc_local.is_file() and os.access(gcc_local, os.X_OK)):
        die(f"解压后未找到: {gcc_local}")
    prepend_path(OR1K_DIR / "bin")


def comment_or1k_incompatible_cflags(path: Path) -> None:
    """把与老旧 or1k-linux-musl 不兼容的 CFLAGS 续行临时注释掉。已注释的行保持原样。"""
    needle = "-msfimm -mshftimm -msoft-div -msoft-mul"
    raw = path.read_bytes()
    newline = b"\r\n" if b"\r\n" in raw else b"\n"
    text = raw.decode("utf-8")
    lines = text.splitlines()
    changed = []
    for line in lines:
        if needle in line and not line.lstrip().startswith("#"):
            changed.append("# " + line)
        else:
            changed.append(line)
    payload = newline.decode("ascii").join(changed)
    if text.endswith("\n") or text.endswith("\r\n"):
        payload += newline.decode("ascii")
    path.write_bytes(payload.encode("utf-8"))


def build_atf() -> None:
    log("======== 编译 ATF (bl31) ========")
    prepend_path(TOOLS_DIR / SOC_INFO["h5"]["toolchain_dir"] / "bin")
    if shutil.which("aarch64-none-linux-gnu-gcc") is None:
        die("ATF 需要 aarch64-none-linux-gnu-gcc 在 PATH 中")
    run(
        ["make", *make_verbose_args(), "CROSS_COMPILE=aarch64-none-linux-gnu-", "PLAT=sun50i_a64", "bl31"],
        cwd=REPO_ROOT / "arm-trusted-firmware",
        fail="ATF 编译失败",
    )
    out = REPO_ROOT / "arm-trusted-firmware/build/sun50i_a64/release/bl31.bin"
    if not out.is_file():
        die(f"未生成 bl31.bin: {out}")


def build_crust() -> None:
    defconfig = os.environ.get("CRUST_DEFCONFIG") or "orangepi_zero_plus_defconfig"
    or1k_mk = REPO_ROOT / "crust/arch/or1k/Makefile"
    bak = Path(str(or1k_mk) + ".lois_bak")
    log(f"======== 编译 Crust (scp) defconfig={defconfig} ========")
    if not or1k_mk.is_file():
        die(f"未找到 {or1k_mk}")
    if shutil.which(OR1K_GCC_NAME) is None:
        die("Crust 需要 or1k-linux-musl-gcc 在 PATH 中")
    shutil.copy2(or1k_mk, bak)
    try:
        comment_or1k_incompatible_cflags(or1k_mk)
        crust = REPO_ROOT / "crust"
        run(["make", defconfig], cwd=crust, fail="Crust 编译失败")
        run(
            ["make", *make_verbose_args(), "CROSS_COMPILE=or1k-linux-musl-", "HOST_COMPILE="],
            cwd=crust,
            fail="Crust 编译失败",
        )
        if not (crust / "build/scp/scp.bin").is_file():
            die("Crust 编译失败")
    finally:
        if bak.is_file():
            os.replace(bak, or1k_mk)
    if not (REPO_ROOT / "crust/build/scp/scp.bin").is_file():
        die("未生成 scp.bin")


def build_uboot(soc: str) -> None:
    info = SOC_INFO[soc]
    jobs = detect_jobs()
    log(f"======== 编译 U-Boot ({soc.upper()}) ========")
    gcc_name = info["cross"] + "gcc"
    if shutil.which(gcc_name) is None:
        die(f"U-Boot {soc.upper()} 需要 {gcc_name}")
    if soc == "h5":
        bl31 = REPO_ROOT / "arm-trusted-firmware/build/sun50i_a64/release/bl31.bin"
        scp = REPO_ROOT / "crust/build/scp/scp.bin"
        if not bl31.is_file():
            die(f"缺少 BL31: {bl31}")
        if not scp.is_file():
            die(f"缺少 SCP: {scp}")
        os.environ["BL31"] = str(bl31)
        os.environ["SCP"] = str(scp)
    uboot = REPO_ROOT / "u-boot"
    run(
        ["make", info["uboot_defconfig"], "ARCH=arm", "CROSS_COMPILE=" + info["cross"]],
        cwd=uboot,
        fail="u-boot defconfig 失败",
    )
    code = run_tee(
        ["make", "ARCH=arm", "CROSS_COMPILE=" + info["cross"], *make_verbose_args(), "-j" + jobs],
        BUILD_ROOT / soc / "u-boot-build.log",
        uboot,
    )
    if code != 0:
        die(f"u-boot 编译失败 (exit {code})")
    if not (uboot / "u-boot-sunxi-with-spl.bin").is_file():
        die("未生成 u-boot-sunxi-with-spl.bin")


def build_kernel(soc: str) -> None:
    info = SOC_INFO[soc]
    jobs = detect_jobs()
    image = LINUX_DIR / "arch/arm64/boot/Image" if soc == "h5" else LINUX_DIR / "arch/arm/boot/Image"
    zimage = LINUX_DIR / "arch/arm/boot/zImage"
    dtb = LINUX_DIR / info["linux_dtb"]
    gcc_name = info["cross"] + "gcc"
    log(f"======== 编译 Linux 内核 ({soc.upper()}) ========")
    if shutil.which(gcc_name) is None:
        die(f"内核 {soc.upper()} 需要 {gcc_name}")
    run(
        ["make", info["kernel_defconfig"], "ARCH=" + info["make_arch"]],
        cwd=LINUX_DIR,
        fail="kernel defconfig 失败",
    )
    run(
        ["make", "olddefconfig", "ARCH=" + info["make_arch"], "CROSS_COMPILE=" + info["cross"]],
        cwd=LINUX_DIR,
        fail="kernel olddefconfig 失败",
    )
    if image.is_file():
        image.unlink()
    if soc == "h3" and zimage.is_file():
        zimage.unlink()
    code = run_tee(
        [
            "make",
            "ARCH=" + info["make_arch"],
            "CROSS_COMPILE=" + info["cross"],
            *make_verbose_args(),
            "-j" + jobs,
            *info["kernel_targets"],
        ],
        BUILD_ROOT / soc / "kernel-build.log",
        LINUX_DIR,
    )
    if code != 0:
        die(f"内核编译失败 (exit {code})，详见 {BUILD_ROOT / soc / 'kernel-build.log'}")
    if not image.is_file():
        die("未生成 " + str(image.relative_to(LINUX_DIR)))
    if soc == "h3" and not zimage.is_file():
        die("未生成 arch/arm/boot/zImage")
    if not dtb.is_file():
        die(f"未生成 DTB: {dtb}")


def copy_file(src: Path, dest: Path, desc: str) -> None:
    try:
        shutil.copy2(src, dest)
    except OSError as exc:
        die(f"{desc}: {dest} ({exc})")


def copy_rtl8723bu_firmware(soc: str) -> None:
    dest = BUILD_ROOT / soc / STAGED_FW_REL
    if not RTL_OBJ.is_file():
        log(f"未编译 8723b.c（没有 {RTL_OBJ}），跳过 RTL8723BU 固件")
        return
    if not RTL_FW_SRC.is_file():
        die(f"已编译 8723b.c，但缺少固件: {RTL_FW_SRC}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    copy_file(RTL_FW_SRC, dest, "复制 RTL8723BU 固件失败")
    log(f"RTL8723BU 固件已复制到 {dest}")


def copy_optional(src: Path, dest: Path) -> None:
    if src.is_file():
        shutil.copy2(src, dest)


def copy_artifacts(soc: str) -> None:
    info = SOC_INFO[soc]
    out = BUILD_ROOT / soc
    out.mkdir(parents=True, exist_ok=True)
    copy_file(
        REPO_ROOT / "u-boot/u-boot-sunxi-with-spl.bin",
        out / info["uboot_name"],
        "复制 u-boot 失败",
    )
    if soc == "h5":
        bl31 = REPO_ROOT / "arm-trusted-firmware/build/sun50i_a64/release/bl31.bin"
        scp = REPO_ROOT / "crust/build/scp/scp.bin"
        if not bl31.is_file():
            die(f"缺少 bl31.bin，无法归档: {bl31}")
        if not scp.is_file():
            die(f"缺少 scp.bin，无法归档: {scp}")
        copy_file(bl31, out / "bl31.bin", "复制 bl31.bin 失败")
        copy_file(scp, out / "scp.bin", "复制 scp.bin 失败")
        image = LINUX_DIR / "arch/arm64/boot/Image"
        if not image.is_file():
            die(f"缺少内核 Image，无法归档: {image}")
        copy_file(image, out / "Image", "复制 Image 失败")
    else:
        image = LINUX_DIR / "arch/arm/boot/Image"
        zimage = LINUX_DIR / "arch/arm/boot/zImage"
        if not image.is_file():
            die(f"缺少内核 Image，无法归档: {image}")
        if not zimage.is_file():
            die(f"缺少内核 zImage，无法归档: {zimage}")
        copy_file(image, out / "Image", "复制 Image 失败")
        copy_file(zimage, out / "zImage", "复制 zImage 失败")
    dtb = LINUX_DIR / info["linux_dtb"]
    if not dtb.is_file():
        die(f"缺少 DTB，无法归档: {dtb}")
    copy_file(dtb, out / info["dtb_name"], "复制 dtb 失败")
    copy_optional(LINUX_DIR / "System.map", out / "System.map")
    copy_optional(LINUX_DIR / ".config", out / "kernel.config")
    copy_rtl8723bu_firmware(soc)
    if soc == "h5":
        log(f"产物已复制到 {out}/ （含 u-boot、bl31、scp、Image、dtb）")
    else:
        log(f"产物已复制到 {out}/ （含 u-boot、Image、zImage、dtb）")


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
    """把 2048、2048M、2048MiB、2G 解析成 MiB。M/MB/MiB 都按 1024 进制。"""
    raw = text.strip().replace(" ", "")
    if not raw:
        die("镜像大小不能为空")
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
            die(f"找不到 rootfs: {raw}")
        return raw.resolve()
    if (Path.cwd() / raw).exists():
        return (Path.cwd() / raw).resolve()
    if (REPO_ROOT / raw).exists():
        return (REPO_ROOT / raw).resolve()
    die(f"找不到 rootfs: {text}。已尝试 {Path.cwd() / raw} 和 {REPO_ROOT / raw}")


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
        "tools/ 下没有找到该芯片的根文件系统。\n"
        f"压缩包（按顺序，找到即用）: {archive_list}\n"
        f"或已解压目录: {dir_list}"
    )


def prepare_rootfs(soc: str) -> tuple[Path, str]:
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
        die(f"本次编译未生成 U-Boot。期望文件: {soc_dir(soc) / info['uboot_name']}")
    if dtb is None:
        die(f"本次编译未生成设备树。期望文件: {soc_dir(soc) / info['dtb_name']}")
    require_min_size(uboot, 100 * 1024, "U-Boot")
    require_min_size(dtb, 1024, "设备树")
    return uboot, dtb


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


def build_h3_zimage() -> Path:
    """H3 的 U-Boot 只有 bootz。bootz 认的是 zImage。"""
    linux_zimage = LINUX_DIR / SOC_INFO["h3"]["linux_image"]
    archived = soc_dir("h3") / "zImage"
    arch = config_is_arm64(LINUX_DIR)
    if arch is False:
        if cross_gcc("h3") is None:
            die("H3 需要 zImage，但找不到 arm-none-linux-gnueabihf-gcc，无法生成。")
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
        "H3 需要 zImage。当前 U-Boot 是 32 位，Kconfig 里 booti 只给 ARM64。\n"
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
            die(f"本次编译未生成内核 Image。期望文件: {soc_dir(soc) / 'Image'}")
    require_min_size(image, 1024 * 1024, "内核 " + info["kernel_file"])
    return {"uboot": uboot, "image": image, "dtb": dtb}


def find_mkimage() -> str:
    local = REPO_ROOT / "u-boot" / "tools" / "mkimage"
    if local.is_file() and os.access(local, os.X_OK):
        return str(local)
    found = shutil.which("mkimage")
    if found:
        return found
    die("未找到 mkimage。请先确认 U-Boot 已编译出 u-boot/tools/mkimage，或安装 u-boot-tools。")


def write_boot_script(soc: str) -> Path:
    info = SOC_INFO[soc]
    content = (
        'setenv bootargs "console=ttyS0,115200 root=/dev/mmcblk0p2 rootwait rw panic=10"\n'
        "setenv devtype mmc\n"
        "setenv devnum 0\n"
        "setenv distro_bootpart 1\n"
        "mmc dev 0\n"
        "load ${devtype} ${devnum}:${distro_bootpart} ${kernel_addr_r} " + info["kernel_file"] + "\n"
        "load ${devtype} ${devnum}:${distro_bootpart} ${fdt_addr_r} " + info["dtb_name"] + "\n"
        + info["boot_cmd"] + " ${kernel_addr_r} - ${fdt_addr_r}\n"
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


def decide_modules(soc: str, packed_image: Path) -> str:
    want64 = soc == "h5"
    arch = config_is_arm64(LINUX_DIR)
    if arch is None:
        log("警告: 未找到可用的 linux/.config，跳过模块安装。镜像里只有编进内核的驱动，以及 rootfs 里原有的模块。")
        return "skipped: no .config"
    if arch != want64:
        die(
            f"当前 linux/.config 是 {'arm64' if arch else 'arm32'}，与本次打包的 {soc} 不一致。"
            "H3/H5 共用同一棵内核树，不能把另一颗芯片的模块装进镜像。"
        )
    order = LINUX_DIR / "modules.order"
    if not order.is_file():
        log("警告: 未找到 linux/modules.order，跳过模块安装。")
        return "skipped: no modules.order"
    linux_image = LINUX_DIR / SOC_INFO[soc]["linux_image"]
    if linux_image.is_file():
        if not filecmp.cmp(linux_image, packed_image, shallow=False):
            die(
                "将写入启动分区的内核与 linux 树中的内核内容不一致，装上去的模块会和实际启动的内核不匹配。\n"
                f"启动用: {packed_image}\n"
                f"内核树: {linux_image}"
            )
    else:
        log(f"警告: linux 树中没有 {linux_image.name}，仍按当前内核树安装模块。若编译中断过，请先重新编译。")
    if cross_gcc(soc) is None:
        die(f"需要安装模块，但找不到 {SOC_INFO[soc]['cross']}gcc。")
    if shutil.which("depmod") is None:
        die("需要安装模块，但找不到 depmod（软件包 kmod）。")
    return "install"


def install_staged_firmware(soc: str, root_mount: str) -> None:
    src = soc_dir(soc) / STAGED_FW_REL
    if not src.is_file():
        die(
            f"没有找到 {src}。rtl8xxxu 启动时会请求 /{STAGED_FW_REL.as_posix()}。"
            "本次编译没有把固件拷到该路径。"
        )
    dest_root = root_mount + "/lib/firmware"
    log(f"======== 把 {soc} 固件装进镜像根分区 ========")
    log(f"{src} -> {dest_root}/rtlwifi/rtl8723bu_nic.bin")
    run(["sudo", "mkdir", "-p", dest_root])
    run(["sudo", "cp", "-a", str(soc_dir(soc) / "lib" / "firmware") + "/.", dest_root + "/"])


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


def choose_image_mib(root_bytes: int, will_install_modules: bool) -> int:
    root_mib = bytes_to_mib(root_bytes)
    modules_mib = MODULES_RESERVE_MIB if will_install_modules else 0
    total = BOOT_END_MIB + root_mib + modules_mib + ROOT_FREE_MIB
    image_mib = max(MIN_IMAGE_MIB, align_up(total, 128))
    log(
        f"未指定镜像大小，使用 {image_mib}MiB"
        f"（下限 {MIN_IMAGE_MIB}MiB；rootfs 约 {root_mib}MiB，模块预留 {modules_mib}MiB，根分区空闲 {ROOT_FREE_MIB}MiB）"
    )
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
    install_staged_firmware(soc, root_point)
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


def write_manifest(
    soc: str,
    artifacts: dict[str, Path],
    rootfs: Path,
    rootfs_origin: str,
    image_mib: int,
    module_action: str,
) -> None:
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
        f"rtl8723bu_firmware={soc_dir(soc) / STAGED_FW_REL}",
        "bootargs=console=ttyS0,115200 root=/dev/mmcblk0p2 rootwait panic=10",
        "",
    ]
    path = soc_dir(soc) / "makeimg-manifest.txt"
    path.write_text("\n".join(lines), encoding="utf-8")
    log(f"清单: {path}")


def image_commands() -> list[str]:
    return [
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


def pack_image(soc: str) -> None:
    ensure_linux()
    require_commands(image_commands())
    open_log(soc)
    stale = loop_record(soc).read_text(encoding="utf-8").strip() if loop_record(soc).is_file() else None
    cleanup_mounts(soc, stale or None)
    log(f"======== 打包 {soc} 启动镜像 ========")
    artifacts = locate_artifacts(soc)
    rootfs, origin = prepare_rootfs(soc)
    assert_rootfs_not_mounted(rootfs)
    boot_scr = write_boot_script(soc)
    module_action = decide_modules(soc, artifacts["image"])
    root_bytes = du_bytes(rootfs)
    image_mib = choose_image_mib(root_bytes, module_action == "install")
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


def build_soc(soc: str) -> None:
    ensure_tools_git_lfs_archives(soc)
    if soc == "h5":
        ensure_archived_toolchain("h5")
        ensure_or1k_toolchain()
        build_atf()
        build_crust()
        build_uboot("h5")
        build_kernel("h5")
    else:
        ensure_archived_toolchain("h3")
        build_uboot("h3")
        build_kernel("h3")
    copy_artifacts(soc)


def main() -> None:
    soc, debug, do_img = parse_cli(sys.argv[1:])
    ensure_linux()
    if debug:
        os.environ["MAKE_VERBOSE"] = "1"
    else:
        os.environ.pop("MAKE_VERBOSE", None)
    os.environ["GCC_COLORS"] = "auto"
    warn_missing_host_tools()
    if do_img:
        require_commands(image_commands())
    (BUILD_ROOT / soc).mkdir(parents=True, exist_ok=True)
    build_soc(soc)
    log(f"======== 全部完成 ({soc}) ========")
    log(f"产物目录: {BUILD_ROOT / soc}/ （应含 u-boot、Image、dtb 与 *.log）")
    if do_img:
        pack_image(soc)
        return
    print_pack_hint(soc)


def print_pack_hint(soc: str) -> None:
    """不带 img 时，编译成功后打印 H3/H5 的打包命令，以及单独执行 makeimg.py 的命令。"""
    log("======== 本次没有打包 ========")
    log(f"芯片 {soc} 已经编译完成，没有生成 SD 卡镜像。")
    log("如果需要编译并打包，执行:")
    log("  python3 build-scripts/buidl_tools.py h5 img")
    log("  python3 build-scripts/buidl_tools.py h5 debug img")
    log("  python3 build-scripts/buidl_tools.py h3 img")
    log("  python3 build-scripts/buidl_tools.py h3 debug img")
    log("单独打包，不重新编译，执行:")
    log("  python3 build-scripts/makeimg.py h5")
    log("  python3 build-scripts/makeimg.py h3")


if __name__ == "__main__":
    main()
