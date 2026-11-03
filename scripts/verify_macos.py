"""Fail CI if any bundled Mach-O slice requires a system newer than macOS 13."""
import argparse
from pathlib import Path
import plistlib
from macholib.MachO import MachO
from macholib.mach_o import LC_BUILD_VERSION, LC_VERSION_MIN_MACOSX

MAGICS = {b'\xfe\xed\xfa\xce', b'\xce\xfa\xed\xfe', b'\xfe\xed\xfa\xcf',
          b'\xcf\xfa\xed\xfe', b'\xca\xfe\xba\xbe', b'\xbe\xba\xfe\xca'}
CPUS = {'x86_64': 0x01000007, 'arm64': 0x0100000C}


def verify(bundle, arch):
    info = plistlib.loads((bundle / 'Contents' / 'Info.plist').read_bytes())
    if info.get('LSMinimumSystemVersion') != '13.0':
        raise RuntimeError('App must declare macOS 13.0 as its minimum')
    count = 0
    visited = set()
    errors = []
    for path in bundle.rglob('*'):
        if not path.is_file() or path.resolve() in visited:
            continue
        visited.add(path.resolve())
        with path.open('rb') as stream:
            if stream.read(4) not in MAGICS:
                continue
        count += 1
        headers = MachO(str(path)).headers
        matching = [h for h in headers if h.header.cputype == CPUS[arch]]
        if not matching:
            errors.append(f'{path}: missing {arch} slice')
        for header in matching:
            minimums = [cmd.minos if lc.cmd == LC_BUILD_VERSION else cmd.version
                        for lc, cmd, _ in header.commands
                        if lc.cmd in (LC_BUILD_VERSION, LC_VERSION_MIN_MACOSX)]
            if not minimums:
                errors.append(f'{path}: missing deployment target')
            for minimum in minimums:
                if minimum > (13 << 16):
                    errors.append(f'{path}: requires {minimum >> 16}.{(minimum >> 8) & 255}.{minimum & 255}')
    if not count:
        errors.append('No Mach-O binaries found')
    if errors:
        raise RuntimeError('\n'.join(errors))
    print(f'Validated {count} Mach-O files: {arch}, minimum OS <= 13.0')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('bundle', type=Path)
    parser.add_argument('--arch', required=True, choices=CPUS)
    args = parser.parse_args()
    verify(args.bundle, args.arch)
