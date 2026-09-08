#!/usr/bin/env python3
"""Run the local Android worker and isolated native RustDesk host together."""
import argparse
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--serial', required=True)
    parser.add_argument('--server', type=Path, required=True, help='scrcpy 4.1 server JAR')
    parser.add_argument('--config', type=Path, default=ROOT / 'runtime/native-backend.json')
    parser.add_argument('--app', type=Path, default=ROOT / '.local/RustDeskAndroidBridge.app')
    parser.add_argument('--interface', help='macOS physical network interface for this host only')
    args = parser.parse_args()
    config = args.config.resolve()
    settings = json.loads(config.read_text())
    interface = args.interface or settings.get('network_interface')
    if interface:
        socket.if_nametoindex(interface)
    executable = args.app.resolve() / 'Contents/MacOS/RustDesk'
    if not executable.is_file() or not args.server.is_file():
        parser.error('Native app or scrcpy server missing')
    runtime = config.parent
    children = []
    def stop(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, stop)
    try:
        with (runtime / 'native-backend-stdout.log').open('a') as backend_log, (runtime / 'native-host-stdout.log').open('a') as host_log:
            children.append(subprocess.Popen([sys.executable, '-u', str(ROOT / 'src/local_bridge.py'), '--serial', args.serial, '--server', str(args.server.resolve()), '--port', str(settings['port']), '--password-file', settings['password_file'], '--log-file', str(runtime / 'native-backend.log')], stdout=backend_log, stderr=backend_log))
            env = dict(os.environ, RUSTDESK_ANDROID_BACKEND_CONFIG=str(config), RUST_LOG='info')
            if interface:
                env['RUSTDESK_ANDROID_NETWORK_INTERFACE'] = interface
            children.append(subprocess.Popen([str(executable)], env=env, stdout=host_log, stderr=host_log))
            print('Native host and Android worker started; Ctrl+C stops both. Logs are private runtime files.', flush=True)
            while all(p.poll() is None for p in children):
                time.sleep(0.5)
            raise SystemExit('A component exited; see runtime logs.')
    except KeyboardInterrupt:
        pass
    finally:
        for proc in reversed(children):
            if proc.poll() is None:
                proc.terminate()
        for proc in children:
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()


if __name__ == '__main__':
    main()
