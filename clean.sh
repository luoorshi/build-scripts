#!/usr/bin/env bash
# 清理各子仓库编译产物、仓库根目录 build/，并清理与 lois 构建脚本相关的 H3/H5 环境变量。
# 不删除 tools/ 下已解压工具链目录。
#
# 用法：
#   bash build-scripts/clean.sh              # 磁盘清理；子进程内会先清环境再执行 make clean
#   bash build-scripts/clean.sh --env-only   # 仅在本子 shell 清环境后退出（父 shell 不变）
#   . build-scripts/clean.sh --env-only      # 清理「当前」shell 的环境变量与 PATH 中的本仓库工具链前缀（推荐在切换 H3/H5 前执行）
#   . build-scripts/clean.sh                 # 同 --env-only

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TOOLS_ROOT="$REPO_ROOT/tools"

# 从 PATH 中移除本仓库解压的工具链 bin（避免 H3 armhf 与 H5 aarch64 或 or1k 混在同一 PATH 中）
lois_strip_toolchain_path() {
	local d newpath="" first=1
	local -a _pa
	IFS=':' read -r -a _pa <<< "${PATH:-}"
	for d in "${_pa[@]}"; do
		[[ -z "$d" ]] && continue
		case "$d" in
		"$TOOLS_ROOT/15.2.rel1-arm/bin" | "$TOOLS_ROOT/15.2.rel1-arm64/bin" | "$TOOLS_ROOT/or1k-linux-musl-7.2.0/bin") continue ;;
		esac
		if [[ "$first" -eq 1 ]]; then
			newpath="$d"
			first=0
		else
			newpath="$newpath:$d"
		fi
	done
	export PATH="$newpath"
}

# 取消由 buidl_tools.sh 或手工导出、可能导致 H3/H5 混用的变量
lois_clean_environment() {
	unset BL31 SCP CROSS_COMPILE ARCH MAKE_VERBOSE KERNEL_IMAGE DTB_PATH BOOT_SCR BOOT_CMD 2>/dev/null || true
	unset CRUST_DEFCONFIG JOBS GCC_COLORS HOST_COMPILE INSTALL_MOD_PATH 2>/dev/null || true
	unset _LOIS_SOURCED _LOIS_BOOT_SCR_TMP 2>/dev/null || true
	lois_strip_toolchain_path
}

# ---- source 模式：只清当前 shell 环境（不跑 make、不删 build）----
if [[ "${BASH_SOURCE[0]}" != "${0}" ]]; then
	case "${1:-}" in
	"" | --env-only)
		lois_clean_environment
		echo "======== 已在当前 shell 清理 lois/H3/H5 相关环境变量与 PATH 中的仓库工具链 bin ========"
		echo "（未删除 $TOOLS_ROOT 下任何文件；完整磁盘清理请执行: bash build-scripts/clean.sh）"
		return 0
		;;
	*)
		echo "错误: source 本脚本仅支持无参数或 --env-only。完整清理请执行: bash ${BASH_SOURCE[0]}" >&2
		return 1
		;;
	esac
fi

# ---- 执行模式：--env-only 仅清子 shell 环境 ----
if [[ "${1:-}" == "--env-only" ]]; then
	lois_clean_environment
	echo "======== 已在子 shell 清理环境变量（父 shell 未变）========"
	echo "若曾在当前终端 source 过 buidl_tools.sh，请在仓库根执行:"
	echo "  . build-scripts/clean.sh --env-only"
	exit 0
fi

set -euo pipefail

lois_clean_environment
echo "======== 已清理构建相关环境变量（本子进程内），开始磁盘清理 ========"

echo "======== 清理 u-boot ========"
if [[ -d "$REPO_ROOT/u-boot" ]]; then
	(
		cd "$REPO_ROOT/u-boot"
		make distclean
	) || echo "警告: u-boot distclean 失败（可能尚未配置过）"
else
	echo "跳过: 无 u-boot 目录"
fi

echo "======== 清理 arm-trusted-firmware ========"
if [[ -d "$REPO_ROOT/arm-trusted-firmware" ]]; then
	(
		cd "$REPO_ROOT/arm-trusted-firmware"
		make PLAT=sun50i_a64 clean
	) || echo "警告: ATF clean 失败"
else
	echo "跳过: 无 arm-trusted-firmware 目录"
fi

echo "======== 清理 crust ========"
if [[ -d "$REPO_ROOT/crust" ]]; then
	(
		cd "$REPO_ROOT/crust"
		make clobber
	) || echo "警告: crust clobber 失败"
else
	echo "跳过: 无 crust 目录"
fi

echo "======== 清理 linux ========"
if [[ -d "$REPO_ROOT/linux" ]]; then
	(
		cd "$REPO_ROOT/linux"
		make clean
	) || echo "警告: linux clean 失败（可能尚未配置）"
else
	echo "跳过: 无 linux 目录"
fi

echo "======== 删除 $REPO_ROOT/build ========"
if [[ -d "$REPO_ROOT/build" ]]; then
	rm -rf "$REPO_ROOT/build"
	echo "已删除 build/"
else
	echo "无 build 目录"
fi

echo "======== 完成（tools 下已解压工具链未删除）========"
echo "若本终端曾 source buidl_tools.sh，建议在仓库根执行以清理当前 shell:"
echo "  . build-scripts/clean.sh --env-only"
