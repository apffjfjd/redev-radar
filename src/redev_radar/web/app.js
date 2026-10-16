'use strict';

const RAMP = ['#eef3fb', '#c6d8f3', '#8fb2e6', '#4f86d3', '#1f5fbf'];
const NO_VALUE_COLOR = '#f1f2f4';
const STATUS_COLORS = { PASS: '#2e9e62', INDETERMINATE: '#e0a030', FAIL: '#b8bec8', NO_CRITERIA: '#ffffff' };
const STATUS_BADGE = { PASS: 'pass', INDETERMINATE: 'hold', UNKNOWN: 'hold' };
const ITEM_RESULT = { PASS: '충족', FAIL: '미충족', UNKNOWN: '자료 부족' };
const REQUIREMENT = { REQUIRED: '필수', OPTIONAL: '선택' };
const BASIS_LABELS = { notice: '유의', reason: '사유', formula: '산식', method: '방법' };

const state = { meta: null, metricDefs: {}, blocks: null, breaks: [], selectedId: null };
let map, blockLayer;

const $ = (id) => document.getElementById(id);

// 자료에서 온 문자열은 항상 텍스트 노드로 넣는다.
function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (key === 'class') el.className = value;
    else if (key.startsWith('on')) el.addEventListener(key.slice(2), value);
    else if (value !== false && value != null) el.setAttribute(key, value);
  }
  for (const child of children.flat()) {
    if (child == null || child === false) continue;
    el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return el;
}

async function api(path) {
  const response = await fetch('/api' + path);
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || `요청 실패 (${response.status})`);
  }
  return response.json();
}

// ---------------------------------------------------------------- 표시 형식

const number = (value, digits = 0) =>
  value.toLocaleString('ko-KR', { minimumFractionDigits: digits, maximumFractionDigits: digits });

function formatValue(value, format, unit) {
  if (value == null) return null;
  switch (format) {
    case 'ratio': return number(value * 100, 1) + '%';
    case 'percent': return number(value, 1) + '%';
    case 'area': return number(value) + '㎡';
    case 'distance': return number(value) + 'm';
    case 'price': return number(value / 10000) + '만원/㎡';
    case 'flag': return value ? '예' : '아니오';
    default: return number(value, Number.isInteger(value) ? 0 : 1) + (unit ? ' ' + unit : '');
  }
}

function formatMetric(code, value, unit) {
  const def = state.metricDefs[code];
  return formatValue(value, def ? def.format : 'number', unit);
}

function formatThreshold(item) {
  const formats = { '비율': 'ratio', '㎡': 'area', 'm': 'distance' };
  return `${item.operator} ${formatValue(item.threshold, formats[item.unit] || 'number', item.unit)}`;
}

function variantLabel(code) {
  const variant = state.meta.variants.find((v) => v.code === code);
  return variant && variant.road_width_m != null ? `${code} · 폭 ${variant.road_width_m}m 이상 도로로 구획` : code;
}

function badge(text, kind) {
  return h('span', { class: 'badge ' + (kind || '') }, text);
}

// ---------------------------------------------------------------- 지도

function colorMode() {
  const [kind, code] = $('color-by').value.split(':');
  return { kind, code };
}

function computeBreaks(values) {
  const sorted = values.filter((v) => v != null).sort((a, b) => a - b);
  if (!sorted.length) return [];
  const breaks = [];
  for (let i = 1; i < RAMP.length; i++) {
    breaks.push(sorted[Math.min(sorted.length - 1, Math.floor((i * sorted.length) / RAMP.length))]);
  }
  return [...new Set(breaks)];
}

function metricColor(value) {
  if (value == null) return NO_VALUE_COLOR;
  let index = 0;
  while (index < state.breaks.length && value >= state.breaks[index]) index++;
  // 구간 수가 줄어든 경우에도 가장 높은 구간이 가장 진한 색이 되도록 맞춘다.
  return RAMP[Math.round((index * (RAMP.length - 1)) / Math.max(state.breaks.length, 1))];
}

function blockStyle(feature) {
  const p = feature.properties;
  const mode = colorMode();
  const selected = p.id === state.selectedId;
  let fill;
  let missing = false;
  if (mode.kind === 'elig') {
    const status = p.eligibility[mode.code] || 'NO_CRITERIA';
    fill = STATUS_COLORS[status];
    missing = status === 'NO_CRITERIA';
  } else {
    const value = p.metrics[mode.code];
    fill = metricColor(value);
    missing = value == null;
  }
  return {
    color: selected ? '#111827' : '#5b6472',
    weight: selected ? 3 : 1,
    dashArray: missing && !selected ? '4 3' : null,
    fillColor: fill,
    fillOpacity: 0.65,
  };
}

