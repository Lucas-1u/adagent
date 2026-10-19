"""Adapter for the main team's single-field customer discovery node contract.

This module does not change the discovery workflow or its existing Run output.
"""
import importlib.util
import json
import os
import time
from contextlib import contextmanager
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .agent import ChatLLM, KeywordPlanner, QualificationAgent
from .clients import ExternalError, JsonHTTP, JustOneAPI
from .collectors import Collector
from .config import settings
from .models import Platform, Run, WorkflowRequest
from .runtime import runtime
from .storage import Store
from .workflow import DiscoveryService


class CustomerInfoContract(BaseModel):
    """The fields owned by the customer node in the main team's State v0.4."""

    model_config = ConfigDict(extra='forbid')
    shop: str
    type: str
    platform: str
    highlights: str
    potential_need: str
    extra: dict[str, Any] = Field(default_factory=dict)


PLATFORM_NAMES = {
    Platform.XIAOHONGSHU: '小红书',
    Platform.DOUYIN: '抖音',
    Platform.TAOBAO: '淘宝',
}


def _category(run: Run, merchant) -> str:
    text = ' '.join(filter(None, [merchant.name, merchant.description]
                   + [product.title for product in merchant.products[:3]]))
    for term, category in (('拼豆', 'handmade'), ('手作', 'handmade'), ('DIY', 'handmade'),
                           ('咖啡', 'coffee'), ('甜品', 'dessert'), ('蛋糕', 'dessert')):
        if term.lower() in text.lower():
            return category
    if run.request.target.categories:
        return run.request.target.categories[0]
    return 'other'


def project_customer_info(run: Run) -> dict:
    """Project a completed Run to one customer; preserve the Run in local storage."""
    if not run.leads:
        return CustomerInfoContract(
            shop='未发现可核查商家', type='unknown', platform='未确定',
            highlights='本次搜索没有取得符合条件的商家证据',
            potential_need='找到商家后再评估营销需求',
            extra={'run_id': run.id, 'status': run.status, 'no_match': True,
                   'errors': run.errors, 'api_calls': run.api_calls,
                   'tool_calls': run.tool_calls, 'llm_calls': run.llm_calls,
                   'search_pairs': (len(run.search_plan.queries) if run.search_plan else 0)}).model_dump()

    def rank(lead):
        assessment = lead.assessment
        decision = assessment.decision if assessment else None
        return ({'target': 2, 'needs_review': 1, 'not_target': 0}.get(decision, 1),
                assessment.score if assessment else -1,
                assessment.confidence if assessment else -1)

    lead = max(run.leads, key=rank)
    merchant, assessment = lead.merchant, lead.assessment
    sample = next((content.title for content in merchant.contents if content.title), None)
    product = next((item.title for item in merchant.products if item.title), None)
    highlight = merchant.description or sample or product or merchant.certification
    return CustomerInfoContract(
        shop=merchant.name or f'{PLATFORM_NAMES[merchant.platform]}商家 {merchant.platform_id}',
        type=_category(run, merchant), platform=PLATFORM_NAMES[merchant.platform],
        highlights=(highlight or '商家特点仍待核实')[:200],
        potential_need=(assessment.opportunities[0] if assessment and assessment.opportunities
                        else '待进一步核实营销需求')[:200],
        extra={'run_id': run.id, 'status': run.status, 'merchant_id': merchant.id,
               'decision': assessment.decision if assessment else None,
               'score': assessment.score if assessment else None,
               'confidence': assessment.confidence if assessment else None,
               'profile_url': merchant.profile_url,
               'lead_count': len(run.leads),
               'api_calls': run.api_calls, 'tool_calls': run.tool_calls,
               'llm_calls': run.llm_calls,
               'search_pairs': (len(run.search_plan.queries) if run.search_plan else 0)}).model_dump()


