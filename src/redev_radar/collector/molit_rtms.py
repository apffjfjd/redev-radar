"""국토교통부 실거래가 공개시스템 — 연립다세대 매매 실거래가 (공공데이터포털 개방 API).

인증키는 환경변수(REDEV_DATA_GO_KR_KEY)로만 받는다.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import date
from typing import Iterable

import httpx

from .base import CollectedDataset

ENDPOINT = "https://apis.data.go.kr/1613000/RTMSDataSvcRHTrade/getRTMSDataSvcRHTrade"
PAGE_SIZE = 1000
OK_CODES = {"00", "000"}


class RtmsError(RuntimeError):
    pass


def parse_response(xml_text: str) -> tuple[list[dict], int]:
    """응답 XML을 (원본 item 목록, 전체 건수)로 바꾼다. 오류 응답이면 RtmsError."""
    root = ET.fromstring(xml_text)
    code = (root.findtext(".//resultCode") or root.findtext(".//returnReasonCode") or "").strip()
    if code not in OK_CODES:
        message = root.findtext(".//resultMsg") or root.findtext(".//returnAuthMsg") or "알 수 없는 오류"
        raise RtmsError(f"실거래가 API 오류 [{code}] {message.strip()}")
    items = [
        {"properties": {child.tag: (child.text or "").strip() for child in item}}
        for item in root.iterfind(".//item")
    ]
    return items, int(root.findtext(".//totalCount") or len(items))


class MolitRtmsAdapter:
    source = "molit_rtms"

    def __init__(self, service_key: str, lawd_cd: str, deal_ym: str, client: httpx.Client | None = None):
        if not service_key:
            raise RtmsError("공공데이터포털 인증키가 없습니다. REDEV_DATA_GO_KR_KEY를 설정하세요.")
        self.service_key = service_key
        self.lawd_cd = lawd_cd
        self.deal_ym = deal_ym
        self.client = client or httpx.Client(timeout=30, transport=httpx.HTTPTransport(retries=3))

    def fetch(self, as_of: date) -> Iterable[CollectedDataset]:
        records: list[dict] = []
        page = 1
        while True:
            response = self.client.get(
                ENDPOINT,
                params={
                    "serviceKey": self.service_key,
                    "LAWD_CD": self.lawd_cd,
                    "DEAL_YMD": self.deal_ym,
                    "pageNo": page,
                    "numOfRows": PAGE_SIZE,
                },
            )
            response.raise_for_status()
            items, total = parse_response(response.text)
            records.extend(items)
            if not items or len(records) >= total:
                break
            page += 1
        yield CollectedDataset(
            dataset="transactions",
            records=records,
            scope=f"{self.lawd_cd}:{self.deal_ym}",
        )