function renderLegend() {
  const mode = colorMode();
  const legend = $('legend');
  legend.replaceChildren();
  const row = (color, label, cls) =>
    h('div', { class: 'legend-row' }, h('span', { class: 'swatch ' + (cls || ''), style: `background:${color}` }), label);

  if (mode.kind === 'elig') {
    const type = state.meta.project_types.find((t) => t.code === mode.code);
    legend.append(h('h4', {}, `${type ? type.name : mode.code} 요건 (예비 판정)`));
    for (const [status, label] of Object.entries(state.meta.eligibility_status)) {
      legend.append(row(STATUS_COLORS[status], label));
    }
  } else {
    const def = state.metricDefs[mode.code];
    legend.append(h('h4', {}, def.label));
    const bounds = [null, ...state.breaks, null];
    for (let i = 0; i < bounds.length - 1; i++) {
      const lo = bounds[i] == null ? null : formatValue(bounds[i], def.format);
      const hi = bounds[i + 1] == null ? null : formatValue(bounds[i + 1], def.format);
      const label = lo == null ? (hi == null ? '전체' : `${hi} 미만`) : hi == null ? `${lo} 이상` : `${lo} ~ ${hi}`;
      legend.append(row(RAMP[Math.round((i * (RAMP.length - 1)) / Math.max(state.breaks.length, 1))], label));
    }
    legend.append(row(NO_VALUE_COLOR, '산출 불가'));
  }
  legend.append(h('div', { class: 'legend-row' }, h('span', { class: 'swatch line' }), '기존 정비구역'));
}

function restyle() {
  const mode = colorMode();
  state.breaks =
    mode.kind === 'metric' ? computeBreaks(state.blocks.features.map((f) => f.properties.metrics[mode.code])) : [];
  if (blockLayer) blockLayer.setStyle(blockStyle);
  renderLegend();
}

async function loadBlocks(fit) {
  state.blocks = await api(`/blocks?run_id=${state.meta.run.id}&variant=${encodeURIComponent($('variant').value)}`);
  if (blockLayer) blockLayer.remove();
  blockLayer = L.geoJSON(state.blocks, {
    style: blockStyle,
    onEachFeature: (feature, layer) => layer.on('click', () => selectBlock(feature.properties.id)),
  }).addTo(map);
  restyle();
  if (fit && state.blocks.features.length) map.fitBounds(blockLayer.getBounds(), { padding: [20, 20] });
}

async function loadReferenceLayers() {
  const runId = state.meta.run.id;
  const [zones, stations] = await Promise.all([
    api(`/layers/redevelopment-zones?run_id=${runId}`),
    api(`/layers/stations?run_id=${runId}`),
  ]);
  L.geoJSON(zones, { style: { color: '#b4232a', weight: 2, dashArray: '6 4', fill: false }, interactive: false }).addTo(map);
  for (const feature of stations.features) {
    const [lon, lat] = feature.geometry.coordinates;
    const p = feature.properties;
    if (p.radius_m) {
      L.circle([lat, lon], { radius: p.radius_m, color: '#7c3aed', weight: 1, dashArray: '3 4', fill: false, interactive: false }).addTo(map);
    }
    L.circleMarker([lat, lon], { radius: 6, color: '#ffffff', weight: 2, fillColor: '#7c3aed', fillOpacity: 1 })
      .bindTooltip(`${p.name} (${p.lines.join(', ')})`)
      .addTo(map);
  }
}

// ---------------------------------------------------------------- 상세 패널

function basisRow(metric) {
  const lines = [];
  for (const [key, label] of Object.entries(BASIS_LABELS)) {
    if (metric.basis[key]) lines.push(h('div', {}, `${label}: ${metric.basis[key]}`));
  }
  const rest = Object.fromEntries(Object.entries(metric.basis).filter(([key]) => !(key in BASIS_LABELS)));
  if (Object.keys(rest).length) lines.push(h('pre', {}, JSON.stringify(rest, null, 1)));
  return h('tr', { class: 'basis-row', hidden: true }, h('td', { colspan: 3 }, lines));
}

