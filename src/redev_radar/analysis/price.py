"""Price Analyzer — 후보지역 내 다세대·연립주택 실거래 매칭 (FR-008)."""

from __future__ import annotations

from datetime import date
from statistics import median

from .. import models as m
from .context import BlockView, RunContext

HOUSE_TYPES = ("다세대", "연립")


def months_before(day: date, months: int) -> date:
    index = day.year * 12 + day.month - 1 - months
    year, month = divmod(index, 12)
    return date(year, month + 1, min(day.day, 28))


def quarter_of(day: date) -> str:
    return f"{day.year}Q{(day.month - 1) // 3 + 1}"


class PriceAnalyzer:
    name = "price"

    def run(self, ctx: RunContext) -> None:
        lookback = int(ctx.criteria.assumption_value("TX_LOOKBACK_MONTHS"))
        min_count = int(ctx.criteria.assumption_value("TX_MIN_COUNT"))
        since = months_before(ctx.base_date, lookback)

        # 실거래가 자료는 PNU 없이 법정동·지번만 제공될 수 있어 지번으로도 필지를 찾는다.
        by_jibun = {(p.sgg_cd, p.umd_nm, p.jibun): p.pnu for p in ctx.data.parcels if p.jibun}
        known_pnus = {p.pnu for p in ctx.data.parcels}
        blocks_by_pnu: dict[str, list[BlockView]] = {}
        for block in ctx.blocks():
            for pnu in block.pnus:
                blocks_by_pnu.setdefault(pnu, []).append(block)

        matched: dict[int, list[m.Transaction]] = {}
        unmatched = 0
        for tx in ctx.data.transactions:
            if tx.deal_type != "SALE" or tx.cancelled or not (since <= tx.deal_date <= ctx.base_date):
                continue
            if tx.house_type and not any(t in tx.house_type for t in HOUSE_TYPES):
                continue
            if tx.pnu and tx.pnu in known_pnus:
                pnu, method = tx.pnu, "PNU"
            else:
                pnu, method = by_jibun.get((tx.sgg_cd, tx.umd_nm, tx.jibun)), "JIBUN"
            if pnu is None:
                unmatched += 1
                continue
            for block in blocks_by_pnu.get(pnu, []):
                matched.setdefault(block.id, []).append(tx)
                ctx.session.add(
                    m.BlockTransaction(run_id=ctx.run.id, block_id=block.id, transaction_id=tx.id, match_method=method)
                )
        if unmatched:
            ctx.warn(f"필지와 연결하지 못한 실거래 {unmatched}건은 블록에 매칭하지 않았습니다.")

        period = {"from": since.isoformat(), "to": ctx.base_date.isoformat(), "house_types": list(HOUSE_TYPES)}
        for block in ctx.blocks():
            deals = matched.get(block.id, [])
            ctx.add_metric(block, "TX_COUNT", len(deals), "건", m.CONFIRMED, {**period, "source": "실거래가 (해제 거래 제외)"})

            priced = [(tx.deal_date, tx.price_krw / tx.exclusive_area) for tx in deals if tx.exclusive_area]
            quarters: dict[str, list[float]] = {}
            for day, per_m2 in priced:
                quarters.setdefault(quarter_of(day), []).append(per_m2)
            basis = {
                **period,
                "count": len(priced),
                "min_count": min_count,
                "formula": "거래금액 / 전용면적의 중앙값",
                "trend": [
                    {"period": q, "count": len(v), "median_price_per_m2": round(median(v))}
                    for q, v in sorted(quarters.items())
                ],
            }
            if len(priced) < min_count:
                # 거래량이 부족하면 가격 수준을 추정하지 않는다.
                basis["reason"] = f"거래량 부족으로 추정 불가 ({len(priced)}건 < {min_count}건)"
                value = None
            else:
                value = median(per_m2 for _, per_m2 in priced)
            ctx.add_metric(block, "TX_MEDIAN_PRICE_PER_M2", value, "원/㎡", m.CONFIRMED, basis)
