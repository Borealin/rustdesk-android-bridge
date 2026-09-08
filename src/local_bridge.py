"""Loopback-only RustDesk/scrcpy interoperability experiment (Python stdlib).

Wire references and limitations: docs/plans/rustdesk-android-bridge.md.
No rendezvous, relay, encryption, file transfer or persistent credentials.
"""
import argparse
import asyncio
import hashlib
import hmac
import logging
import secrets
import signal
import stat
import struct
import subprocess
import time
from pathlib import Path

LOG = logging.getLogger("bridge")
MAX_PACKET = 16 * 1024 * 1024


def varint(n):
    n &= (1 << 64) - 1
    out = bytearray()
    while n > 127:
        out.append((n & 127) | 128)
        n >>= 7
    out.append(n)
    return bytes(out)


def pb(number, value):
    if isinstance(value, str):
        value = value.encode()
    if isinstance(value, bytes):
        return varint(number * 8 + 2) + varint(len(value)) + value
    return varint(number * 8) + varint(value)


def parse(data):
    result, pos = {}, 0

    def read_varint():
        nonlocal pos
        value = 0
        for shift in range(0, 70, 7):
            if pos >= len(data):
                raise ValueError("truncated protobuf varint")
            b = data[pos]
            pos += 1
            if shift == 63 and b > 1:
                raise ValueError("protobuf varint overflow")
            value |= (b & 127) << shift
            if b < 128:
                return value
        raise ValueError("invalid protobuf varint")

    while pos < len(data):
        tag = read_varint()
        number, wire = tag >> 3, tag & 7
        if not number:
            raise ValueError("invalid protobuf field")
        if wire == 0:
            value = read_varint()
        elif wire in (1, 2, 5):
            size = read_varint() if wire == 2 else (8 if wire == 1 else 4)
            if pos + size > len(data):
                raise ValueError("truncated protobuf field")
            value = data[pos:pos + size]
            pos += size
        else:
            raise ValueError("unsupported protobuf wire type")
        result[number] = value
    return result


def frame(data):
    n = len(data)
    if n > MAX_PACKET:
        raise ValueError("frame too large")
    count = next(k for k in range(1, 5) if n < 1 << (8 * k - 2))
    return ((n << 2) | (count - 1)).to_bytes(count, "little") + data


async def receive(reader):
    first = await reader.readexactly(1)
    count = (first[0] & 3) + 1
    header = first + await reader.readexactly(count - 1)
    size = int.from_bytes(header, "little") >> 2
    if size > MAX_PACKET:
        raise ValueError("frame too large")
    return await reader.readexactly(size)


def unzigzag(n):
    return (n >> 1) ^ -(n & 1)


def touch(action, x, y, width, height):
    # scrcpy pointer id -2 means a virtual finger, not a mouse pointer.
    return struct.pack(">BBQiiHHHII", 2, action, (1 << 64) - 2, x, y,
                       width, height, 0 if action in (1, 3) else 65535, 0, 0)


def keycode(code, action):
    return struct.pack(">BBIII", 0, action, code, 0, 0)