function metricsSection(detail) {
  const groups = {};
  for (const metric of detail.metrics) {
    const def = state.metricDefs[metric.code] || { label: metric.code, group: '기타' };
    (groups[def.group] = groups[def.group] || []).push({ metric, def });
  }
  const nodes = [];
  for (const [group, entries] of Object.entries(groups)) {
    const body = h('tbody');
    for (const { metric, def } of entries) {
      const text = formatMetric(metric.code, metric.value, metric.unit);
      const basis = basisRow(metric);
      const row = h(
        'tr',
        { class: 'metric-row', title: '눌러서 산출 근거 보기', onclick: () => (basis.hidden = !basis.hidden) },
        h('td', {}, def.label),
        h('td', { class: 'num' }, text == null ? h('span', { class: 'unavailable' }, '산출 불가') : text),
        h(
          'td',
          {},
          badge(state.meta.value_kinds[metric.value_kind] || metric.value_kind, 'outline'),
          ' ',
          badge(state.meta.verifications[metric.verification] || metric.verification, 'outline'),
        ),
      );
      body.append(row, basis);
    }
    nodes.push(h('h3', {}, group), h('table', {}, body));
  }
  return nodes;
}

function eligibilitySection(detail) {
  const nodes = [h('h3', {}, '사업방식별 요건 (예비 판정)')];
  for (const result of detail.eligibility) {
    const type = state.meta.project_types.find((t) => t.code === result.project_type);
    const body = h('div', { class: 'card-body' });
    if (result.items.length) {
      const rows = result.items.map((item) =>
        h(
          'tr',
          {},
          h('td', {}, state.meta.items[item.item_code] || item.item_code),
          h('td', {}, REQUIREMENT[item.requirement]),
          h('td', { class: 'num' }, formatMetric(item.metric_code, item.actual, item.unit) || '-'),
          h('td', { class: 'num' }, formatThreshold(item)),
          h('td', {}, badge(ITEM_RESULT[item.result], STATUS_BADGE[item.result])),
        ),
      );
      body.append(
        h(
          'table',
          {},
          h('thead', {}, h('tr', {}, h('th', {}, '항목'), h('th', {}, '구분'), h('th', { class: 'num' }, '값'), h('th', { class: 'num' }, '기준'), h('th', {}, '결과'))),
          h('tbody', {}, rows),
        ),
      );
      if (result.summary.optional_total) {
        body.append(h('p', { class: 'law' }, `선택 요건 ${result.summary.optional_total}개 중 ${result.summary.min_optional}개 이상 충족 필요 (현재 ${result.summary.optional_pass}개 충족)`));
      }
      const laws = [...new Set(result.items.map((i) => `${i.basis.law_name || '근거 미기재'} · ${i.basis.law_article || ''} · 시행 ${i.basis.effective_from}`))];
      body.append(h('div', { class: 'law' }, '적용 기준: ', laws.map((law) => h('div', {}, law))));
      if (result.summary.has_unverified_criteria) {
        body.append(h('div', { class: 'note' }, '법령 원문과 대조되지 않은 기준값이 적용되었습니다. 판정을 그대로 신뢰하지 마세요.'));
      }
    } else {
      body.append(h('p', { class: 'law' }, result.summary.reason || ''));
    }
    const restrictions = result.summary.restrictions || [];
    if (restrictions.length) {
      body.append(h('p', { class: 'law' }, '겹치는 지구·구역·시설 (판정 미반영, 별도 검토 필요): ' + restrictions.map((r) => r.name).join(', ')));
    }
    nodes.push(
      h(
        'details',
        { class: 'card' },
        h('summary', {}, type ? type.name : result.project_type, badge(state.meta.eligibility_status[result.status], STATUS_BADGE[result.status])),
        body,
      ),
    );
  }
  return nodes;
}

function overlapSection(detail) {
  const nodes = [h('h3', {}, '기존 정비구역')];
  if (!detail.zone_overlaps.length) {
    nodes.push(h('p', { class: 'sub' }, '겹치는 기존 정비구역이 없습니다.'));
    return nodes;
  }
  const rows = detail.zone_overlaps.map((o) =>
    h(
      'tr',
      {},
      h('td', {}, o.zone_name),
      h('td', {}, [o.project_type, o.stage, o.stage_date].filter(Boolean).join(' · ')),
      h('td', { class: 'num' }, `${number(o.overlap_ratio * 100, 1)}% `, badge(o.overlap_kind === 'FULL' ? '포함' : '일부 중복', o.overlap_kind === 'FULL' ? '' : 'hold')),
    ),
  );
  nodes.push(h('table', {}, h('thead', {}, h('tr', {}, h('th', {}, '구역'), h('th', {}, '추진현황'), h('th', { class: 'num' }, '블록 대비 중복'))), h('tbody', {}, rows)));
  return nodes;
}

