import hashlib
import json
from pathlib import Path
from pydantic import ValidationError
from .clients import ExternalError
from .models import Assessment, Merchant, Platform, SearchPlan, TargetProfile, WorkflowRequest
from .sales import sales_status

PROMPT = Path(__file__).with_name('research.md')
KEYWORD_PROMPT = Path(__file__).with_name('search_keyword.md')


class ChatLLM:
    """Small Chat Completions adapter for providers supporting function tools."""
    def __init__(self, http, base_url, api_key, model, trace=None):
        if not base_url or not api_key or not model:
            raise ValueError('LLM_BASE_URL, LLM_API_KEY and LLM_MODEL are required')
        if not base_url.startswith('https://'):
            raise ValueError('LLM_BASE_URL must use HTTPS')
        self.http, self.base_url, self.api_key, self.model = http, base_url.rstrip('/'), api_key, model
        self.trace = trace
        self.calls = 0

    def complete(self, messages, *, tools=None, json_object=False):
        body = {'model': self.model, 'messages': messages}
        if tools is not None:
            body['tools'] = tools
            body['tool_choice'] = 'auto'
        if json_object:
            body['response_format'] = {'type': 'json_object'}
        self.calls += 1
        stage = 'search_data' if tools is not None else 'search_keyword'
        try:
            response = self.http.request('POST', self.base_url + '/chat/completions',
                headers={'Authorization': f'Bearer {self.api_key}'}, json=body)
        except ExternalError as exc:
            if self.trace:
                self.trace.record(stage, body, error=str(exc))
            raise
        if self.trace:
            self.trace.record(stage, body, response=response)
        try:
            choice = response['choices'][0]
            if choice.get('finish_reason') not in ('stop', 'tool_calls'):
                raise ValueError('incomplete')
            message = choice['message']
            if not isinstance(message, dict):
                raise ValueError('invalid message')
            return message
        except (KeyError, IndexError, TypeError, ValueError):
            raise ExternalError('llm_invalid_message') from None


class KeywordPlanner:
    def __init__(self, llm):
        self.llm = llm
        self.prompt = KEYWORD_PROMPT.read_text(encoding='utf-8')

    def plan(self, request: WorkflowRequest) -> SearchPlan:
        message = self.llm.complete([
            {'role': 'system', 'content': self.prompt},
            {'role': 'user', 'content': json.dumps({'description': request.description,
                'features': request.features, 'target': request.target.model_dump()}, ensure_ascii=False)},
        ], json_object=True)
        try:
            return SearchPlan.model_validate_json(message['content'])
        except (KeyError, TypeError, ValidationError, ValueError):
            raise ExternalError('llm_invalid_search_plan') from None


class QualificationAgent:
    """OpenAI-compatible Chat Completions protocol, without a provider SDK."""
    def __init__(self, http, base_url, api_key, model, trace=None):
        if not base_url or not api_key or not model:
            raise ValueError('LLM_BASE_URL, LLM_API_KEY and LLM_MODEL are required')
        if not base_url.startswith('https://'):
            raise ValueError('LLM_BASE_URL must use HTTPS')
        self.http, self.base_url, self.api_key, self.model = http, base_url.rstrip('/'), api_key, model
        self.trace = trace
        self.prompt = PROMPT.read_text(encoding='utf-8')
        self.prompt_hash = hashlib.sha256(self.prompt.encode()).hexdigest()
        self.calls = 0

    def analyze(self, merchant: Merchant, target: TargetProfile,
                sales_bounds: tuple[int, int] | None = None) -> Assessment:
        payload = merchant.model_dump(mode='json', exclude={'schema_version', 'observed_at', 'evidence'})
        # A few representative items keep the research input bounded and distinguish
        # missing data from an uncollected or truncated content list.
        if merchant.platform == Platform.TAOBAO and sales_bounds is not None:
            payload['products'].sort(key=lambda p: sales_status(p['sales_display'], *sales_bounds) != 'in_range')
        payload['products'] = payload['products'][:5]
        payload['contents'] = payload['contents'][:5]
        refs = {f'E{index}': evidence.id for index, evidence in enumerate(merchant.evidence, 1)}
        payload['evidence'] = [
            {'ref': ref, 'endpoint': evidence.endpoint, 'params': evidence.params}
            for ref, evidence in zip(refs, merchant.evidence)
        ]
        input_data = {'target': target.model_dump(), 'merchant': payload}
        if merchant.platform == Platform.TAOBAO and sales_bounds is not None:
            input_data['taobao_sales_range'] = {'min': sales_bounds[0], 'max': sales_bounds[1]}
        user = json.dumps(input_data, ensure_ascii=False)
        if len(user) > 48000:
            raise ExternalError('llm_input_too_large')
        body = {'model': self.model, 'messages': [
            {'role': 'system', 'content': self.prompt}, {'role': 'user', 'content': user}],
            'response_format': {'type': 'json_object'}}
        ids = set(refs.values())
        for attempt in range(2):
            self.calls += 1
            try:
                response = self.http.request('POST', self.base_url + '/chat/completions',
                    headers={'Authorization': f'Bearer {self.api_key}'}, json=body)
            except ExternalError as exc:
                if self.trace:
                    self.trace.record('research', body, error=str(exc))
                raise
            if self.trace:
                self.trace.record('research', body, response=response)
            try:
                choice = response['choices'][0]
                if choice.get('finish_reason') != 'stop':
                    raise ValueError('incomplete')
                raw = json.loads(choice['message']['content'])
                if not isinstance(raw, dict):
                    raise ValueError('invalid assessment object')
                for reason in raw.get('reasons', []):
                    if not isinstance(reason, dict) or not isinstance(reason.get('evidence_ids'), list):
                        continue
                    resolved = []
                    for cited in reason['evidence_ids']:
                        if isinstance(cited, str) and cited in ids:
                            resolved.append(cited)
                            continue
                        ref = f'E{cited}' if type(cited) is int else cited
                        if not isinstance(ref, str) or ref not in refs:
                            raise ValueError('unknown evidence reference')
                        resolved.append(refs[ref])
                    reason['evidence_ids'] = resolved
                result = Assessment.model_validate(raw)
                if any(eid not in ids for reason in result.reasons for eid in reason.evidence_ids):
                    raise ValueError('unknown evidence')
                break
            except (KeyError, IndexError, TypeError, ValueError, ValidationError):
                if attempt:
                    raise ExternalError('llm_invalid_assessment') from None
                body['messages'].append({'role': 'user', 'content':
                    '上次输出不符合 JSON 契约或引用了无效证据。请重新输出完整 JSON；'
                    'evidence_ids 必须是非空数组，优先使用以下 E 编号字符串：'
                    + json.dumps(list(refs), ensure_ascii=False)})
        if result.decision == 'target' and (
            result.is_business is not True or result.score < target.min_score
            or result.confidence < target.min_confidence
            or merchant.identity_kind == 'unresolved_product'
            or (target.city and not merchant.location)
        ):
            result.decision = 'needs_review'
            result.missing_data.append('程序门槛未满足，需人工复核')
        if merchant.platform == Platform.TAOBAO and sales_bounds is not None:
            statuses = [sales_status(p.sales_display, *sales_bounds) for p in merchant.products]
            if 'in_range' not in statuses and result.decision == 'target':
                result.decision = 'needs_review'
                result.missing_data.append('缺少可确认处于目标范围的商品销量')
        result.model = self.model
        result.prompt_version = '3.1:' + self.prompt_hash[:12]
        return result
