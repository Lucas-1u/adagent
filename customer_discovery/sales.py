"""Conservative screening of Taobao's displayed product sales counts."""
import re
from decimal import Decimal


_SALES = re.compile(
    r'^(?:(?:月销|已售|销量|近30天销量)\s*[:：]?\s*)?'
    r'(\d+(?:\.\d+)?)\s*([千万亿]?)\s*(\+|以上)?\s*'
    r'(?:件|单|笔)?$'
)
_MULTIPLIER = {'': 1, '千': 1000, '万': 10000, '亿': 100000000}


def sales_status(display: str | None, minimum: int, maximum: int) -> str:
    """Return low/high/in_range/unknown; '+' is only a lower bound."""
    if display is None:
        return 'unknown'
    text = display.strip().replace(',', '').replace('，', '')
    match = _SALES.fullmatch(text)
    if match is None:
        return 'unknown'
    count = Decimal(match[1]) * _MULTIPLIER[match[2]]
    if count > maximum:
        return 'high'
    if match[3]:
        return 'unknown'
    if count < minimum:
        return 'low'
    return 'in_range'