function sparkline(trend) {
  const width = 380, height = 60, pad = 6;
  const values = trend.map((t) => t.median_price_per_m2);
  const min = Math.min(...values), max = Math.max(...values);
  const x = (i) => pad + (i * (width - 2 * pad)) / Math.max(trend.length - 1, 1);
  const y = (v) => height - pad - ((v - min) / (max - min || 1)) * (height - 2 * pad);
  const ns = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(ns, 'svg');
  svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
  svg.setAttribute('class', 'spark');
  svg.setAttribute('role', 'img');
  svg.setAttribute('aria-label', '분기별 전용면적당 실거래가 중앙값 추이');
  const line = document.createElementNS(ns, 'polyline');
  line.setAttribute('points', values.map((v, i) => `${x(i)},${y(v)}`).join(' '));
  line.setAttribute('fill', 'none');
  line.setAttribute('stroke', '#1f5fbf');
  line.setAttribute('stroke-width', '2');
  svg.append(line);
  values.forEach((v, i) => {
    const dot = document.createElementNS(ns, 'circle');
    dot.setAttribute('cx', x(i));
    dot.setAttribute('cy', y(v));
    dot.setAttribute('r', 2.5);
    dot.setAttribute('fill', '#1f5fbf');
    svg.append(dot);
  });
  return svg;
}

function transactionSection(detail, transactions) {
  const nodes = [h('h3', {}, '다세대·연립 실거래')];
  const price = detail.metrics.find((x) => x.code === 'TX_MEDIAN_PRICE_PER_M2');
  if (price && price.basis.reason) nodes.push(h('div', { class: 'note' }, price.basis.reason));
  const trend = price ? price.basis.trend || [] : [];
  if (trend.length > 1) {
    nodes.push(sparkline(trend));
    nodes.push(
      h(
        'table',
        {},
        h('thead', {}, h('tr', {}, h('th', {}, '분기'), h('th', { class: 'num' }, '건수'), h('th', { class: 'num' }, '전용면적당 중앙값'))),
        h('tbody', {}, trend.map((t) => h('tr', {}, h('td', {}, t.period), h('td', { class: 'num' }, t.count), h('td', { class: 'num' }, formatValue(t.median_price_per_m2, 'price'))))),
      ),
    );
  }
  if (!transactions.length) {
    nodes.push(h('p', { class: 'sub' }, '조회 기간에 매칭된 실거래가 없습니다.'));
    return nodes;
  }
  const shown = transactions.slice(0, 30);
  nodes.push(
    h('p', { class: 'sub' }, `최근 거래 ${shown.length}건 (전체 ${transactions.length}건)`),
    h(
      'table',
      {},
      h('thead', {}, h('tr', {}, h('th', {}, '계약일'), h('th', {}, '지번'), h('th', { class: 'num' }, '전용'), h('th', { class: 'num' }, '층'), h('th', { class: 'num' }, '거래금액'))),
      h(
        'tbody',
        {},
        shown.map((t) =>
          h(
            'tr',
            {},
            h('td', {}, t.deal_date),
            h('td', {}, `${t.umd_nm || ''} ${t.jibun || ''}`),
            h('td', { class: 'num' }, t.exclusive_area != null ? number(t.exclusive_area, 1) + '㎡' : '-'),
            h('td', { class: 'num' }, t.floor != null ? t.floor : '-'),
            h('td', { class: 'num' }, number(t.price_krw / 10000) + '만원'),
          ),
        ),
      ),
    ),
  );
  return nodes;
}

function alternativesSection(detail) {
  if (!detail.alternatives.length) return [];
  const nodes = [
    h('h3', {}, '같은 지역의 다른 후보 경계'),
    h('p', { class: 'sub' }, '경계 설정에 따라 지표와 판정이 달라집니다. 눌러서 비교하세요.'),
  ];
  for (const alt of detail.alternatives) {
    const summary = [
      formatMetric('BLOCK_AREA_M2', alt.metrics.BLOCK_AREA_M2),
      alt.metrics.AGED_COUNT_RATIO != null ? '노후 ' + formatMetric('AGED_COUNT_RATIO', alt.metrics.AGED_COUNT_RATIO) : null,
      alt.metrics.FAR_HEADROOM_BASE != null ? '여력 ' + formatMetric('FAR_HEADROOM_BASE', alt.metrics.FAR_HEADROOM_BASE) : null,
    ].filter(Boolean).join(' · ');
    nodes.push(
      h('button', { class: 'link alt', type: 'button', onclick: () => selectBlock(alt.id) }, h('span', {}, alt.variant), h('span', {}, summary)),
    );
  }
  return nodes;
}

