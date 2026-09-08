#!/usr/bin/env python3
"""Initialize private native-host settings from this Mac's RustDesk configuration."""
import argparse
import json
import os
from pathlib import Path
import secrets
import tomllib

ROOT = Path(__file__).resolve().parents[1]


def write_private(path, text):
    with open(path, 'x', opener=lambda p, flags: os.open(p, flags, 0o600)) as f:
        f.write(text)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=Path.home() / 'Library/Preferences/com.carriez.RustDesk/RustDesk2.toml')
    parser.add_argument('--runtime', type=Path, default=ROOT / 'runtime')
    parser.add_argument('--port', type=int, default=21132)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error('port must be between 1024 and 65535')
    dest = args.runtime.resolve()
    dest.mkdir(parents=True, exist_ok=True, mode=0o700)
    config = dest / 'native-backend.json'
    if config.exists():
        raise SystemExit('Configuration exists; it was not overwritten.')
    source = tomllib.loads(args.source.read_text()).get('options', {})
    options = {k: source[k] for k in ('custom-rendezvous-server', 'relay-server', 'api-server', 'key') if source.get(k)}
    if not options.get('custom-rendezvous-server') or not options.get('key'):
        raise SystemExit('A self-hosted ID server and public key are required in the source configuration.')
    passwords = [dest / 'native-backend-password', dest / 'native-host-password']
    if any(p.exists() for p in passwords):
        raise SystemExit('Password files already exist; no credentials were overwritten.')
    for path in passwords:
        write_private(path, secrets.token_urlsafe(32) + '\n')
    write_private(config, json.dumps(dict(port=args.port, password_file=str(passwords[0]), frontend_password_file=str(passwords[1]), frontend_options=options), indent=2) + '\n')
    print('Private configuration prepared. Network options are imported only on the first native-host launch.')


if __name__ == '__main__':
    main()
