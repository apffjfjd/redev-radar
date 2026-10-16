"""지표·판정 코드의 표시 정보. 화면과 API가 함께 쓴다."""

from __future__ import annotations

# code → (표시명, 그룹, 표시 형식)  형식: ratio(백분율), number, area, distance, percent, price, flag
METRICS: dict[str, tuple[str, str, str]] = {
    "BLOCK_AREA_M2": ("구역 면적", "기본", "area"),
    "BLDG_COUNT": ("건축물 동수", "기본", "number"),
    "AGED_COUNT_RATIO": ("노후 건축물 동수 비율", "노후도", "ratio"),
    "AGED_FLOOR_RATIO": ("노후 건축물 연면적 비율", "노후도", "ratio"),
    "AVG_BUILDING_AGE": ("평균 경과연수", "노후도", "number"),
    "RECENT_BUILD_RATIO": ("최근 신축 비중", "노후도", "ratio"),
    "AGED_CLUSTERING": ("노후 건축물 밀집도", "노후도", "ratio"),
    "ROAD_ACCESS_RATIO": ("주택접도율", "대지·기반시설", "ratio"),
    "SMALL_PARCEL_RATIO": ("과소필지 비율", "대지·기반시설", "ratio"),
    "HOUSEHOLD_DENSITY": ("호수밀도", "대지·기반시설", "number"),
    "SEMI_BASEMENT_RATIO": ("반지하주택 비율", "대지·기반시설", "ratio"),
    "LAND_USE_RESTRICTIONS": ("겹치는 지구·구역·시설", "대지·기반시설", "number"),
    "STATION_DISTANCE_M": ("역까지 직선거리", "역세권 접근성", "distance"),
    "STATION_EDGE_DISTANCE_M": ("역까지 직선거리 (블록 경계 기준)", "역세권 접근성", "distance"),
    "ENTRANCE_DISTANCE_M": ("출입구까지 직선거리", "역세권 접근성", "distance"),
    "WALK_DISTANCE_M": ("보행 이동거리", "역세권 접근성", "distance"),
    "TRANSFER_STATION": ("환승역 여부", "역세권 접근성", "flag"),
    "LINE_COUNT": ("이용 가능 노선 수", "역세권 접근성", "number"),
    "CBD_DISTANCE_M": ("가장 가까운 업무지구까지 거리", "역세권 접근성", "distance"),
    "CURRENT_FAR": ("현황 용적률", "용적률", "percent"),
    "ORDINANCE_FAR": ("조례상 용적률", "용적률", "percent"),
    "DEVELOPABLE_FAR_LOW": ("개발 가능 용적률 (보수)", "용적률", "percent"),
    "DEVELOPABLE_FAR_BASE": ("개발 가능 용적률 (기준)", "용적률", "percent"),
    "DEVELOPABLE_FAR_HIGH": ("개발 가능 용적률 (낙관)", "용적률", "percent"),
    "FAR_HEADROOM_LOW": ("추가 용적률 여력 (보수)", "용적률", "percent"),
    "FAR_HEADROOM_BASE": ("추가 용적률 여력 (기준)", "용적률", "percent"),
    "FAR_HEADROOM_HIGH": ("추가 용적률 여력 (낙관)", "용적률", "percent"),
    "ZONE_OVERLAP_RATIO": ("기존 정비구역 중복 비율", "기존 정비구역", "ratio"),
    "TX_COUNT": ("실거래 건수", "실거래가", "number"),
    "TX_MEDIAN_PRICE_PER_M2": ("전용면적당 실거래가 중앙값", "실거래가", "price"),
}

ITEMS: dict[str, str] = {
    "MIN_AREA": "최소 면적",
    "MAX_AREA": "최대 면적",
    "AGED_COUNT_RATIO": "노후 건축물 동수 비율",
    "AGED_FLOOR_RATIO": "노후 건축물 연면적 비율",
    "ROAD_ACCESS_RATIO": "주택접도율",
    "SMALL_PARCEL_RATIO": "과소필지 비율",
    "HOUSEHOLD_DENSITY": "호수밀도",
    "SEMI_BASEMENT_RATIO": "반지하주택 비율",
    "STATION_DISTANCE": "역까지 거리",
}

ELIGIBILITY_STATUS: dict[str, str] = {
    "PASS": "요건 충족",
    "FAIL": "요건 미충족",
    "INDETERMINATE": "판정 보류 (자료 부족)",
    "NO_CRITERIA": "기준 미등록",
}

VALUE_KINDS: dict[str, str] = {"CONFIRMED": "확정", "ESTIMATED": "추정"}
VERIFICATIONS: dict[str, str] = {"PRELIMINARY": "예비 분석", "LEGAL_VERIFIED": "법적 검증"}

NOTICES: list[str] = [
    "이 시스템은 투자 판단을 자동화하지 않습니다. 사람이 검토할 후보를 줄이고 판단 근거를 정량화하는 분석 보조 도구입니다.",
    "노후도·요건 판정·용적률 여력은 공공데이터에 기반한 예비 분석 결과이며 법적 검증 결과가 아닙니다.",
    "후보 경계는 실제 정비구역 경계가 아니며, 같은 지역이라도 경계 설정에 따라 지표와 판정이 달라질 수 있습니다.",
]


def metric_catalog() -> list[dict]:
    return [{"code": code, "label": label, "group": group, "format": fmt} for code, (label, group, fmt) in METRICS.items()]
