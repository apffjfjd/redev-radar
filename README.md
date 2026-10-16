# redev-radar

역세권 재개발 빌라 경매물건 탐색기. 설계 문서 **BD-0004** / 요구사항 **REQ-0004** 의 구현이다.

투자 판단을 자동화하지 않는다. 사람이 검토할 후보를 줄이고 판단 근거를 정량화하는 분석 보조 도구다.

## 구현 범위

현재 구현은 **MVP(FR-001 ~ FR-009) + 공통(FR-010)** — "흐름 1. 후보지역 발굴" 이다.

| FR | 내용 | 위치 |
|---|---|---|
| FR-001 | 공간정보 수집·적재 (기준일별 누적) | `collector/`, `normalizer/` |
| FR-002 | 후보 블록, 복수 후보 경계 | `analysis/block_generator.py` |
| FR-003 | 건축물 노후도 | `analysis/physical/aging.py` |
| FR-004 | 사업방식별 요건 판정 | `analysis/physical/site.py`, `eligibility.py` |
| FR-005 | 역세권 접근성 | `analysis/physical/accessibility.py` |
| FR-006 | 현황·개발 가능 용적률 | `analysis/physical/far.py` |
| FR-007 | 기존 정비구역 중복·추진현황 | `analysis/physical/zone_overlap.py` |
| FR-008 | 다세대·연립 실거래 매칭 | `analysis/price.py`, `collector/molit_rtms.py` |
| FR-009 | 지도 기반 탐색 화면 | `api/query.py`, `web/` |
| FR-010 | 기준값·가정값 관리 | `criteria/`, `api/criteria.py` |

2차·3차 확장과 단계 미지정 기능(FR-011 ~ FR-034)은 구현하지 않았다. 분석 단계는
`analysis/pipeline.py` 의 목록에 추가하는 방식으로 확장한다.

## 실행

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"

.venv/bin/redev-radar demo     # 초기화 + 합성 샘플 수집 + 분석
.venv/bin/redev-radar serve    # http://127.0.0.1:8000
.venv/bin/python -m pytest
```

서버만 띄워 Swagger(`/docs`)로 API를 시험하려면 `scripts/run.sh` 를 실행한다.
DB에 데이터가 없으면 더미 데이터(기준값 시드 + 합성 샘플 + 분석 1회)를 만든 뒤 서버를 띄운다
(`--reset` 으로 DB 재생성, `PORT=8010` 으로 포트 지정). 종료는 `Ctrl+C` 또는 `scripts/stop.sh`.

`demo` 의 샘플 데이터는 가상의 동네를 생성한 **합성 데이터**이며 실제 지역의 자료가 아니다.

### 실제 자료로 실행

```bash
redev-radar init-db

# 파일 수집 (GeoJSON / CSV). SHP는 ogr2ogr 등으로 GeoJSON(EPSG:4326)으로 변환한다.
redev-radar collect file --dataset parcels --path 지적도.geojson --scope 11680 \
    --map PNU=pnu --map JIBUN=jibun --as-of 2026-09-01

# 국토교통부 연립다세대 매매 실거래가 API (REDEV_DATA_GO_KR_KEY 필요)
redev-radar collect rtms --lawd 11680 --ym 202608

