#!/usr/bin/env bash
# 统一编译入口：H3（u-boot + kernel）/ H5（ATF + crust + u-boot + kernel）。支持 source 执行。
# SD 卡 .img 需单独执行 build-scripts/make-img.sh（内含 sudo，默认不在本脚本中运行）。
# 用法（仓库根目录）：. build-scripts/buidl_tools.sh h3 [debug]
#                    . build-scripts/buidl_tools.sh h5 [debug]
# tools 下工具链若为 Git LFS：脚本会检测指针/过小文件，必要时自动安装 git-lfs（apt/dnf/yum/pacman，需 sudo）并执行 git lfs pull。
# 禁用自动拉取：export LOIS_SKIP_GIT_LFS=1

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TOOLS_DIR="$REPO_ROOT/tools"
BUILD_ROOT="$REPO_ROOT/build"

if [[ "${BASH_SOURCE[0]}" != "${0}" ]]; then
	_LOIS_SOURCED=1
else
	_LOIS_SOURCED=0
	set -euo pipefail
fi

# 注意：用「. 本脚本」source 执行时，die 内的 return 只结束 die 自身，不会结束调用方函数。
# 因此在各子函数中必须写「die ... || return 1」或「cmd || { die ...; return 1; }」，否则错误后仍会往下跑。
die() {
	echo "错误: $*" >&2
	if [[ "$_LOIS_SOURCED" -eq 1 ]]; then
		return 1
	else
		exit 1
	fi
}

# tools 下大体积工具链常由 Git LFS 管理；指针文件仅百余字节，直接 tar 会报 xz/gzip format not recognized。
_archive_size_bytes() {
	local f="$1"
	local s
	if s=$(stat -c%s "$f" 2>/dev/null); then
		echo "$s"
	elif s=$(stat -f%z "$f" 2>/dev/null); then
		echo "$s"
	else
		wc -c <"$f" | tr -d '[:space:]'
	fi
}

# 缺失、过小、或首行为 Git LFS 指针时返回 0（需要拉取）；否则返回 1
_archive_needs_git_lfs_pull() {
	local archive="$1"
	local min_bytes="${2:-100000}"

	[[ -f "$archive" ]] || return 0

	local sz line1
	sz=$(_archive_size_bytes "$archive")
	IFS= read -r line1 <"$archive" || true
	if [[ "$line1" == "version https://git-lfs.github.com/spec/v1" ]]; then
		return 0
	fi
	if [[ "${sz:-0}" -lt "$min_bytes" ]]; then
		return 0
	fi
	return 1
}

# 解析 git 工作区根目录（与在 tools/ 下执行 git 命令行为一致）；供 git lfs pull 使用
_lois_git_toplevel_for_lfs() {
	local top
	top=$(git -C "$REPO_ROOT" rev-parse --show-toplevel 2>/dev/null) || top=""
	[[ -n "$top" ]] && echo "$top" && return 0
	top=$(git -C "$TOOLS_DIR" rev-parse --show-toplevel 2>/dev/null) || top=""
	[[ -n "$top" ]] && echo "$top" && return 0
	return 1
}

require_real_toolchain_archive() {
	local archive="$1"
	local desc="$2"
	local min_bytes="${3:-100000}"

	[[ -f "$archive" ]] || die "未找到 $desc: $archive" || return 1

	local sz line1
	sz=$(_archive_size_bytes "$archive")
	IFS= read -r line1 <"$archive" || true
	if [[ "${sz:-0}" -lt "$min_bytes" ]]; then
		if [[ "$line1" == "version https://git-lfs.github.com/spec/v1" ]]; then
			die "$desc 仍为 Git LFS 指针（${sz} 字节）。请在仓库根执行: git lfs pull
或按 tools/README.md 将完整压缩包放到: $archive" || return 1
		fi
		die "$desc 文件过小（${sz} 字节），可能损坏或未完整下载: $archive" || return 1
	fi
}

