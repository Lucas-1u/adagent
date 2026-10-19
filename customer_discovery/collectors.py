"""Documented request contracts and response mappings verified by live samples.

The vendor leaves `data` untyped. Unknown list shapes raise SchemaError,
not an empty successful search. See docs/data-sources.md for scope.
"""
import json
from typing import Any
from urllib.parse import quote
from .clients import ExternalError
from .models import Content, Evidence, Merchant, Platform, Product


class SchemaError(ExternalError):
    pass


class IdentityConflict(SchemaError):
    """Search and detail responses identify different merchants or products."""


SEARCH = {
    Platform.DOUYIN: '/api/douyin/search-user/v2',
    Platform.XIAOHONGSHU: '/api/xiaohongshu/search-user/v2',
    Platform.TAOBAO: '/api/taobao/search-item-list/v1',
}
DETAIL = {
    Platform.DOUYIN: ('/api/douyin/get-user-detail/v3', 'secUid'),
    Platform.XIAOHONGSHU: ('/api/xiaohongshu/get-user/v3', 'userId'),
    Platform.TAOBAO: ('/api/taobao/get-item-detail/v9', 'itemId'),
}
CONTENT = {
    Platform.DOUYIN: '/api/douyin/get-user-video-list/v3',
    Platform.XIAOHONGSHU: '/api/xiaohongshu/get-user-note-list/v4',
}
TAOBAO_SHOP_ITEMS = '/api/taobao/get-shop-item-list/v1'


def pick(data, *paths):
    for path in paths:
        value = data
        for key in path.split('.'):
            value = value.get(key) if isinstance(value, dict) else None
        if value is not None and value != '':
            return value
    return None


def string(value):
    return str(value) if isinstance(value, (str, int, float)) and not isinstance(value, bool) else None


def number(value):
    # Preserve unknown units (e.g. 1.2万+) in raw evidence, never pretend exact counts.
    if isinstance(value, bool):
        return None
    text = str(value).replace(',', '')
    return int(text) if text.isdigit() else None


def records(data, keys):
    if isinstance(data, list):
        if all(isinstance(row, dict) for row in data):
            return data
        raise SchemaError('invalid_list_items')
    if isinstance(data, dict):
        for key in keys:
            found = pick(data, key)
            if isinstance(found, list):
                return records(found, ())
        if isinstance(data.get('data'), (dict, list)):
            return records(data['data'], keys)
    raise SchemaError('unrecognized_list_shape')


def unwrap(data, *keys):
    if not isinstance(data, dict):
        raise SchemaError('unrecognized_object_shape')
    for key in keys:
        if isinstance(data.get(key), dict):
            return data[key]
    return data


def product(row):
    pid = string(pick(row, 'item_id', 'itemId', 'num_iid', 'sku_id', 'skuId', 'id'))
    if not pid:
        raise SchemaError('missing_product_id')
    return Product(id=pid, title=string(pick(row, 'title', 'name', 'item_title', 'itemName')),
                   price_display=string(pick(row, 'price', 'view_price', 'priceText', 'priceYuanDouble')),
                   sales_display=string(pick(row, 'sales', 'sales_count', 'view_sales', 'sold',
                                             'soldCount', 'salesCount', 'monthSales', 'monthly_sales')),
                   url=string(pick(row, 'url', 'detail_url', 'item_url')),
                   image_url=string(pick(row, 'pic_url', 'image', 'image_url', 'picUrlFull', 'picUrl', 'imageUrl')))


