"""LLM-planned discovery, bounded tool calling, then per-merchant research."""
import json
from pathlib import Path
from .clients import ExternalError
from .models import Run, WorkflowRequest, now
from .tools import SearchTools, TOOL_SPECS

SEARCH_PROMPT = Path(__file__).with_name('search_data.md')


def narrow_plan(plan, request):
    """把 LLM 的计划裁剪到请求允许的组数（演示模式 = 1 组 = 1 次搜索调用）。"""
    queries = plan.queries[:request.plan_query_limit]
    if request.plan_query_limit == 1:
        queries = [queries[0].model_copy(update={'platforms': queries[0].platforms[:1]})]
    return plan.model_copy(update={'queries': queries})


class DiscoveryService:
    def __init__(self, collector, planner, llm, researcher, store, mode='live'):
        self.collector, self.planner, self.llm, self.researcher = collector, planner, llm, researcher
        self.store, self.mode = store, mode

    def discover(self, request: WorkflowRequest) -> Run:
        if request.analyze and self.researcher is None:
            raise ValueError('Research agent is required when analyze=true')
        run = Run(request=request, mode=self.mode)
        self.store.save(run)
        previous_llm_calls = self.llm.calls
        self.collector.api.calls = 0
        self.collector.api.max_calls = request.max_api_calls
        tools = None
        try:
            run.search_plan = self.planner.plan(request)
            run.search_plan = narrow_plan(run.search_plan, request)
            self.store.save(run)
            tools = SearchTools(self.collector, run, self.store)
            self._search(run, tools)
            tools.finish()
            if request.analyze:
                for lead in run.leads:
                    previous_research_calls = getattr(self.researcher, 'calls', None)
                    try:
                        lead.assessment = self.researcher.analyze(lead.merchant, request.target,
                            (request.taobao_min_sales, request.taobao_max_sales))
                    except ExternalError as exc:
                        lead.analysis_error = str(exc)
                    finally:
                        run.llm_calls += (self.researcher.calls - previous_research_calls
                                          if previous_research_calls is not None else 1)
                    self.store.save(run)
            blocking = [error for error in run.errors if error != 'stopped_after_first_lead']
            issues = bool(blocking) or any(l.analysis_error or l.merchant.warnings for l in run.leads)
            run.status = ('partial' if run.leads else 'failed') if issues else 'completed'
        except ExternalError as exc:
            if tools is not None:
                tools.finish()
            run.errors.append(str(exc))
            run.status = 'partial' if run.leads else 'failed'
        except Exception:
            run.errors.append('internal_error')
            run.status = 'failed'
            raise
        finally:
            run.api_calls = self.collector.api.calls
            run.llm_calls += self.llm.calls - previous_llm_calls
            run.finished_at = now()
            self.store.save(run)
        return run

    def _search(self, run, tools):
        messages = [
            {'role': 'system', 'content': SEARCH_PROMPT.read_text(encoding='utf-8')},
            {'role': 'user', 'content': json.dumps({
                'description': run.request.description, 'features': run.request.features,
                'target': run.request.target.model_dump(mode='json'),
                'search_plan': run.search_plan.model_dump(mode='json'),
                'limits': {'pages': run.request.pages, 'max_candidates': run.request.max_candidates,
                           'max_api_calls': run.request.max_api_calls, 'max_tool_calls': run.request.max_tool_calls,
                           'taobao_shops_per_search': run.request.taobao_shops_per_search,
                           'social_merchants_per_search': run.request.social_merchants_per_search,
                           'taobao_min_sales': run.request.taobao_min_sales,
                           'taobao_max_sales': run.request.taobao_max_sales},
            }, ensure_ascii=False)},
        ]
        reminded_for_name_search = False
        reminded_for_search = False
        for _ in range(run.request.max_tool_calls + 4):
            message = self.llm.complete(messages, tools=TOOL_SPECS)
            calls = message.get('tool_calls') or []
            if not calls:
                if not tools.searched_pairs:
                    raise ExternalError('llm_stopped_before_search')
                missing = tools.allowed - tools.searched_pairs
                gaps = [p for p in tools.search_progress() if p['confirmed'] < p['goal']]
                if (gaps and not reminded_for_search
                    and run.tool_calls < run.request.max_tool_calls
                    and self.collector.api.calls < run.request.max_api_calls
                    and any(p['next_page'] or p['pending'] for p in gaps)):
                    reminded_for_search = True
                    messages.append({'role': 'assistant', 'content': message.get('content') or ''})
                    messages.append({'role': 'user', 'content':
                        '部分关键词与平台尚未达到已确认商家目标。淘宝待查看店铺先调用 inspect_shop；'
                        '小红书/抖音待查看账号先调用 enrich_merchant，再调用 review_account。'
                        '若仍不足，按页码 1、5、10… 跳页继续搜索。进度：' +
                        json.dumps(gaps, ensure_ascii=False)})
                    continue
                if gaps:
                    run.errors.append(f'merchant_goal_unmet:{len(gaps)}')
                if missing:
                    run.errors.append(f'planned_searches_skipped:{len(missing)}')
                sparse = [m.id for m in tools.candidates.values()
                          if m.name and (not m.description or not m.location)
                          and m.id not in tools.name_searched]
                if sparse and not reminded_for_name_search and run.tool_calls < run.request.max_tool_calls:
                    reminded_for_name_search = True
                    messages.append({'role': 'assistant', 'content': message.get('content') or ''})
                    messages.append({'role': 'user', 'content':
                        '这些商家信息不足。请先对其调用 search_shop_by_name，再结束：' +
                        json.dumps(sparse[:20], ensure_ascii=False)})
                    continue
                if sparse:
                    run.errors.append(f'sparse_merchants_not_name_searched:{len(sparse)}')
                return
            if not isinstance(calls, list) or len(calls) > run.request.max_tool_calls:
                raise ExternalError('llm_invalid_tool_calls')
            safe_calls = []
            for call in calls:
                try:
                    call_id, function = call['id'], call['function']
                    name, arguments = function['name'], function['arguments']
                    if not isinstance(call_id, str) or not isinstance(name, str) or not isinstance(arguments, str):
                        raise ValueError()
                    safe_calls.append({'id': call_id, 'type': 'function',
                        'function': {'name': name, 'arguments': arguments}})
                except (KeyError, TypeError, ValueError):
                    raise ExternalError('llm_invalid_tool_calls') from None
            messages.append({'role': 'assistant', 'content': message.get('content'), 'tool_calls': safe_calls})
            for call in safe_calls:
                if run.tool_calls >= run.request.max_tool_calls:
                    result = {'error': 'tool_call_limit_exceeded'}
                else:
                    run.tool_calls += 1
                    result = tools.invoke(call['function']['name'], call['function']['arguments'])
                messages.append({'role': 'tool', 'tool_call_id': call['id'],
                                 'content': json.dumps(result, ensure_ascii=False)[:12000]})
                self.store.save(run)
            if run.request.stop_after_first_lead and run.leads:
                run.errors.append('stopped_after_first_lead')
                return
            if run.tool_calls >= run.request.max_tool_calls:
                run.errors.append('tool_call_limit_reached')
                return
        raise ExternalError('llm_tool_loop_exceeded')

    def node(self, state: dict) -> dict:
        request = WorkflowRequest.model_validate(state['discovery_request'])
        run = self.discover(request)
        return {'customer_info': run.model_dump(mode='json')}