ensure_git_lfs_cli() {
	if git lfs version &>/dev/null; then
		return 0
	fi
	echo "未检测到 Git LFS 客户端，尝试安装 git-lfs ..."
	if command -v apt-get &>/dev/null; then
		sudo apt-get update -qq &&
			sudo apt-get install -y git-lfs ||
			die "通过 apt 安装 git-lfs 失败，请手动执行: sudo apt install -y git-lfs"
	elif command -v dnf &>/dev/null; then
		sudo dnf install -y git-lfs ||
			die "通过 dnf 安装 git-lfs 失败，请手动: sudo dnf install -y git-lfs"
	elif command -v yum &>/dev/null; then
		sudo yum install -y git-lfs ||
			die "通过 yum 安装 git-lfs 失败，请手动: sudo yum install -y git-lfs"
	elif command -v pacman &>/dev/null; then
		sudo pacman -S --noconfirm git-lfs ||
			die "通过 pacman 安装 git-lfs 失败，请手动: sudo pacman -S git-lfs"
	else
		die "未找到 git-lfs，且无法自动安装（无 apt-get/dnf/yum/pacman）。请自行安装 git-lfs 后重试。"
	fi
	git lfs version &>/dev/null || die "安装后 git lfs 仍不可用，请检查 PATH 与安装日志"
	echo "git-lfs 已可用: $(git lfs version 2>/dev/null | head -1)"
}

# 按 h3/h5 仅检查本流程需要的压缩包；异常时安装 git-lfs 并 git lfs pull
ensure_tools_git_lfs_archives() {
	local target="$1"
	local archives=()
	case "$target" in
	h5)
		archives=(
			"$TOOLS_DIR/arm-gnu-toolchain-15.2.rel1-x86_64-aarch64-none-linux-gnu.tar.xz"
			"$TOOLS_DIR/or1k-linux-musl-7.2.0-20180317.tar.gz"
		)
		;;
	h3)
		archives=(
			"$TOOLS_DIR/arm-gnu-toolchain-15.2.rel1-x86_64-arm-none-linux-gnueabihf.tar.xz"
		)
		;;
	*)
		return 0
		;;
	esac

	local need=0 a
	for a in "${archives[@]}"; do
		if _archive_needs_git_lfs_pull "$a"; then
			need=1
			break
		fi
	done
	[[ "$need" -eq 0 ]] && return 0

	if [[ -n "${LOIS_SKIP_GIT_LFS:-}" ]]; then
		die "tools 工具链包未就绪（LFS 指针或过小），但已设置 LOIS_SKIP_GIT_LFS，跳过自动拉取。请自行放入完整压缩包或取消该变量。"
	fi

	echo "======== 检测到 tools 工具链包为 Git LFS 指针或未完整，将安装/使用 git-lfs 并拉取 ========"
	if ! git -C "$REPO_ROOT" rev-parse --is-inside-work-tree &>/dev/null; then
		die "无法自动拉取：$REPO_ROOT 不是 git 工作区。请将真实压缩包放入 tools/，或使用含 LFS 的 git clone。"
	fi

	ensure_git_lfs_cli || return 1
	(
		cd "$REPO_ROOT" || exit 1
		git lfs install
		git lfs pull
	) || die "git lfs pull 失败。请检查网络、LFS 远端与凭证。"

	for a in "${archives[@]}"; do
		if _archive_needs_git_lfs_pull "$a"; then
			die "git lfs pull 后仍异常（仍为指针或过小）: $a"
		fi
	done
	echo "======== tools 大文件已通过 Git LFS 就绪 ========"
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
		require_real_toolchain_archive "$archive" "ARM32 工具链压缩包"
		echo "正在解压 ARM32 工具链到 $dir ..."
		mkdir -p "$dir"
		tar -xJf "$archive" -C "$dir" --strip-components=1 || die "ARM32 工具链解压失败（请确认 tar 支持 -J/xz，且压缩包完整）"
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
		require_real_toolchain_archive "$archive" "AArch64 工具链压缩包"
		echo "正在解压 AArch64 工具链到 $dir ..."
		mkdir -p "$dir"
		tar -xJf "$archive" -C "$dir" --strip-components=1 || die "AArch64 工具链解压失败（请确认 tar 支持 -J/xz，且压缩包完整）"
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
		require_real_toolchain_archive "$archive" "or1k 工具链压缩包"
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
	cd "$REPO_ROOT/arm-trusted-firmware" || { die "无法进入 arm-trusted-firmware"; return 1; }
	export PATH="$TOOLS_DIR/15.2.rel1-arm64/bin:$PATH"
	command -v aarch64-none-linux-gnu-gcc &>/dev/null || { die "ATF 需要 aarch64-none-linux-gnu-gcc 在 PATH 中"; return 1; }
	make "${vflag[@]}" CROSS_COMPILE=aarch64-none-linux-gnu- PLAT=sun50i_a64 bl31 || { die "ATF 编译失败"; return 1; }
	local out="$REPO_ROOT/arm-trusted-firmware/build/sun50i_a64/release/bl31.bin"
	[[ -f "$out" ]] || { die "未生成 bl31.bin: $out"; return 1; }
}

