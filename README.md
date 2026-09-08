# rustdesk-android-bridge

运行在电脑上的 Android 机架代理：目标是让普通 RustDesk 客户端通过 scrcpy/ADB 操作手机，并复用已有 root 设备的凭据页面可视化能力。

当前阶段：原生 RustDesk 1.4.7 加密会话 + scrcpy 后端已完成构建和首轮中继视频实测；见 [原生模式构建与运行](native/README.md)。新版输入和故障矩阵仍待验收。此前 Python 标准库本机兼容性原型已验证：本地 RustDesk 1.4.7 已显示 Pixel 8 的 H.264 视频及系统 SECURE 凭据页面；基本点击和拖动经用户真机复测通过，完整锁屏矩阵尚待验证。

## 文档

- [技术方案](docs/plans/rustdesk-android-bridge.md)：架构、兼容性、实施阶段与验证边界。
- [版本管理约定](CONTRIBUTING.md)：分支、提交与证据管理。
- [本地客户端实验](docs/evidence/local-rustdesk-validation-2026-09-06.md)：运行方式和实际证明边界。

## 仓库结构

```text
docs/plans/     每个功能的当前方案
docs/evidence/  精选、可复核的实验记录与必要图片
src/           本机协议兼容性原型
tests/         协议边界与会话回归测试
```

本仓库是独立项目。固定版本 RustDesk 补丁和 AGPL 许可证副本保存在 `native/`，完整上游 checkout 与构建产物只在本地生成。公开仓库仅保留脱敏后的验证摘要；原始日志、屏幕截图、设备标识和个人会话记录不随项目分发。

## 运行本机原型

要求 Python 3.10+、已授权的 ADB 设备、scrcpy **4.1** server，以及 `/Applications/RustDesk.app`。原型直接推送 server JAR 并启动，不需要打开 scrcpy 窗口。

```bash
python3 src/local_bridge.py \
  --serial <ADB_SERIAL> \
  --server "$(brew --prefix scrcpy)/share/scrcpy/scrcpy-server" \
  --port 21129 --launch-client --log-file runtime/local-client.log
```

程序生成本次进程内使用的随机会话密码，通过客户端连接参数打开会话。监听地址固定为 `127.0.0.1`，没有对公网或局域网开放的选项。当前直连协议未实现加密，RustDesk 会显示未加密标识；仅用于同机测试，不能作为远程部署产物。重启后密码变化，如客户端仍打开旧标签页，应先关闭旧会话再重新运行。

现有 root 模块仅作为设备预置环境使用；启动原型不会安装 root 模块、修改锁屏配置或输入 PIN。按 Ctrl+C 停止原型，释放会话和本次 ADB 转发。

## 检查

```bash
python3 -m py_compile src/local_bridge.py
python3 -m unittest discover -s tests -v
```

当前固定 H.264、最大边长 1280、最大 30 fps、4 Mbps；Python 原型没有音频、剪贴板同步、文件传输、ID 服务、编码切换或完整键盘映射；原生模式复用 RustDesk ID 注册及加密中继。当前 `PeerInfo` 按 Android/1.4.7 兼容字段发送，仅表示实验目标协议，不代表完整实现原版 RustDesk 功能。
