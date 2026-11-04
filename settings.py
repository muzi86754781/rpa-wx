"""Provider profiles; keys stay in memory and are never written to preferences."""
from dataclasses import dataclass, field, replace
import json
import os
from pathlib import Path
import sys
import tempfile
from urllib.parse import urlsplit


PRESETS = {
    'openai': dict(label='OpenAI', base_url='https://api.openai.com/v1', protocol='responses',
                   vision_model='gpt-6.1-terra', reply_model='gpt-6.1-terra', json_mode='schema',
                   console='https://platform.openai.com/api-keys', env='OPENAI_API_KEY'),
    'aliyun-beijing': dict(label='阿里云百炼（北京）',
                   base_url='https://dashscope.aliyuncs.com/compatible-mode/v1', protocol='chat',
                   vision_model='qwen3-vl-flash', reply_model='qwen-plus', json_mode='object',
                   console='https://bailian.console.aliyun.com/', env='DASHSCOPE_API_KEY'),
    'aliyun-singapore': dict(label='阿里云百炼（新加坡）',
                   base_url='https://dashscope-intl.aliyuncs.com/compatible-mode/v1', protocol='chat',
                   vision_model='qwen3-vl-flash', reply_model='qwen-plus', json_mode='object',
                   console='https://modelstudio.console.alibabacloud.com/', env=''),
    'aliyun-us': dict(label='阿里云百炼（美国）',
                   base_url='https://dashscope-us.aliyuncs.com/compatible-mode/v1', protocol='chat',
                   vision_model='qwen3-vl-flash', reply_model='qwen-plus', json_mode='object',
                   console='https://modelstudio.console.alibabacloud.com/', env=''),
    'custom': dict(label='自定义 OpenAI 兼容服务', base_url='', protocol='chat',
                   vision_model='', reply_model='', json_mode='object', console='', env=''),
}
PUBLIC_FIELDS = ('provider', 'base_url', 'protocol', 'vision_model', 'reply_model', 'json_mode')
QUOTA_HELP = 'https://help.aliyun.com/zh/model-studio/new-free-quota'


def normalize_url(value):
    value = value.strip().rstrip('/')
    try:
        url = urlsplit(value)
        url.port  # Validate a malformed port before sending any request.
    except ValueError:
        raise ValueError('Base URL 格式无效。') from None
    local = url.hostname in ('localhost', '127.0.0.1', '::1')
    if (not url.hostname or not (url.scheme == 'https' or (local and url.scheme == 'http'))
            or url.username is not None or url.password is not None
            or url.query or url.fragment or any(char.isspace() for char in value)
            or '{' in value or '}' in value):
        raise ValueError('Base URL 需为完整 HTTPS 地址（本机可用 HTTP），不能包含密钥、查询参数或占位符。')
    if url.path.endswith(('/responses', '/chat/completions')):
        raise ValueError('Base URL 请填写到 /v1 等接口根路径，不要包含 /responses 或 /chat/completions。')
    return value


@dataclass
class Profile:
    provider: str
    base_url: str
    protocol: str
    vision_model: str
    reply_model: str
    json_mode: str
    key: str = field(default='', repr=False)

    @property
    def label(self):
        return PRESETS[self.provider]['label']

    def validated(self, require_key=True):
        if self.provider not in PRESETS or self.protocol not in ('responses', 'chat'):
            raise ValueError('请选择支持的模型服务商和接口协议。')
        if self.json_mode not in ('schema', 'object', 'prompt'):
            raise ValueError('请选择有效的 JSON 输出模式。')
        vision, reply = self.vision_model.strip(), self.reply_model.strip()
        if not vision or not reply or any(char in vision + reply for char in '\r\n'):
            raise ValueError('请填写截图识别模型和回复模型的 ID。')
        if self.provider.startswith('aliyun-'):
            from model_catalog import BUILTIN_BY_ID
            for model, role, label in ((vision, 'vision', '截图识别'), (reply, 'reply', '文字回复')):
                info = BUILTIN_BY_ID.get(model)
                if info is not None and role not in info.roles:
                    raise ValueError(f'{model} 属于{info.category}，不能用于当前{label}流程，请从模型目录选择适用模型。')
        key = self.key.strip()
        if require_key and not key:
            raise ValueError('请在右上角设置中填写当前服务商的 API Key。')
        if '\r' in key or '\n' in key:
            raise ValueError('API Key 不能包含换行，请重新复制完整密钥。')
        return replace(self, base_url=normalize_url(self.base_url), key=key,
                       vision_model=vision, reply_model=reply)

    def public_dict(self):
        return {name: getattr(self, name) for name in PUBLIC_FIELDS}


def default_profile(provider):
    preset = PRESETS[provider]
    return Profile(provider, **{name: preset[name] for name in PUBLIC_FIELDS if name != 'provider'},
                   key=os.environ.get(preset['env'], '') if preset['env'] else '')


def settings_path():
    if sys.platform == 'darwin':
        root = Path.home() / 'Library' / 'Application Support'
    elif sys.platform == 'win32':
        root = Path(os.environ.get('APPDATA', str(Path.home() / 'AppData' / 'Roaming')))
    else:
        root = Path.home() / '.config'
    return root / 'RpaWx' / 'settings.json'


class SettingsStore:
    def __init__(self, path=None):
        self.path = Path(path) if path is not None else settings_path()
        self.profiles = {name: default_profile(name) for name in PRESETS}
        self.active = 'openai'
        self.notice = ''
        self.load()

    @property
    def current(self):
        return self.profiles[self.active]

    def load(self):
        try:
            data = json.loads(self.path.read_text(encoding='utf-8'))
            if not isinstance(data, dict) or not isinstance(data.get('profiles'), dict):
                raise ValueError('invalid preferences')
            for provider, values in data['profiles'].items():
                if provider not in PRESETS or not isinstance(values, dict):
                    continue
                baseline = self.profiles[provider]
                public = {name: values[name] for name in PUBLIC_FIELDS if name != 'provider' and name in values}
                if not all(isinstance(value, str) for value in public.values()):
                    continue
                try:
                    self.profiles[provider] = replace(baseline, **public).validated(require_key=False)
                except ValueError:
                    continue
            active = data.get('active')
            if isinstance(active, str) and active in PRESETS:
                self.active = active
        except FileNotFoundError:
            pass
        except (OSError, ValueError):
            self.notice = '原设置文件无法读取，已使用默认配置；请在设置中重新确认。'

    def save(self, profile):
        profile = profile.validated(require_key=False)
        profiles = dict(self.profiles, **{profile.provider: profile})
        payload = dict(active=profile.provider,
                       profiles={name: value.public_dict() for name, value in profiles.items()})
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=self.path.parent,
                                              delete=False, prefix='.settings-') as stream:
                temporary = Path(stream.name)
                json.dump(payload, stream, ensure_ascii=False, indent=2)
            temporary.replace(self.path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        self.profiles, self.active = profiles, profile.provider