class Scrcpy:
    def __init__(self, args):
        self.args = args
        self.proc = None
        self.port = None
        self.control = None
        self.video_writer = None
        self.logger = None
        self.control_reader_task = None
        self.width = self.height = 0

    async def adb(self, *args):
        p = await asyncio.create_subprocess_exec(
            "adb", "-s", self.args.serial, *args,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            out, err = await asyncio.wait_for(p.communicate(), 15)
        except BaseException:
            p.kill()
            await p.wait()
            raise
        if p.returncode:
            raise RuntimeError("adb command failed: " + err.decode(errors="replace")[:300])
        return out.decode().strip()

    async def start(self):
        if await self.adb("get-state") != "device":
            raise RuntimeError("target device not authorized")
        remote = "/data/local/tmp/rustdesk-bridge-scrcpy.jar"
        await self.adb("push", self.args.server, remote)
        scid = secrets.randbelow(0x7fffffff)
        self.port = int(await self.adb("forward", "tcp:0", f"localabstract:scrcpy_{scid:08x}"))
        self.proc = await asyncio.create_subprocess_exec(
            "adb", "-s", self.args.serial, "shell", f"CLASSPATH={remote}", "app_process", "/",
            "com.genymobile.scrcpy.Server", "4.1", f"scid={scid:08x}",
            "tunnel_forward=true", "audio=false", "control=true", "video_codec=h264",
            "max_size=1280", "max_fps=30", "video_bit_rate=4000000",
            "clipboard_autosync=false", "cleanup=true", "log_level=info",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        self.logger = asyncio.create_task(self.read_log())
        for _ in range(100):
            try:
                reader, writer = await asyncio.open_connection("127.0.0.1", self.port)
                await asyncio.wait_for(reader.readexactly(1), .3)
                self.video, self.video_writer = reader, writer
                break
            except (ConnectionError, asyncio.IncompleteReadError, asyncio.TimeoutError):
                if 'writer' in locals():
                    writer.close()
                if self.proc.returncode is not None:
                    raise RuntimeError("scrcpy server exited")
                await asyncio.sleep(.1)
        else:
            raise RuntimeError("scrcpy startup timeout")
        control_reader, self.control = await asyncio.open_connection("127.0.0.1", self.port)
        self.control_reader_task = asyncio.create_task(self.drain_control(control_reader))
        self.name = (await asyncio.wait_for(self.video.readexactly(64), 10)).split(b"\0")[0].decode()
        codec = await self.video.readexactly(4)
        if codec != b"h264":
            raise ValueError("expected H264 from scrcpy")
        header = await asyncio.wait_for(self.video.readexactly(12), 15)
        if not header[0] & 128:
            raise ValueError("expected scrcpy 4.1 session header")
        _, self.width, self.height = struct.unpack(">III", header)
        LOG.info("scrcpy_ready name=%s size=%sx%s codec=h264", self.name, self.width, self.height)

    async def drain_control(self, reader):
        while await reader.read(4096):
            pass  # No clipboard autosync is requested; consume device responses.

    async def read_log(self):
        while line := await self.proc.stdout.readline():
            LOG.info("scrcpy %s", line.decode(errors="replace").strip())

    async def close(self):
        for w in (self.control, self.video_writer):
            if w:
                w.close()
        if self.proc and self.proc.returncode is None:
            self.proc.terminate()
            try:
                await asyncio.wait_for(self.proc.wait(), 3)
            except asyncio.TimeoutError:
                self.proc.kill()
                await self.proc.wait()
        for t in (self.logger, self.control_reader_task):
            if t:
                t.cancel()
        await asyncio.gather(*(t for t in (self.logger, self.control_reader_task) if t), return_exceptions=True)
        if self.port:
            await self.adb("forward", "--remove", f"tcp:{self.port}")


class Session:
    def __init__(self, reader, writer, args, password):
        self.reader, self.writer, self.args, self.password = reader, writer, args, password
        self.phone = Scrcpy(args)
        self.down = False
        self.x = self.y = 0
        self.press_started = None
        self.duplicate_downs = 0
        self.middle_started = None
        self.mobile_touch = False
        self.touch_injected = False
        self.pending_since = None
        self.touch_changed = asyncio.Event()
        self.press_position = (0, 0)
        self.frames = self.events = self.acks = 0
        self.tasks = []

    async def send(self, message):
        self.writer.write(frame(message))
        await asyncio.wait_for(self.writer.drain(), 5)

    async def run(self):
        salt, challenge = secrets.token_hex(12), secrets.token_hex(12)
        await self.send(pb(9, pb(1, salt) + pb(2, challenge)))
        digest = hashlib.sha256(hashlib.sha256((self.password + salt).encode()).digest() + challenge.encode()).digest()
        for _ in range(5):
            msg = parse(await asyncio.wait_for(receive(self.reader), 60))
            if 7 not in msg:
                continue  # Direct-IP client may first send an empty key handshake.
            login = parse(msg[7])
            if any(k in login for k in (7, 8, 15, 16)):
                raise ValueError("only desktop viewing is supported")
            if not hmac.compare_digest(login.get(2, b""), digest):
                await self.send(pb(8, pb(1, "Wrong Password")))
                continue
            # The controlled target is Android regardless of the client's
            # platform string (some clients omit or report it differently).
            self.mobile_touch = True
            LOG.info("android_touch_deferred=True")
            option = parse(login.get(6, b""))
            decoding = parse(option.get(10, b""))
            LOG.info("authenticated client_version=%s decoding=%s", login.get(11, b"").decode(),
                     {k:v for k,v in decoding.items() if isinstance(v,int)})
            if not decoding.get(2):
                await self.send(pb(8, pb(1, "This prototype requires an H.264 capable client")))
                return
            break
        else:
            raise ValueError("authentication failed")
        await self.phone.start()
        display = pb(3, self.phone.width) + pb(4, self.phone.height) + pb(5, self.phone.name) + pb(6, 1)
        peer = (pb(1, "Android bridge") + pb(2, self.phone.name) + pb(3, "Android") +
                pb(4, display) + pb(7, "1.4.7") + pb(10, pb(1, 1)))
        await self.send(pb(8, pb(2, peer)))
        LOG.info("login_response_sent")
        self.tasks = [asyncio.create_task(self.video()), asyncio.create_task(self.inputs()),
                      asyncio.create_task(self.heartbeat()), asyncio.create_task(self.touch_deadlines())]
        done, _ = await asyncio.wait(self.tasks, return_when=asyncio.FIRST_COMPLETED)
        for t in done:
            t.result()

    async def video(self):
        config = b""
        base_pts = None
        while True:
            h = await self.phone.video.readexactly(12)
            if h[0] & 128:
                await self.release()
                _, self.phone.width, self.phone.height = struct.unpack(">III", h)
                config, base_pts = b"", None
                switch = pb(4, self.phone.width) + pb(5, self.phone.height)
                await self.send(pb(19, pb(5, switch)))
                LOG.info("display_changed size=%sx%s", self.phone.width, self.phone.height)
                continue
            flags, size = struct.unpack(">QI", h)
            if size > MAX_PACKET:
                raise ValueError("scrcpy frame too large")
            data = await self.phone.video.readexactly(size)
            if flags & (1 << 62):
                config = data
                continue
            key = bool(flags & (1 << 61))
            pts = flags & ((1 << 61) - 1)
            if base_pts is None:
                base_pts = pts
            if key:
                data = config + data
            encoded = pb(1, data) + pb(2, int(key)) + pb(3, (pts - base_pts) // 1000)
            await self.send(pb(6, pb(10, pb(1, encoded))))
            self.frames += 1
            if self.frames == 1 or self.frames % 120 == 0:
                LOG.info("video_sent frames=%s key=%s bytes=%s acks=%s inputs=%s", self.frames, key, size, self.acks, self.events)

    async def control(self, data):
        self.phone.control.write(data)
        await self.phone.control.drain()

    async def heartbeat(self):
        while True:
            await self.send(pb(5, pb(1, int(time.time() * 1000))))
            LOG.info("heartbeat frames=%s inputs=%s acks=%s", self.frames, self.events, self.acks)
            await asyncio.sleep(10)

    async def commit_touch(self):
        if self.down and not self.touch_injected:
            self.touch_injected = True
            x, y = self.press_position
            await self.control(touch(0, x, y, self.phone.width, self.phone.height))

    async def touch_deadlines(self):
        # Mobile RustDesk recognizers send an early DOWN before a tap is resolved.
        # Match its Android host's 100 ms tap + 400 ms long-press decision window.
        while True:
            self.touch_changed.clear()
            if self.down and not self.touch_injected and self.pending_since is not None:
                remaining = max(0, self.pending_since + 0.5 - time.monotonic())
                try:
                    await asyncio.wait_for(self.touch_changed.wait(), remaining)
                except asyncio.TimeoutError:
                    await self.commit_touch()
            else:
                await self.touch_changed.wait()

    async def release(self):
        if self.down and (not self.mobile_touch or self.touch_injected):
            await self.control(touch(3, self.x, self.y, self.phone.width, self.phone.height))
        self.down = self.touch_injected = False
        self.pending_since = None
        self.touch_changed.set()

    async def inputs(self):
        while True:
            msg = parse(await asyncio.wait_for(receive(self.reader), 120))
            if 5 in msg:
                # Reply to client probes only, never echo our own returned probe.
                if parse(msg[5]).get(2):
                    await self.send(pb(5, msg[5]))
            if 19 in msg:
                misc = parse(msg[19])
                if 12 in misc:
                    self.acks += 1
                elif 9 in misc:
                    return
                else:
                    LOG.info("misc_fields=%s", list(misc))
            if 10 in msg:
                e = parse(msg[10]); mask = e.get(1, 0)
                kind, button = mask & 7, mask >> 3
                # RustDesk button messages carry placeholder (0, 0). Only
                # movement updates the cursor; down/up use its last position.
                if kind == 0:
                    x, y = unzigzag(e.get(2, 0)), unzigzag(e.get(3, 0))
                    self.x = min(max(x, 0), self.phone.width - 1)
                    self.y = min(max(y, 0), self.phone.height - 1)
                if button == 1 and kind in (1, 2):
                    if kind == 1 and not self.down:
                        self.press_started = time.monotonic()
                        self.duplicate_downs = 0
                        self.down = True
                        self.press_position = (self.x, self.y)
                        self.pending_since = self.press_started
                        self.touch_changed.set()
                        if not self.mobile_touch:
                            await self.commit_touch()
                    elif kind == 1:
                        # Mobile touch gestures may send DOWN both at contact and
                        # at tap recognition. A held finger cannot go down twice.
                        self.duplicate_downs += 1
                    elif self.down:
                        held_ms = (time.monotonic() - self.press_started) * 1000 if self.press_started is not None else 0
                        started = time.monotonic()
                        if not self.touch_injected:
                            # A resolved stationary tap: do not replay recognizer/network dwell.
                            await self.commit_touch()
                        await self.control(touch(1, self.x, self.y, self.phone.width, self.phone.height))
                        self.down = self.touch_injected = False
                        self.pending_since = None
                        self.touch_changed.set()
                        LOG.info("touch_release held_ms=%.1f duplicate_downs=%s write_ms=%.1f",
                                 held_ms, self.duplicate_downs, (time.monotonic() - started) * 1000)
                        self.press_started = None
                elif kind == 0 and self.down:
                    if not self.touch_injected:
                        dx, dy = self.x - self.press_position[0], self.y - self.press_position[1]
                        if dx * dx + dy * dy > 8 * 8:
                            await self.commit_touch()
                    if self.touch_injected:
                        await self.control(touch(2, self.x, self.y, self.phone.width, self.phone.height))
                elif kind == 2 and button in (2, 8):
                    # Current mobile clients use Back; retain the old right-button alias.
                    await self.control(keycode(4, 0) + keycode(4, 1))
                    LOG.info("navigation action=back")
                elif button == 4 and kind == 1:
                    if self.middle_started is None:
                        self.middle_started = time.monotonic()
                elif button == 4 and kind == 2 and self.middle_started is not None:
                    # RustDesk's Apps button holds middle for 500 ms; its Android
                    # host distinguishes a long middle press at 200 ms.
                    held_ms = (time.monotonic() - self.middle_started) * 1000
                    self.middle_started = None
                    code = 187 if held_ms >= 200 else 3
                    await self.control(keycode(code, 0) + keycode(code, 1))
                    LOG.info("navigation action=%s held_ms=%.1f", "recents" if code == 187 else "home", held_ms)
                elif kind in (3, 4):
                    dx, dy = unzigzag(e.get(2, 0)), unzigzag(e.get(3, 0))
                    await self.control(struct.pack(">BiiHHhhI", 3, self.x, self.y, self.phone.width,
                                       self.phone.height, max(-32768,min(32767,dx*2048)),
                                       max(-32768,min(32767,dy*2048)), 0))
                self.events += 1
                LOG.info("input_mouse count=%s kind=%s button=%s", self.events, kind, button)
            if 15 in msg:
                e = parse(msg[15])
                # Legacy control keys only; physical scancodes are deliberately not guessed.
                mapping = {2:67, 5:112, 6:20, 8:4, 21:3, 22:21, 27:66, 28:22, 30:62, 31:61, 32:19, 72:66}
                control = e.get(3)
                code = mapping.get(control)
                if control is not None and 33 <= control <= 42:
                    code = 7 + control - 33
                if code is not None:
                    if e.get(2):
                        await self.control(keycode(code, 0) + keycode(code, 1))
                    else:
                        await self.control(keycode(code, 0 if e.get(1) else 1))
                elif (e.get(1) or e.get(2)) and (5 in e or 6 in e):
                    text = e.get(6, b"") if 6 in e else chr(e[5]).encode()
                    if len(text) <= 300:
                        await self.control(b"\x01" + struct.pack(">I", len(text)) + text)
                self.events += 1
                LOG.info("input_key count=%s fields=%s", self.events, list(e))

    async def close(self):
        for t in self.tasks:
            t.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        try:
            if self.phone.control:
                await self.release()
        finally:
            await self.phone.close()
            self.writer.close()
        LOG.info("session_closed frames=%s inputs=%s acks=%s", self.frames, self.events, self.acks)


async def main(args):
    password = read_password_file(args.password_file) if args.password_file else secrets.token_urlsafe(18)
    active = set()
    handlers = set()

    async def handle(reader, writer):
        if active:
            writer.close()
            return
        s = Session(reader, writer, args, password)
        active.add(s)
        handlers.add(asyncio.current_task())
        try:
            await s.run()
        except (Exception, asyncio.CancelledError) as e:
            LOG.info("session_end reason=%s", type(e).__name__ + ': ' + str(e))
        finally:
            try:
                await s.close()
            finally:
                active.discard(s)
                handlers.discard(asyncio.current_task())

    server = await asyncio.start_server(handle, "127.0.0.1", args.port)
    LOG.info("listening 127.0.0.1:%s serial=%s transport=loopback-only", args.port, args.serial)
    if args.launch_client:
        subprocess.Popen(["/Applications/RustDesk.app/Contents/MacOS/RustDesk", "--connect",
                          f"127.0.0.1:{args.port}", "--password", password],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        LOG.info("local_client_launched")
    stop = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        asyncio.get_running_loop().add_signal_handler(sig, stop.set)
    await stop.wait()
    server.close()
    await server.wait_closed()
    for s in list(active):
        s.writer.close()
    if handlers:
        await asyncio.gather(*list(handlers), return_exceptions=True)


def read_password_file(path):
    path = Path(path)
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
        raise ValueError("password file must be a regular file readable only by its owner")
    value = path.read_text().strip()
    if len(value) < 24 or len(value) > 256:
        raise ValueError("backend password must contain 24 to 256 characters")
    return value


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serial", required=True)
    parser.add_argument("--server", required=True, help="scrcpy 4.1 server JAR")
    parser.add_argument("--port", type=int, default=21128)
    parser.add_argument("--launch-client", action="store_true")
    parser.add_argument("--password-file", type=Path, help="owner-only backend password file for the native host")
    parser.add_argument("--log-file", type=Path, help="local diagnostic log (no input contents)")
    args = parser.parse_args()
    if not Path(args.server).is_file():
        parser.error("scrcpy server does not exist")
    handlers = [logging.StreamHandler()]
    if args.log_file:
        args.log_file.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(args.log_file))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", handlers=handlers)
    asyncio.run(main(args))
