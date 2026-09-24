# build-scripts

本目录提供 Quark-N（全志 H3 / H5）在 **Linux** 下的统一编译与清理脚本。镜像制作步骤与分区布局与 [H5制作镜像详细指南.md](H5制作镜像详细指南.md) 一致，并已改为使用 `losetup --find` 动态分配 loop 设备。

**说明**：脚本文件名 **`buidl_tools.sh`** 按约定保留此拼写（`buidl`），请勿与 `build` 混淆。

## 脚本一览

| 文件 | 作用 |
|------|------|
| [buidl_tools.sh](buidl_tools.sh) | 按参数编译 H3 或 H5（含 u-boot、linux 内核；H5 另含 ATF/Crust），复制产物到 `build/<soc>/` |
| [make-img.sh](make-img.sh) | 单独生成 SD 卡镜像 `.img`（需 sudo；优先使用 `build/<soc>/` 下的 Image/dtb） |
| [clean.sh](clean.sh) | 对 u-boot、ATF、crust、linux 执行清理，并删除仓库根目录 `build/` |

## 运行环境

- **必须在 Linux（或 WSL 等等价环境）下执行**编译与镜像步骤。
- 编译：需要 `bash`、`make`、`tar`（含 xz 支持 `tar -xJf`）、主机 `gcc`/`bison`/`flex`（缺失时仅警告）。
- 镜像：需要 `sudo`，以及 `dd`、`parted`、`losetup`、`mkfs.vfat`、`mkfs.ext4`（通常来自 `dosfstools`、`e2fsprogs`、`util-linux`）。
- 可选：若使用环境变量 **`BOOT_CMD`** 从 `boot.cmd` 自动生成 `boot.scr`，需要 **`mkimage`**（u-boot 自带的 `tools/mkimage` 或发行版包 `u-boot-tools`）。

## 工具链压缩包与解压目录

脚本会检测已解压目录；若不存在则解压（与 `u-boot/Quark-n-H3-build.sh`、`Quark-n-H5-build.sh` 逻辑一致）。

| 用途 | 压缩包路径（相对仓库根） | 解压到 |
|------|--------------------------|--------|
| H3 u-boot（armhf） | `tools/arm-gnu-toolchain-15.2.rel1-x86_64-arm-none-linux-gnueabihf.tar.xz` | `tools/15.2.rel1-arm` |
| H5 u-boot / ATF（aarch64） | `tools/arm-gnu-toolchain-15.2.rel1-x86_64-aarch64-none-linux-gnu.tar.xz` | `tools/15.2.rel1-arm64` |
| Crust（OpenRISC / or1k） | `tools/or1k-linux-musl-7.2.0-20180317.tar.gz` | `tools/or1k-linux-musl-7.2.0`（若顶层目录不一致会尝试查找 `or1k-linux-musl-gcc`） |

**`clean.sh` 不会删除**上述已解压目录。

## 使用 `buidl_tools.sh`

在**仓库根目录**执行（支持 **source**，失败时 **`return`** 而非 **`exit`**，避免关闭当前 shell）。无论是否 source，脚本结束后会 **`cd` 回执行前的当前目录**，避免终端留在 `linux/`、`u-boot/` 等子目录。

```bash
. build-scripts/buidl_tools.sh h3          # H3：u-boot + kernel
. build-scripts/buidl_tools.sh h3 debug    # 同上，make 增加 V=1
. build-scripts/buidl_tools.sh h5
. build-scripts/buidl_tools.sh h5 debug
```

也可直接执行：

```bash
bash build-scripts/buidl_tools.sh h5
```

### 参数

1. 第一个参数：**`h3`** 或 **`h5`**（必填）。
2. 可选第二个参数：**`debug`** —— 存在时各相关 `make` 增加 **`V=1`**。

### 并行数

可通过环境变量 **`JOBS`** 指定并行任务数；未设置时使用 `nproc`，否则回退为 `4`。

### H5 构建顺序（内联逻辑，不调用其他仓库内旧脚本）

