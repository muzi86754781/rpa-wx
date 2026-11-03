"""Smoke-test the packaged executable, even when the Windows binary has no console."""
import argparse
import json
from pathlib import Path
import subprocess
import tempfile


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('executable')
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as folder:
        output = Path(folder) / 'result.json'
        subprocess.run([str(Path(args.executable).resolve()), '--smoke-test', '--smoke-output', str(output)],
                       check=True, timeout=60)
        result = json.loads(output.read_text(encoding='utf-8'))
        assert result.get('ok') is True, result
        assert result['ui'][0] >= 500, result
        print(result)
