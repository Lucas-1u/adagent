"""字段名归一的共用小工具
==========================

背景：LLM 偶尔会把英文字段名写成中文（`{"用户": ..., "问题": [...]}`），
Pydantic 会直接判 `Field required` 并让整个节点降级。这里在 `model_validate`
之前做一次「键名归一」：只改键名，不动内容，也不凭空补字段。
"""

from typing import Any


def normalize_keys(data: Any, aliases: dict[str, str], *, keep_unknown: bool = True) -> Any:
    """把 `aliases` 里登记的中文/变体键名换成标准键名（递归处理 dict 与 list）。

    - 只做键名映射，值原样保留；
    - 已存在标准键时不覆盖（标准键优先）；
    - `keep_unknown=True` 时保留未登记的键（配合契约里的 `extra` 收集）。
    """
    if isinstance(data, list):
        return [normalize_keys(item, aliases, keep_unknown=keep_unknown) for item in data]
    if not isinstance(data, dict):
        return data

    out: dict[str, Any] = {}
    for key, value in data.items():
        target = aliases.get(str(key).strip(), key)
        if target in out and target != key:
            continue
        out[target] = normalize_keys(value, aliases, keep_unknown=keep_unknown)
    return out


def pick(data: dict, *names: str, default: Any = "") -> Any:
    """按顺序取第一个非空值（兼容模型用不同近义键名的情况）。"""
    for name in names:
        value = data.get(name)
        if value not in (None, "", [], {}):
            return value
    return default
