# 当前设备的系统配置参考

这组文件对应树莓派 5、Ubuntu 26.04.1 LTS、aarch64 与 `7.0.0-1020-raspi` 内核。硬件配置应用脚本不会安装操作系统、替换基准网页服务、覆盖内核启动参数或自动重启。

| 文件 | 用途 |
| --- | --- |
| `boot-layout.fragment.txt` | 保留 Ubuntu `current/` 正常启动与 `new/` tryboot 路径的参考片段 |
| `boot-config.reference.txt` | 实机完整启动配置，供对照，不能直接整份覆盖目标机 |
| `cmdline.txt.example` | 已验证启动参数的脱敏参考，保留目标机器自己的 `root=` 值 |
| `observed-system.json` | 实机系统、服务、硬件环境、权限与音频状态记录 |
| `packages.reference.txt` | 目标系统已安装的相关软件版本 |
| `asound.A311.reference.state` | 仅 A311 的混音器状态参考，PCM 60%，Mic Capture 100%，AGC 关闭 |
| `lelamp-web-console.service.example` | 当前系统级网页服务的复现模板，使用 `uv` 执行 |
| `lelamp-web-console.repository.service.example` | 全新 Git checkout 布局的系统级网页服务模板，uv 项目位于 `lelamp_runtime/` |
| `console.env.example` | 不含认证或厂商密钥的部署路径与环境示例 |

## 启动路径与参数

正常启动的 `os_prefix=current/` 指向 `/boot/firmware/current/`，因此内核命令行文件是 `/boot/firmware/current/cmdline.txt`。tryboot 分支保留 `os_prefix=new/`。`boot-layout.fragment.txt` 只用于对照已存在的布局，不直接追加，以免重复或更改启动路径。相机和 HDMI 音频设置按上级目录的硬件片段合并到 `config.txt`，不要替换整个 Ubuntu 配置文件。

`cmdline.txt.example` 必须保持单行。文件中的 root 占位不能直接写入系统：保留目标机器已有的根分区定位值，同时保留其余必需的启动参数。硬件应用脚本不会修改 cmdline。

## 账户、目录和服务

现场部署账户为 `z`，项目路径为 `/home/z/Project/lelamp-web`。新机器使用其他账户或路径时，同时修改模板的 `User`、`Group`、`WorkingDirectory`、两个环境文件路径与 `ExecStart`。项目及其 `tmp`、uv 缓存和运行状态目录需要能由服务账户写入；`bin/uv` 必须可执行，项目依赖应先通过 uv 准备好，否则 `--offline --no-sync` 不会自动下载缺失依赖。

服务通过 `SupplementaryGroups=dialout video audio` 访问串口、视频和声卡。该设置只赋予服务进程相应补充组，不需要给全部登录进程增加权限。原生投影显示工具按硬件说明以 root 运行并协调 KMS 控制权；网页服务不因此获得原生 DRM 显示控制。

模板读取环境文件的顺序是私有 `/home/z/Project/lelamp-web/console.env`，随后公共 `/etc/lelamp/hardware.env`。systemd 的 EnvironmentFile 会覆盖 Environment，后加载的环境文件值也会覆盖前面的值；因此 hardware.env 固定设备选择并保持硬件运动、RGB 禁用。`LELAMP_STARTUP_HOME=0` 在 `StartupRuntimeMixin` 中跳过启动归位，其他运动写入由 `OPENCLAW_ENABLE_HARDWARE=0` 禁用。`apply_profile.py --apply` 同时安装公共文件及对应 drop-in，只执行 daemon-reload，网页进程需要稍后手动重启才读取新配置。

已有 `console.env` 不得被示例替换。认证和厂商密钥只在目标机器本地管理；私有文件建议 `0600` 并由服务账户可读。Git 中只保留这个无密钥示例。udev 别名在后续设备枚举后出现；确认 `/dev/lelamp-servo` 存在再重启读取硬件配置的网页服务。

本机监听 `0.0.0.0:8790`，投影网页预览端口为 `8765`。这是系统级 `multi-user.target` 服务，与仓库原有固定 `/home/lemp/lelamp` 的用户级启动脚本不同。使用这里的 uv 启动模板，避免旧脚本的硬件启用默认值、旧声卡编号和直接 Python 启动方式。

全新 Git checkout 使用 `lelamp-web-console.repository.service.example`，默认路径为 `/home/z/Project/lelamp`。仓库的 `pyproject.toml` 和 `uv.lock` 在 `lelamp_runtime/` 内，因此模板的 `--project` 和 `--directory` 都指向该目录；现场旧部署模板仍准确保留其根目录项目路径。可把已有 `/home/z/Project/lelamp-web/bin/uv` 复制到新 checkout 的 `bin/uv`，或将模板中的 uv 路径替换为本机已有的绝对路径。随后先通过 uv 准备新项目的依赖，构建网页产物，并把环境示例中的 TMPDIR/UV_CACHE_DIR 路径改到新 checkout 的 `tmp`。离线启动不会补装缺失依赖，也不会复制现有机器的私有认证配置。

## ALSA 保存与恢复

已确认可听的 USB 声卡配置目标为 A311 的 PCM 60%，不依赖 card 数字编号。应用配置时执行：

```sh
sudo amixer -c A311 sset PCM 60% unmute
sudo alsactl store A311
sudo amixer -c A311 sget PCM
```

状态由 ALSA 保存到 `/var/lib/alsa/asound.state`。设备就绪后可以用 `sudo alsactl restore A311` 恢复已保存状态并再次读回；检查发行版自带的 ALSA 恢复服务与 USB 枚举顺序。保存一次不能证明每次启动后音量都仍为 60%，不要在未经复查时这样宣称。当前配置包不额外安装自动恢复服务。

调整系统服务或重启树莓派之前，先按投影说明关机、现场确认待机并继续保持串口至少 15 秒，再安全结束原生显示和串口会话。