def normalize(platform: Platform, row: dict, evidence: list[Evidence]) -> Merchant:
    if platform in CONTENT:
        row = unwrap(row, 'user_info', 'user', 'basic_info', 'basicInfo')
        keys = ('sec_uid', 'secUid') if platform == Platform.DOUYIN else ('user_id', 'userId', 'userid', 'id')
        uid = string(pick(row, *keys))
        if not uid:
            raise SchemaError('missing_account_id')
        url = ('https://www.douyin.com/user/' if platform == Platform.DOUYIN else 'https://www.xiaohongshu.com/user/profile/') + quote(uid, safe='')
        return Merchant(id=f'{platform}:account:{uid}', platform=platform, platform_id=uid,
                        identity_kind='account', name=string(pick(row, 'nickname', 'nick_name', 'name')),
                        description=string(pick(row, 'signature', 'desc', 'description')),
                        location=string(pick(row, 'city', 'location')),
                        certification=string(pick(row, 'enterprise_verify_reason', 'official_verify', 'verification', 'sub_title')),
                        followers=number(pick(row, 'follower_count', 'fans', 'fans_count', 'followers')),
                        content_count=number(pick(row, 'aweme_count', 'note_count', 'notes_count')),
                        profile_url=url, evidence=evidence)
    item = product(unwrap(row, 'item', 'item_info'))
    shop_id = string(pick(row, 'shop_id', 'shopId', 'shop.shop_id', 'shop.shopId', 'shop.id', 'seller.shop_id', 'seller.shopId'))
    kind = 'shop' if shop_id else 'unresolved_product'
    uid = shop_id or item.id
    return Merchant(id=f'{platform}:{kind}:{uid}', platform=platform, platform_id=uid,
                    identity_kind=kind,
                    seller_id=string(pick(row, 'seller_id', 'sellerId', 'user_id', 'userId',
                                          'seller.userId', 'seller.sellerId', 'shop.userId')),
                    name=string(pick(row, 'shop_name', 'shopName', 'shop.name', 'shop.shop_name', 'shop.shopName', 'seller.shopName')),
                    location=string(pick(row, 'shop.location', 'seller.location', 'sellerLoc')),
                    profile_url=string(pick(row, 'shop_url', 'shop.url', 'seller.shopUrl')),
                    products=[item], evidence=evidence,
                    warnings=[] if shop_id else ['shop_identity_unresolved'])


