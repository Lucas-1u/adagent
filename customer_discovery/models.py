"""Versioned contracts shared by collectors, storage and the orchestrator."""
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Literal
from uuid import uuid4
from pydantic import BaseModel, ConfigDict, Field, model_validator


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Model(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)


class Platform(StrEnum):
    DOUYIN = 'douyin'
    XIAOHONGSHU = 'xiaohongshu'
    TAOBAO = 'taobao'


SearchPlatform = Literal[Platform.XIAOHONGSHU, Platform.TAOBAO, Platform.DOUYIN]


class TargetProfile(Model):
    description: str = Field(default='需要内容制作、品牌宣传或商品推广的中小商家', min_length=1, max_length=2000)
    categories: list[str] = Field(default_factory=list, max_length=20)
    city: str | None = None
    excluded_categories: list[str] = Field(default_factory=list, max_length=20)
    min_score: int = Field(default=65, ge=0, le=100)
    min_confidence: float = Field(default=0.65, ge=0, le=1)


class WorkflowRequest(Model):
    """The LLM plans concrete searches from an abstract business brief."""
    description: str = Field(min_length=3, max_length=2000)
    features: list[str] = Field(default_factory=list, max_length=10)
    pages: int = Field(default=30, ge=1, le=100)
    taobao_shops_per_search: int = Field(default=5, ge=1, le=20)
    social_merchants_per_search: int = Field(default=5, ge=1, le=20)
    taobao_min_sales: int = Field(default=10, ge=0)
    taobao_max_sales: int = Field(default=1000, ge=0)
    max_candidates: int = Field(default=60, ge=1, le=120)
    max_api_calls: int = Field(default=220, ge=1, le=500)
    max_tool_calls: int = Field(default=180, ge=1, le=300)
    # 演示/单店模式的收敛开关：只执行计划里的前 N 组搜索，拿到第一家就收工
    plan_query_limit: int = Field(default=6, ge=1, le=6)
    stop_after_first_lead: bool = False
    analyze: bool = True
    target: TargetProfile = Field(default_factory=TargetProfile)

    @model_validator(mode='after')
    def clean(self):
        self.features = list(dict.fromkeys(x.strip() for x in self.features))
        if any(not x or len(x) > 100 for x in self.features):
            raise ValueError('features must contain 1–100 characters')
        if self.taobao_min_sales > self.taobao_max_sales:
            raise ValueError('taobao_min_sales must not exceed taobao_max_sales')
        return self


class SearchQuery(Model):
    keyword: str = Field(min_length=2, max_length=100)
    platforms: list[SearchPlatform] = Field(min_length=1, max_length=3)
    reason: str | None = Field(default=None, max_length=300)

    @model_validator(mode='after')
    def unique_platforms(self):
        self.platforms = list(dict.fromkeys(self.platforms))
        return self


class SearchPlan(Model):
    queries: list[SearchQuery] = Field(min_length=1, max_length=6)

    @model_validator(mode='after')
    def no_duplicates(self):
        pairs = [(q.keyword, p) for q in self.queries for p in q.platforms]
        if len(pairs) != len(set(pairs)):
            raise ValueError('Duplicate keyword-platform search')
        return self


class SearchProgress(Model):
    keyword: str
    platform: Platform
    confirmed: int = Field(ge=0)
    goal: int = Field(ge=1)
    last_page: int = Field(ge=0)
    pending: int = Field(ge=0)


class Evidence(Model):
    id: str = Field(default_factory=lambda: str(uuid4()))
    endpoint: str
    params: dict[str, Any] = Field(default_factory=dict)
    collected_at: str = Field(default_factory=now)
    data: Any


class Product(Model):
    id: str
    title: str | None = None
    price_display: str | None = None
    sales_display: str | None = None
    url: str | None = None
    image_url: str | None = None


class Content(Model):
    id: str
    title: str | None = None
    published_at: str | None = None
    likes: int | None = None
    comments: int | None = None
    shares: int | None = None


class Merchant(Model):
    schema_version: str = '1.0'
    id: str
    platform: Platform
    platform_id: str
    seller_id: str | None = None
    identity_kind: Literal['account', 'shop', 'unresolved_product']
    name: str | None = None
    description: str | None = None
    location: str | None = None
    certification: str | None = None
    profile_url: str | None = None
    followers: int | None = None
    content_count: int | None = None
    products: list[Product] = Field(default_factory=list)
    contents: list[Content] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    observed_at: str = Field(default_factory=now)


class Reason(Model):
    text: str = Field(min_length=1, max_length=2000)
    evidence_ids: list[str] = Field(min_length=1)


class Assessment(Model):
    decision: Literal['target', 'not_target', 'needs_review']
    score: int = Field(ge=0, le=100, strict=True)
    confidence: float = Field(ge=0, le=1)
    is_business: bool | None
    reasons: list[Reason] = Field(min_length=1, max_length=10)
    missing_data: list[str] = Field(default_factory=list)
    opportunities: list[str] = Field(default_factory=list)
    model: str | None = None
    prompt_version: str = '3.1'


class Lead(Model):
    merchant: Merchant
    assessment: Assessment | None = None
    analysis_error: str | None = None

    @model_validator(mode='after')
    def merchant_only(self):
        if self.merchant.identity_kind == 'unresolved_product':
            raise ValueError('A lead requires a platform merchant identity')
        return self


class Run(Model):
    id: str = Field(default_factory=lambda: str(uuid4()))
    schema_version: str = '2.0'
    status: Literal['running', 'completed', 'partial', 'failed'] = 'running'
    request: WorkflowRequest
    search_plan: SearchPlan | None = None
    search_progress: list[SearchProgress] = Field(default_factory=list)
    started_at: str = Field(default_factory=now)
    finished_at: str | None = None
    leads: list[Lead] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    discarded: dict[str, int] = Field(default_factory=dict)
    api_calls: int = 0
    llm_calls: int = 0
    tool_calls: int = 0
    mode: Literal['live', 'demo'] = 'live'