async function selectBlock(id) {
  state.selectedId = id;
  history.replaceState(null, '', '#block=' + id);
  blockLayer.setStyle(blockStyle);
  const panel = $('panel');
  try {
    const [detail, transactions] = await Promise.all([api(`/blocks/${id}`), api(`/blocks/${id}/transactions`)]);
    if (state.selectedId !== id) return;
    if (detail.variant !== $('variant').value) {
      // 다른 후보 경계의 블록을 열면 지도도 그 경계로 바꾼다.
      $('variant').value = detail.variant;
      await loadBlocks(false);
    }
    panel.replaceChildren(
      h('h2', {}, `후보 블록 ${detail.block_key}`),
      h('p', { class: 'sub' }, `${variantLabel(detail.variant)} · 필지 ${number(detail.parcel_count)}개 · 분석 기준일 ${detail.run.base_date}`),
      h('div', { class: 'note' }, '후보 경계에 대한 예비 분석 결과입니다. 법적 검증 결과가 아니며, 실제 정비구역 경계·요건 충족 여부와 다를 수 있습니다.'),
      ...eligibilitySection(detail),
      ...overlapSection(detail),
      ...metricsSection(detail),
      ...transactionSection(detail, transactions),
      ...alternativesSection(detail),
    );
    panel.scrollTop = 0;
  } catch (error) {
    panel.replaceChildren(h('div', { class: 'note' }, error.message));
  }
}

// ---------------------------------------------------------------- 시작

function buildControls() {
  $('variant').replaceChildren(...state.meta.variants.map((v) => h('option', { value: v.code }, variantLabel(v.code))));
  if (state.meta.variants.some((v) => v.code === 'MEDIUM')) $('variant').value = 'MEDIUM';

  const colorBy = $('color-by');
  colorBy.append(
    h('optgroup', { label: '사업방식별 요건 판정' }, state.meta.project_types.map((t) => h('option', { value: 'elig:' + t.code }, t.name))),
  );
  const groups = {};
  for (const def of state.meta.metrics) (groups[def.group] = groups[def.group] || []).push(def);
  for (const [group, defs] of Object.entries(groups)) {
    colorBy.append(h('optgroup', { label: group }, defs.map((d) => h('option', { value: 'metric:' + d.code }, d.label))));
  }
  colorBy.value = 'metric:AGED_COUNT_RATIO';

  $('variant').addEventListener('change', () => loadBlocks(false));
  colorBy.addEventListener('change', restyle);
}

function showBanner(messages) {
  const banner = $('banner');
  banner.hidden = !messages.length;
  banner.replaceChildren(...messages.map((message) => h('div', {}, message)));
}

async function init() {
  const config = await api('/config');
  $('notices').replaceChildren(...config.notices.map((notice) => h('li', {}, notice)));
  map = L.map('map').setView([37.5665, 126.978], 12);
  L.tileLayer(config.tile_url, { attribution: config.tile_attribution, maxZoom: 19 }).addTo(map);

  // 조회할 분석 실행은 주소의 run 값으로 고른다. 없으면 가장 최근에 완료된 실행이다.
  const requested = new URLSearchParams(location.search).get('run');
  try {
    state.meta = await api('/meta' + (requested ? `?run_id=${encodeURIComponent(requested)}` : ''));
  } catch (error) {
    showBanner([error.message]);
    return;
  }
  state.metricDefs = Object.fromEntries(state.meta.metrics.map((d) => [d.code, d]));
  const run = state.meta.run;
  $('run-info').textContent = `분석 기준일 ${run.base_date}`;
  const runs = (await api('/runs')).filter((r) => r.status === 'COMPLETED');
  $('run').replaceChildren(...runs.map((r) => h('option', { value: r.id }, `#${r.id} · 기준일 ${r.base_date}`)));
  $('run').value = run.id;
  $('run').addEventListener('change', () => (location.href = '?run=' + $('run').value));
  const messages = run.warnings.map((warning) => '경고: ' + warning);
  if (state.meta.sources.includes('sample')) {
    messages.unshift('합성 샘플 데이터로 분석한 결과입니다. 실제 지역의 자료가 아니며 배경 지도와 무관합니다.');
  }
  showBanner(messages);

  buildControls();
  await loadBlocks(true);
  await loadReferenceLayers();
  const linked = /^#block=(\d+)$/.exec(location.hash);
  if (linked) await selectBlock(Number(linked[1]));
}

init().catch((error) => showBanner([error.message]));
