#!/usr/bin/env python3
"""Prepare a pinned upstream checkout with the reviewed Android backend patch."""
import argparse
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
REV = '0c86d4616298f09435f6236599b300964aa61460'

def run(*args, cwd=None):
    return subprocess.run(args, cwd=cwd, check=True)

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--checkout', type=Path, default=ROOT/'.local/rustdesk-host')
    args = parser.parse_args()
    dest = args.checkout.resolve()
    if not dest.exists():
        run('git','clone','--depth','1','--branch','1.4.7',
            'https://github.com/rustdesk/rustdesk.git',str(dest))
    actual = subprocess.check_output(['git','rev-parse','HEAD'],cwd=dest,text=True).strip()
    if actual != REV:
        raise SystemExit('Upstream revision differs; use a fresh checkout.')
    run('git','submodule','update','--init','--depth','1','libs/hbb_common',cwd=dest)
    for patch, target in [(ROOT/'native/rustdesk-1.4.7.patch', dest),
                          (ROOT/'native/hbb-common-interface.patch', dest/'libs/hbb_common')]:
        check = subprocess.run(['git','apply','--check',str(patch)],cwd=target,capture_output=True)
        if check.returncode == 0:
            run('git','apply',str(patch),cwd=target)
        else:
            reverse = subprocess.run(['git','apply','--reverse','--check',str(patch)],cwd=target,capture_output=True)
            if reverse.returncode:
                raise SystemExit('Checkout has conflicting changes; no reset was performed.')
    shutil.copyfile(ROOT/'native/overlay/network_interface.rs',dest/'libs/hbb_common/src/network_interface.rs')
    shutil.copyfile(ROOT/'native/overlay/android_backend.rs',dest/'src/server/android_backend.rs')
    print('Prepared RustDesk 1.4.7 Android backend source.')

if __name__ == '__main__':
    main()
