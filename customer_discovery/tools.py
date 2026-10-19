"""Bounded, validated function tools exposed to the search LLM."""
from uuid import uuid4
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from .clients import ExternalError
from .collectors import IdentityConflict, normalize
from .models import Lead, Merchant, Platform, Run, SearchPlan, SearchPlatform, SearchProgress, WorkflowRequest
from .sales import sales_status


def merge(left: Merchant, right: Merchant) -> None:
    """Combine records only after their exact platform merchant ID matches."""
    known = {e.id for e in left.evidence}
    left.evidence.extend(e for e in right.evidence if e.id not in known)
    known = {p.id for p in left.products}
    left.products.extend(p for p in right.products if p.id not in known)
    known = {c.id for c in left.contents}
    left.contents.extend(c for c in right.contents if c.id not in known)
    left.warnings = list(dict.fromkeys(left.warnings + right.warnings))
    for field in ('name', 'description', 'location', 'certification', 'followers', 'content_count', 'profile_url', 'seller_id'):
        if getattr(left, field) is None:
            setattr(left, field, getattr(right, field))


def discard(run: Run, reason: str) -> None:
    run.discarded[reason] = run.discarded.get(reason, 0) + 1


class Arguments(BaseModel):
    model_config = ConfigDict(extra='forbid')


class SearchArgs(Arguments):
    keyword: str = Field(min_length=2, max_length=100)
    platform: SearchPlatform
    page: int = Field(ge=1, le=100)


class MerchantArgs(Arguments):
    merchant_id: str = Field(min_length=1, max_length=300)


class ReviewAccountArgs(MerchantArgs):
    is_business: bool
    reason: str = Field(min_length=5, max_length=300)


class NameArgs(MerchantArgs):
    page: int = Field(ge=1, le=100)


class PromoteArgs(MerchantArgs):
    replace_merchant_id: str = Field(min_length=1, max_length=300)


TOOL_SPECS = [
    {'type': 'function', 'function': {'name': 'search_merchants',
     'description': 'Search one planned keyword on one planned platform and page. Returns merchant summaries and discard counts.',
     'parameters': {'type': 'object', 'properties': {
         'keyword': {'type': 'string'},
         'platform': {'type': 'string', 'enum': ['xiaohongshu', 'taobao', 'douyin']},
         'page': {'type': 'integer', 'minimum': 1, 'maximum': 100}},
         'required': ['keyword', 'platform', 'page'], 'additionalProperties': False}}},
    {'type': 'function', 'function': {'name': 'inspect_shop',
     'description': 'Inspect a Taobao shop found through an in-range product; verifies shop identity via item detail and checks the seller catalog when possible. A shop counts toward the five-shop goal only after this step.',
     'parameters': {'type': 'object', 'properties': {'merchant_id': {'type': 'string'}},
                    'required': ['merchant_id'], 'additionalProperties': False}}},
    {'type': 'function', 'function': {'name': 'enrich_merchant',
     'description': 'Inspect a pending Xiaohongshu/Douyin account profile and recent content before deciding whether it is a business; also enriches retained merchants. Cached by merchant ID.',
     'parameters': {'type': 'object', 'properties': {'merchant_id': {'type': 'string'}},
                    'required': ['merchant_id'], 'additionalProperties': False}}},
    {'type': 'function', 'function': {'name': 'review_account',
     'description': 'After enrich_merchant, decide whether a Xiaohongshu/Douyin account has concrete business evidence. Only accepted accounts count toward the per-keyword platform goal.',
     'parameters': {'type': 'object', 'properties': {
         'merchant_id': {'type': 'string'}, 'is_business': {'type': 'boolean'},
         'reason': {'type': 'string', 'minLength': 5, 'maxLength': 300}},
         'required': ['merchant_id', 'is_business', 'reason'], 'additionalProperties': False}}},
    {'type': 'function', 'function': {'name': 'search_shop_by_name',
     'description': 'Search the known merchant name on its own platform; merge only results with its exact platform ID.',
     'parameters': {'type': 'object', 'properties': {
         'merchant_id': {'type': 'string'}, 'page': {'type': 'integer', 'minimum': 1, 'maximum': 100}},
         'required': ['merchant_id', 'page'], 'additionalProperties': False}}},
    {'type': 'function', 'function': {'name': 'promote_merchant',
     'description': 'Replace a retained merchant with a newly discovered, unretained merchant after comparing their summaries. No external API call.',
     'parameters': {'type': 'object', 'properties': {
         'merchant_id': {'type': 'string'}, 'replace_merchant_id': {'type': 'string'}},
         'required': ['merchant_id', 'replace_merchant_id'], 'additionalProperties': False}}},
]


