"""PII wrappers for prompt construction and lightweight leak auditing."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Iterable


@dataclass(frozen=True)
class PIIValue:
    field_path: str
    value: str


def _normalise_scalar(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def wrap_pii_value(field_path: str, value: Any) -> str:
    scalar = _normalise_scalar(value)
    return f'<pii field="{field_path}">{scalar}</pii>'


def iter_pii_values(values: Any, prefix: str) -> Iterable[PIIValue]:
    if isinstance(values, dict):
        for key, value in values.items():
            child_prefix = f"{prefix}.{key}" if prefix else str(key)
            yield from iter_pii_values(value, child_prefix)
        return
    if isinstance(values, list):
        for index, value in enumerate(values):
            child_prefix = f"{prefix}[{index}]"
            yield from iter_pii_values(value, child_prefix)
        return

    scalar = _normalise_scalar(values).strip()
    if len(scalar) < 3:
        return
    yield PIIValue(field_path=prefix, value=scalar)


def wrap_pii_mapping(values: Any, prefix: str) -> Any:
    if isinstance(values, dict):
        return {key: wrap_pii_mapping(value, f"{prefix}.{key}" if prefix else str(key)) for key, value in values.items()}
    if isinstance(values, list):
        return [wrap_pii_mapping(value, f"{prefix}[{index}]") for index, value in enumerate(values)]
    return wrap_pii_value(prefix, values)


def pii_json(values: Any, prefix: str) -> str:
    return json.dumps(wrap_pii_mapping(values, prefix), ensure_ascii=True, sort_keys=True)


def audit_prompt_for_unwrapped_pii(prompt: str, values: Any, prefix: str) -> list[str]:
    leaks: list[str] = []
    for item in iter_pii_values(values, prefix):
        wrapped = wrap_pii_value(item.field_path, item.value)
        escaped_wrapped = json.dumps(wrapped, ensure_ascii=True).strip('"')
        if item.value in prompt and wrapped not in prompt and escaped_wrapped not in prompt:
            leaks.append(item.field_path)
    return leaks


def assert_prompt_has_wrapped_pii(prompt: str, values: Any, prefix: str) -> None:
    leaks = audit_prompt_for_unwrapped_pii(prompt, values, prefix)
    if leaks:
        raise ValueError(f"Unwrapped PII fields found in prompt: {', '.join(sorted(leaks))}")
