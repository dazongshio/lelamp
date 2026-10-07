# Raspberry Pi 5 系统配置与恢复

本文补充[硬件配置与验证](./raspberry-pi-5.md)，记录 2026-10-07 实机的 Ubuntu 启动布局、系统服务、设备权限、音频状态与恢复流程。硬件参数、投影控制帧及现场验收结果见硬件说明；本页覆盖承载它们的系统配置。

这是当前机器的参考记录与部署模板，不是整盘镜像。参考文件不能覆盖另一台机器的根分区标识、启动布局、用户名或私密认证配置。本包不包含密码、控制台令牌、API 密钥、私人录音照片或供应商资料原件，也不自动启动投影或机械臂运动。

## 文件与实机基线

系统参考文件位于 `config/hardware/raspberry-pi-5/system/`：

| 文件 | 用途 |
| --- | --- |
| [boot-config.reference.txt](../../config/hardware/raspberry-pi-5/system/boot-config.reference.txt) | 当前 Ubuntu 启动配置的参考布局，供人工核对 |
| [cmdline.txt.example](../../config/hardware/raspberry-pi-5/system/cmdline.txt.example) | 内核命令行示例；根分区值是占位符 |
| [observed-system.json](../../config/hardware/raspberry-pi-5/system/observed-system.json) | 不含凭据的系统、服务与设备状态记录 |
| [packages.reference.txt](../../config/hardware/raspberry-pi-5/system/packages.reference.txt) | 实机软件包及 uv 版本记录 |
| [lelamp-web-console.service.example](../../config/hardware/raspberry-pi-5/system/lelamp-web-console.service.example) | 现场旧部署的系统级网页服务参考 |
| [lelamp-web-console.repository.service.example](../../config/hardware/raspberry-pi-5/system/lelamp-web-console.repository.service.example) | 当前仓库布局的系统服务模板，需调整 uv 与部署路径 |
| [console.env.example](../../config/hardware/raspberry-pi-5/system/console.env.example) | 私密运行环境的空白示例，仅在本机填写认证值 |
| [boot-layout.fragment.txt](../../config/hardware/raspberry-pi-5/system/boot-layout.fragment.txt) | current/new 启动布局片段，供合并前核对 |
| [asound.A311.reference.state](../../config/hardware/raspberry-pi-5/system/asound.A311.reference.state) | 仅 A311 的 ALSA 状态参考，不是全机声卡状态 |
| [README.md](../../config/hardware/raspberry-pi-5/system/README.md) | 系统模板适用边界与人工安装说明 |

当前实机基线：

| 项目 | 已读取的值 |
| --- | --- |
| 主机 | Raspberry Pi 5，aarch64 |
| 系统 | Ubuntu 26.04.1 LTS |
| 内核 | 7.0.0-1020-raspi |
| LeLamp 现场运行目录 | `/home/z/Project/lelamp-web` |
| 原仓库克隆目录 | `/home/z/Project/lelamp` |
| 已安装 uv | `/home/z/Project/lelamp-web/bin/uv`，0.12.23，aarch64 |
| ALSA 工具 | `alsa-utils` 1.2.15.2-1ubuntu1 |
| 相机 | `libcamera-ipa:arm64` 0.7.0-1ubuntu2；`rpicam-apps-core` 1.11.1-1ubuntu2 |
| DRM | `libdrm2:arm64` 2.4.131-1 |
| systemd / udev | 259.5-0ubuntu3.4 |

这些版本是复查记录，不要求在其他系统强制安装完全相同版本。按目标系统准备原生依赖，再运行本包测试与实际硬件验证；不要将系统软件包、uv 二进制或完整虚拟环境提交到仓库。

本次补充已在本地和目标树莓派通过 44 项软件测试及配置预览。两份系统服务模板也通过目标机 `systemd-analyze verify` 检查；新仓库模板的验证副本使用现场已有 uv 的绝对路径，因为新 checkout 尚未安装自己的 `bin/uv`。这些检查没有安装或启用新服务，也没有改变当前投影进程。

## Ubuntu 启动布局：保留目标机加载链

当前机器的内核、初始内存盘与命令行文件在：

