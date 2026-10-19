# -*- coding: utf-8 -*-
"""校验「单店模式」的调用预算：一次关键词搜索 + 一次详情，拿到第一家就收工。

用假 collector / 假 LLM / 假 planner 跑真实的 DiscoveryService 主循环，
断言 JustOneAPI 调用次数、工具调用次数、计划裁剪结果都符合演示口径。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from customer_discovery import tools as tools_module  # noqa: E402
from customer_discovery.models import (  # noqa: E402
    Assessment, Evidence, Merchant, Platform, Run, SearchPlan, SearchQuery, WorkflowRequest,
)
from customer_discovery.workflow import DiscoveryService, narrow_plan  # noqa: E402


class FakeApi:
    def __init__(self):
        self.calls = 0
        self.max_calls = 3

    def charge(self):
        if self.calls >= self.max_calls:
            raise RuntimeError('api_call_budget_exhausted')
        self.calls += 1


class FakeCollector:
    def __init__(self):
        self.api = FakeApi()

    def search(self, platform, keyword, page):
        self.api.charge()
        evidence = Evidence(endpoint='search', params={'keyword': keyword, 'page': page}, data={})
        return evidence, [{'fake': True}]

    def enrich(self, merchant):
        self.api.charge()
        enriched = merchant.model_copy(update={'description': '武汉江岸区的手作体验店，主营拼豆'})
        return enriched


class FakePlanner:
    def plan(self, request):
        return SearchPlan(queries=[
            SearchQuery(keyword='武汉 拼豆 手作', platforms=[Platform.XIAOHONGSHU, Platform.DOUYIN, Platform.TAOBAO]),
            SearchQuery(keyword='武汉 拼豆店 探店', platforms=[Platform.XIAOHONGSHU, Platform.TAOBAO]),
            SearchQuery(keyword='光谷 手工 DIY', platforms=[Platform.DOUYIN]),
        ])


class FakeLLM:
    """按脚本走一遍：搜索 → 详情 → 判定商家 → 收工。"""

    def __init__(self):
        self.calls = 0
        self.step = 0
        self.merchant_id = None

    def complete(self, messages, tools=None):
        self.calls += 1
        last = messages[-1]
        if last.get('role') == 'tool':
            payload = last['content']
            if '"merchants"' in payload and self.merchant_id is None:
                import json
                data = json.loads(payload)
                if data.get('merchants'):
                    self.merchant_id = data['merchants'][0].get('merchant_id')
        if self.step == 0:
            self.step += 1
            return {'content': '', 'tool_calls': [self._call('search_merchants', {
                'keyword': '武汉 拼豆 手作', 'platform': 'xiaohongshu', 'page': 1})]}
        if self.step == 1:
            self.step += 1
            return {'content': '', 'tool_calls': [self._call('enrich_merchant', {
                'merchant_id': self.merchant_id})]}
        if self.step == 2:
            self.step += 1
            return {'content': '', 'tool_calls': [self._call('review_account', {
                'merchant_id': self.merchant_id, 'is_business': True,
                'reason': '有门店地址、营业时间与到店体验内容，可确认是实体手作店'})]}
        return {'content': '已经找到可核查的商家', 'tool_calls': []}

    def _call(self, name, arguments):
        import json
        return {'id': f'call_{self.step}_{name}', 'type': 'function',
                'function': {'name': name, 'arguments': json.dumps(arguments, ensure_ascii=False)}}


class NullStore:
    def save(self, run):
        return None

    def save_evidence(self, run_id, evidence):
        return None

    def close(self):
        return None


def main() -> int:
    # 用假的 normalize 返回一个可核查的小红书账号，避免依赖真实接口数据结构
    def fake_normalize(platform, row, evidence):
        return Merchant(id='xiaohongshu-u1', platform=platform, platform_id='u1',
                        identity_kind='account', name='有家拼豆店', location='武汉 学府广场一楼')

    tools_module.normalize = fake_normalize

    request = WorkflowRequest(
        description='寻找武汉地区适合年轻女性消费的拼豆店，帮它做一轮推广',
        pages=1, taobao_shops_per_search=1, social_merchants_per_search=1,
        max_candidates=6, max_api_calls=3, max_tool_calls=6,
        plan_query_limit=1, stop_after_first_lead=True, analyze=False,
    )
    llm = FakeLLM()
    service = DiscoveryService(FakeCollector(), FakePlanner(), llm, None, NullStore())

    # 先单独验一下计划裁剪
    plan = narrow_plan(FakePlanner().plan(request), request)
    pairs = [(q.keyword, p) for q in plan.queries for p in q.platforms]
    print('plan after narrow:', pairs)

    run: Run = service.discover(request)
    print('api_calls     :', run.api_calls)
    print('tool_calls    :', run.tool_calls)
    print('llm_calls     :', run.llm_calls)
    print('leads         :', [lead.merchant.name for lead in run.leads])
    print('status        :', run.status)
    print('errors        :', run.errors)
    print('search_plan   :', [(q.keyword, [p.value for p in q.platforms]) for q in run.search_plan.queries])

    from customer_discovery.integration import project_customer_info
    info = project_customer_info(run)
    print('customer_info :', info['shop'], '| extra:', {k: info['extra'][k] for k in
          ('api_calls', 'tool_calls', 'search_pairs', 'lead_count') if k in info['extra']})

    checks = [
        ('计划只剩 1 组', len(pairs) == 1),
        ('API 调用不超过 3 次', run.api_calls <= 3),
        ('实际只用了 2 次（搜索 + 详情）', run.api_calls == 2),
        ('工具调用 3 次（搜索/详情/判定）', run.tool_calls == 3),
        ('拿到 1 家商家', len(run.leads) == 1),
        ('命中「拿到就收工」', 'stopped_after_first_lead' in run.errors),
    ]
    failed = [name for name, ok in checks if not ok]
    for name, ok in checks:
        print(('  PASS ' if ok else '  FAIL ') + name)
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
