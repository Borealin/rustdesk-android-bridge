"""Credential/configuration isolation at the native host bootstrap boundary."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class NativeConfigTests(unittest.TestCase):
    def test_import_excludes_desktop_identity_and_preserves_existing_secrets(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / 'source.toml'
            source.write_text('id="desktop-id"\npassword="desktop-secret"\n[options]\ncustom-rendezvous-server="id.example.invalid"\nrelay-server="relay.example.invalid"\napi-server="https://api.example.invalid"\nkey="test-public-key"\naccess-token="must-not-copy"\n')
            runtime = root / 'runtime'
            command = [sys.executable, str(ROOT / 'scripts/configure_native.py'), '--source', str(source), '--runtime', str(runtime)]
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            config = json.loads((runtime / 'native-backend.json').read_text())
            self.assertEqual(set(config['frontend_options']), {'custom-rendezvous-server', 'relay-server', 'api-server', 'key'})
            passwords = [Path(config[k]) for k in ('password_file', 'frontend_password_file')]
            before = [p.read_bytes() for p in passwords]
            self.assertNotEqual(*before)
            for p in passwords + [runtime / 'native-backend.json']:
                self.assertEqual(p.stat().st_mode & 0o077, 0)
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(before, [p.read_bytes() for p in passwords])
            for secret in before:
                self.assertNotIn(secret.decode().strip(), result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