class Collector:
    def __init__(self, api):
        self.api = api

    def fetch(self, endpoint, params):
        return Evidence(endpoint=endpoint, params=params, data=self.api.get(endpoint, params))

    def search(self, platform, keyword, page):
        params = {'keyword': keyword, 'page': page}
        if platform == Platform.TAOBAO:
            params['sort'] = '_coefp'
        ev = self.fetch(SEARCH[platform], params)
        if platform == Platform.DOUYIN and isinstance(ev.data, dict) and isinstance(ev.data.get('business_data'), list):
            rows = []
            for card in ev.data['business_data']:
                raw = pick(card, 'data.raw_data')
                if not isinstance(raw, str):
                    continue
                try:
                    parsed = json.loads(raw)
                except ValueError:
                    continue
                user = pick(parsed, 'user_info')
                if isinstance(user, dict) and user.get('sec_uid'):
                    rows.append(user)
            if ev.data['business_data'] and not rows:
                raise SchemaError('unrecognized_douyin_cards')
            return ev, rows
        keys = ('user_list', 'users', 'items', 'list') if platform in CONTENT else (
            'items', 'item_list', 'list', 'result.items', 'auctions', 'model.itemList', 'products')
        return ev, records(ev.data, keys)

    def enrich(self, merchant):
        platform = merchant.platform
        endpoint, param = DETAIL[platform]
        value = merchant.platform_id if platform in CONTENT else merchant.products[0].id
        detail = self.fetch(endpoint, {param: value})
        merchant.evidence.append(detail)
        row = unwrap(detail.data, 'data')
        if platform in CONTENT:
            row = unwrap(row, 'user', 'user_info', 'basic_info', 'basicInfo')
            id_keys = ('sec_uid', 'secUid') if platform == Platform.DOUYIN else ('user_id', 'userId', 'userid', 'id')
            returned_id = string(pick(row, *id_keys))
            if returned_id is not None and returned_id != value:
                raise IdentityConflict('detail_identity_mismatch')
            row = {**row, id_keys[0]: value}
        else:
            item_row = unwrap(row, 'item', 'item_info')
            returned_id = string(pick(item_row, 'item_id', 'itemId', 'num_iid', 'sku_id', 'skuId', 'id'))
            if returned_id is not None and returned_id != value:
                raise IdentityConflict('detail_identity_mismatch')
            if item_row is row:
                row = {**row, 'item_id': value}
        updated = normalize(platform, row, merchant.evidence)
        if merchant.identity_kind == 'shop' and updated.identity_kind == 'shop' and merchant.id != updated.id:
            raise IdentityConflict('detail_shop_identity_mismatch')
        # A sparse detail must not erase search facts or a resolved shop identity.
        if merchant.identity_kind == 'shop' and updated.identity_kind == 'unresolved_product':
            updated.id, updated.platform_id, updated.identity_kind = merchant.id, merchant.platform_id, 'shop'
            updated.warnings = []
        for field in ('name', 'description', 'location', 'certification', 'followers', 'content_count', 'profile_url', 'seller_id'):
            if getattr(updated, field) is None:
                setattr(updated, field, getattr(merchant, field))
        if platform not in CONTENT:
            old = merchant.products[0].model_dump()
            new = updated.products[0].model_dump(exclude_none=True)
            updated.products = [Product.model_validate({**old, **new})]
        merchant = updated
        if platform in CONTENT:
            try:
                ev = self.fetch(CONTENT[platform], {param: value})
                merchant.evidence.append(ev)
                rows = records(ev.data, ('aweme_list', 'notes', 'items', 'list'))
                for row in rows[:10]:
                    row = unwrap(row, 'note_card')
                    cid = string(pick(row, 'aweme_id', 'note_id', 'id'))
                    if cid:
                        merchant.contents.append(Content(id=cid, title=string(pick(row, 'desc', 'title', 'display_title')),
                            published_at=string(pick(row, 'create_time', 'time', 'publish_time')),
                            likes=number(pick(row, 'statistics.digg_count', 'interact_info.liked_count', 'liked_count', 'likes')),
                            comments=number(pick(row, 'statistics.comment_count', 'interact_info.comment_count', 'comments_count')),
                            shares=number(pick(row, 'statistics.share_count', 'interact_info.share_count', 'share_count'))))
                    else:
                        merchant.warnings.append('content_missing_id')
            except ExternalError as exc:
                merchant.warnings.append(f'content:{exc}')
        return merchant

    def inspect_taobao_shop(self, merchant):
        """Verify the shop through item detail, then inspect its catalog when seller ID is known."""
        if merchant.platform != Platform.TAOBAO or merchant.identity_kind != 'shop':
            raise SchemaError('not_a_taobao_shop')
        enriched = self.enrich(merchant)
        detail_root = unwrap(enriched.evidence[-1].data, 'data')
        detail_row = unwrap(detail_root, 'item', 'item_info')
        reported_shop = string(pick(detail_row, 'shop_id', 'shopId', 'shop.shop_id',
                                    'shop.shopId', 'shop.id', 'seller.shopId') or
                               pick(detail_root, 'shop_id', 'shopId', 'shop.shop_id',
                                    'shop.shopId', 'shop.id', 'seller.shopId'))
        if reported_shop != merchant.platform_id:
            raise IdentityConflict('shop_identity_unverified')
        if not enriched.seller_id:
            enriched.warnings.append('shop_catalog_unavailable_no_seller_id')
            return enriched
        try:
            catalog = self.fetch(TAOBAO_SHOP_ITEMS,
                                 {'userId': enriched.seller_id, 'sort': '_default', 'page': 1})
            enriched.evidence.append(catalog)
            rows = records(catalog.data, ('items', 'item_list', 'list', 'model.itemList', 'products'))
            known = {p.id for p in enriched.products}
            for row in rows[:10]:
                shop_id = string(pick(row, 'shop_id', 'shopId', 'shop.shopId', 'shop.id'))
                if shop_id and shop_id != enriched.platform_id:
                    raise IdentityConflict('shop_catalog_identity_mismatch')
                try:
                    item = product(unwrap(row, 'item', 'item_info'))
                except SchemaError:
                    continue
                if item.id not in known:
                    enriched.products.append(item)
                    known.add(item.id)
        except IdentityConflict:
            raise
        except ExternalError as exc:
            enriched.warnings.append(f'shop_catalog:{exc}')
        return enriched