build_crust() {
	local vflag=()
	[[ -n "${MAKE_VERBOSE:-}" ]] && vflag=(V=1)
	local def="${CRUST_DEFCONFIG:-orangepi_zero_plus_defconfig}"
	local or1k_mk="$REPO_ROOT/crust/arch/or1k/Makefile"
	local bak="${or1k_mk}.lois_bak"

	echo "======== 编译 Crust (scp) defconfig=$def ========"
	[[ -f "$or1k_mk" ]] || { die "未找到 $or1k_mk"; return 1; }

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
	) || { die "Crust 编译失败"; return 1; }

	[[ -f "$REPO_ROOT/crust/build/scp/scp.bin" ]] || { die "未生成 scp.bin"; return 1; }
}

build_uboot_h5() {
	local n vflag=() st
	n=$(detect_jobs)
	[[ -n "${MAKE_VERBOSE:-}" ]] && vflag=(V=1)
	echo "======== 编译 U-Boot (H5) ========"
	cd "$REPO_ROOT/u-boot" || { die "无法进入 u-boot"; return 1; }
	export BL31="$REPO_ROOT/arm-trusted-firmware/build/sun50i_a64/release/bl31.bin"
	export SCP="$REPO_ROOT/crust/build/scp/scp.bin"
	[[ -f "$BL31" ]] || { die "缺少 BL31: $BL31"; return 1; }
	[[ -f "$SCP" ]] || { die "缺少 SCP: $SCP"; return 1; }
	command -v aarch64-none-linux-gnu-gcc &>/dev/null || { die "U-Boot H5 需要 aarch64-none-linux-gnu-gcc"; return 1; }
	# make clean || die "u-boot make clean 失败"
	make quark-luoorshi-h5_defconfig ARCH=arm CROSS_COMPILE=aarch64-none-linux-gnu- || { die "u-boot defconfig 失败"; return 1; }
	set +o pipefail
	make ARCH=arm CROSS_COMPILE=aarch64-none-linux-gnu- "${vflag[@]}" -j"$n" 2>&1 | tee "$BUILD_ROOT/h5/u-boot-build.log"
	st="${PIPESTATUS[0]}"
	set -o pipefail 2>/dev/null || true
	[[ "$st" -eq 0 ]] || { die "u-boot 编译失败 (exit $st)"; return 1; }
	[[ -f "$REPO_ROOT/u-boot/u-boot-sunxi-with-spl.bin" ]] || { die "未生成 u-boot-sunxi-with-spl.bin"; return 1; }
}

