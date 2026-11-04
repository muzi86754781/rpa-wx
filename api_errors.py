"""Safe API diagnostics and bounded, cancellable operation-level retries."""
import json
import math
import random
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

BILLING_ERRORS = {
    'credit_balance_exhausted': 'API 可用余额已用完，请在 API 平台 Billing 中充值或查看可领取额度。',
    'insufficient_quota': 'API 额度不足，请检查当前服务商的余额、免费额度和用量限制。',
    'billing_hard_limit_reached': 'API 账单额度已达到上限，请检查 API 平台的账单与限额。',
    'organization_spend_limit_exceeded': 'API 组织消费已达到上限，请在组织设置中检查消费限额。',
    'project_spend_limit_exceeded': 'API 项目消费已达到上限，请在项目设置中检查消费限额。',
    'organization_usage_limit_exceeded': 'API 组织已达到服务方分配的用量上限，请申请提高限额。',
    'usage_limit_exceeded': 'API 已达到用量上限，请检查账单、余额及组织和项目的限额。',
    'AllocationQuota.FreeTierOnly': '百炼免费额度已用完或已过期，且启用了用完即停。请在百炼控制台检查模型额度。',
    'Throttling.AllocationQuota': '百炼配额受到限制，请在控制台检查模型免费额度和资源配额。',
    'Arrearage': '百炼账户存在欠费或余额不足，请在阿里云费用中心检查账单。',
}


class APIError(RuntimeError):
    def __init__(self, status, message, code='', retryable=False, retry_after=0, provider='OpenAI'):
        self.status, self.message, self.code = status, message, code
        self.retryable, self.retry_after = retryable, retry_after
        self.input_pending = False
        self.provider = provider
        super().__init__(message)

    def __str__(self):
        text = f'{self.provider} API {self.status}：{self.message}'
        if self.code:
            text += f'（{self.code}）'
        if self.retryable and self.retry_after:
            text += f' 请至少等待 {math.ceil(self.retry_after)} 秒后重试。'
        if self.input_pending:
            text += ' 微信可能保留已输入的草稿，请手动检查后再继续。'
        return text


def retry_delay(headers):
    value = headers.get('Retry-After') if headers else None
    if not value:
        return 0
    try:
        seconds = float(value)
    except (ValueError, TypeError):
        try:
            date = parsedate_to_datetime(value)
            if date.tzinfo is None:
                date = date.replace(tzinfo=timezone.utc)
            seconds = (date - datetime.now(timezone.utc)).total_seconds()
        except (ValueError, TypeError, OverflowError):
            return 0
    return max(0, seconds) if math.isfinite(seconds) else 0


def api_error(error, provider='OpenAI'):
    """Inspect only structured codes; never display raw bodies or server messages."""
    try:
        data = json.loads(error.read(65536))
        detail = data.get('error', data) if isinstance(data, dict) else {}
        detail = detail if isinstance(detail, dict) else {}
    except (ValueError, OSError):
        detail = {}
    finally:
        error.close()
    code, kind = detail.get('code'), detail.get('type')
    def failure(*args, **kwargs):
        return APIError(*args, **kwargs, provider=provider)
    if not isinstance(code, str):
        code = ''
    if code in BILLING_ERRORS or kind == 'insufficient_quota':
        reason = code if code in BILLING_ERRORS else 'insufficient_quota'
        return failure(error.code, BILLING_ERRORS[reason], reason)
    delay = retry_delay(error.headers)
    if error.code == 429:
        rate_codes = ('rate_limit_exceeded', 'slow_down', 'Throttling.RateQuota',
                      'Throttling.BurstRate', 'LimitRequests', 'limit_requests',
                      'ResourceExhausted', 'limit_burst_rate')
        if code in rate_codes or kind == 'rate_limit_error':
            known = code if code in rate_codes else 'rate_limit_error'
            return failure(429, '请求或 Token 速率达到限制，请减少调用频率或检查服务商的调用限额。',
                            known, True, delay)
        return failure(429, '请求被限制，但未返回可识别的原因。请先检查 API Billing 余额和 Limits 限额；'
                        '余额不足与临时限流都可能返回 429。')
    if error.code == 503:
        return failure(503, '模型服务暂时繁忙，请稍后重试。', retryable=True, retry_after=delay)
    messages = {
        400: '请求参数不被接受，请检查模型是否支持图像输入和结构化输出。',
        401: 'API Key 无效或与接入地址不匹配，请检查服务商、地域、Base URL 和密钥。',
        403: 'API 访问被拒绝，请检查账户、项目权限和服务可用地区。',
        404: '模型或接口不可用，请检查模型 ID 和账户访问权限。',
    }
    if error.code in (301, 302, 303, 307, 308):
        return failure(error.code, '接口地址发生跳转，请在设置中填写服务商的最终 Base URL。')
    return failure(error.code, messages.get(error.code, '服务请求失败，请稍后重试或查看服务状态。'))


class Cooldown:
    def __init__(self):
        self.deadline = 0

    def remaining(self):
        return max(0, self.deadline - time.monotonic())

    def defer(self, seconds):
        self.deadline = max(self.deadline, time.monotonic() + seconds)

    def wait(self, cancel, notify):
        while self.remaining() > 0:
            notify(f'API 限流冷却中，剩余 {math.ceil(self.remaining())} 秒；可点击停止。')
            if cancel.wait(min(1, self.remaining())):
                raise RuntimeError('操作已停止，不再重试 API 请求。')
        if cancel.is_set():
            raise RuntimeError('操作已停止，不再重试 API 请求。')


def run_operation(work, cancel, cooldown, notify, prepare_retry=None):
    for attempt in range(3):
        remaining = cooldown.remaining()
        if remaining > 120:
            raise RuntimeError(f'API 仍处于冷却期，请至少 {math.ceil(remaining)} 秒后再试。')
        cooldown.wait(cancel, notify)
        try:
            return work()
        except APIError as error:
            if not error.retryable:
                raise
            delay = max(error.retry_after, 15 * 2 ** attempt + random.uniform(0, 1))
            cooldown.defer(delay)
            error.retry_after = delay
            if (attempt == 2 or delay > 120 or prepare_retry is None or not prepare_retry()):
                raise
            notify(f'API 临时限流或繁忙，冷却后重新截图重试（{attempt + 1}/2）。')