1. 准备 AArch64 与 or1k 工具链。
2. **ATF**：在 `arm-trusted-firmware` 中执行  
   `make CROSS_COMPILE=aarch64-none-linux-gnu- PLAT=sun50i_a64 bl31`  
   产物：`arm-trusted-firmware/build/sun50i_a64/release/bl31.bin`（路径已修正，**不使用**旧脚本中的 `arm-trusted-firmware-master`）。
3. **Crust**：编译前脚本会**临时**按 [tools/README.md](../tools/README.md) 的说明，在 `crust/arch/or1k/Makefile` 中注释掉与老旧 `or1k-linux-musl` 不兼容的 `CFLAGS` 续行；编译结束（成功或失败）后**自动恢复原文件**，不会在仓库中永久留下修改。  
   默认 `make orangepi_zero_plus_defconfig` 再 `make CROSS_COMPILE=or1k-linux-musl- HOST_COMPILE=`。  
   可通过 **`CRUST_DEFCONFIG`** 覆盖默认 defconfig。若宿主机构建仍失败，可尝试将 `HOST_COMPILE=` 改为 `HOST_COMPILE=x86_64-linux-gnu-`。
4. **U-Boot**：在编译前导出 `BL31`、`SCP`，然后 `make clean`、`quark-luoorshi-h5_defconfig`、再 `make`。
5. **Linux 内核**：在 `linux/` 下 `make quark-luoorshi-h5_defconfig ARCH=arm64`、`olddefconfig`、`make Image dtbs modules`（与 `linux/Quark-n-H5-build.sh` 一致，脚本内联不直接调用）。

### H3 构建顺序

1. 准备 armhf 工具链。
2. **U-Boot**：`make clean`、`quark-luoorshi-h3_defconfig`、`make`。  
3. **不编译** ATF / Crust。
4. **Linux 内核**：`make quark-luoorshi-h3_defconfig ARCH=arm`、`olddefconfig`、`make Image dtbs modules`（与 `linux/Quark-n-H3-build.sh` 一致，脚本内联不直接调用）。

### Linux 内核与镜像中的 boot 分区

- 编译日志：`build/h3/kernel-build.log`、`build/h5/kernel-build.log`。
- **编译成功后**会把内核与 DTB **复制到** `build/<soc>/`（见下表）。`make-img.sh` 在未设置环境变量时的查找顺序为：
  1. **`build/<soc>/Image`**、**`build/<soc>/<dtb 文件名>`**（优先）
  2. 回退：`linux/arch/.../boot/Image` 与对应 dts 路径下的 dtb  
  制作 `.img` 时会把内核复制为 `Image`，dtb 复制为 **`sun50i-h5-quark-luoorshi.dtb`**（H5）或 **`sun8i-h3-quark-luoorshi.dtb`**（H3）。可在运行前 `export KERNEL_IMAGE` / `DTB_PATH` 覆盖。

> **注意**：内核 `.ko` 模块仍留在 `linux/` 树内（`make modules`），**不会**整包复制到 `build/`。若要把模块装进 rootfs，需另行 `make modules_install INSTALL_MOD_PATH=...`（见指南）。

## 产物与镜像输出

- 目录：**`build/`**（仓库根下）。**仅创建当前编译目标的子目录**（编 `h3` 只建 `build/h3/`，编 `h5` 只建 `build/h5/`）。
  - **`build/h3/`**：
    - `u-boot-sunxi-with-spl-h3.bin`
    - `Image`、`sun8i-h3-quark-luoorshi.dtb`
    - 可选：`System.map`、`kernel.config`
    - 日志：`u-boot-build.log`、`kernel-build.log`
  - **`build/h5/`**：
    - `u-boot-sunxi-with-spl-h5.bin`、`bl31.bin`、`scp.bin`
    - `Image`、`sun50i-h5-quark-luoorshi.dtb`
    - 可选：`System.map`、`kernel.config`
    - 日志：`u-boot-build.log`、`kernel-build.log`
- 镜像文件（由 **`make-img.sh`** 生成，**不**由 `buidl_tools.sh` 自动生成）：
  - **`build/quark-n-h3-sdcard.img`**
  - **`build/quark-n-h5-sdcard.img`**

