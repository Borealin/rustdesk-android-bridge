# 原生加密会话 + scrcpy 后端首轮验证

日期：2026-09-08。范围：macOS arm64、本机已安装 RustDesk 1.4.7、Pixel 8 / Android 16、scrcpy 4.1。所有地址、ID、凭据与截图内容保持本地，不包含在此记录。

## 已观察

- 固定 RustDesk 1.4.7 源码应用 native 补丁，FFI 生成成功，`cargo rustc --features flutter --lib --locked` 加本机 VMAF 链接参数成功。12 项 Python 测试通过。
- 本地 bundle 替换 Rust dylib 后 ad-hoc 签名和验证成功，原版 Flutter 资源启动正常。不是完整 Flutter 重编译，也未验证可分发安装包。
- 独立手机 ID 没有复用桌面 ID；沿用 Mac 已有的 ID Server、Relay Server、公钥，配置中的 key_confirmed 为真。
- 最终构建会话在 20:45:35 记录 `android backend ready; native session encryption active`；之前的 rendezvous 日志显示 `create_relay` 与 `secure: true`。公网侧持续使用原生加密 FramedStream。
- 20:46:05 的原版 RustDesk 客户端窗口显示绿色加密盾牌、Pixel 8 应用抽屉，手机时钟为当前时间；worker 持续发送 576×1280 H.264 帧。两端仍在同一 Mac，但这次连接经过配置的远端中继，不是直接连接 Python 端口。
- 开发中重启 native host 后 worker 记录 EOF、session_closed，随后原客户端重新认证、重启 scrcpy 并恢复画面。最后一轮通过仓库 run_native 脚本启动。
- 输入计数增长只能证明消息到达 worker；未经对应 UI 动作逐项比对，不据此宣称新版点击拖动已验收。

本地证据入口：`runtime/native-build.log`、`runtime/native-host-stdout.log`、`runtime/native-backend.log`、`runtime/native-encrypted-client.png`。这些路径均被 Git 忽略，日志可能含有私有信息。原型早先通过的点击拖动记录见 [坐标修复](input-coordinates-2026-09-07.md)。

## 尚未验收

- 新版点击/拖动、旋转、锁屏及完整键盘矩阵；CUA 原生管道不可用，当前只完成截图检查。
- 原生入口的错误密码、非加密会话、非桌面登录、权限撤销：拒绝路径已实现并检查，但没有完成对应运行时反例。Python 错误密码不启动手机的测试不能代替原生入口测试。
- 不同网络的控制端、长期稳定性、多设备、自动恢复及安装分发。
- 原服务器 21115 NAT 探测与默认 API 存在失败告警，21116/21117 可达且本次中继成功；没有修改远端部署或认定服务器全面健康。

## 修改面

RustDesk 现有文件仅 core_main.rs（独立配置初始化）、server.rs（模块声明）、connection.rs（加密认证后的媒体/输入分流与授权管理消息隔离）。新逻辑集中在 overlay/android_backend.rs；环境变量未设置时保留原路径。Python 仅增加密码文件接口，原坐标处理不改。

## iOS 点击重复 DOWN 修复

当晚 iOS 会话通过加密中继连接，20:54–20:55 的 worker 日志中，43 个完整按下/抬起周期出现 42 次重复 DOWN；按住时长中位数 359 ms，5 次达到 400 ms。设备当时 long_press_timeout 为 400 ms。此计数无法区分用户刻意长按与误判，不作为因果结论。

固定 RustDesk 源码 `flutter/lib/common/widgets/remote_input.dart` 在移动被控端的 onLongPressDown 和 onTapUp 路径都可发送 DOWN；worker 原来把重复 DOWN 直接注入，违反单触点的事件序列。scrcpy 4.1 Controller.injectTouch 不会替调用方去重，重复 ACTION_DOWN 会再次设置 lastTouchDown。修复为状态转换式注入：已按下时忽略重复 DOWN，未按下时忽略 UP，保留 MOVE、真实长按和断线取消。新增接收侧 held_ms / duplicate_downs / write_ms 诊断，不记录坐标或文本。

13 项测试通过，服务已重启加载修复；运行时已观察重复 DOWN 被去重及抬起写入耗时低于 1 ms。用户随后在 iOS 复测轻点、拖动和刻意长按，反馈“可以了，几个操作都正常了”。该输入修复已通过用户真机复测；不能把网络中继延迟都归因于此缺陷。

