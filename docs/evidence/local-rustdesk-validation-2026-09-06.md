# 本地 RustDesk ↔ scrcpy 验证摘要

本报告经过脱敏，保留技术结果和证明边界。原始截图、日志、设备序列号和本机路径不公开，因此不能仅凭本报告独立复核原始画面。

## 环境

- macOS；未修改的 RustDesk 1.4.7，build 65。
- Pixel 8 / Android 16，经已授权 USB ADB 连接。
- scrcpy 4.1，Homebrew server JAR SHA-256：`deacb991ed2509715160ffdc7907e47b4160eb30d1566217e9047fd5b8850cae`。
- 凭据页面实验使用预先配置的 root 测试设备；代理没有安装或修改 root 模块。
- Python 标准库原型；H.264，576×1280，最大 30 fps，4 Mbps，仅监听回环地址。

## 结果

2026-09-06：RustDesk 完成密码挑战认证，创建 H.264 VideoToolbox 解码器；实际客户端窗口显示手机桌面。系统 `ConfirmLockPassword` 页面仍带 `SECURE` 标记，客户端中可见 PIN 输入框和数字键盘。没有输入或保存 PIN。

手机编码 → 主机封装 RustDesk 视频消息 → 客户端解码；不是发送静态 screencap，也没有主机二次编码。

首轮缺少服务端保活，约 120 秒断开。增加 TestDelay 心跳并避免循环回显后，连接持续超过 7 分钟。视频发送计数不能代替呈现证明，显示结论来自当时的客户端窗口观察。

2026-09-07：修复按钮消息的坐标语义后，操作人员在实际客户端复测，确认基本点击和拖动正常。见 [坐标修复](input-coordinates-2026-09-07.md)。

## 复现命令

```bash
adb devices -l
scrcpy --version
python3 -m py_compile src/local_bridge.py
python3 -m unittest discover -s tests -v
python3 src/local_bridge.py \
  --serial <ADB_SERIAL> \
  --server "$(brew --prefix scrcpy)/share/scrcpy/scrcpy-server" \
  --port 21129 --launch-client --log-file runtime/local-client.log
```

当前 10 项协议测试通过。没有运行 Cargo/Flutter 构建；部署的是临时 scrcpy-server，不是安装 APK 或烧录固件。

## 尚未验证

- 经代理完成重启后首次解锁（BFU）、Private Space 解锁和正确凭据提交。
- 完整键盘、多指输入、旋转及断网重连矩阵。
- 跨机器网络连接、加密、中继和 ID 服务。

系统 PIN 确认页面可见不等于所有受保护内容都可见，也不代表凭据校验被替代。当前原型没有加密，只用于本机实验。

## 来源

- RustDesk：`dc04b911a1f94919d4dbaedc172fab83b8cba1a4`。
- 对应 hbb_common gitlink：`3d6fb2c397f2a9a717440f7e29afed5ab5f5dc03`。
- scrcpy：tag `v4.1` 的 `doc/develop.md`、`app/src/control_msg.c`。
