#!/usr/bin/env bash
# 生成 SD 卡 .img（需 sudo：分区、格式化、挂载、写入 SPL）。
# 用法（仓库根目录）：bash build-scripts/make-img.sh h3
#                    bash build-scripts/make-img.sh h5
# 依赖：编译流程已跑过且 build/h3|h5 下已有 u-boot（及建议有 Image/dtb）；可选环境变量 KERNEL_IMAGE、DTB_PATH、
# BOOT_CMD（生成 boot.scr）、BOOT_SCR 与主脚本 README 说明一致。未设置时优先用 build/<soc>/ 归档，再回退 linux/ 树。

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD_ROOT="$REPO_ROOT/build"

die() {
	echo "错误: $*" >&2
	exit 1
}

# 若用户未设置，优先使用 build/<soc>/ 归档产物，否则回退到 linux/ 树内路径
export_default_kernel_paths() {
	local soc="$1"
	if [[ "$soc" == "h5" ]]; then
		if [[ -z "${KERNEL_IMAGE:-}" ]]; then
			if [[ -f "$BUILD_ROOT/h5/Image" ]]; then
				export KERNEL_IMAGE="$BUILD_ROOT/h5/Image"
			elif [[ -f "$REPO_ROOT/linux/arch/arm64/boot/Image" ]]; then
				export KERNEL_IMAGE="$REPO_ROOT/linux/arch/arm64/boot/Image"
			fi
		fi
		if [[ -z "${DTB_PATH:-}" ]]; then
			if [[ -f "$BUILD_ROOT/h5/sun50i-h5-quark-luoorshi.dtb" ]]; then
				export DTB_PATH="$BUILD_ROOT/h5/sun50i-h5-quark-luoorshi.dtb"
			elif [[ -f "$REPO_ROOT/linux/arch/arm64/boot/dts/allwinner/sun50i-h5-quark-luoorshi.dtb" ]]; then
				export DTB_PATH="$REPO_ROOT/linux/arch/arm64/boot/dts/allwinner/sun50i-h5-quark-luoorshi.dtb"
			fi
		fi
	elif [[ "$soc" == "h3" ]]; then
		if [[ -z "${KERNEL_IMAGE:-}" ]]; then
			if [[ -f "$BUILD_ROOT/h3/Image" ]]; then
				export KERNEL_IMAGE="$BUILD_ROOT/h3/Image"
			elif [[ -f "$REPO_ROOT/linux/arch/arm/boot/Image" ]]; then
				export KERNEL_IMAGE="$REPO_ROOT/linux/arch/arm/boot/Image"
			fi
		fi
		if [[ -z "${DTB_PATH:-}" ]]; then
			if [[ -f "$BUILD_ROOT/h3/sun8i-h3-quark-luoorshi.dtb" ]]; then
				export DTB_PATH="$BUILD_ROOT/h3/sun8i-h3-quark-luoorshi.dtb"
			elif [[ -f "$REPO_ROOT/linux/arch/arm/boot/dts/allwinner/sun8i-h3-quark-luoorshi.dtb" ]]; then
				export DTB_PATH="$REPO_ROOT/linux/arch/arm/boot/dts/allwinner/sun8i-h3-quark-luoorshi.dtb"
			fi
		fi
	fi
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
	local img rel_uboot uboot_path img_abs uboot_abs tmp_extra extra_copy dtb_boot_name

	fail_img() {
		[[ -n "${tmp_extra:-}" && -d "${tmp_extra}" ]] && rm -rf "$tmp_extra"
		cleanup_boot_scr_tmp
		die "$@"
	}

	if [[ "$soc" == "h5" ]]; then
		img="$BUILD_ROOT/quark-n-h5-sdcard.img"
		rel_uboot="h5/u-boot-sunxi-with-spl-h5.bin"
		dtb_boot_name="sun50i-h5-quark-luoorshi.dtb"
	elif [[ "$soc" == "h3" ]]; then
		img="$BUILD_ROOT/quark-n-h3-sdcard.img"
		rel_uboot="h3/u-boot-sunxi-with-spl-h3.bin"
		dtb_boot_name="sun8i-h3-quark-luoorshi.dtb"
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
		cp -f "$DTB_PATH" "$tmp_extra/$dtb_boot_name" && extra_copy=1
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
DTB_BOOT_NAME='$dtb_boot_name'
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
  [[ -f \"\$EXTRA/\$DTB_BOOT_NAME\" ]] && cp -f \"\$EXTRA/\$DTB_BOOT_NAME\" /mnt/lois_boot/
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

main() {
	local target="${1:-}"
	case "$target" in
	h3 | h5) ;;
	*)
		die "用法: $0 <h3|h5>"
		;;
	esac

	export_default_kernel_paths "$target"
	make_sdcard_img "$target"
	echo "======== 镜像步骤完成 ($target) ========"
}

main "$@"
