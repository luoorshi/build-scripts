#!/usr/bin/env bash
# 统一编译入口：H3（u-boot + kernel）/ H5（ATF + crust + u-boot + kernel）。支持 source 执行。
# 用法（仓库根目录）：. build-scripts/lois_buidl_tools.sh h3 [debug]
#                    . build-scripts/lois_buidl_tools.sh h5 [debug]

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TOOLS_DIR="$REPO_ROOT/tools"
BUILD_ROOT="$REPO_ROOT/build"

if [[ "${BASH_SOURCE[0]}" != "${0}" ]]; then
	_LOIS_SOURCED=1
else
	_LOIS_SOURCED=0
	set -euo pipefail
fi

die() {
	echo "错误: $*" >&2
	if [[ "$_LOIS_SOURCED" -eq 1 ]]; then
		return 1
	else
		exit 1
	fi
}

warn_missing_host_tools() {
	local missing=()
	command -v gcc &>/dev/null || missing+=("gcc")
	command -v bison &>/dev/null || missing+=("bison")
	command -v flex &>/dev/null || missing+=("flex")
	if ((${#missing[@]} > 0)); then
		echo "========================================="
		echo "警告：系统可能缺少编译工具: ${missing[*]}"
		echo "建议: sudo apt install -y build-essential bison flex"
		echo "========================================="
	fi
}

detect_jobs() {
	if [[ -n "${JOBS:-}" ]]; then
		echo "$JOBS"
	elif command -v nproc &>/dev/null; then
		nproc
	else
		echo 4
	fi
}

ensure_arm32_toolchain() {
	local dir="$TOOLS_DIR/15.2.rel1-arm"
	local archive="$TOOLS_DIR/arm-gnu-toolchain-15.2.rel1-x86_64-arm-none-linux-gnueabihf.tar.xz"
	local gcc_local="$dir/bin/arm-none-linux-gnueabihf-gcc"

	if [[ -x "$gcc_local" ]]; then
		if [[ ":$PATH:" != *":$dir/bin:"* ]]; then
			export PATH="$dir/bin:$PATH"
		fi
	elif command -v arm-none-linux-gnueabihf-gcc &>/dev/null; then
		:
	else
		[[ -f "$archive" ]] || die "未找到 ARM32 工具链压缩包: $archive"
		echo "正在解压 ARM32 工具链到 $dir ..."
		mkdir -p "$dir"
		tar -xJf "$archive" -C "$dir" --strip-components=1 || die "ARM32 工具链解压失败"
		[[ -x "$gcc_local" ]] || die "解压后未找到: $gcc_local"
		export PATH="$dir/bin:$PATH"
	fi
}

ensure_aarch64_toolchain() {
	local dir="$TOOLS_DIR/15.2.rel1-arm64"
	local archive="$TOOLS_DIR/arm-gnu-toolchain-15.2.rel1-x86_64-aarch64-none-linux-gnu.tar.xz"
	local gcc_local="$dir/bin/aarch64-none-linux-gnu-gcc"

	if [[ -x "$gcc_local" ]]; then
		if [[ ":$PATH:" != *":$dir/bin:"* ]]; then
			export PATH="$dir/bin:$PATH"
		fi
	elif command -v aarch64-none-linux-gnu-gcc &>/dev/null; then
		:
	else
		[[ -f "$archive" ]] || die "未找到 AArch64 工具链压缩包: $archive"
		echo "正在解压 AArch64 工具链到 $dir ..."
		mkdir -p "$dir"
		tar -xJf "$archive" -C "$dir" --strip-components=1 || die "AArch64 工具链解压失败"
		[[ -x "$gcc_local" ]] || die "解压后未找到: $gcc_local"
		export PATH="$dir/bin:$PATH"
	fi
}

ensure_or1k_toolchain() {
	local dir="$TOOLS_DIR/or1k-linux-musl-7.2.0"
	local archive="$TOOLS_DIR/or1k-linux-musl-7.2.0-20180317.tar.gz"
	local gcc_local="$dir/bin/or1k-linux-musl-gcc"

	if [[ -x "$gcc_local" ]]; then
		if [[ ":$PATH:" != *":$dir/bin:"* ]]; then
			export PATH="$dir/bin:$PATH"
		fi
	elif command -v or1k-linux-musl-gcc &>/dev/null; then
		:
	else
		[[ -f "$archive" ]] || die "未找到 or1k 工具链压缩包: $archive"
		echo "正在解压 or1k 工具链到 $dir ..."
		mkdir -p "$dir"
		if ! tar -xzf "$archive" -C "$dir" --strip-components=1 2>/dev/null; then
			rm -rf "${dir:?}"/*
			mkdir -p "$dir"
			tar -xzf "$archive" -C "$dir" || die "or1k 工具链解压失败"
			if [[ ! -x "$gcc_local" ]]; then
				local sub
				sub=$(find "$dir" -maxdepth 4 -type f -name 'or1k-linux-musl-gcc' 2>/dev/null | head -1)
				[[ -n "$sub" && -x "$sub" ]] || die "解压后未找到 or1k-linux-musl-gcc"
				export PATH="$(dirname "$sub"):$PATH"
				return 0
			fi
		fi
		[[ -x "$gcc_local" ]] || die "解压后未找到: $gcc_local"
		export PATH="$dir/bin:$PATH"
	fi
}

build_atf() {
	local vflag=()
	[[ -n "${MAKE_VERBOSE:-}" ]] && vflag=(V=1)
	echo "======== 编译 ATF (bl31) ========"
	cd "$REPO_ROOT/arm-trusted-firmware" || die "无法进入 arm-trusted-firmware"
	export PATH="$TOOLS_DIR/15.2.rel1-arm64/bin:$PATH"
	command -v aarch64-none-linux-gnu-gcc &>/dev/null || die "ATF 需要 aarch64-none-linux-gnu-gcc 在 PATH 中"
	make "${vflag[@]}" CROSS_COMPILE=aarch64-none-linux-gnu- PLAT=sun50i_a64 bl31 || die "ATF 编译失败"
	local out="$REPO_ROOT/arm-trusted-firmware/build/sun50i_a64/release/bl31.bin"
	[[ -f "$out" ]] || die "未生成 bl31.bin: $out"
}

build_crust() {
	local vflag=()
	[[ -n "${MAKE_VERBOSE:-}" ]] && vflag=(V=1)
	local def="${CRUST_DEFCONFIG:-orangepi_zero_plus_defconfig}"
	local or1k_mk="$REPO_ROOT/crust/arch/or1k/Makefile"
	local bak="${or1k_mk}.lois_bak"

	echo "======== 编译 Crust (scp) defconfig=$def ========"
	[[ -f "$or1k_mk" ]] || die "未找到 $or1k_mk"

	# 临时注释 or1k CFLAGS 中与老旧 or1k-linux-musl 不兼容的一行（见 tools/README.md），子 shell 退出时必恢复
	(
		cp -a "$or1k_mk" "$bak" || exit 1
		trap '[[ -f "$bak" ]] && mv -f "$bak" "$or1k_mk"' EXIT
		sed -i '/-msfimm -mshftimm -msoft-div -msoft-mul/{
/^[[:space:]]*#/!s/^/# /
}' "$or1k_mk" || exit 1
		cd "$REPO_ROOT/crust" || exit 1
		command -v or1k-linux-musl-gcc &>/dev/null || exit 1
		make "$def" || exit 1
		make "${vflag[@]}" CROSS_COMPILE=or1k-linux-musl- HOST_COMPILE= || exit 1
		[[ -f "$REPO_ROOT/crust/build/scp/scp.bin" ]] || exit 1
	) || die "Crust 编译失败"

	[[ -f "$REPO_ROOT/crust/build/scp/scp.bin" ]] || die "未生成 scp.bin"
}

build_uboot_h5() {
	local n vflag=()
	n=$(detect_jobs)
	[[ -n "${MAKE_VERBOSE:-}" ]] && vflag=(V=1)
	echo "======== 编译 U-Boot (H5) ========"
	cd "$REPO_ROOT/u-boot" || die "无法进入 u-boot"
	export BL31="$REPO_ROOT/arm-trusted-firmware/build/sun50i_a64/release/bl31.bin"
	export SCP="$REPO_ROOT/crust/build/scp/scp.bin"
	[[ -f "$BL31" ]] || die "缺少 BL31: $BL31"
	[[ -f "$SCP" ]] || die "缺少 SCP: $SCP"
	command -v aarch64-none-linux-gnu-gcc &>/dev/null || die "U-Boot H5 需要 aarch64-none-linux-gnu-gcc"
	make clean || die "u-boot make clean 失败"
	make quark-luoorshi-h5_defconfig ARCH=arm CROSS_COMPILE=aarch64-none-linux-gnu- || die "u-boot defconfig 失败"
	set +o pipefail
	make ARCH=arm CROSS_COMPILE=aarch64-none-linux-gnu- "${vflag[@]}" -j"$n" 2>&1 | tee "$BUILD_ROOT/h5/u-boot-build.log"
	local st="${PIPESTATUS[0]}"
	set -o pipefail 2>/dev/null || true
	[[ "$st" -eq 0 ]] || die "u-boot 编译失败 (exit $st)"
	[[ -f "$REPO_ROOT/u-boot/u-boot-sunxi-with-spl.bin" ]] || die "未生成 u-boot-sunxi-with-spl.bin"
}

build_uboot_h3() {
	local n vflag=()
	n=$(detect_jobs)
	[[ -n "${MAKE_VERBOSE:-}" ]] && vflag=(V=1)
	echo "======== 编译 U-Boot (H3) ========"
	cd "$REPO_ROOT/u-boot" || die "无法进入 u-boot"
	command -v arm-none-linux-gnueabihf-gcc &>/dev/null || die "U-Boot H3 需要 arm-none-linux-gnueabihf-gcc"
	make clean || die "u-boot make clean 失败"
	make quark-luoorshi-h3_defconfig ARCH=arm CROSS_COMPILE=arm-none-linux-gnueabihf- || die "u-boot defconfig 失败"
	set +o pipefail
	make ARCH=arm CROSS_COMPILE=arm-none-linux-gnueabihf- "${vflag[@]}" -j"$n" 2>&1 | tee "$BUILD_ROOT/h3/u-boot-build.log"
	local st="${PIPESTATUS[0]}"
	set -o pipefail 2>/dev/null || true
	[[ "$st" -eq 0 ]] || die "u-boot 编译失败 (exit $st)"
	[[ -f "$REPO_ROOT/u-boot/u-boot-sunxi-with-spl.bin" ]] || die "未生成 u-boot-sunxi-with-spl.bin"
}

# 内联 linux/Quark-n-H5-build.sh，不调用该脚本
build_kernel_h5() {
	local n vflag=()
	n=$(detect_jobs)
	[[ -n "${MAKE_VERBOSE:-}" ]] && vflag=(V=1)
	echo "======== 编译 Linux 内核 (H5) ========"
	cd "$REPO_ROOT/linux" || die "无法进入 linux"
	command -v aarch64-none-linux-gnu-gcc &>/dev/null || die "内核 H5 需要 aarch64-none-linux-gnu-gcc"
	make quark-luoorshi-h5_defconfig ARCH=arm64 || die "kernel defconfig 失败"
	make olddefconfig ARCH=arm64 CROSS_COMPILE=aarch64-none-linux-gnu- || die "kernel olddefconfig 失败"
	set +o pipefail
	make ARCH=arm64 CROSS_COMPILE=aarch64-none-linux-gnu- "${vflag[@]}" -j"$n" Image dtbs modules 2>&1 | tee "$BUILD_ROOT/h5/kernel-build.log"
	local st="${PIPESTATUS[0]}"
	set -o pipefail 2>/dev/null || true
	[[ "$st" -eq 0 ]] || die "内核编译失败 (exit $st)"
	[[ -f "$REPO_ROOT/linux/arch/arm64/boot/Image" ]] || die "未生成 arch/arm64/boot/Image"
}

# 内联 linux/Quark-n-H3-build.sh，不调用该脚本
build_kernel_h3() {
	local n vflag=()
	n=$(detect_jobs)
	[[ -n "${MAKE_VERBOSE:-}" ]] && vflag=(V=1)
	echo "======== 编译 Linux 内核 (H3) ========"
	cd "$REPO_ROOT/linux" || die "无法进入 linux"
	command -v arm-none-linux-gnueabihf-gcc &>/dev/null || die "内核 H3 需要 arm-none-linux-gnueabihf-gcc"
	make quark-luoorshi-h3_defconfig ARCH=arm || die "kernel defconfig 失败"
	make olddefconfig ARCH=arm CROSS_COMPILE=arm-none-linux-gnueabihf- || die "kernel olddefconfig 失败"
	set +o pipefail
	make ARCH=arm CROSS_COMPILE=arm-none-linux-gnueabihf- "${vflag[@]}" -j"$n" Image dtbs modules 2>&1 | tee "$BUILD_ROOT/h3/kernel-build.log"
	local st="${PIPESTATUS[0]}"
	set -o pipefail 2>/dev/null || true
	[[ "$st" -eq 0 ]] || die "内核编译失败 (exit $st)"
	[[ -f "$REPO_ROOT/linux/arch/arm/boot/Image" ]] || die "未生成 arch/arm/boot/Image"
}

# 若用户未设置，则使用本次编译产物的默认路径，供 make_sdcard_img 复制到 boot 分区
export_default_kernel_paths() {
	local soc="$1"
	if [[ "$soc" == "h5" ]]; then
		[[ -z "${KERNEL_IMAGE:-}" && -f "$REPO_ROOT/linux/arch/arm64/boot/Image" ]] &&
			export KERNEL_IMAGE="$REPO_ROOT/linux/arch/arm64/boot/Image"
		[[ -z "${DTB_PATH:-}" && -f "$REPO_ROOT/linux/arch/arm64/boot/dts/allwinner/sun50i-h5-quark-luoorshi.dtb" ]] &&
			export DTB_PATH="$REPO_ROOT/linux/arch/arm64/boot/dts/allwinner/sun50i-h5-quark-luoorshi.dtb"
	elif [[ "$soc" == "h3" ]]; then
		[[ -z "${KERNEL_IMAGE:-}" && -f "$REPO_ROOT/linux/arch/arm/boot/Image" ]] &&
			export KERNEL_IMAGE="$REPO_ROOT/linux/arch/arm/boot/Image"
		[[ -z "${DTB_PATH:-}" && -f "$REPO_ROOT/linux/arch/arm/boot/dts/allwinner/sun8i-h3-quark-luoorshi.dtb" ]] &&
			export DTB_PATH="$REPO_ROOT/linux/arch/arm/boot/dts/allwinner/sun8i-h3-quark-luoorshi.dtb"
	fi
}

copy_artifacts_h5() {
	mkdir -p "$BUILD_ROOT/h5"
	cp -f "$REPO_ROOT/u-boot/u-boot-sunxi-with-spl.bin" "$BUILD_ROOT/h5/u-boot-sunxi-with-spl-h5.bin" || die "复制 u-boot 失败"
	cp -f "$REPO_ROOT/arm-trusted-firmware/build/sun50i_a64/release/bl31.bin" "$BUILD_ROOT/h5/bl31.bin" 2>/dev/null || true
	cp -f "$REPO_ROOT/crust/build/scp/scp.bin" "$BUILD_ROOT/h5/scp.bin" 2>/dev/null || true
	echo "产物已复制到 $BUILD_ROOT/h5/"
}

copy_artifacts_h3() {
	mkdir -p "$BUILD_ROOT/h3"
	cp -f "$REPO_ROOT/u-boot/u-boot-sunxi-with-spl.bin" "$BUILD_ROOT/h3/u-boot-sunxi-with-spl-h3.bin" || die "复制 u-boot 失败"
	echo "产物已复制到 $BUILD_ROOT/h3/"
}

# 若设置了 BOOT_CMD 指向 boot.cmd，则用 mkimage 生成 boot.scr（H3: -A arm，H5: -A arm64）
maybe_mkimage_boot_scr() {
	local soc="$1"
	local cmd="$BOOT_CMD"
	local out_scr mkimg_arch

	[[ -n "$cmd" && -f "$cmd" ]] || return 0
	command -v mkimage &>/dev/null || die "已设置 BOOT_CMD=$cmd 但未找到 mkimage（通常来自 u-boot 编译产物 tools/mkimage 或系统包 u-boot-tools）"

	if [[ "$soc" == "h3" ]]; then
		mkimg_arch="arm"
	elif [[ "$soc" == "h5" ]]; then
		mkimg_arch="arm64"
	else
		return 0
	fi

	out_scr="$(mktemp -t boot.scr.XXXXXX)" || die "mktemp 失败"
	mkimage -C none -A "$mkimg_arch" -T script -d "$cmd" "$out_scr" || die "mkimage 生成 boot.scr 失败"
	export BOOT_SCR="$out_scr"
	_LOIS_BOOT_SCR_TMP="$out_scr"
	echo "已从 BOOT_CMD 生成临时 boot.scr: $out_scr (mkimage -A $mkimg_arch)"
}

cleanup_boot_scr_tmp() {
	if [[ -n "${_LOIS_BOOT_SCR_TMP:-}" && -f "${_LOIS_BOOT_SCR_TMP}" ]]; then
		rm -f "${_LOIS_BOOT_SCR_TMP}"
	fi
	unset _LOIS_BOOT_SCR_TMP
}

make_sdcard_img() {
	local soc="$1"
	local img rel_uboot uboot_path img_abs uboot_abs tmp_extra extra_copy

	fail_img() {
		[[ -n "${tmp_extra:-}" && -d "${tmp_extra}" ]] && rm -rf "$tmp_extra"
		cleanup_boot_scr_tmp
		die "$@"
	}

	if [[ "$soc" == "h5" ]]; then
		img="$BUILD_ROOT/quark-n-h5-sdcard.img"
		rel_uboot="h5/u-boot-sunxi-with-spl-h5.bin"
	elif [[ "$soc" == "h3" ]]; then
		img="$BUILD_ROOT/quark-n-h3-sdcard.img"
		rel_uboot="h3/u-boot-sunxi-with-spl-h3.bin"
	else
		fail_img "make_sdcard_img: 无效 soc=$soc"
	fi

	maybe_mkimage_boot_scr "$soc" || return 1

	uboot_path="$BUILD_ROOT/$rel_uboot"
	[[ -f "$uboot_path" ]] || fail_img "缺少 u-boot 文件: $uboot_path"

	command -v sudo &>/dev/null || fail_img "制作 .img 需要 sudo"
	for c in parted mkfs.vfat mkfs.ext4 losetup dd; do
		command -v "$c" &>/dev/null || fail_img "缺少命令: $c（需 parted、dosfstools、e2fsprogs、util-linux）"
	done

	echo "======== 生成 SD 镜像（需 sudo）: $img ========"

	extra_copy=0
	tmp_extra=$(mktemp -d) || fail_img "mktemp 失败"

	if [[ -n "${KERNEL_IMAGE:-}" && -f "$KERNEL_IMAGE" ]]; then
		cp -f "$KERNEL_IMAGE" "$tmp_extra/Image" && extra_copy=1
	fi
	if [[ -n "${DTB_PATH:-}" && -f "$DTB_PATH" ]]; then
		cp -f "$DTB_PATH" "$tmp_extra/dtb.dtb" && extra_copy=1
	fi
	if [[ -n "${BOOT_SCR:-}" && -f "$BOOT_SCR" ]]; then
		cp -f "$BOOT_SCR" "$tmp_extra/boot.scr" && extra_copy=1
	fi

	if [[ "$extra_copy" -eq 0 ]]; then
		echo "提示: 未设置 KERNEL_IMAGE/DTB_PATH/BOOT_SCR/BOOT_CMD 或文件不存在，boot 分区仅格式化（详见 README）。"
	fi

	mkdir -p "$BUILD_ROOT"
	dd if=/dev/zero of="$img" bs=1M count=2048 status=none || fail_img "dd 创建镜像失败"

	img_abs=$(readlink -f "$img") || fail_img "readlink 失败: $img"
	uboot_abs=$(readlink -f "$uboot_path") || fail_img "readlink 失败: $uboot_path"

	sudo bash -euo pipefail -c "
set -e
IMG='$img_abs'
UBOOT='$uboot_abs'
EXTRA='$tmp_extra'
parted -s \"\$IMG\" mktable msdos
parted -s \"\$IMG\" mkpart primary fat32 1MiB 256MiB
parted -s \"\$IMG\" mkpart primary ext4 256MiB 100%
LOOP=\$(losetup --find --show --partscan \"\$IMG\")
cleanup() {
  set +e
  mountpoint -q /mnt/lois_boot && umount /mnt/lois_boot
  mountpoint -q /mnt/lois_root && umount /mnt/lois_root
  [[ -n \"\${LOOP:-}\" ]] && losetup -d \"\$LOOP\"
}
trap cleanup EXIT
sleep 0.3
partprobe \"\$LOOP\" 2>/dev/null || true
sleep 0.3
P1=\"\${LOOP}p1\"
P2=\"\${LOOP}p2\"
[[ -b \"\$P1\" ]] || { echo \"未找到块设备 \$P1\"; exit 1; }
mkfs.vfat -n BOOT \"\$P1\"
mkfs.ext4 -F -L rootfs \"\$P2\"
mkdir -p /mnt/lois_boot /mnt/lois_root
mount \"\$P1\" /mnt/lois_boot
mount \"\$P2\" /mnt/lois_root
if [[ -d \"\$EXTRA\" ]]; then
  [[ -f \"\$EXTRA/Image\" ]] && cp -f \"\$EXTRA/Image\" /mnt/lois_boot/
  [[ -f \"\$EXTRA/dtb.dtb\" ]] && cp -f \"\$EXTRA/dtb.dtb\" /mnt/lois_boot/
  [[ -f \"\$EXTRA/boot.scr\" ]] && cp -f \"\$EXTRA/boot.scr\" /mnt/lois_boot/
fi
mkdir -p /mnt/lois_root/proc /mnt/lois_root/sys /mnt/lois_root/dev /mnt/lois_root/run /mnt/lois_root/tmp 2>/dev/null || true
chmod 1777 /mnt/lois_root/tmp 2>/dev/null || true
umount /mnt/lois_boot
umount /mnt/lois_root
dd if=\"\$UBOOT\" of=\"\$LOOP\" bs=1k seek=8 conv=notrunc
sync
losetup -d \"\$LOOP\"
LOOP=
trap - EXIT
" || fail_img "镜像制作失败（sudo 步骤）"
	rm -rf "$tmp_extra"
	tmp_extra=""
	cleanup_boot_scr_tmp
	[[ -f "$img" ]] || fail_img "镜像未生成: $img"
	echo "镜像已生成: $img"
}

lois_main() {
	local target="${1:-}"
	local dbg="${2:-}"

	case "$target" in
	h3 | h5) ;;
	*)
		echo "用法: . build-scripts/lois_buidl_tools.sh <h3|h5> [debug]" >&2
		die "缺少或无效参数: 需要 h3 或 h5"
		;;
	esac

	if [[ "$dbg" == "debug" ]]; then
		export MAKE_VERBOSE=1
	else
		unset MAKE_VERBOSE
	fi

	warn_missing_host_tools
	mkdir -p "$BUILD_ROOT/h3" "$BUILD_ROOT/h5"
	export GCC_COLORS=auto

	if [[ "$target" == "h5" ]]; then
		ensure_aarch64_toolchain || return 1
		ensure_or1k_toolchain || return 1
		build_atf || return 1
		build_crust || return 1
		build_uboot_h5 || return 1
		build_kernel_h5 || return 1
		export_default_kernel_paths h5
		copy_artifacts_h5 || return 1
		make_sdcard_img h5 || return 1
	else
		ensure_arm32_toolchain || return 1
		build_uboot_h3 || return 1
		build_kernel_h3 || return 1
		export_default_kernel_paths h3
		copy_artifacts_h3 || return 1
		make_sdcard_img h3 || return 1
	fi

	echo "======== 全部完成 ($target) ========"
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
	lois_main "$@"
else
	lois_main "$@" || return
fi