build_uboot_h3() {
	local n vflag=() st
	n=$(detect_jobs)
	[[ -n "${MAKE_VERBOSE:-}" ]] && vflag=(V=1)
	echo "======== 编译 U-Boot (H3) ========"
	cd "$REPO_ROOT/u-boot" || { die "无法进入 u-boot"; return 1; }
	command -v arm-none-linux-gnueabihf-gcc &>/dev/null || { die "U-Boot H3 需要 arm-none-linux-gnueabihf-gcc"; return 1; }
	# make clean || die "u-boot make clean 失败"
	make quark-luoorshi-h3_defconfig ARCH=arm CROSS_COMPILE=arm-none-linux-gnueabihf- || { die "u-boot defconfig 失败"; return 1; }
	set +o pipefail
	make ARCH=arm CROSS_COMPILE=arm-none-linux-gnueabihf- "${vflag[@]}" -j"$n" 2>&1 | tee "$BUILD_ROOT/h3/u-boot-build.log"
	st="${PIPESTATUS[0]}"
	set -o pipefail 2>/dev/null || true
	[[ "$st" -eq 0 ]] || { die "u-boot 编译失败 (exit $st)"; return 1; }
	[[ -f "$REPO_ROOT/u-boot/u-boot-sunxi-with-spl.bin" ]] || { die "未生成 u-boot-sunxi-with-spl.bin"; return 1; }
}

# 内联 linux/Quark-n-H5-build.sh，不调用该脚本
build_kernel_h5() {
	local n vflag=() st img dtb
	n=$(detect_jobs)
	[[ -n "${MAKE_VERBOSE:-}" ]] && vflag=(V=1)
	img="$REPO_ROOT/linux/arch/arm64/boot/Image"
	dtb="$REPO_ROOT/linux/arch/arm64/boot/dts/allwinner/sun50i-h5-quark-luoorshi.dtb"
	echo "======== 编译 Linux 内核 (H5) ========"
	cd "$REPO_ROOT/linux" || { die "无法进入 linux"; return 1; }
	command -v aarch64-none-linux-gnu-gcc &>/dev/null || { die "内核 H5 需要 aarch64-none-linux-gnu-gcc"; return 1; }
	make quark-luoorshi-h5_defconfig ARCH=arm64 || { die "kernel defconfig 失败"; return 1; }
	make olddefconfig ARCH=arm64 CROSS_COMPILE=aarch64-none-linux-gnu- || { die "kernel olddefconfig 失败"; return 1; }
	# 删除旧 Image，避免 make 失败后仍因残留文件被误判为成功（source 场景尤甚）
	rm -f "$img"
	set +o pipefail
	make ARCH=arm64 CROSS_COMPILE=aarch64-none-linux-gnu- "${vflag[@]}" -j"$n" Image dtbs modules 2>&1 | tee "$BUILD_ROOT/h5/kernel-build.log"
	st="${PIPESTATUS[0]}"
	set -o pipefail 2>/dev/null || true
	[[ "$st" -eq 0 ]] || { die "内核编译失败 (exit $st)，详见 $BUILD_ROOT/h5/kernel-build.log"; return 1; }
	[[ -f "$img" ]] || { die "未生成 arch/arm64/boot/Image"; return 1; }
	[[ -f "$dtb" ]] || { die "未生成 DTB: $dtb"; return 1; }
}