```text
/boot/firmware/config.txt
/boot/firmware/current/vmlinuz
/boot/firmware/current/initrd.img
/boot/firmware/current/cmdline.txt
```

当前没有 `/boot/firmware/cmdline.txt`。不能按其他发行版的习惯，在该不存在的位置写入命令行后就认为配置会生效。

Ubuntu 的 `config.txt` 使用 `os_prefix=current/` 选择正常启动文件；当前参考中的 `[tryboot]` 条件使用 `os_prefix=new/`，选择另一组启动文件。条件名称不是目录名称，不能据此把文件路径写成 `tryboot/`。内核、initramfs、cmdline 的相对位置必须与对应前缀配套。修改硬件 overlay 时保留这些系统加载项与条件段，不删除或更换内核/初始内存盘，不把整份参考配置直接覆盖目标机。

先读取目标机实际配置和挂载位置：

```sh
cat /etc/os-release
uname -r
findmnt -no SOURCE,FSTYPE,OPTIONS /
findmnt -no SOURCE,FSTYPE,OPTIONS /boot/firmware
sed -n '1,240p' /boot/firmware/config.txt
cat /boot/firmware/current/cmdline.txt
cat /proc/cmdline
```

若目标机使用不同的前缀、条件段或文件位置，以目标机为准。`/proc/cmdline` 是本次启动实际使用的参数；修改磁盘文件后，当前运行内核不会立即改用新参数。

命令行示例保留本机其余参数，但用 `root=<KEEP_TARGET_ROOT>` 代替根分区标识：

```text
zswap.enabled=1 zswap.compressor=zstd multipath=off dwc_otg.lpm_enable=0 console=tty1 root=<KEEP_TARGET_ROOT> rootfstype=ext4 panic=10 rootwait fixrtc quiet splash
```

示例中的 `root=<KEEP_TARGET_ROOT>` 整个参数必须替换为目标机现有的完整 `root=XXX` 参数，保留等号右侧的原值；不能把完整参数再次放到已有 `root=` 后面。不要把占位符原样写入，也不要复制其他机器的 UUID、PARTUUID 或设备路径。本硬件配置的应用脚本只合并相机/KMS 启动项，**不修改 cmdline、root、内核或 initramfs**。

本机硬件启动目标为 CAM0 IMX290、CAM1 IMX219 与 `vc4-kms-v3d,noaudio`。完整参数及默认时钟边界见硬件说明。参考文件保留 Ubuntu 的 current/new 前缀与 tryboot 条件布局，应用工具遇到无法判断的 `include` 或条件配置时停止，交由人工核对。

## 系统服务与用户服务不能混用

系统级服务由 PID 1 管理，配置一般放在 `/etc/systemd/system/`，使用 `sudo systemctl`。用户级服务放在对应用户的 `~/.config/systemd/user/`，使用该用户会话中的 `systemctl --user`；它们拥有不同的管理器、启用目标、环境与服务状态。

当前实机状态：

| 单元 | 管理范围 | 配置/运行状态 | 实机用户与权限 |
| --- | --- | --- | --- |
| `lelamp-web-console.service` | 系统级 | enabled、active；网页/API 8790，投影预览 8765 | User/Group 为 z；补充组 dialout、video、audio |
| `workmate-device.service` | 系统级 | enabled、active；保留现有部署 | User 为 workmate；补充组 audio、video、render |
| `gdm.service` | 系统级 | disabled、inactive | 本机当前不运行图形登录管理器 |
| `alsa-restore.service` | 系统级 | active、static | 恢复已有 ALSA 状态 |
| `alsa-state.service` | 系统级 | inactive、static | 记录现状，不强制启用 |
| `alsa-utils.service` | 系统级 | masked | 记录现状，不强制解除屏蔽 |

当前用户服务中没有 LeLamp 或 projector 单元。不要因为仓库有用户服务模板，就认为本机正在用它或已经启用用户级投影。

仓库旧 `systemd/user/lelamp-web-console.service` 使用另一部署的 `/home/lemp` 路径与 `default.target`。它不能直接替换本机的系统级服务。旧协作文档服务模板也使用 `%h` 和 `default.target`，应按其用户服务上下文核对；把它放到系统管理器中会改变路径含义。本系统模板使用实际部署的系统服务方式，不搬迁这些旧用户单元。