镜像流程：`dd` 创建约 2048MiB 空文件 → `parted` MBR → 分区 1：FAT32（1MiB–256MiB）→ 分区 2：ext4（剩余）→ `losetup --find --show --partscan` → 格式化 →（可选）复制启动文件 → 卸载 → **`dd` 烧录 u-boot** 到 loop 设备 **`bs=1k seek=8 conv=notrunc`**（与指南一致）。

若内核未编译成功或 `build/<soc>/` 与 `linux/` 下都没有 `Image`/dtb，且你也未通过环境变量提供文件，则 boot 分区可能仅有格式化结果；脚本会打印提示，可按 [H5制作镜像详细指南.md](H5制作镜像详细指南.md) 手工补齐。

### 可选环境变量（内核 / 启动脚本）

| 变量 | 含义 |
|------|------|
| `KERNEL_IMAGE` | 内核镜像路径，复制到 boot 分区文件名为 `Image` |
| `DTB_PATH` | 设备树源路径；写入 FAT 时改名为 **`sun50i-h5-quark-luoorshi.dtb`**（H5）或 **`sun8i-h3-quark-luoorshi.dtb`**（H3） |
| `BOOT_SCR` | 已生成的 `boot.scr` 路径 |
| `BOOT_CMD` | 若设置且文件存在，则用 `mkimage` 生成临时 `boot.scr`：**H3** 使用 **`-A arm`**，**H5** 使用 **`-A arm64`**（与指南一致） |

示例：

```bash
export KERNEL_IMAGE=/path/to/Image
export DTB_PATH=/path/to/board.dtb
export BOOT_CMD=/path/to/boot.cmd
. build-scripts/buidl_tools.sh h5
```

## 使用 `clean.sh`

在仓库根目录：

```bash
bash build-scripts/clean.sh
```

脚本在**执行磁盘清理前**会先在本进程内清理与 `buidl_tools.sh` / H3 / H5 相关的环境变量，并从 `PATH` 中去掉本仓库下的三套工具链 `bin` 目录（`15.2.rel1-arm`、`15.2.rel1-arm64`、`or1k-linux-musl-7.2.0`），避免 `CROSS_COMPILE`、`BL31`、`SCP` 等与错误工具链残留在环境中干扰 `make`。

若你曾在**同一终端**里用 `. build-scripts/buidl_tools.sh ...` 编译，父 shell 的 `PATH` 仍可能带工具链前缀；切换 H3/H5 或清理前建议在仓库根执行：

```bash
. build-scripts/clean.sh --env-only
```

（仅清当前 shell 的环境变量与上述 `PATH` 前缀，**不**删除 `tools/` 内文件，也不跑 `make clean`。）

仅做环境清理、不做磁盘清理时也可：`bash build-scripts/clean.sh --env-only`（注意这只影响该子 shell，一般更推荐上面 source 方式。）

行为概要（`bash clean.sh` 无 `--env-only` 时）：

- **u-boot**：`make distclean`
- **arm-trusted-firmware**：`make PLAT=sun50i_a64 clean`
- **crust**：`make clobber`
- **linux**：`make clean`
- **删除** 整个 **`build/`** 目录

**不删除** `tools/15.2.rel1-arm`、`tools/15.2.rel1-arm64`、`tools/or1k-linux-musl-7.2.0` 等已解压工具链。

若某子目录尚未配置过 `make`，对应步骤可能报错，脚本会打印警告并继续。

## 与《H5 制作镜像详细指南》的对应关系

| 指南中的操作 | 本脚本中的实现 |
|--------------|----------------|
| `dd` / `parted` 分区 | `make-img.sh` 内 sudo 段落 |
| `losetup /dev/loop0` | 改为 `losetup --find --show --partscan` |
| `mkfs.vfat` / `mkfs.ext4` | 同上 |
| 复制 Image、dtb、boot.scr | 由 `KERNEL_IMAGE`、`DTB_PATH`、`BOOT_SCR` / `BOOT_CMD` 控制（默认优先 `build/<soc>/`） |
| `dd` 烧录 u-boot `seek=8` | 同上 |

更完整的 rootfs、chroot、模块安装等仍请参考指南正文。