# 内联 linux/Quark-n-H3-build.sh，不调用该脚本
build_kernel_h3() {
	local n vflag=() st img dtb
	n=$(detect_jobs)
	[[ -n "${MAKE_VERBOSE:-}" ]] && vflag=(V=1)
	img="$REPO_ROOT/linux/arch/arm/boot/Image"
	zimg="$REPO_ROOT/linux/arch/arm/boot/zImage"
	dtb="$REPO_ROOT/linux/arch/arm/boot/dts/allwinner/sun8i-h3-quark-luoorshi.dtb"
	echo "======== 编译 Linux 内核 (H3) ========"
	cd "$REPO_ROOT/linux" || { die "无法进入 linux"; return 1; }
	command -v arm-none-linux-gnueabihf-gcc &>/dev/null || { die "内核 H3 需要 arm-none-linux-gnueabihf-gcc"; return 1; }
	make quark-luoorshi-h3_defconfig ARCH=arm || { die "kernel defconfig 失败"; return 1; }
	make olddefconfig ARCH=arm CROSS_COMPILE=arm-none-linux-gnueabihf- || { die "kernel olddefconfig 失败"; return 1; }
	# 删除旧 Image/zImage，避免 make 失败后仍因残留文件被误判为成功（source 场景尤甚）
	rm -f "$img" "$zimg"
	set +o pipefail
	# H3 U-Boot 无 booti，镜像用 bootz 启动 zImage。Image 仍保留，供核对未压缩内核。
	make ARCH=arm CROSS_COMPILE=arm-none-linux-gnueabihf- "${vflag[@]}" -j"$n" Image zImage dtbs modules 2>&1 | tee "$BUILD_ROOT/h3/kernel-build.log"
	st="${PIPESTATUS[0]}"
	set -o pipefail 2>/dev/null || true
	[[ "$st" -eq 0 ]] || { die "内核编译失败 (exit $st)，详见 $BUILD_ROOT/h3/kernel-build.log"; return 1; }
	[[ -f "$img" ]] || { die "未生成 arch/arm/boot/Image"; return 1; }
	[[ -f "$zimg" ]] || { die "未生成 arch/arm/boot/zImage"; return 1; }
	[[ -f "$dtb" ]] || { die "未生成 DTB: $dtb"; return 1; }
}

copy_artifacts_h5() {
	local img dtb bl31 scp
	img="$REPO_ROOT/linux/arch/arm64/boot/Image"
	dtb="$REPO_ROOT/linux/arch/arm64/boot/dts/allwinner/sun50i-h5-quark-luoorshi.dtb"
	bl31="$REPO_ROOT/arm-trusted-firmware/build/sun50i_a64/release/bl31.bin"
	scp="$REPO_ROOT/crust/build/scp/scp.bin"
	mkdir -p "$BUILD_ROOT/h5"
	cp -f "$REPO_ROOT/u-boot/u-boot-sunxi-with-spl.bin" "$BUILD_ROOT/h5/u-boot-sunxi-with-spl-h5.bin" || { die "复制 u-boot 失败"; return 1; }
	[[ -f "$bl31" ]] || { die "缺少 bl31.bin，无法归档: $bl31"; return 1; }
	[[ -f "$scp" ]] || { die "缺少 scp.bin，无法归档: $scp"; return 1; }
	cp -f "$bl31" "$BUILD_ROOT/h5/bl31.bin" || { die "复制 bl31.bin 失败"; return 1; }
	cp -f "$scp" "$BUILD_ROOT/h5/scp.bin" || { die "复制 scp.bin 失败"; return 1; }
	[[ -f "$img" ]] || { die "缺少内核 Image，无法归档: $img"; return 1; }
	[[ -f "$dtb" ]] || { die "缺少 DTB，无法归档: $dtb"; return 1; }
	cp -f "$img" "$BUILD_ROOT/h5/Image" || { die "复制 Image 失败"; return 1; }
	cp -f "$dtb" "$BUILD_ROOT/h5/sun50i-h5-quark-luoorshi.dtb" || { die "复制 dtb 失败"; return 1; }
	cp -f "$REPO_ROOT/linux/System.map" "$BUILD_ROOT/h5/System.map" 2>/dev/null || true
	cp -f "$REPO_ROOT/linux/.config" "$BUILD_ROOT/h5/kernel.config" 2>/dev/null || true
	echo "产物已复制到 $BUILD_ROOT/h5/ （含 u-boot、bl31、scp、Image、dtb）"
}