只读查看实际管理器和公开属性：

```sh
systemctl show lelamp-web-console.service -p User -p Group -p SupplementaryGroups -p WorkingDirectory -p FragmentPath -p DropInPaths -p ActiveState -p SubState -p UnitFileState
systemctl show workmate-device.service -p User -p SupplementaryGroups -p ActiveState -p UnitFileState
systemctl show gdm.service alsa-restore.service alsa-state.service alsa-utils.service -p Id -p ActiveState -p UnitFileState
systemctl --user list-unit-files 'lelamp*' 'projector*'
systemctl get-default
```

`systemctl --user` 需在目标用户的会话执行；不要通过 `sudo systemctl --user` 误查 root 的用户管理器。记录只包含指定公开属性，不读取私密环境文件，也不导出服务的完整环境或认证参数。

Workmate 是本机已存在的独立设备服务，本包只记录它的状态，不复制其完整配置，不变更或删除它。

## 服务环境与权限：验证最终进程

现场运行目录是 `/home/z/Project/lelamp-web`。新机器需根据实际用户、仓库位置和 uv 位置调整系统服务模板的 `User`、`Group`、`WorkingDirectory`、`ExecStart` 及环境路径。当前原仓库目录没有 `bin/uv`，PATH 中也没有 uv；进入原仓库后仍需指定已安装 uv：

```sh
export LELAMP_UV_BIN=/home/z/Project/lelamp-web/bin/uv
mkdir -p "$PWD/tmp"
```

其他机器使用自己的已安装 uv 绝对路径或 PATH。所有 Python 环境和执行用 uv 管理，临时/缓存路径设在当前项目 `tmp`。本包不创建容器或隔离环境。

现场旧部署的根目录有项目定义，但当前 GitHub 仓库的项目文件是 `lelamp_runtime/pyproject.toml`。因此：

- `lelamp-web-console.service.example` 记录旧现场的启动布局。
- 新克隆使用 `lelamp-web-console.repository.service.example`，其 `--project`、`--directory` 指向仓库的 `lelamp_runtime`。
- 新模板示例里的 uv 路径也必须改成实际安装的绝对路径，不能假定新克隆自带 `bin/uv`。
- 不使用旧 `scripts/start_fixed_console.sh` 启动本配置，它的执行方式与硬件默认开关不适用于本次部署。

新克隆需先准备项目依赖，不能直接用带 `--no-sync` 的服务模板代替环境安装。设置有效 uv 后在仓库根目录执行：

```sh
TMPDIR="$PWD/tmp" UV_CACHE_DIR="$PWD/tmp/uv-cache" PYTHONDONTWRITEBYTECODE=1 "$LELAMP_UV_BIN" sync --project "$PWD/lelamp_runtime"
```

按目标功能选择仓库已有依赖选项，并完成前端构建与运行入口验证后再安装服务；不要在当前正在投影的机器上为了复制模板额外重建运行环境。

网页服务的设备权限依赖：

```ini
[Service]
SupplementaryGroups=dialout video audio
```

实机串口节点为 root:dialout、0660，声卡节点为 root:audio、0660，主 DRM 节点为 root:video、0660。z 的交互式账号没有 audio/video/dialout 补充组，但系统服务声明的补充组已对服务进程生效。服务附加组不会自动赋予当前 SSH shell 同样权限，不要根据 `id` 的结果误判服务权限；硬件诊断工具使用明确的执行身份。

查看组与实际服务进程：

```sh
getent group dialout video audio
LELAMP_SERVICE_PID="$(systemctl show --value -p MainPID lelamp-web-console.service)"
test "$LELAMP_SERVICE_PID" -gt 0 && sed -n '/^Groups:/p' "/proc/$LELAMP_SERVICE_PID/status"
```

