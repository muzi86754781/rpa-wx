"""Offline Alibaba model directory and paginated, same-endpoint synchronization."""
from dataclasses import asdict, dataclass, replace
import hashlib
import json
from pathlib import Path
import re
import tempfile
from urllib.parse import urlencode, urlsplit, urlunsplit
import urllib.request
import urllib.error

from api_errors import api_error
from catalog_data import CATALOG_DATE, CATALOG_GROUPS
from settings import normalize_url

MODELS_DOC = 'https://help.aliyun.com/zh/model-studio/models'
LIST_DOC = 'https://help.aliyun.com/zh/model-studio/list-models'
ROLE_LABELS = {'vision': '截图识别', 'reply': '文字回复'}
ID_PATTERN = re.compile(r'[A-Za-z0-9][A-Za-z0-9._/-]{0,199}\Z')


@dataclass(frozen=True)
class ModelInfo:
    id: str
    category: str
    roles: tuple
    source: str
    json_mode: str = 'prompt'
    origin: str = '内置文档'
    note: str = ''


def describe(model_id, source=MODELS_DOC):
    """Conservative task compatibility; catalog-only models remain searchable."""
    name = model_id.lower()
    category, roles, mode, note = '其他', (), 'prompt', ''
    if any(word in name for word in ('embedding', 'rerank')):
        category = '向量与重排'
    elif name.startswith(('tripo/', 'tripo-')):
        category = '3D生成'
    elif name.startswith('happyoyster'):
        category = '世界模型'
    elif name.startswith('decision-'):
        category = '决策模型'
    elif name.startswith('happyhorse') or (name.startswith(('wan', 'wanx')) and not any(word in name for word in ('t2i', 'i2i', 'image', 'background', 'poster', 'sketch', 'style-repaint', 'virtualmodel', 'x-painting'))):
        category = '视频生成'
    elif name.startswith(('wan', 'qwen-image', 'flux', 'stable-diffusion', 'z-image')):
        category = '图像生成'
    elif 'asr' in name or name.startswith(('paraformer', 'sensevoice')):
        category = '语音识别'
    elif 'tts' in name or name.startswith(('cosyvoice', 'sambert')) or '/speech-' in name or 'voice-' in name:
        category = '语音合成'
    elif 'omni' in name or 'audio' in name or 'livetranslate' in name or name.startswith('gummy'):
        category = '全模态与语音'
        note = '音视频或专用接口模型；本程序当前仅收录目录。'
    elif name.startswith(('qwen-mt-', 'qwen-doc', 'qwen-deep-research', 'qwen-vl-ocr', 'qwen-ocr', 'qwen-gui')) or '-ocr' in name:
        category = '领域模型'
        note = '翻译、抽取、研究或交互专用模型，需要对应任务接口。'
    elif name.startswith(('qvq', 'qwq')):
        category = '深度推理'
        note = '该系列涉及流式输出，当前仅收录目录。'
    elif name.startswith(('qwen-vl-', 'qwen2-vl-', 'qwen2.5-vl-', 'qwen3-vl-')) or re.match(r'qwen3\.[5-9]-', name):
        category, roles = '视觉与文本', ('vision', 'reply')
        if re.match(r'qwen3\.[5-9]-', name) or name.startswith(('qwen3-vl-flash', 'qwen3-vl-plus')) or name in ('qwen-vl-max', 'qwen-vl-plus'):
            mode = 'object'
        if 'thinking' in name:
            mode = 'prompt'
            note = '固定思考模型；不传关闭思考参数，识别使用提示词 JSON。'
    elif name.startswith(('kimi-k3', 'kimi/kimi-k3', 'kimi-k2.5', 'kimi/kimi-k2.5', 'kimi-k2.6', 'kimi/kimi-k2.6')):
        category, roles, mode = '视觉与文本', ('vision', 'reply'), 'object'
    elif name.startswith(('qwen', 'deepseek', 'glm', 'zhipu/glm', 'kimi', 'minimax', 'mimo', 'step')):
        category, roles = '文本生成', ('reply',)
    if not roles and not note:
        note = '此类别不适用于当前截图识别或文字回复流程。'
    return ModelInfo(model_id, category, roles, source, mode, note=note)


def builtin_catalog():
    by_id = {}
    for slug, ids in CATALOG_GROUPS:
        for model_id in ids:
            by_id.setdefault(model_id, describe(model_id, 'https://help.aliyun.com/zh/model-studio/' + slug))
    return tuple(sorted(by_id.values(), key=lambda row: row.id.casefold()))


BUILTIN = builtin_catalog()
BUILTIN_BY_ID = {row.id: row for row in BUILTIN}


def catalog_ids(rows, role):
    return tuple(row.id for row in rows if role in row.roles)


def filter_catalog(rows, query='', category='全部', role=None):
    words = query.casefold().split()
    return tuple(row for row in rows if (category == '全部' or row.category == category)
                 and (role is None or role in row.roles)
                 and all(word in (row.id + ' ' + row.category).casefold() for word in words))


def optional_thinking(model):
    name = model.lower()
    if any(word in name for word in ('thinking', 'instruct', 'character')):
        return False
    return (bool(re.match(r'qwen3\.[5-9]-', name))
            or name.startswith(('qwen3-vl-flash', 'qwen3-vl-plus'))
            or name == 'qwen-plus' or name.startswith('qwen-plus-20')
            or name == 'qwen-flash' or name.startswith('qwen-flash-20'))


def catalog_endpoint(base_url):
    parsed = urlsplit(normalize_url(base_url))
    suffix = '/compatible-mode/v1'
    if not parsed.path.endswith(suffix):
        raise ValueError('同步百炼目录需使用 /compatible-mode/v1 的接入地址；请在设置中填写官方 Base URL。')
    path = parsed.path[:-len(suffix)] + '/api/v1/models'
    return urlunsplit((parsed.scheme, parsed.netloc, path, '', ''))


