# 点击和拖动坐标修复

日期：2026-09-07。来源：用户报告点击、拖动不对，怀疑坐标映射。

## 根因与修复

RustDesk Flutter `processEventToPeer` 在 DOWN/UP 将 x/y 填零，只有 MOVE 带远端像素位置。原型对三种消息都更新位置，导致 DOWN、UP 注入到左上角。现仅 MOVE 更新位置，按钮沿用最近位置；保留 ZigZag 解码和视频尺寸语义。

源码证据：[input_model.dart 固定快照](https://github.com/rustdesk/rustdesk/blob/dc04b911a1f94919d4dbaedc172fab83b8cba1a4/flutter/lib/models/input_model.dart#L1868)。同文件 `_handlePointerDevicePos` 已处理客户端 canvas 缩放，不应在代理再次按 Mac 窗口尺寸缩放。

## 自动验证

执行 `python3 -m unittest discover -s tests -v`：10 项通过。

新增固定 wire fixture 覆盖 MOVE(100,200) → DOWN(无坐标) → MOVE(300,400) → UP(显式零坐标)，检查最终 scrcpy 包的 action、x/y 和参考画面尺寸；另测合法移动到 (0,0)。

用修复前实现执行第一项新测试，确认旧代码失败：

```text
旧：DOWN(0,0), MOVE(300,400), UP(0,0)
新：DOWN(100,200), MOVE(300,400), UP(300,400)
参考尺寸：576×1280
```

## 运行验证边界

修复版已启动并由本地 RustDesk 1.4.7 认证，Pixel 8 scrcpy H.264 流正常启动。新会话 `127.0.0.1:21130` 避免旧标签页保留过期会话；旧 21129 监听已关闭。日志位于忽略目录 `runtime/local-client-coordinates-2026-09-07.log`。

新会话收到鼠标按下、拖动事件；操作人员在修复版会话中复测，确认本次点击和拖动落点问题已解决。未输入 PIN，未更改 root、远程桌面或 RustDesk 全局配置。自动测试不代替真机落点观察。
