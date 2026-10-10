# luci-proto-n2n

面向 OpenWrt / ImmortalWrt 的 N2N VPN 协议插件，包含 `n2n-edge` 客户端后端和 `luci-proto-n2n` 前端。在 LuCI 的“网络 → 接口”中创建 N2N 接口，由 netifd 统一管理客户端的启停、TAP 设备和网络状态，不需要额外安装 `luci-app-n2n`。

本项目只管理 **edge 客户端**，不包含 supernode 服务端。当前后端使用上游 **N2N 3.0**；LuCI 插件版本与 N2N 程序版本分别管理。

## 工作方式

- 配置保存在 `/etc/config/network`，使用 `proto 'n2n'`，不是旧版插件的 `/etc/config/n2n`。
- netifd 通过 `/lib/netifd/proto/n2n.sh` 启停 `/usr/bin/n2n-edge`。每个启用的 N2N 接口对应一个常驻 edge 进程，使用 `-f` 前台运行，由 netifd 托管，不另外启动独立的 N2N init 服务。
- 社区名和加密密钥分别通过 `N2N_COMMUNITY`、`N2N_KEY` 环境变量传递给 edge，不放进启动命令行。配置文件中的密钥仍然是明文，具有足够权限的用户也可以读取进程环境；这不是密钥加密存储。
- 支持静态 IPv4 地址和 overlay DHCP。DHCP 模式由 netifd 创建 `<接口名>_4` 动态子接口并启动 DHCP 客户端，edge 本身继续负责 TAP 和 VPN 数据传输。
- 当前构建启用 pthread、OpenSSL、Zstandard 和 libcap。Linux 上通常可以看到主线程和一个 resolver 辅助线程，它们属于同一个进程，并不是运行了两个 edge。
- “接口已启动”表示本地 TAP 已就绪，不保证 supernode、其他 peer 或远端网络已经可达。

## 手动作为 feed 构建

以下命令在已经准备好编译依赖的 OpenWrt / ImmortalWrt 源码根目录执行，也适用于匹配设备架构和系统版本的 SDK。需要使用支持 LuCI JavaScript 协议插件的版本，并提前更新、安装标准的 `packages`、`luci` 等依赖 feed。

把本仓库加入源码树：

```sh
echo "src-git n2nproto https://github.com/altman08/luci-proto-n2n.git" >> feeds.conf.default
./scripts/feeds update n2nproto
./scripts/feeds install -f -p n2nproto n2n-edge luci-proto-n2n
```

如果使用 `feeds.conf`，则将第一条命令中的文件名改为 `feeds.conf`。已有同名 feed 时应替换原条目，不要重复追加。安装命令使用 `-f -p n2nproto`，确保选择本仓库的前后端包。

选择包：

```sh
make menuconfig
```

先选择目标平台及设备，再选择：

```text
Network -> VPN -> n2n-edge
LuCI -> Protocols -> luci-proto-n2n
```

或者在 `.config` 中设置以下配置项；已有同名项时先修改或移除，避免重复：

```text
CONFIG_PACKAGE_n2n-edge=m
CONFIG_PACKAGE_luci-proto-n2n=m
CONFIG_PACKAGE_luci-i18n-n2n-zh-cn=m
```

然后执行：

```sh
make defconfig
make package/n2n-edge/compile V=s
make package/luci-proto-n2n/compile V=s
```

`m` 表示生成独立软件包，不内置到固件；如需编入固件，改为 `y`（菜单中的 `<*>`）。简体中文包 `luci-i18n-n2n-zh-cn` 为可选项，不需要时可省略对应配置；通过菜单选择时，可在 `LuCI → Modules → Translations` 中启用简体中文（`zh_Hans`）。中文包由 `luci-proto-n2n` 的 LuCI 构建流程从 `po/zh_Hans/n2n.po` 自动生成，不需要单独的源码编译目标。

生成的安装包通常位于：

```text
bin/packages/<arch>/n2nproto/
```