def parse_model(item):
    model_id = item.get('model')
    if not isinstance(model_id, str) or not ID_PATTERN.fullmatch(model_id):
        raise ValueError('模型目录返回了无效模型 ID，未替换原目录。')
    row = describe(model_id, LIST_DOC)
    modalities = item.get('inference_metadata') or {}
    if not isinstance(modalities, dict):
        raise ValueError('模型目录模态数据无效。')
    inputs = modalities.get('request_modality') or []
    outputs = modalities.get('response_modality') or []
    capabilities = item.get('capabilities') or []
    features = item.get('features') or []
    if any(not isinstance(values, list) or not all(isinstance(value, str) for value in values)
           for values in (inputs, outputs, capabilities, features)):
        raise ValueError('模型目录能力数据无效。')
    if inputs or outputs or capabilities:
        # Preserve known exclusions for task-specific / realtime APIs.
        general = bool(row.roles) or (row.category == '其他')
        roles = []
        if general and ('Text' in outputs or 'TG' in capabilities or 'VU' in capabilities):
            if 'Image' in inputs or 'VU' in capabilities:
                roles.append('vision')
            if 'Text' in inputs or 'TG' in capabilities or 'VU' in capabilities:
                roles.append('reply')
        row = replace(row, roles=tuple(roles))
    mode = 'object' if 'structured-outputs' in features and 'thinking' not in model_id.lower() else row.json_mode
    return replace(row, json_mode=mode, origin='官方接口')


def fetch_catalog(base_url, key, cancel, transport=None):
    if not key.strip() or '\r' in key or '\n' in key:
        raise ValueError('请先在设置中填写百炼 API Key，再同步目录。')
    if transport is None:
        from core import open_request
        transport = open_request
    endpoint = catalog_endpoint(base_url)
    collected, seen_pages, received, expected_total = {}, set(), 0, None
    for page in range(1, 201):
        if cancel.is_set():
            raise RuntimeError('已取消同步，保留原模型目录。')
        query = urlencode(dict(page_no=page, page_size=100, language='zh-CN'))
        request = urllib.request.Request(endpoint + '?' + query,
                     headers={'Authorization': 'Bearer ' + key.strip(), 'Accept': 'application/json'})
        try:
            with transport(request, timeout=30) as response:
                body = json.load(response)
        except urllib.error.HTTPError as error:
            raise api_error(error, provider='阿里云百炼目录') from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise RuntimeError('百炼模型目录连接失败；可继续使用内置目录，或填写业务空间专属 Base URL 后重试。') from None
        except ValueError:
            raise ValueError('百炼目录响应不是有效 JSON，保留原目录。') from None
        if cancel.is_set():
            raise RuntimeError('已取消同步，保留原模型目录。')
        output = body.get('output') if isinstance(body, dict) else None
        if not isinstance(output, dict) or body.get('success') is False:
            raise ValueError('百炼未返回模型目录；请核对地域和业务空间专属接入地址。')
        total, models = output.get('total'), output.get('models')
        if (type(total) is not int or total < 0 or total > 20000 or not isinstance(models, list)
                or not all(isinstance(item, dict) for item in models)):
            raise ValueError('百炼目录分页信息无效，保留原目录。')
        if expected_total is not None and total != expected_total:
            raise ValueError('同步期间官方目录发生变化，请重试；保留原目录。')
        expected_total = total
        if received + len(models) > total:
            raise ValueError('百炼目录分页数量不一致，保留原目录。')
        if not models and received < total:
            raise ValueError('百炼目录分页不完整，保留原目录。')
        digest = hashlib.sha256(json.dumps(models, sort_keys=True).encode()).digest()
        if digest in seen_pages and models:
            raise ValueError('百炼返回了重复分页，未替换原目录。')
        seen_pages.add(digest)
        for item in models:
            row = parse_model(item)
            if row.id == key.strip() or (len(key.strip()) >= 8 and key.strip() in row.id):
                raise ValueError('百炼目录包含无效模型标识，未缓存。')
            previous = collected.get(row.id)
            if previous:
                row = replace(row, roles=tuple(sorted(set(row.roles + previous.roles))))
            collected[row.id] = row
        received += len(models)
        if received >= total:
            return tuple(sorted(collected.values(), key=lambda row: row.id.casefold()))
    raise ValueError('目录超过分页上限，未替换原目录，请稍后重试。')


def cache_path(settings_path, base_url):
    identity = hashlib.sha256(catalog_endpoint(base_url).encode()).hexdigest()[:20]
    return Path(settings_path).with_name('aliyun-models-' + identity + '.json')


def save_cache(path, rows):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                          delete=False, prefix='.catalog-') as stream:
            temporary = Path(stream.name)
            json.dump({'models': [asdict(row) for row in rows]}, stream, ensure_ascii=False)
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def load_catalog(path):
    try:
        data = json.loads(Path(path).read_text(encoding='utf-8'))
        models = data['models']
        if not isinstance(models, list) or len(models) > 20000:
            raise ValueError('invalid cache')
        rows = []
        for item in models:
            row = ModelInfo(**item)
            if (not isinstance(row.id, str) or not ID_PATTERN.fullmatch(row.id)
                    or not isinstance(row.roles, list) or any(role not in ROLE_LABELS for role in row.roles)
                    or row.json_mode not in ('schema', 'object', 'prompt')
                    or not all(isinstance(value, str) for value in (row.category, row.source, row.origin, row.note))):
                raise ValueError('invalid cache')
            rows.append(replace(row, roles=tuple(row.roles)))
        return tuple(rows)
    except (OSError, ValueError, KeyError, TypeError):
        return BUILTIN
