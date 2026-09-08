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

14 项协议与配置测试通过，服务已重启加载。客户端三键的 UI 验收待用户复测。依据为固定版本 `flutter/lib/models/input_model.dart` 的 onMobileBack/onMobileHome/onMobileApps，以及 Android InputService.kt 的中键分界逻辑。
