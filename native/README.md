# Native RustDesk host + scrcpy worker

实验集成，当前验证平台为 macOS arm64。沿用 RustDesk 的 ID 注册、中继、公钥校验、密码认证及加密帧；认证成功且 `FramedStream::is_secured()` 为真时才连接回环媒体后端。手机 H.264 压缩流不在 Mac 解码再编码。

## 固定源码

- RustDesk 1.4.7: `0c86d4616298f09435f6236599b300964aa61460`
- hbb_common gitlink: `df6badca5bf81b4e9836256cf8e31c993ad70dd1`
- scrcpy server: 4.1
- 本地 FFI 生成工具：Flutter 3.24.5 / flutter_rust_bridge_codegen 1.80.1（uuid feature）/ cargo-expand 1.0.95。

`python3 scripts/prepare_native.py` 拉取固定源码、应用补丁并复制 overlay；已有 checkout 必须保持固定 HEAD，遇到冲突不重置。上游源码放在被忽略的 `.local/` 中。`overlay/android_backend.rs` 和补丁中的 RustDesk 衍生代码遵循 AGPL-3.0，见 [上游许可证副本](LICENSE.RustDesk)。此目录不分发原版应用或第三方二进制。

## 构建

需要 Xcode、Rust、上游原生依赖（aom/libvpx/opus/libyuv）以及上述 Flutter/FFI 工具。遵循固定版本上游构建说明准备 vcpkg；这次本机验证使用 Homebrew 原生库及本地构建的 libyuv，而非已验证的通用安装器。

本机验证的 libyuv 提交为 `af1aaca84027a69ab25f251ade1bf1714b180d89`，静态 `yuv` target。原生库布局为 `.local/native-deps/arm64-osx/{include,lib}`，`.local/vcpkg-layout/installed` 指向 `.local/native-deps`。Homebrew AOM 启用了 VMAF，因此最后一步显式链接 VMAF。该布局不是完整 vcpkg 安装。

从 `.local/rustdesk-host` 执行（工具需在 PATH）：

```bash
export VCPKG_ROOT="$PWD/../vcpkg-layout"
export VCPKG_INSTALLED_ROOT="$PWD/../native-deps"
export LIBCLANG_PATH="$(xcode-select -p)/Toolchains/XcodeDefault.xctoolchain/usr/lib"
(cd flutter && flutter pub get)
RUST_LOG=info flutter_rust_bridge_codegen \
  --rust-input ./src/flutter_ffi.rs \
  --dart-output ./flutter/lib/generated_bridge.dart \
  --c-output ./flutter/macos/Runner/bridge_generated.h
cargo rustc --features flutter --lib --locked -j8 -- \
  -L native=/opt/homebrew/opt/libvmaf/lib -l static=vmaf
```

这里使用 Xcode libclang；本机 Homebrew LLVM 22 生成的 AOM 绑定不完整。生成的 Flutter lock/FFI 文件只留在本地 upstream checkout，不混入补丁。Debug 构建通过不表示兼容旧版 macOS；本机原生依赖存在 deployment-target 警告，尚未做分发构建。

回到仓库根目录运行 `python3 scripts/package_native_macos.py`。脚本复用本机已安装的 **1.4.7** Flutter bundle、替换 Rust dylib、改为独立 bundle ID、删除 URL scheme 注册并做本地 ad-hoc 签名。已有输出不覆盖，可用 `--output` 指定新路径。不是完整 Flutter 重编译，不用于公开发布安装包。

## 私有配置与运行

需要 Python 3.11+（配置导入用 tomllib）。

```bash
python3 scripts/configure_native.py
python3 scripts/run_native.py \
  --serial <ADB_SERIAL> \
  --server "$(brew --prefix scrcpy)/share/scrcpy/scrcpy-server"
```

配置脚本只读取 Mac 已有 RustDesk 的 ID Server / Relay Server / API Server / 公钥，不复制其身份和密码。首次启动建立独立的手机 ID、永久密码及 `RustDeskAndroidBridge` 配置目录。后续网络/密码配置由该独立实例的原生配置管理；JSON 内的 frontend_options 不会在每次启动覆盖 UI 设置。

`runtime/native-host-password` 是控制端连接手机 ID 所用的密码；`native-backend-password` 仅供回环 worker 使用。两者都是随机的 owner-only 文件。不要发布 runtime 文件、日志、应用配置或设备标识。现有 Mac RustDesk 配置不修改。

读取独立 ID：

```bash
RUSTDESK_ANDROID_BACKEND_CONFIG="$PWD/runtime/native-backend.json" \
  .local/RustDeskAndroidBridge.app/Contents/MacOS/RustDesk --get-id
```

普通客户端配置同一自建服务器后连接这个 ID。worker 仅监听 `127.0.0.1:21132`，不转发该端口到公网；远端连接走原生 RustDesk 会话。服务端 21115 NAT 探测和 API 不可达时可能产生告警；21116/21117 可用并不等于服务器所有功能健康。

## 边界

只支持单设备、单控制者、H.264、基础输入。远端文件/隧道/终端/摄像头登录拒绝；输入只去手机，授权管理器非必要消息不会订阅 Mac 音频、剪贴板或桌面服务。撤销输入权限会结束手机会话。断开时清理后端、取消触点。

原版 TCP tunnel 的 `set_raw()` 会清除 session key，不能用来包装本项目的未加密后端。本补丁在公网方向持续使用原版加密 FramedStream，未调用 set_raw。

验证与未完成项见 [实测记录](../docs/evidence/native-encrypted-validation-2026-09-08.md)。

## macOS TUN 与 NAT 探测

若 TUN 改写本地 socket 地址，DIRECT 规则仍可能影响 RustDesk 复用源端口的 NAT 探测。可仅让独立 Android host 的原生 TCP/UDP socket 绑定物理接口：

```bash
python3 scripts/run_native.py --interface <PHYSICAL_INTERFACE> \
  --serial <ADB_SERIAL> --server <SCRCPY_SERVER_JAR>
```

也可在私有 `runtime/native-backend.json` 设置 `network_interface`，命令行优先。不设则沿用原路由。接口不存在时启动报错，切换网卡后需更新选择。实现使用 macOS IP_BOUND_IF/IPV6_BOUND_IF；回环 worker 不绑定物理接口。仅在 Android host 环境变量存在时生效，不修改系统路由、Clash 配置或已安装的 RustDesk。

`hbb-common-interface.patch` 修改固定 hbb_common 的 TCP/UDP socket 创建点并注册 `network_interface.rs` 模块。原 gitlink 不变，prepare 脚本同时应用两个补丁。范围仅为原生 TCP/UDP 会话、探测和监听；reqwest API 请求和 WebSocket 不是本次绑定覆盖面。API 失败告警不能直接当成远控中继失败。

实测：默认 TUN 下第一 NAT 端口可达、复用端口的第二次连接超时；物理接口绑定下两次响应保持同一外部端口，原生 NAT 探测约 573 ms 成功，且观察到局域网加密直连。跨蜂窝网络 P2P 尚未确认，不能保证所有 NAT 组合都可直连。