class SharedLLMHTTP:
    """Route the existing Chat Completions body through app.llm_client.

The shared v0.4 public helpers only support single-message chat. Its existing
client and recorder also support the multi-message tool calls required here.
"""

    def __init__(self, llm_client):
        self.llm_client = llm_client

    def request(self, method: str, url: str, **kwargs):
        if method != 'POST' or not url.endswith('/chat/completions'):
            raise ExternalError('unsupported_shared_llm_request')
        body = kwargs['json']
        messages = body['messages']
        purpose = ('customer_search_data' if body.get('tools') else
                   'customer_research' if any(message.get('role') == 'system' and
                       '研究' in (message.get('content') or '') for message in messages)
                   else 'customer_search_keyword')
        started = time.monotonic()
        try:
            response = self.llm_client._get_client().chat.completions.create(**body)
            data = response.model_dump(mode='json')
        except Exception:
            raise ExternalError('shared_llm_call_failed') from None
        system = next((message.get('content') or '' for message in messages
                       if message.get('role') == 'system'), '')
        message = data['choices'][0]['message']
        self.llm_client._record_call(
            purpose=purpose, system=system,
            prompt=json.dumps(messages, ensure_ascii=False),
            response=json.dumps(message, ensure_ascii=False),
            reasoning=message.get('reasoning_content'),
            elapsed=round(time.monotonic() - started, 2), model=body['model'])
        return data


@contextmanager
def _shared_runtime(llm_client):
    config = settings()
    store = Store(config['DISCOVERY_DB'] or 'data/discovery.sqlite3')
    http = JsonHTTP()
    try:
        api = JustOneAPI(config['JUSTONEAPI_TOKEN'], http)
        llm_http = SharedLLMHTTP(llm_client)
        model = llm_client.default_model()
        llm = ChatLLM(llm_http, 'https://shared-llm.invalid', 'managed-by-app', model)
        researcher = QualificationAgent(llm_http, 'https://shared-llm.invalid',
                                        'managed-by-app', model)
        yield DiscoveryService(Collector(api), KeywordPlanner(llm), llm,
                               researcher, store, 'live')
    finally:
        http.close()
        store.close()


def integration_runtime():
    """Use the main team's LLM when installed; retain standalone project startup."""
    if (importlib.util.find_spec('app') is not None
            and importlib.util.find_spec('app.llm_client') is not None):
        from app import llm_client
        return _shared_runtime(llm_client)
    return runtime()


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, '').strip()
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value >= 1 else default


def _env_flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name, '').strip().lower()
    if not raw:
        return default
    return raw not in ('0', 'false', 'no', 'off')


def build_request(text: str) -> WorkflowRequest:
    """演示口径：一句话需求 → 只搜一轮关键词、拿到第一家商家就收工。

    默认 1 次搜索 + 最多 1 次详情 = 2 次 JustOneAPI 调用（上限 3 留一次余量）。
    需要完整市场扫描时，用环境变量放开：
        DISCOVERY_PLAN_QUERIES       计划执行几组「关键词 × 平台」，默认 1
        DISCOVERY_MAX_API_CALLS      JustOneAPI 调用上限，默认 3
        DISCOVERY_MAX_TOOL_CALLS     工具调用上限，默认 6
        DISCOVERY_SHOPS_PER_SEARCH   每组淘宝目标店铺数，默认 1
        DISCOVERY_MERCHANTS_PER_SEARCH 每组社媒目标商家数，默认 1
        DISCOVERY_PAGES              可翻到的页数上限，默认 1
        DISCOVERY_STOP_AFTER_FIRST   拿到第一家即停，默认 1
    """
    queries = min(_env_int('DISCOVERY_PLAN_QUERIES', 1), 6)
    return WorkflowRequest(
        description=text,
        pages=_env_int('DISCOVERY_PAGES', 1),
        taobao_shops_per_search=_env_int('DISCOVERY_SHOPS_PER_SEARCH', 1),
        social_merchants_per_search=_env_int('DISCOVERY_MERCHANTS_PER_SEARCH', 1),
        max_candidates=_env_int('DISCOVERY_MAX_CANDIDATES', 6),
        max_api_calls=_env_int('DISCOVERY_MAX_API_CALLS', 3),
        max_tool_calls=_env_int('DISCOVERY_MAX_TOOL_CALLS', 6),
        plan_query_limit=queries,
        stop_after_first_lead=_env_flag('DISCOVERY_STOP_AFTER_FIRST', True),
    )


def run_customer_node(state: dict) -> dict:
    """Read only user_input and return only the customer_info state patch."""
    text = state.get('user_input')
    if not isinstance(text, str) or len(text.strip()) < 3:
        raise ValueError('user_input must contain at least three characters')
    request = build_request(text.strip())
    with integration_runtime() as service:
        result = service.discover(request)
    return {'customer_info': project_customer_info(result)}
