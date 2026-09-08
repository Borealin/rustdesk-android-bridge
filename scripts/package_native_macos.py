#!/usr/bin/env python3
"""Create an isolated local test bundle from installed RustDesk 1.4.7 and the patched Rust library."""
import argparse
from pathlib import Path
import plistlib
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-app',type=Path,default=Path('/Applications/RustDesk.app'))
    parser.add_argument('--library',type=Path,default=ROOT/'.local/rustdesk-host/target/debug/liblibrustdesk.dylib')
    parser.add_argument('--output',type=Path,default=ROOT/'.local/RustDeskAndroidBridge.app')
    args=parser.parse_args()
    with (args.source_app/'Contents/Info.plist').open('rb') as f: info=plistlib.load(f)
    if info.get('CFBundleShortVersionString')!='1.4.7':
        raise SystemExit('This patch and generated FFI are pinned to RustDesk 1.4.7.')
    if not args.library.is_file(): raise SystemExit('Build the patched Rust library first.')
    if args.output.exists(): raise SystemExit('Output exists; choose a new --output to preserve it.')
    subprocess.run(['ditto',str(args.source_app),str(args.output)],check=True)
    shutil.copyfile(args.library,args.output/'Contents/Frameworks/liblibrustdesk.dylib')
    info['CFBundleIdentifier']='io.github.rustdesk-android-bridge.host'
    info['CFBundleName']='RustDeskAndroidBridge'
    info['CFBundleDisplayName']='RustDesk Android Bridge'
    info.pop('CFBundleURLTypes',None)
    with (args.output/'Contents/Info.plist').open('wb') as f: plistlib.dump(info,f)
    subprocess.run(['codesign','--force','--deep','--sign','-',str(args.output)],check=True)
    subprocess.run(['codesign','--verify','--deep',str(args.output)],check=True)
    print('Isolated local test bundle prepared. The installed app was not modified.')

if __name__=='__main__': main()