def summary(merchant: Merchant):
    return {
        'merchant_id': merchant.id, 'platform': merchant.platform.value,
        'name': (merchant.name or '')[:120], 'description': (merchant.description or '')[:500],
        'location': merchant.location, 'certification': merchant.certification,
        'followers': merchant.followers, 'product_count': len(merchant.products),
        'content_count': len(merchant.contents), 'warnings': merchant.warnings[:5],
        'product_samples': [{'title': (p.title or '')[:100], 'price': p.price_display,
                             'sales': p.sales_display} for p in merchant.products[:3]],
        'content_samples': [{'title': (c.title or '')[:100], 'likes': c.likes,
                             'comments': c.comments} for c in merchant.contents[:3]],
        'missing': [key for key in ('name', 'description', 'location') if not getattr(merchant, key)],
    }


class SearchTools:
    def __init__(self, collector, run: Run, store):
        assert isinstance(run.request, WorkflowRequest) and isinstance(run.search_plan, SearchPlan)
        self.collector, self.run, self.store = collector, run, store
        self.candidates: dict[str, Merchant] = {}
        self.alternates: dict[str, Merchant] = {}
        self.pending_shops: dict[str, Merchant] = {}
        self.pending_accounts: dict[str, Merchant] = {}
        self.pending_sources: dict[str, set[tuple[str, Platform]]] = {}
        self.confirmed_shops: dict[tuple[str, Platform], set[str]] = {}
        self.confirmed_accounts: dict[tuple[str, Platform], set[str]] = {}
        self.last_search_page: dict[tuple[str, Platform], int] = {}
        self.cache: dict[tuple, dict] = {}
        self.enriched: set[str] = set()
        self.rejected_accounts: set[str] = set()
        self.searched_pairs: set[tuple[str, Platform]] = set()
        self.name_searched: set[str] = set()
        self.allowed = {(q.keyword, p) for q in run.search_plan.queries for p in q.platforms}

    def _goal(self, platform: Platform) -> int:
        return (self.run.request.taobao_shops_per_search if platform == Platform.TAOBAO
                else self.run.request.social_merchants_per_search)

    def _confirmed(self, pair: tuple[str, Platform]) -> set[str]:
        return (self.confirmed_shops if pair[1] == Platform.TAOBAO
                else self.confirmed_accounts).setdefault(pair, set())

    def search_progress(self):
        progress = []
        for keyword, platform in sorted(self.allowed):
            pair = (keyword, platform)
            last = self.last_search_page.get(pair, 0)
            next_page = 1 if last == 0 else 5 if last == 1 else last + 5
            progress.append({'keyword': keyword, 'platform': platform.value,
                             'confirmed': len(self._confirmed(pair)), 'goal': self._goal(platform),
                             'last_page': last, 'next_page': next_page if next_page <= self.run.request.pages else None,
                             'pending': [mid for mid in self.pending_shops | self.pending_accounts
                                         if pair in self.pending_sources.get(mid, set())][:20]})
        return progress

    def _result(self, merchant_ids: list[str], *, scanned=0, error=None, pair=None):
        merchants = []
        for mid in dict.fromkeys(merchant_ids):
            merchant = (self.candidates.get(mid) or self.alternates.get(mid)
                        or self.pending_shops.get(mid) or self.pending_accounts.get(mid))
            if merchant is not None:
                item = {**summary(merchant), 'retained': mid in self.candidates}
                if merchant.platform == Platform.TAOBAO:
                    statuses = [sales_status(p.sales_display, self.run.request.taobao_min_sales,
                                             self.run.request.taobao_max_sales) for p in merchant.products]
                    item['sales_screen'] = 'in_range' if 'in_range' in statuses else 'unknown'
                    item['stage'] = ('product_screened' if mid in self.pending_shops else 'shop_inspected')
                else:
                    item['stage'] = ('profile_inspected' if mid in self.enriched else 'account_found')
                    if mid not in self.pending_accounts:
                        item['stage'] = 'business_confirmed'
                merchants.append(item)
        result = {'merchants': merchants[:20],
                'scanned': scanned, 'discarded': self.run.discarded.copy(),
                'api_calls': self.collector.api.calls,
                'api_calls_remaining': max(0, self.collector.api.max_calls - self.collector.api.calls),
                'error': error}
        if pair:
            result['confirmed_merchant_count'] = len(self._confirmed(pair))
            result['merchant_goal'] = self._goal(pair[1])
            if pair[1] == Platform.TAOBAO:
                result['confirmed_shop_count'] = result['confirmed_merchant_count']
                result['shop_goal'] = result['merchant_goal']
            result['last_search_page'] = self.last_search_page.get(pair, 0)
        return result

    def _collect(self, platform: Platform, keyword: str, page: int, match_id: str | None = None):
        ev, rows = self.collector.search(platform, keyword, page)
        changed: list[str] = []
        unresolved_lookups = 0
        for index, row in enumerate(rows):
            if match_id is None and len(set(changed)) >= 20:
                self.run.discarded['page_result_limit'] = (
                    self.run.discarded.get('page_result_limit', 0) + len(rows) - index)
                break
            try:
                evidence = ev.model_copy(update={'id': str(uuid4()), 'data': row})
                merchant = normalize(platform, row, [evidence])
                if platform == Platform.TAOBAO:
                    status = sales_status(merchant.products[0].sales_display,
                                          self.run.request.taobao_min_sales,
                                          self.run.request.taobao_max_sales)
                    if status != 'in_range':
                        discard(self.run, f'sales_{status}')
                        continue
                if match_id and merchant.identity_kind != 'unresolved_product' and merchant.id != match_id:
                    continue
                if merchant.identity_kind == 'unresolved_product':
                    # A product is never stored unless its detail establishes a shop ID.
                    if unresolved_lookups >= 5:
                        discard(self.run, 'unresolved_lookup_limit')
                        continue
                    unresolved_lookups += 1
                    try:
                        merchant = self.collector.enrich(merchant)
                    except (ExternalError, ValidationError) as exc:
                        if str(exc) == 'api_call_budget_exhausted':
                            self.run.errors.append('api_call_budget_reached; results may be incomplete')
                        discard(self.run, 'missing_shop_id')
                        continue
                if merchant.identity_kind == 'unresolved_product':
                    discard(self.run, 'missing_shop_id')
                    continue
                if platform == Platform.TAOBAO:
                    status = sales_status(merchant.products[0].sales_display,
                                          self.run.request.taobao_min_sales,
                                          self.run.request.taobao_max_sales)
                    if status != 'in_range':
                        discard(self.run, f'sales_{status}')
                        continue
                if match_id and merchant.id != match_id:
                    continue
                if merchant.id in self.rejected_accounts:
                    discard(self.run, 'previously_rejected_account')
                    continue
                if match_id is None:
                    pair = (keyword, platform)
                    if merchant.id in self.candidates:
                        merge(self.candidates[merchant.id], merchant)
                        if len(self._confirmed(pair)) < self._goal(platform):
                            self._confirmed(pair).add(merchant.id)
                    elif merchant.id in self.alternates:
                        merge(self.alternates[merchant.id], merchant)
                        if len(self._confirmed(pair)) < self._goal(platform):
                            self._confirmed(pair).add(merchant.id)
                    else:
                        pending = self.pending_shops if platform == Platform.TAOBAO else self.pending_accounts
                        self.pending_sources.setdefault(merchant.id, set()).add(pair)
                        if merchant.id in pending:
                            merge(pending[merchant.id], merchant)
                        else:
                            if len(pending) >= self.run.request.max_candidates * 5:
                                old_id = next(iter(pending))
                                del pending[old_id]
                                self.pending_sources.pop(old_id, None)
                                discard(self.run, 'pending_merchant_limit')
                            pending[merchant.id] = merchant
                    changed.append(merchant.id)
                    continue
                if merchant.id in self.candidates:
                    merge(self.candidates[merchant.id], merchant)
                elif len(self.candidates) < self.run.request.max_candidates and match_id is None:
                    self.candidates[merchant.id] = merchant
                elif match_id is None:
                    if merchant.id in self.alternates:
                        merge(self.alternates[merchant.id], merchant)
                    else:
                        if len(self.alternates) >= self.run.request.max_candidates * 5:
                            self.alternates.pop(next(iter(self.alternates)))
                            discard(self.run, 'candidate_limit')
                        self.alternates[merchant.id] = merchant
                else:
                    discard(self.run, 'candidate_limit')
                    continue
                changed.append(merchant.id)
            except (ExternalError, ValidationError):
                discard(self.run, 'invalid_candidate')
        self.run.leads = [Lead(merchant=m) for m in self.candidates.values()]
        self.run.api_calls = self.collector.api.calls
        self.store.save(self.run)
        return self._result(changed, scanned=len(rows),
                            pair=(keyword, platform) if match_id is None else None)

    def search_merchants(self, args: SearchArgs):
        if (args.keyword, args.platform) not in self.allowed:
            return {'error': 'keyword_platform_not_in_plan'}
        if args.page > self.run.request.pages:
            return {'error': 'page_limit_exceeded'}
        pair = (args.keyword, args.platform)
        if len(self._confirmed(pair)) >= self._goal(args.platform):
            return {'error': 'shop_goal_reached' if args.platform == Platform.TAOBAO
                    else 'merchant_goal_reached'}
        previous_page = self.last_search_page.get(pair, 0)
        if previous_page == 0 and args.page != 1:
            return {'error': 'start_at_page_one'}
        if previous_page and args.page <= previous_page:
            return {'error': 'page_not_increasing'}
        pending = self.pending_shops if args.platform == Platform.TAOBAO else self.pending_accounts
        if previous_page and any(pair in self.pending_sources.get(mid, set()) for mid in pending):
            return {'error': 'inspect_pending_shops_first' if args.platform == Platform.TAOBAO
                    else 'review_pending_accounts_first'}
        minimum_next = 5 if previous_page == 1 else previous_page + 5
        if previous_page and args.page < minimum_next:
            return {'error': 'page_jump_too_small', 'next_page': minimum_next}
        key = ('search', args.keyword, args.platform, args.page)
        if key not in self.cache:
            result = self._collect(args.platform, args.keyword, args.page)
            self.last_search_page[pair] = args.page
            result['last_search_page'] = args.page
            self.cache[key] = result
            self.searched_pairs.add((args.keyword, args.platform))
        return self.cache[key]

    def inspect_shop(self, args: MerchantArgs):
        merchant = self.pending_shops.get(args.merchant_id)
        if merchant is None:
            if args.merchant_id in self.candidates or args.merchant_id in self.alternates:
                return self._result([args.merchant_id])
            return {'error': 'unknown_pending_shop_id'}
        sources = self.pending_sources.get(args.merchant_id, set())
        if sources and all(len(self.confirmed_shops.get(pair, set())) >=
                           self.run.request.taobao_shops_per_search for pair in sources):
            return {'error': 'shop_goal_reached'}
        try:
            inspected = self.collector.inspect_taobao_shop(merchant)
        except IdentityConflict:
            del self.pending_shops[args.merchant_id]
            self.pending_sources.pop(args.merchant_id, None)
            discard(self.run, 'identity_conflict')
            return self._result([], error='identity_conflict')
        known = {p.id for p in inspected.products}
        inspected.products.extend(p for p in merchant.products if p.id not in known)
        inspected.products = [p for p in inspected.products if sales_status(
            p.sales_display, self.run.request.taobao_min_sales,
            self.run.request.taobao_max_sales) == 'in_range']
        if not inspected.products:
            del self.pending_shops[args.merchant_id]
            self.pending_sources.pop(args.merchant_id, None)
            discard(self.run, 'sales_out_of_range_after_detail')
            return self._result([], error='sales_out_of_range_after_detail')
        del self.pending_shops[args.merchant_id]
        if len(self.candidates) < self.run.request.max_candidates:
            self.candidates[args.merchant_id] = inspected
        else:
            if len(self.alternates) >= self.run.request.max_candidates * 5:
                self.alternates.pop(next(iter(self.alternates)))
                discard(self.run, 'candidate_limit')
            self.alternates[args.merchant_id] = inspected
        sources = self.pending_sources.pop(args.merchant_id, set())
        for pair in sources:
            confirmed = self.confirmed_shops.setdefault(pair, set())
            if len(confirmed) < self.run.request.taobao_shops_per_search:
                confirmed.add(args.merchant_id)
        self.run.leads = [Lead(merchant=m) for m in self.candidates.values()]
        self.run.api_calls = self.collector.api.calls
        self.store.save(self.run)
        result = self._result([args.merchant_id])
        result['shop_counts'] = [{'keyword': keyword, 'confirmed': len(self.confirmed_shops[pair]),
                                  'goal': self.run.request.taobao_shops_per_search}
                                 for pair in sources for keyword in [pair[0]]]
        return result

    def enrich_merchant(self, args: MerchantArgs):
        if args.merchant_id in self.pending_shops:
            return self.inspect_shop(args)
        if args.merchant_id in self.pending_accounts:
            if args.merchant_id not in self.enriched:
                try:
                    account = self.collector.enrich(self.pending_accounts[args.merchant_id])
                except IdentityConflict:
                    del self.pending_accounts[args.merchant_id]
                    self.pending_sources.pop(args.merchant_id, None)
                    self.rejected_accounts.add(args.merchant_id)
                    discard(self.run, 'identity_conflict')
                    return self._result([], error='identity_conflict')
                self.pending_accounts[args.merchant_id] = account
                self.enriched.add(args.merchant_id)
                self.run.api_calls = self.collector.api.calls
                self.store.save(self.run)
            return self._result([args.merchant_id])
        if args.merchant_id not in self.candidates:
            return {'error': 'unknown_merchant_id'}
        if args.merchant_id not in self.enriched:
            try:
                original = self.candidates[args.merchant_id]
                enriched = self.collector.enrich(original)
            except IdentityConflict:
                del self.candidates[args.merchant_id]
                discard(self.run, 'identity_conflict')
                self.run.leads = [Lead(merchant=m) for m in self.candidates.values()]
                self.store.save(self.run)
                return self._result([], error='identity_conflict')
            if enriched.platform == Platform.TAOBAO:
                known = {p.id for p in enriched.products}
                enriched.products.extend(p for p in original.products if p.id not in known)
                statuses = [sales_status(p.sales_display, self.run.request.taobao_min_sales,
                                         self.run.request.taobao_max_sales) for p in enriched.products]
                if statuses and all(status in ('low', 'high') for status in statuses):
                    del self.candidates[args.merchant_id]
                    discard(self.run, 'sales_out_of_range')
                    self.run.leads = [Lead(merchant=m) for m in self.candidates.values()]
                    self.store.save(self.run)
                    return self._result([], error='sales_out_of_range')
                if 'unknown' in statuses and 'sales_unverified' not in enriched.warnings:
                    enriched.warnings.append('sales_unverified')
            self.candidates[args.merchant_id] = enriched
            self.enriched.add(args.merchant_id)
            self.run.leads = [Lead(merchant=m) for m in self.candidates.values()]
            self.run.api_calls = self.collector.api.calls
            self.store.save(self.run)
        return self._result([args.merchant_id])

    def review_account(self, args: ReviewAccountArgs):
        account = self.pending_accounts.get(args.merchant_id)
        if account is None:
            return {'error': 'unknown_pending_account_id'}
        if args.merchant_id not in self.enriched:
            return {'error': 'profile_inspection_required'}
        sources = self.pending_sources.get(args.merchant_id, set())
        if sources and all(len(self._confirmed(pair)) >= self._goal(pair[1]) for pair in sources):
            return {'error': 'merchant_goal_reached'}
        sources = self.pending_sources.pop(args.merchant_id, set())
        del self.pending_accounts[args.merchant_id]
        if not args.is_business:
            self.rejected_accounts.add(args.merchant_id)
            discard(self.run, 'not_business_account')
            self.store.save(self.run)
            return {'error': None, 'decision': 'discarded', 'reason': args.reason,
                    'merchant_id': args.merchant_id}
        if len(self.candidates) < self.run.request.max_candidates:
            self.candidates[args.merchant_id] = account
        else:
            if len(self.alternates) >= self.run.request.max_candidates * 5:
                self.alternates.pop(next(iter(self.alternates)))
                discard(self.run, 'candidate_limit')
            self.alternates[args.merchant_id] = account
        counts = []
        for pair in sources:
            confirmed = self._confirmed(pair)
            if len(confirmed) < self._goal(pair[1]):
                confirmed.add(args.merchant_id)
            counts.append({'keyword': pair[0], 'platform': pair[1].value,
                           'confirmed': len(confirmed), 'goal': self._goal(pair[1])})
        self.run.leads = [Lead(merchant=m) for m in self.candidates.values()]
        self.store.save(self.run)
        return {**self._result([args.merchant_id]), 'decision': 'accepted',
                'reason': args.reason, 'merchant_counts': counts}

    def search_shop_by_name(self, args: NameArgs):
        merchant = self.candidates.get(args.merchant_id)
        if merchant is None:
            return {'error': 'unknown_merchant_id'}
        if not merchant.name:
            return {'error': 'merchant_name_missing'}
        if len(merchant.name) > 100:
            return {'error': 'merchant_name_too_long'}
        if args.page > self.run.request.pages:
            return {'error': 'page_limit_exceeded'}
        key = ('name', merchant.id, args.page)
        if key not in self.cache:
            self.cache[key] = self._collect(merchant.platform, merchant.name, args.page, merchant.id)
            self.name_searched.add(merchant.id)
        return self.cache[key]

    def promote_merchant(self, args: PromoteArgs):
        if args.merchant_id not in self.alternates:
            return {'error': 'unknown_alternate_merchant_id'}
        if args.replace_merchant_id not in self.candidates:
            return {'error': 'unknown_replacement_merchant_id'}
        del self.candidates[args.replace_merchant_id]
        self.enriched.discard(args.replace_merchant_id)
        self.name_searched.discard(args.replace_merchant_id)
        self.candidates[args.merchant_id] = self.alternates.pop(args.merchant_id)
        self.run.leads = [Lead(merchant=m) for m in self.candidates.values()]
        self.store.save(self.run)
        return {**self._result([args.merchant_id]), 'replaced_merchant_id': args.replace_merchant_id}

    def invoke(self, name: str, raw_arguments: str):
        models = {'search_merchants': SearchArgs, 'enrich_merchant': MerchantArgs,
                  'review_account': ReviewAccountArgs,
                  'search_shop_by_name': NameArgs, 'promote_merchant': PromoteArgs,
                  'inspect_shop': MerchantArgs}
        if name not in models:
            return {'error': 'unknown_tool'}
        try:
            args = models[name].model_validate_json(raw_arguments)
        except (ValidationError, ValueError):
            return {'error': 'invalid_tool_arguments'}
        try:
            return getattr(self, name)(args)
        except ExternalError as exc:
            code = str(exc)
            if 'business_code_601' in code:
                # 余额/额度类错误：立即断电，避免 LLM 继续重试烧调用
                self.collector.api.max_calls = self.collector.api.calls
                self.run.errors.append('api_quota_exhausted')
            self.run.errors.append(f'{name}:{code}')
            return {'error': code, 'api_calls': self.collector.api.calls}

    def finish(self):
        self.run.leads = [Lead(merchant=m) for m in self.candidates.values()]
        self.run.search_progress = [SearchProgress(
            keyword=item['keyword'], platform=item['platform'], confirmed=item['confirmed'],
            goal=item['goal'], last_page=item['last_page'], pending=len(item['pending']))
            for item in self.search_progress()]
        for merchant in self.candidates.values():
            for evidence in merchant.evidence:
                self.store.save_evidence(self.run.id, evidence)
        self.run.api_calls = self.collector.api.calls
        self.store.save(self.run)