copy_artifacts_h3() {
	local img dtb
	img="$REPO_ROOT/linux/arch/arm/boot/Image"
	zimg="$REPO_ROOT/linux/arch/arm/boot/zImage"
	dtb="$REPO_ROOT/linux/arch/arm/boot/dts/allwinner/sun8i-h3-quark-luoorshi.dtb"
	mkdir -p "$BUILD_ROOT/h3"
	cp -f "$REPO_ROOT/u-boot/u-boot-sunxi-with-spl.bin" "$BUILD_ROOT/h3/u-boot-sunxi-with-spl-h3.bin" || { die "复制 u-boot 失败"; return 1; }
	[[ -f "$img" ]] || { die "缺少内核 Image，无法归档: $img"; return 1; }
	[[ -f "$zimg" ]] || { die "缺少内核 zImage，无法归档: $zimg"; return 1; }
	[[ -f "$dtb" ]] || { die "缺少 DTB，无法归档: $dtb"; return 1; }
	cp -f "$img" "$BUILD_ROOT/h3/Image" || { die "复制 Image 失败"; return 1; }
	cp -f "$zimg" "$BUILD_ROOT/h3/zImage" || { die "复制 zImage 失败"; return 1; }
	cp -f "$dtb" "$BUILD_ROOT/h3/sun8i-h3-quark-luoorshi.dtb" || { die "复制 dtb 失败"; return 1; }
	cp -f "$REPO_ROOT/linux/System.map" "$BUILD_ROOT/h3/System.map" 2>/dev/null || true
	cp -f "$REPO_ROOT/linux/.config" "$BUILD_ROOT/h3/kernel.config" 2>/dev/null || true
	echo "产物已复制到 $BUILD_ROOT/h3/ （含 u-boot、Image、zImage、dtb）"
}

# 实际构建逻辑（会多次 cd）；由 lois_main 包装以在结束时恢复调用前的工作目录（source 时终端路径不变）
_lois_main_inner() {
	local target="${1:-}"
	local dbg="${2:-}"

	case "$target" in
	h3 | h5) ;;
	*)
		echo "用法: . build-scripts/buidl_tools.sh <h3|h5> [debug]" >&2
		die "缺少或无效参数: 需要 h3 或 h5" || return 1
		;;
	esac

	if [[ "$dbg" == "debug" ]]; then
		export MAKE_VERBOSE=1
	else
		unset MAKE_VERBOSE
	fi

	warn_missing_host_tools
	# 只创建当前编译目标目录，避免编 h3 时多出空的 build/h5（或反之）
	mkdir -p "$BUILD_ROOT/$target"
	export GCC_COLORS=auto

	ensure_tools_git_lfs_archives "$target" || return 1

	if [[ "$target" == "h5" ]]; then
		ensure_aarch64_toolchain || return 1
		ensure_or1k_toolchain || return 1
		build_atf || return 1
		build_crust || return 1
		build_uboot_h5 || return 1
		build_kernel_h5 || return 1
		copy_artifacts_h5 || return 1
	else
		ensure_arm32_toolchain || return 1
		build_uboot_h3 || return 1
		build_kernel_h3 || return 1
		copy_artifacts_h3 || return 1
	fi

	echo "======== 全部完成 ($target) ========"
	echo "产物目录: $BUILD_ROOT/$target/ （应含 u-boot、Image、dtb 与 *.log）"
	echo "提示: 默认不生成 SD 卡 .img。需要镜像时在仓库根执行: bash build-scripts/make-img.sh $target"
}

lois_main() {
	local _lois_saved_pwd _st _lois_saved_shopts
	# source 执行时顶层未 set -e；在本函数内临时打开，结束后用 set +o 快照恢复，避免污染调用方 shell
	_lois_saved_pwd=$(pwd)
	_lois_saved_shopts=$(set +o)
	set -euo pipefail
	_st=0
	_lois_main_inner "$@" || _st=$?
	eval "$_lois_saved_shopts" 2>/dev/null || true
	builtin cd "$_lois_saved_pwd" 2>/dev/null || true
	return "$_st"
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
	lois_main "$@"
else
	lois_main "$@" || return
fi