网络检查：该会话实际存在到 Relay 的 TCP 连接，路由经过 Mac 的 TUN 接口；先验证 RustDesk 流量排除 TUN，再测直连或更近中继。没有修改全局代理、手机长按阈值或人为截短按压。

源码依据：[scrcpy 4.1 Controller](https://github.com/Genymobile/scrcpy/blob/v4.1/server/src/main/java/com/genymobile/scrcpy/control/Controller.java)、[RustDesk 1.4.7 remote_input.dart](https://github.com/rustdesk/rustdesk/blob/0c86d4616298f09435f6236599b300964aa61460/flutter/lib/common/widgets/remote_input.dart)。

## 移动端导航键映射修复

客户端 `InputModel.onMobileBack` 对 1.3.8+ 被控端发送 Back 鼠标键（button 8），旧客户端使用 right（button 2）；worker 原来只处理 button 2，导致新版返回无效。`onMobileHome` 发送中键点击（button 4），`onMobileApps` 则发送中键 DOWN、等待 500 ms、再发送 UP；worker 原来在 DOWN 就执行 HOME，因此最近应用也变成 HOME。

修复在 UP 执行返回，并对中键按住时长采用上游 Android 的 200 ms 分界，分别发送 Android HOME=3 / APP_SWITCH=187。为避免异步定时任务和断线后的延迟操作，两者均在中键释放时执行；因此最近应用比上游 200 ms 定时触发稍晚，通常在客户端 500 ms 释放后出现。重复中键 DOWN 不重置计时，孤立 UP 不执行动作。正常触摸路径未修改。

14 项协议与配置测试通过，服务已重启加载。用户重连后复测返回、Home、最近应用，反馈“正常了”，三键 UI 验收通过。依据为固定版本 `flutter/lib/models/input_model.dart` 的 onMobileBack/onMobileHome/onMobileApps，以及 Android InputService.kt 的中键分界逻辑。

## 远端软键盘轻点误作长按

用户明确反馈：点击远端 Android 软键盘，q/w 得到长按候选 1/2。重复 DOWN 去重修复保持了接收侧 350 ms 左右的按住时长，不能单独解决该场景。

核对固定 RustDesk Android InputService.kt：startGesture 先缓存路径；移动、endGesture 或 tap timeout + long-press timeout 到期后才 dispatchGesture，不是收到 DOWN 就立即注入。scrcpy 控制接口直接注入原始 DOWN，旧桥接把移动端识别等待完整暴露给键盘。

新策略对 Android 被控目标统一启用，不依赖控制端 my_platform（现场客户端未匹配 iOS/Android，首次分平台门控未生效，已移除）：静止 DOWN 暂存；500 ms 内抬起合成连续 DOWN/UP，不重放接收端等待；移动超过 8 个视频像素即提交起点并实时转发拖动；静止超过 500 ms 开始真实按下，之后按原始 UP 释放。长按触发增加了约 500 ms 判定等待，这是当前兼容方案的明确取舍，并非改变手机系统阈值。桌面鼠标操作 Android 目标同样使用手势提交策略。待提交触摸断线时不补点击，已注入触摸仍 CANCEL。

18 项回归测试通过，包括缺省平台认证启用、等待 350 ms 的静止 tap、真实长按提交、拖动和断线取消。服务已加载，用户反馈“可以了”，远端软键盘轻点已复测通过。此策略不能保证任意网络抖动、客户端版本或所有键盘的阈值，后续更精确方案需要客户端显式发送手势语义/时间。

## 控制端本地键盘直接输入

iOS 日志出现 `KeyEvent` 字段 `[2,4]`（press+chr）及 `[6]`（无状态标记的 seq）。旧 worker 漏掉 chr，并要求 seq 携带 down/press，因此字符和输入法文本被丢弃。原生 RustDesk Android InputService.onKeyEvent 对 seq 无条件视为提交，Legacy chr 在 down/press 转成 Unicode；Android 13+ 使用 InputConnection.commitText。

本桥接继续保留 scrcpy 后端：Legacy chr/unicode 的有效字符输入、无标记 seq 文本提交、ControlKey、Map/Translate 的 Android 键码、packed/unpacked 修饰键分别处理。ASCII 可打印文本通过 scrcpy INJECT_TEXT；其他 Unicode、换行等通过 SET_CLIPBOARD(paste=true) 提交。后者会更新手机剪贴板，不读取或回传剪贴板内容，也不保证禁止粘贴的字段可输入。尚未引入手机 IME 插件或 AccessibilityService。

控制键按 press 生成 DOWN+UP；分离 down/up 和 repeat 有独立状态，断线释放 held keys。键盘 Home/End/Escape 使用编辑键语义，与悬浮导航 Home/Back 分开。Legacy 文本型字符不承诺组合快捷键；Map/Translate 携带的修饰键可透传。尚未验收硬件键盘完整布局、复杂组合和输入法连续组合提交的所有时序。

20 项测试通过，覆盖真实接收路径中的 chr、seq、Unicode 粘贴封包、退格/回车、按键释放、修饰键及重复按下。运行版已更新，用户随后反馈“没问题了”，iOS 本地键盘验收通过；完整物理键盘矩阵仍待验证。

依据：[RustDesk Android InputService](https://github.com/rustdesk/rustdesk/blob/0c86d4616298f09435f6236599b300964aa61460/flutter/android/app/src/main/kotlin/com/carriez/flutter_hbb/InputService.kt)、[scrcpy 4.1 Controller](https://github.com/Genymobile/scrcpy/blob/v4.1/server/src/main/java/com/genymobile/scrcpy/control/Controller.java)。

## TUN 影响 NAT 探测与定向接口绑定

查询运行中的 Mihomo API：RustDesk 会话命中 DIRECT，但由 TUN 接入；不是走代理节点。绑定物理网卡与默认路由的并行原生 TestNatRequest 探针显示：默认路径访问 21116 约 278 ms，复用源地址/端口访问 21115 在 4 s 超时；物理绑定路径两次约 280/278 ms，服务器看到的外部端口一致。原始探针仅保存在私有 runtime。

增加仅独立桥接进程生效的 macOS 网络接口选项，使用 IP_BOUND_IF/IPV6_BOUND_IF 绑定原生 TCP/UDP socket。没有改 Clash 配置、系统路由或原 Mac RustDesk。回环连接排除绑定。补丁编译成功、打包签名通过，prepare 脚本应用检查和 20 项 Python 回归通过。

21:17:20 原生日志 `Tested nat type: ASYMMETRIC in 572.808708ms`。21:17:49 加密后端成功；21:18 的 socket 快照显示物理局域网 IP 到同网段客户端的直接 TCP 连接。随后来自另一公网地址的连接仍出现直连超时并请求中继；蜂窝网络直连与稳定性尚未确认，不把 Mac NAT 修复等同于任意网络的 P2P 保证。

构建日志 `runtime/native-network-build.log`，会话日志 `runtime/native-host-stdout.log`；原始地址、接口选择、配置和日志保持本地。现有 API/sysinfo/heartbeat 失败告警独立于已成功的 NAT 与媒体会话，本次没有修改远端 API 服务。

回归面：hbb_common 的 lib.rs 注册新模块、tcp.rs 的外部连接/具体地址监听、udp.rs 的非回环 UDP 创建；仅 macOS 且 Android host 与接口环境变量都存在时改行为。接口缺失/无效报错而不静默走回旧路径。没有调整加密握手、媒体格式、输入逻辑或 gitlink。


### iOS 连接回归的当前状态

21:35–21:38 iOS 请求到达桥接端，建立中继 TCP 后进入会话初始化，但约 30 秒超时，未进入 Android 后端。21:38 移除私有 network_interface 配置、无接口参数重启，iOS 仍超时。21:39 切换到 `.local/RustDeskAndroidBridge-before-network.app` 基线构建，iOS 同样未完成登录，因此不能将网卡绑定认定为唯一根因。

21:40:46–21:40:50 Mac 原版客户端通过同一中继完成原生加密登录，后端收到客户端版本 1.4.7、启动 scrcpy、发送 80 帧并收到一个输入事件，最终由客户端主动关闭。依据仍为私有 native-host-stdout.log、native-backend.log 和原版 RustDesk 客户端日志。此对照证明基线的中继与 Android 媒体路径可用，不等同于 iOS 已恢复。iOS 当时界面一直“正在连接”；正在请求重启客户端及 Wi-Fi/蜂窝对照，根因未确认。现场保留无接口绑定的基线服务，不变更 Clash 或原版 RustDesk 配置。
