"""Build native packages on the host OS. Never embeds credentials."""
import argparse
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def run(*args):
    subprocess.run([str(arg) for arg in args], cwd=ROOT, check=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--version', default='0.1.0')
    parser.add_argument('--arch', choices=['x86_64', 'arm64'], default='x86_64')
    args = parser.parse_args()
    if not all(c.isdigit() or c == '.' for c in args.version):
        parser.error('version must contain digits and dots only')
    release = ROOT / 'release'
    release.mkdir(exist_ok=True)
    command = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--windowed',
               '--onedir', '--name', 'RpaWx', '--noupx', '--collect-data', 'certifi']
    if sys.platform == 'darwin':
        command += ['--osx-bundle-identifier', 'io.github.muzi86754781.rpawx',
                    '--target-arch', args.arch, '--codesign-identity', '-']
    elif sys.platform != 'win32':
        parser.error('Only Windows and macOS packages are supported')
    run(*command, 'app.py')
    if sys.platform == 'win32':
        compiler = shutil.which('ISCC.exe') or r'C:\Program Files (x86)\Inno Setup 6\ISCC.exe'
        if not Path(compiler).exists():
            raise RuntimeError('Inno Setup 6 compiler not found')
        run(compiler, f'/DAppVersion={args.version}', ROOT / 'packaging' / 'windows.iss')
    else:
        bundle = ROOT / 'dist' / 'RpaWx.app'
        info_path = bundle / 'Contents' / 'Info.plist'
        with info_path.open('rb') as stream:
            info = plistlib.load(stream)
        info.update(CFBundleName='WeChat Agent', CFBundleDisplayName='WeChat Agent',
                    CFBundleShortVersionString=args.version, CFBundleVersion=args.version,
                    LSMinimumSystemVersion='13.0', NSHighResolutionCapable=True,
                    NSHumanReadableCopyright='WeChat Agent contributors')
        with info_path.open('wb') as stream:
            plistlib.dump(info, stream)
        # Plist modification requires re-signing; this is ad-hoc, not Developer ID.
        run('codesign', '--force', '--deep', '--sign', '-', bundle)
        run('codesign', '--verify', '--deep', '--strict', bundle)
        run(sys.executable, 'scripts/verify_macos.py', bundle, '--arch', args.arch)
        staging = ROOT / 'build' / f'dmg-{args.arch}'
        staging.mkdir(parents=True, exist_ok=True)
        shutil.copytree(bundle, staging / 'RpaWx.app', dirs_exist_ok=True, symlinks=True)
        shutil.copy2(ROOT / 'packaging' / 'MAC-INSTALL.txt', staging / '安装说明.txt')
        link = staging / 'Applications'
        if not link.is_symlink():
            link.symlink_to('/Applications', target_is_directory=True)
        output = release / f'RpaWx-v{args.version}-macos-13-{args.arch}.dmg'
        run('hdiutil', 'create', '-volname', 'WeChat Agent', '-srcfolder', staging,
            '-ov', '-format', 'UDZO', output)
        run('hdiutil', 'verify', output)


if __name__ == '__main__':
    main()
