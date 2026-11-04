"""Download public Alibaba documentation for a reviewable offline model snapshot."""
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import ssl
import urllib.request

import certifi

SOURCES = {
    'models': '总览', 'text-generation-model': '文本生成', 'vision': '视觉理解',
    'deepseek': '文本生成', 'glm': '文本生成', 'kimi-api': '文本生成',
    'minimax': '文本生成', 'mimo': '文本生成', 'qwen-coder': '编程',
    'qwen-structured-output': '结构化输出', 'image-model': '图像生成',
    'video-model': '视频生成', 'asr-model': '语音识别', 'tts-model': '语音合成',
    'embedding-rerank-model': '向量与重排', 'omni': '全模态',
    'tripo-3d-generation-guide': '3D生成', 'happyoyster-overview': '世界模型',
    'decision-model-preview': '决策模型', 'new-free-quota': '历史与快照',
    'model-pricing': '模型定价目录', 'deepseek-api': '文本生成',
    'minimax-m3-1-flash': '文本生成', 'video-generate-edit-model': '视频生成',
    'audio-generation': '音频生成', 's2s-model': '语音对话',
    'machine-translation': '翻译', 'data-mining-qwen-doc': '数据挖掘',
    'role-play': '角色扮演', 'gui-automation': '界面交互', 'qwen-vl-ocr': '文字提取',
}
ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / '.local' / 'catalog-sources'
PATTERN = re.compile(r'(?<![\w/.-])(?:kimi/|ZHIPU/|Tripo/)?(?:qwen|qwq|qvq|deepseek|kimi|glm|MiniMax|mimo|step|wanx?|happyhorse|happyoyster|decision|text-embedding|multimodal-embedding|gte|bge|paraformer|fun-asr|sensevoice|cosyvoice|sambert|gummy|flux|z-image|stable-diffusion|Tripo)[A-Za-z0-9._/-]*', re.I)


class Article(HTMLParser):
    def __init__(self):
        super().__init__()
        self.skip = 0
        self.parts = []
        self.links = []
        self.column = 0
        self.in_cell = False
        self.in_code = False
        self.first_codes = []

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'):
            self.skip += 1
        if tag == 'tr':
            self.column = 0
        if tag == 'td':
            self.column += 1
            self.in_cell = self.column == 1
        if tag == 'code' and self.in_cell:
            self.in_code = True
        if tag == 'a':
            href = dict(attrs).get('href', '')
            if '/model-studio/' in href:
                self.links.append(href)

    def handle_endtag(self, tag):
        if tag in ('script', 'style'):
            self.skip = max(0, self.skip - 1)
        if tag == 'td':
            self.in_cell = False
        if tag == 'code':
            self.in_code = False

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)
            if self.in_code and re.fullmatch(r'[A-Za-z][A-Za-z0-9._/-]*', data.strip()) and '-' in data:
                self.first_codes.append(data.strip())


def collect(item):
    slug, category = item
    url = 'https://help.aliyun.com/zh/model-studio/' + slug
    try:
        cached = DEST / (slug + '.html')
        if cached.exists():
            source = cached.read_text(encoding='utf-8')
        else:
            req = urllib.request.Request(url, headers={'User-Agent': 'RpaWx-model-catalog/1.0'})
            with urllib.request.urlopen(req, timeout=45, context=ssl.create_default_context(cafile=certifi.where())) as response:
                source = response.read().decode('utf-8')
            cached.write_text(source, encoding='utf-8')
        article = Article()
        article.feed(source)
        text = ' '.join(article.parts)
        (DEST / (slug + '.txt')).write_text(text, encoding='utf-8')
        ids = sorted({match.group(0).rstrip('./-') for match in PATTERN.finditer(text)
                      if '-' in match.group(0) and not match.group(0).endswith(('.py', '.js', '.json', '.png', '.jpg'))})
        ids = sorted(set(ids + article.first_codes))
        return dict(source=url, category=category, ids=ids, links=sorted(set(article.links)))
    except Exception as error:
        return dict(source=url, category=category, error=type(error).__name__)


if __name__ == '__main__':
    DEST.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=4) as pool:
        entries = list(pool.map(collect, SOURCES.items()))
    (DEST / 'extracted.json').write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding='utf-8')
    for entry in entries:
        print(entry['source'].rsplit('/', 1)[-1], entry.get('error', str(len(entry['ids'])) + ' candidates' if 'ids' in entry else 'unknown'))