redev-radar analyze --base-date 2026-09-30
```

주기 실행은 OS 스케줄러에서 `collect` 와 `analyze` 를 호출한다.

데이터셋과 공통 스키마 필드 (`--map 원본필드=공통필드` 로 맞춘다):

| dataset | 도형 | 필드 |
|---|---|---|
| `admin_areas` | 면 | `code`, `name` |
| `parcels` | 면 | `pnu`(필수), `sgg_cd`, `umd_nm`, `jibun`, `jimok`, `area_m2` |
| `buildings` | 면/점 | `building_key`, `pnu`, `use_approval_date`, `structure`, `main_use`, `gross_floor_area`, `far_floor_area`, `ground_floors`, `underground_floors`, `household_count`, `has_semi_basement` |
| `roads` | 선 | `road_key`, `name`, `width_m` |
| `stations` | 점 | `station_key`, `name`(필수), `lines` |
| `station_entrances` | 점 | `station_key`(필수), `entrance_no` |
| `zoning_areas` | 면 | `zoning_name`(필수), `category`(`USE_ZONE`/`DEV_RESTRICTION`/`PLANNED_FACILITY`/`OVERLAY`), `zoning_code` |
| `redevelopment_zones` | 면 | `name`(필수), `zone_key`, `project_type`, `stage`, `stage_date` |
| `business_districts` | 점 | `name`(필수) |
| `transactions` | 없음 | `deal_date`, `price_krw`(필수), `pnu`, `sgg_cd`, `umd_nm`, `jibun`, `house_type`, `exclusive_area`, `land_share_area`, `floor`, `build_year`, `cancelled` |

점 자료는 CSV의 `lon`, `lat` 열로도 받을 수 있다.

## 구조

```
collector/   출처별 어댑터. 원본과 수집 시점을 스냅샷으로 누적 보관
normalizer/  공통 스키마로 정규화. 누락 항목은 누락으로 남긴다
criteria/    법령·조례 기준값, 분석 가정값 (시행일·지자체·사업방식별 이력)
analysis/    단계형 파이프라인. 적재된 자료만 읽고 결과는 AnalysisRun 단위로 저장
api/         조회 API(읽기 전용) + 기준값 관리 API
web/         Leaflet 지도 화면
```

- **기준일**: `analyze --base-date` 는 (dataset, scope)별로 그 날짜 이전에 공개된 최신 스냅샷과
  그 날짜에 시행 중인 기준값만 사용한다.
- **재현성**: 분석 실행은 사용한 스냅샷 id와 기준값·가정값 사본을 함께 보관한다.
  실행 이후 기준값을 수정해도 기존 결과는 바뀌지 않는다.
- **표기**: 모든 지표는 확정/추정, 예비 분석/법적 검증 구분과 산출 근거(`basis`)를 갖는다.
  산출할 수 없는 값은 `null` 과 사유로 남긴다.
- **조회 일관성**: 화면은 `COMPLETED` 실행만 읽는다.

## 설계 문서의 미결정 사항에 대해 택한 값

BD-0004가 미결정으로 남긴 항목 중 구현에 필요했던 것들이다. 모두 데이터(가정값)이거나
한 모듈에 국한되어 있어 결정이 바뀌면 교체할 수 있다.

| 항목 | 구현 | 바꾸는 곳 |
|---|---|---|
| 기준값의 실제 수치·대상 조례 | **법령 원문과 대조하지 않은 예시 시드** (`verified=false`, 화면에 경고 표시) | `/api/criteria`, `criteria/seed/criteria.json` |
| 역세권 판정 기준 | 역 중심 직선거리 500m를 탐색 범위로 사용 | 가정값 `STATION_AREA_RADIUS_M` |
| 블록 생성 알고리즘 | 일정 폭 이상 도로로 둘러싸인 가구 단위로 필지 병합 | `analysis/block_generator.py` |
| 복수 후보 경계 규칙·개수 | 구획 도로폭 4m / 8m / 12m 의 3종 | 가정값 `BLOCK_ROAD_WIDTH_M` |
| 대표 경계 선택 | 전부 유지, 화면에서 선택·비교 | — |
| 개발 가능 용적률 | 보수=조례, 기준=법정 상한, 낙관=1단계 종상향 | 가정값 `DEVELOPABLE_FAR` |
| 기준 미등록 지자체·사업방식 | 분석에서 제외하지 않고 `NO_CRITERIA` + 경고 | `analysis/physical/eligibility.py` |
| 보행거리 | 도로 중심선 보행망 최단경로 (추정) | `analysis/physical/accessibility.py` |
| 지도 배경 | OpenStreetMap | `REDEV_TILE_URL` |
| 사용자·인증 | 1인용 가정, 인증 없음. `serve` 는 127.0.0.1에만 바인딩 | — |

## 알려진 제한

- **저장소**: BD-0004는 PostgreSQL + PostGIS를 우선 고려하도록 제안하지만, 현재는 도형을
  WKB로 저장하고 공간 연산을 Shapely로 수행한다(기본 SQLite). 서울 전역 규모에서는
  공간 인덱스가 필요하므로 PostGIS 전환이 필요하다. `db.Geometry` 와 `analysis/context.py`
  의 자료 읽기가 전환 지점이다.
- **외부 출처**: 자동 수집 어댑터는 실거래가 API 하나뿐이며, 인증키가 없어 **실제 호출로
  검증하지 못했다**(응답 해석은 직접 작성한 예시 XML로만 테스트). 나머지 출처는 파일
  수집으로 받는다.
- 분석은 전체 재계산이다. 변경된 자료의 영향 블록만 재분석하는 기능은 없다.
- 기준값 등록 화면은 없다. `/docs` (Swagger UI) 또는 API로 등록·수정한다.
