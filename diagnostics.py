"""Bounded local diagnostics. No request bodies, screenshots or chat text."""
import json
import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path
import platform
import re
import threading
import time
import traceback
import zipfile

VERSION = '0.6.2'
_lock = threading.RLock()
_handler = None
_logger = logging.Logger('rpa-wx-diagnostics')
_secrets = set()
FIELDS = {'request_id', 'provider', 'model', 'task', 'duration_ms', 'status', 'code',
          'exception_type', 'frames', 'conversation', 'reason', 'details', 'image_size',
          'crop', 'unit', 'list_box', 'valid_rows', 'invalid_rows', 'cache_hit',
          'stage', 'characters', 'sent', 'skipped', 'opened', 'checked', 'scroll_sign',
          'lines', 'moved', 'version', 'platform'}


class PrivateHandler(RotatingFileHandler):
    def _open(self):
        stream = super()._open()
        os.chmod(self.baseFilename, 0o600)
        return stream

    def handleError(self, record):
        pass


def add_secret(value):
    if isinstance(value, str) and value:
        with _lock:
            _secrets.add(value)


def clean(value):
    if isinstance(value, str):
        for secret in sorted(_secrets, key=len, reverse=True):
            value = value.replace(secret, '[REDACTED]')
        value = re.sub(r'(?i)bearer\s+[^\s"\']+', 'Bearer [REDACTED]', value)
        value = re.sub(r'\bsk-[A-Za-z0-9_-]{8,}', '[REDACTED]', value)
        return value[:1500]
    if isinstance(value, dict):
        return {str(k): clean(v) for k, v in value.items()
                if str(k).lower() not in {'key', 'api_key', 'authorization', 'message',
                                          'input_text', 'context', 'payload', 'response', 'image'}}
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value[:40]]
    if type(value) in (int, float, bool) or value is None:
        if isinstance(value, float) and not (-float('inf') < value < float('inf')):
            return 'non_finite'
        return value
    return type(value).__name__


def setup(folder):
    global _handler
    with _lock:
        close()
        folder = Path(folder)
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        _handler = PrivateHandler(folder / 'diagnostics.jsonl', maxBytes=2 * 1024 * 1024,
                                  backupCount=3, encoding='utf-8')
        _handler.setFormatter(logging.Formatter('%(message)s'))
        _logger.addHandler(_handler)
        _logger.setLevel(logging.INFO)
        record('app_start', version=VERSION, platform=platform.system() + ' ' + platform.release())


def close():
    global _handler
    with _lock:
        if _handler is not None:
            _logger.removeHandler(_handler)
            _handler.close()
            _handler = None


def record(event, **fields):
    with _lock:
        if _handler is None:
            return
        entry = dict(time=time.strftime('%Y-%m-%dT%H:%M:%S%z'), event=event,
                     **{k: clean(v) for k, v in fields.items() if k in FIELDS})
        try:
            _logger.info(json.dumps(entry, ensure_ascii=False, allow_nan=False))
        except (OSError, ValueError):
            pass  # Diagnostics must never interrupt a send or disclose to stderr.


def folder():
    return Path(_handler.baseFilename).parent if _handler is not None else None


def tail():
    with _lock:
        if _handler is None:
            return '日志未能初始化，请检查本机目录权限。'
        _handler.flush()
        try:
            return Path(_handler.baseFilename).read_text(encoding='utf-8')[-120000:]
        except OSError:
            return '无法读取日志。'


def export_bundle(destination):
    with _lock:
        location = folder()
        if location is None:
            raise OSError('日志未能初始化。')
        if Path(destination).resolve().parent == location.resolve() and Path(destination).name.startswith('diagnostics.jsonl'):
            raise OSError('不能覆盖正在记录的日志。')
        _handler.flush()
        # Re-sanitize on export, including secrets registered after startup.
        with zipfile.ZipFile(destination, 'w', zipfile.ZIP_DEFLATED) as bundle:
            for path in sorted(location.glob('diagnostics.jsonl*')):
                if path.is_file():
                    bundle.writestr(path.name, scrub_file(path))
            bundle.writestr('system.json', json.dumps(dict(version=VERSION, platform=platform.platform()), ensure_ascii=False))


def scrub_file(path):
    rows = []
    for line in path.read_text(encoding='utf-8').splitlines():
        try:
            rows.append(json.dumps(clean(json.loads(line)), ensure_ascii=False))
        except ValueError:
            rows.append(json.dumps(dict(event='incomplete_log_line_omitted')))
    return '\n'.join(rows) + '\n'


def record_exception(stage, error):
    record('program_error', stage=stage, exception_type=type(error).__name__,
           reason=str(error) if isinstance(error, (RuntimeError, ValueError)) else '非业务异常，见错误类型与位置',
           frames=[dict(file=Path(frame.filename).name, line=frame.lineno, function=frame.name)
                   for frame in traceback.extract_tb(error.__traceback__)])
