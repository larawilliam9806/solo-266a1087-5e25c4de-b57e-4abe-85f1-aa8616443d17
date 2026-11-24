"""请求/响应数据模型。"""
from __future__ import annotations

import json
from typing import List

from pydantic import BaseModel, Field, field_validator


class GateEntry(BaseModel):
    """闸机清单中的一行：匿名券号 + 馆号。"""

    ticket_no: str = Field(..., description="匿名券号")
    hall: str = Field(..., description="馆号，例如 甲 / 乙")

    @field_validator("ticket_no", "hall")
    @classmethod
    def _strip_and_require(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("券号与馆号不能为空")
        return value


class ReplaceDayListRequest(BaseModel):
    """某日闸机整日清单（全量换交）。允许传空清单。"""

    entries: List[GateEntry] = Field(default_factory=list)


def encode_halls(halls: list[str]) -> str:
    """馆组合的规范化编码：去重、按馆号排序后存 JSON 数组字符串。"""
    return json.dumps(sorted(set(halls)), ensure_ascii=False)


def decode_halls(halls_json: str) -> list[str]:
    return json.loads(halls_json)
