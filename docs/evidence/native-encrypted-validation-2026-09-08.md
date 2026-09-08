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