服务覆盖配置在 `lelamp-web-console.service.d/90-lelamp-hardware.conf`，公开硬件环境安装为 `/etc/lelamp/hardware.env`。其中保持运动/RGB 关闭、稳定音频设备及串口选择。控制台令牌和 API 密钥仍只放本机原有私密配置；现场路径为 `/home/z/Project/lelamp-web/console.env`。这里只记录路径，不读取或公开内容，也不把凭据提交到公开硬件文件。当前 runtime 的 `services/startup_runtime.py` 读取 `LELAMP_STARTUP_HOME=0` 后跳过启动归位。本配置同时关闭该项和 `OPENCLAW_ENABLE_HARDWARE`；启动归位开关不是所有运动的总闸，完整禁动仍需 `OPENCLAW_ENABLE_HARDWARE=0`。本次没有通过实际运动验证这些开关。

**EnvironmentFile 的优先级需要按最终加载顺序检查。** 文件中的设置覆盖 `Environment=`；多个环境文件重复同名变量时，后读取的文件覆盖前面的文件。因此，仅在 drop-in 中新增 `Environment=` 不能保证覆盖已有私密环境文件。本包让公开硬件环境作为后加载的 `EnvironmentFile`，保留原认证来源，同时落实硬件配置。若另一个后来加载的 drop-in 再添加环境文件，仍可能改变最终值。[systemd 官方说明](https://github.com/systemd/systemd/blob/main/man/systemd.exec.xml)

现有机器的 A311 音频选择来自 `30-audio-access.conf`；发布配置会把同一硬件选择落实到公开硬件环境。应用后需重启网页服务，再核对新进程补充组、实际设备和 `OPENCLAW_ENABLE_HARDWARE=0`、`OPENCLAW_ENABLE_RGB=0`。不要导出全部环境或打开私密 `console.env` 来完成这项核对。

已有网页服务保留原认证配置；系统 `.service.example` 是新部署的参考，不通过应用硬件 profile 自动替换现有 base unit。新部署时先调整模板并确认应用入口和私密认证已经配置，再安装为系统 unit。不能直接覆盖一台正在使用的机器的服务基础配置。

### 人工安装系统级网页 unit

以下命令仅用于已经按目标机器调整好路径、完成依赖/前端构建并确认运行入口的模板。当前机器发布这些配置时没有执行安装。新克隆选 `repository.service.example`；复现旧现场布局时再选择另一模板。

先把已有 base unit 备份到项目 `tmp`，再安装选定且已调整的模板：

```sh
LELAMP_SYSTEM_BACKUP="$PWD/tmp/system-unit-backup-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$LELAMP_SYSTEM_BACKUP"
chmod 700 "$LELAMP_SYSTEM_BACKUP"
if test -f /etc/systemd/system/lelamp-web-console.service; then
  sudo cp -a /etc/systemd/system/lelamp-web-console.service "$LELAMP_SYSTEM_BACKUP/lelamp-web-console.service"
fi
LELAMP_SYSTEM_TEMPLATE="$PWD/config/hardware/raspberry-pi-5/system/lelamp-web-console.repository.service.example"
sudo install -m 0644 "$LELAMP_SYSTEM_TEMPLATE" /etc/systemd/system/lelamp-web-console.service
sudo systemctl daemon-reload
sudo systemctl enable lelamp-web-console.service
```

这里的 `enable` 只设置后续启动关联，没有使用 `--now`。不要在私密环境、公开硬件环境、uv 依赖或 udev 别名未就绪时立即启动。

确认模板引用的本机私密环境已按空白示例准备（文件权限 0600、真实认证值留在本机）、`/etc/lelamp/hardware.env` 已应用、uv 绝对路径和 Python 项目环境有效，并且别名存在后，再手动重启并核对新进程：

```sh
if test -e /dev/lelamp-servo && test -e /dev/lelamp-projector; then
  sudo systemctl restart lelamp-web-console.service
  systemctl show lelamp-web-console.service -p ActiveState -p SubState -p MainPID -p SupplementaryGroups
else
  printf '%s\n' '设备别名未就绪：先完成安全重新枚举，再重启服务。' >&2
fi
```

上述 `test` 是操作前检查，不会创建别名；不要忽略失败再执行重启。还应按前述 `/proc/主PID/status` 的 Groups 确认实际权限，并通过正常认证的网页测试各设备。不要把单位文件能安装或服务 active 视为模型调用、机械运动或投影出图已验证。


## udev：加载规则与重新枚举分开

公开规则按当前每种 VID:PID 仅一只设备建立别名：

| 设备 | USB 标识 | 别名 |
| --- | --- | --- |
| 舵机控制器 | 1a86:55d3 | `/dev/lelamp-servo` |
| CH340/CH341 投影控制 | 1a86:7523 | `/dev/lelamp-projector` |

多只同型号适配器时需先增加能区分实物的属性；不能只靠这两个宽泛匹配确定设备身份。

`udevadm control --reload-rules` 加载规则，不会为当前已枚举的设备立即补建别名。应用脚本不会执行 `udevadm trigger`，以免扰动运行中的投影串口。别名在后续安全的设备重新连接或树莓派重启后出现。

投影运行时保持既有连接；先完成关机、现场确认待机与至少 15 秒延时，再安排重新枚举。别名出现前不要把服务改成依赖不存在的 `/dev/lelamp-servo` 后立即重启。

```sh
ls -l /dev/lelamp-servo /dev/lelamp-projector
ls -l /dev/serial/by-id/
ls -l /dev/snd/
ls -l /dev/dri/
```

## ALSA：仅保存和恢复 A311

本机已禁用 HDMI 音频、保留 HDMI 视频；麦克风和扬声器使用 `plughw:CARD=A311,DEV=0`，PCM 目标 60%。稳定卡名比数字声卡编号可靠。当前没有 `/etc/asound.conf` 或 `/home/z/.asoundrc`，本包也不通过创建全局 ALSA default 设备改写其他应用的声音路由。

保存和恢复都限定 A311：

```sh
sudo amixer -c A311 sget PCM
sudo amixer -c A311 sset PCM 60% unmute
sudo alsactl store A311
sudo alsactl restore A311
```

普通 shell 没有 audio 权限时，用明确具有权限的身份执行音频操作。系统的 `alsa-restore.service` 当前 active/static，不需要因本硬件 profile 再创建一个重复的全局恢复服务，也不解除 `alsa-utils.service` 的既有 mask。恢复之后读回 PCM 并实际试听；其他程序可能再次修改音量。

公开的 `asound.A311.reference.state` 只包含 `state.A311`：PCM 开关打开、左右值 88（约 60%）；麦克风采集开关打开、音量 147（100%）；自动增益关闭。它是同一 A311 设备的参考状态，恢复会同时影响该卡的这些控制，需先核对同型号/control 兼容性；不能直接套到其他声卡。

明确需要恢复参考中的 A311 控制时才执行：

```sh
sudo alsactl -f config/hardware/raspberry-pi-5/system/asound.A311.reference.state restore A311
sudo amixer -c A311 sget PCM
```

```sh
cat /proc/asound/cards
sudo aplay -l
sudo arecord -l
sudo amixer -c A311 sget PCM
```

## 投影当前是临时运行，不是开机自动恢复

现场已实际显示彩条与校准图，依靠项目 `tmp` 中的临时串口保持和原生 KMS 进程。两轴翻转适合本机当前安装方向。当前没有 projector 用户服务或系统自动投影单元，新发布脚本尚未替换现场进程，也未完成它们在 Pi 上的全流程启动、出图、关机验证。

本机 `gdm.service` 当前 disabled/inactive。新显示工具的 `--manage-display-manager` 只恢复它启动前原本 active 的管理器，不会因为测试结束就把此前 disabled 的 GDM 自动启用。需要桌面时，在安全结束投影后单独决定是否启动图形服务；本包不改变默认启动目标或自动启用 GDM。

未来若增加持久投影服务，仍需验证完整 CEA 720p60、保持串口生命周期、开关状态判断、异常退出和恢复行为。不能简单用 `Restart=always` 加连续 power-toggle 保证开机；切换帧没有独立开/关语义，也没有状态 ACK。

结束旧现场测试后再试新工具，避免两个进程同时占用串口或 KMS。操作顺序为：确认光机运行 → 单次切换关机 → 现场确认待机/镜头熄灭 → 继续持串口至少 15 秒 → 显式结束串口 → 结束 KMS。新工具的精确命令见[硬件说明](./raspberry-pi-5.md#新投影工具的运行命令)。

## 应用、后续生效与回退

先完成以上只读核对，并在仓库根目录设置有效的 uv 路径。默认预览，不修改系统：

```sh
TMPDIR="$PWD/tmp" UV_CACHE_DIR="$PWD/tmp/uv-cache" PYTHONDONTWRITEBYTECODE=1 "$LELAMP_UV_BIN" run --no-project python scripts/hardware/apply_profile.py
```

检查差异与目标机一致后应用：

应用工具的自动备份只覆盖它将修改的配置文件，不包含应用前的混音器状态。若需要完整音频回退，先把当前 A311 状态单独保存到项目 `tmp`；公开的参考状态是本机 60% 设置，不能代替目标机原状态：

```sh
LELAMP_AUDIO_BACKUP="$PWD/tmp/alsa-before-profile-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$LELAMP_AUDIO_BACKUP"
sudo alsactl -f "$LELAMP_AUDIO_BACKUP/asound.A311.state" store A311
```

```sh
sudo env TMPDIR="$PWD/tmp" UV_CACHE_DIR="$PWD/tmp/uv-cache" PYTHONDONTWRITEBYTECODE=1 "$LELAMP_UV_BIN" run --no-project python scripts/hardware/apply_profile.py --apply
```

应用前备份在项目 `tmp/hardware-profile-backup-时间/`，按系统路径保存已有目标文件。本包应用硬件 profile，包括 boot overlay、udev、公开硬件环境与网页 drop-in，并保存 A311 PCM；不会替换完整 boot 参考文件、cmdline 或 base service，也不会自动重启、触发 USB、切换投影电源、移动舵机或点亮 RGB。

生效步骤按实际影响区分：

1. boot overlay 需在后续安全重启后生效；cmdline 和根分区标识保持目标机原值。
2. udev 别名需后续重新枚举；运行中的投影不能被直接触发或拔出。
3. 网页服务环境和补充组需别名可用后手动重启服务，确认新进程。
4. ALSA PCM 设置立即生效，保存后仍应在后续启动读回确认。
5. 新投影工具需另行完成现场验证；硬件 profile 安装不等于自动投影部署。

准备重启树莓派前，先按投影关机延时要求结束当前保持会话。重启后的最小复核：

```sh
uname -r
cat /proc/cmdline
sudo env TMPDIR="$PWD/tmp" rpicam-still --list-cameras
cat /proc/asound/cards
sudo amixer -c A311 sget PCM
ls -l /dev/lelamp-servo /dev/lelamp-projector
systemctl is-active lelamp-web-console.service
systemctl show lelamp-web-console.service -p User -p SupplementaryGroups -p MainPID
```

只读枚举通过后，再分别拍照、试听和验证实际投影。舵机/RGB 继续保持关闭，不用启动运动来证明系统配置生效。

回退时选择本次应用产生的备份，按其中实际存在的路径恢复。对于新创建且没有旧副本的文件，依据安装记录处理，不删除整个服务或配置目录。恢复 boot 仍需后续安全重启；恢复 drop-in/公开环境后加载 unit 并按需要手动重启网页；恢复 udev 只加载规则，仍不触发运行中的投影。

音频使用应用前单独保存的 A311 状态恢复，再保存为系统下次启动使用的状态：

```sh
sudo alsactl -f "$LELAMP_AUDIO_BACKUP/asound.A311.state" restore A311
sudo alsactl store A311
sudo amixer -c A311 sget PCM
```

这里的 `LELAMP_AUDIO_BACKUP` 必须指向此前实际保存的目录；没有该备份就无法从本包恢复另一机器原来的音量、采集增益或 AGC 设置。

例如，在确认选对备份后恢复已有的启动配置：

```sh
LELAMP_PROFILE_BACKUP="$PWD/tmp/hardware-profile-backup-实际时间"
sudo cp -a "$LELAMP_PROFILE_BACKUP/boot/firmware/config.txt" /boot/firmware/config.txt
sudo systemctl daemon-reload
sudo udevadm control --reload-rules
```

这里只示例已备份的文件；不要对缺失项假定有旧配置，也不要用参考文件代替目标机备份。私密认证文件不在本公开包中，既不覆盖也不作为本页的读取或发布对象。