具体为 `.ipk` 还是 `.apk` 取决于所用源码或 SDK 的包管理格式。如需编译完整固件，将相关包选为 `y` 后执行 `make -j"$(nproc)"`，固件产物位于 `bin/targets/<目标平台>/<子目标>/`。

如果使用本地仓库目录，可将上述 `src-git` 条目替换为：

```text
src-link n2nproto /path/to/luci-proto-n2n
```

`src-link` 后面必须是本地目录路径，不能填写 Git URL；目录应指向同时包含 `luci-proto-n2n/` 和 `net/` 的本仓库根目录。配置后执行相同的 feed 更新、安装和编译命令。

## 安装软件包

可以安装自行编译的软件包，或从 [Releases](https://github.com/altman08/luci-proto-n2n/releases) 下载匹配系统版本、架构和包管理格式的前后端包。内核模块 `kmod-tun` 必须与当前固件的内核版本匹配，不能只按 CPU 架构选择。

当前 GitHub Actions 工作流配置使用 OpenWrt `25.12.5` SDK，为 `x86_64`、`aarch64_cortex-a53`、`aarch64_cortex-a72`、`aarch64_generic`、`arm_cortex-a7`、`arm_cortex-a9` 构建软件包。此列表是工作流的构建配置，不代表已经在所有设备上完成实机验证。工作流发布 `n2n-edge`、`luci-proto-n2n`、`luci-i18n-n2n-zh-cn` 及校验文件，不打包依赖库。中文包也带有 SDK 和架构后缀，以避免不同构建产物重名。

将匹配的安装包上传到路由器的 `/tmp`，先确保固件软件源可用，再按系统的包管理器安装。下面的文件名需要替换为实际下载的文件名。

使用 opkg 的系统：

```sh
opkg update
opkg install '/tmp/n2n-edge_<版本>_<架构>.ipk' '/tmp/luci-proto-n2n_<版本>_all.ipk' '/tmp/luci-i18n-n2n-zh-cn_<版本>_all.ipk'
```

使用 apk 的系统：

```sh
apk update
apk add --allow-untrusted '/tmp/n2n-edge-<版本>.apk' '/tmp/luci-proto-n2n-<版本>.apk' '/tmp/luci-i18n-n2n-zh-cn-<版本>.apk'
```

本地 APK 如未被系统信任的密钥签名，需要 `--allow-untrusted`；只安装来源可信的软件包。Release 文件名还带有 SDK 和架构后缀，请以实际文件名为准。不要使用强制忽略依赖的方式安装不兼容的软件包。

`luci-proto-n2n` 会依赖 `n2n-edge`、`netifd`、`resolveip`、`ip-tiny` 等包；后端依赖 OpenSSL、pthread、Zstandard、libcap 和 TUN 内核模块。

**本项目的 `n2n-edge` 与原有完整 `n2n` 包声明冲突。** 迁移前先备份配置、停止旧 edge 实例，再处理旧包及其依赖。如果路由器还依赖原有 `n2n` 包运行 supernode，不要直接卸载，应先安排服务端迁移。本项目不会自动迁移旧配置。

## 使用与配置

安装后，在 LuCI 的“网络 → 接口 → 添加新接口”中选择 **N2N VPN** 协议，不需要选择已有物理网卡。安装 `luci-i18n-n2n-zh-cn` 后，在“系统 → 系统 → 语言和界面”选择简体中文（或使用会自动选择中文的浏览器语言），并强制刷新浏览器即可显示中文字段、帮助说明和校验提示；不需要中文界面时可以不安装翻译包。

### 常规设置

| 选项 | 说明 |
| --- | --- |
| Supernodes | 至少填写一个 IPv4 地址或域名及端口，例如 `vpn.example.com:1234`；多个节点逐项添加，不支持 IPv6 Supernode 地址 |
| Community | 社区名，长度为 1–15 字节；建议使用 ASCII，所有 peer 保持一致 |
| Payload encryption | 加密方式：Twofish、AES、ChaCha20、Speck-CTR 或不加密；默认 AES |
| Encryption key | 加密模式下必填，所有 peer 使用相同密钥及算法；密钥不能与社区名相同 |
| Compression | 不压缩、LZO 或 Zstandard，默认不压缩；分别对应省略 `-z`、`-z1`、`-z2`，不会向 N2N 3.0 传入无效的 `-z0` |
| IPv4 address assignment | 静态地址或 overlay DHCP，默认静态地址 |
| IPv4 address / prefix length | 静态模式填写 overlay 地址及前缀长度，默认前缀为 `24`；各 peer 地址应避免冲突 |
| IPv4 gateway | 可选的 overlay 网关；填写后在默认路由未禁用时会发布默认路由，仅点对点或站点互联时通常留空 |

不加密模式必须移除原有密钥，且不建议用于不可信网络。压缩会增加 CPU 开销，对已经压缩或加密的数据收益通常有限，不保证提高吞吐。

### 高级设置

- **TAP 设备名称**：默认是 `n2n-<接口名>`。例如接口 `n2n0` 默认生成设备 `n2n-n2n0`；不需要前缀时，将此字段填写为 `n2n0`。设备名只能包含字母、数字、下划线和连字符，长度不超过 15 个字符，且不能与已有设备或其他 N2N 实例冲突。
- **Underlying interface**：可选的底层出口，例如 `wan`，用于建立到 Supernode 的网络依赖；不能选择 N2N 接口自身。
- **MTU / TAP MAC address**：MTU 默认 `1290`，支持 `576–1500`；MAC 留空时由 N2N 生成，重启后可能改变。
- **Local UDP port**：默认 `0`，由系统分配可用端口。多个实例使用固定端口时应避免冲突。
- **Management UDP port**：仅监听 loopback，默认 `0`，由系统分配端口；`0` 不是禁用管理接口。多个实例的固定管理端口不能相同，不建议将管理端口转发到公网。
- **Allow routed traffic**：默认开启，允许 LAN 等其他主机的流量通过 edge；DHCP 模式强制开启。此选项不会自动创建站点路由或防火墙转发规则。
- **Encrypt packet headers**：可选，Supernode 也需要对应社区和头部加密配置。
- **Forward multicast traffic**：可选，默认关闭；需要经过 overlay 传递组播流量时按需开启。
- **TAP startup timeout**：默认 `30` 秒，支持 `5–120` 秒。它限制本地 TAP 就绪等待，不是 peer 连通性检测超时。
- **Use DNS servers advertised by peer / DHCP hostname**：仅用于 overlay DHCP 模式。

### 静态地址示例

以下内容添加到 `/etc/config/network`，或在 LuCI 中填写等价配置。示例地址、社区名和密钥需要替换；已有 `n2n0` 接口时修改原配置，不要重复创建。

```text
config interface 'n2n0'
        option proto 'n2n'
        list supernode 'vpn.example.com:1234'
        option community 'my-community'
        option cipher '3'
        option key 'replace-with-a-strong-secret'
        option compression '0'
        option mode 'static'
        option ipaddr '10.20.30.2'
        option prefix '24'
        option tunname 'n2n0'
        option routing '1'
```

配置完成后执行：

```sh
ifup n2n0
ifstatus n2n0
```

DHCP 模式将 `mode` 改为 `dhcp`，不需要静态地址、前缀和网关字段。必须有能够通过其他 N2N peer 到达的 DHCP 服务器；这不是 N2N Supernode 自带的地址分配模式。不要预先创建 `n2n0_4` 配置段，该名称由动态 DHCP 子接口使用。

### 路由、防火墙与迁移注意事项

- 将 N2N 接口加入合适的防火墙区域，按需配置输入策略、LAN 与 VPN 之间的转发，以及到远端网段的静态路由；本项目不会直接插入 iptables 规则，也不会自动允许所有 VPN 流量。
- 配置 overlay 默认路由时，应保证 Supernode 仍通过底层网络可达，避免让 VPN 的建立流量绕回自身；不要让解析 Supernode 所需的 DNS 完全依赖尚未建立的 VPN。
- 不要同时让旧 `/etc/init.d/n2n` 或手动命令管理同一个 edge/TAP。`luci-app-n2n` 的旧配置不能直接替代本项目的 network 接口配置。
- 当前不提供 LAN 二层桥接向导、Supernode 管理、完整 peer 状态面板，也没有暴露 edge 的所有命令行参数。需要 LAN 跨站互通时，应另外规划两端路由和防火墙。

## 常见问题

### 协议列表没有 N2N

先确认前后端文件存在，再检查 netifd 是否已经识别协议：

```sh
ls -l /lib/netifd/proto/n2n.sh
ls -l /www/luci-static/resources/protocol/n2n.js
sh -n /lib/netifd/proto/n2n.sh
(cd /lib/netifd/proto && ./n2n.sh '' dump)
ubus call network get_proto_handlers '{}' | grep -A 8 '"n2n"'
```

如果手动 `dump` 正常，但 netifd 尚未识别，新安装后可能需要重启网络服务以重新扫描协议。**网络重启可能断开 SSH 并影响其他接口，先确保有重新连接或本地管理方式。**

```sh
/etc/init.d/network restart
```

重新连接后复查协议，并在浏览器按 `Ctrl+F5` 强制刷新。仅更新前端 `n2n.js` 时通常只需刷新浏览器，不必重启整个网络。

### 保存提示无效字段

检查常规和高级设置中标红的字段，将鼠标移到输入框查看校验说明。常见原因包括 Supernode 缺少端口、社区名超过 15 字节、密钥与社区名相同、静态地址无效，以及 TAP 名称过长或管理端口重复。

### 接口已启动，但无法通信

```sh
ifstatus n2n0
ip address show dev n2n0
logread | grep -E 'netifd|n2n' | tail -n 60
```

这里假设接口名和自定义 TAP 名都为 `n2n0`，实际使用时按配置替换。检查 Supernode 可达性、双方社区/加密算法/密钥、overlay 地址、路由和防火墙。DHCP 模式还需要检查 `ifstatus n2n0_4` 和 overlay DHCP 服务。

`TAP_TIMEOUT` 表示 edge 未在设定时间内准备好 TAP，`MISSING_TUN` 表示 TUN 设备不可用，`DEVICE_IN_USE` 表示 TAP 名已经被占用。修正配置后可使用 `ifup <接口名>` 重试。分享日志和配置时遮住密钥，不要公开完整进程环境。

### htop 显示两个线程或较大的 SHR

当前构建启用 pthread，resolver 线程负责后台重新解析 Supernode 地址。关闭 pthread 需要重新编译，届时解析会改为主线程同步执行，域名解析慢或超时可能阻塞 VPN 数据处理；不是运行时杀掉线程。

htop 的线程行共用进程地址空间，不要把内存相加。`VIRT` 不等于实际 RAM，`SHR` 包含可共享的文件映射页面，不代表全部为进程独占内存。仅关闭线程通常不会显著减少 OpenSSL、Zstandard 等库的驻留页面，本项目没有承诺固定的内存占用或性能收益。

## 回归测试

在本仓库根目录执行：

```sh
python3 tests/test_n2n.py -v
```

测试使用模拟环境覆盖协议参数、敏感值传递、地址校验、压缩、静态/DHCP 流程和前端校验，同时检查简体中文翻译是否完整、流水线能否收集 IPK/APK 中文包及生成校验文件，以及缺少中文包时是否拒绝发布。测试不会启动真实 edge 或修改主机网络；产物收集测试使用自动清理的临时目录。部分测试需要 Bash；Node.js 不可用时会明确跳过前端测试。自动化回归不替代目标设备上的编译、联网和稳定性验证。
