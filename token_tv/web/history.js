'use strict';
/* No external chart dependencies. Dates use the server's Korean calendar. */
(() => {
 const root = document.querySelector('#usage-history');
 if (!root) return;
 if (document.documentElement.hasAttribute('data-demo')) {root.hidden = true; return}
 const q = id => document.getElementById('history-' + id);
 const chart = q('chart'), nav = q('navigator'), selection = q('selection');
 const NS = 'http://www.w3.org/2000/svg', DAY = 86400000, LEFT = 58, RIGHT = 66, TOP = 26, BOTTOM = 252;
 let WIDTH = 900;
 const ordinal = iso => Math.round(Date.parse(iso + 'T00:00:00Z') / DAY);
 const iso = day => new Date(day * DAY).toISOString().slice(0, 10);
 const finite = value => typeof value === 'number' && Number.isFinite(value);
 const fmt = value => finite(value) ? new Intl.NumberFormat('ko-KR', {maximumFractionDigits: 3}).format(value) : '—';
 const amount = value => finite(value) ? fmt(value) + ' %p' : '—';
 const kstDay = epoch => new Date(epoch * 1000 + 9 * 3600000).toISOString().slice(0, 10);
 const make = (tag, attrs = {}, text) => {
  const node = document.createElementNS(NS, tag);
  for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
  if (text !== undefined) node.textContent = text;
  return node;
 };
 let data = null, rangeStart = null, rangeEnd = null, domainStart = 0, domainEnd = 0;
 let selectedAccount = '', generation = 0, controller = null, drag = null, follow = true;
 let visible = [], failed = false;

 function clampRange(start, end) {
  let size = Math.max(1, Math.min(domainEnd - domainStart, Math.round(end - start)));
  start = Math.max(domainStart, Math.min(domainEnd - size, Math.round(start)));
  return [start, start + size];
 }
 function change(start, end, manual = true) {
  if (!data) return;
  [rangeStart, rangeEnd] = clampRange(start, end);
  if (manual) follow = false;
  q('tooltip').hidden = true;
  render();
 }
 function updateNavigator() {
  const length = domainEnd - domainStart;
  selection.style.left = ((rangeStart - domainStart) / length * 100) + '%';
  selection.style.width = ((rangeEnd - rangeStart) / length * 100) + '%';
  for (const edge of ['start', 'end']) {
   const handle = q('handle-' + edge), value = edge === 'start' ? rangeStart : rangeEnd - 1;
   handle.setAttribute('aria-valuemin', String(edge === 'start' ? domainStart : rangeStart));
   handle.setAttribute('aria-valuemax', String(edge === 'start' ? rangeEnd - 1 : domainEnd - 1));
   handle.setAttribute('aria-valuenow', String(value));
   handle.setAttribute('aria-valuetext', iso(value));
  }
 }
 function overview() {
  const svg = q('overview'), nodes = [];
  const maximum = Math.max(1, ...data.days.map(point => point.used_pp || 0));
  const width = 900 / (domainEnd - domainStart);
  for (const point of data.days) {
   if (point.used_pp === null) continue;
   const height = Math.max(1, point.used_pp / maximum * 32);
   nodes.push(make('rect', {x: (ordinal(point.date) - domainStart) * width + width * .15,
    y: 41 - height, width: Math.max(.5, width * .7), height, class: 'history-mini-bar'}));
  }
  svg.replaceChildren(...nodes);
 }
 function render() {
  if (!data) return;
  WIDTH = Math.max(260, Math.round(chart.clientWidth || 900));
  chart.setAttribute('viewBox', `0 0 ${WIDTH} 300`);
  const start = iso(rangeStart), end = iso(rangeEnd - 1), span = rangeEnd - rangeStart;
  visible = data.days.filter(point => point.date >= start && point.date <= end);
  q('start').value = start; q('end').value = end;
  q('start').min = iso(domainStart); q('start').max = end;
  q('end').min = start; q('end').max = iso(domainEnd - 1);
  q('zoom-in').disabled = span <= 1;
  q('zoom-out').disabled = span >= domainEnd - domainStart;
  updateNavigator();
  const measured = visible.filter(point => point.used_pp !== null);
  q('range-total').textContent = measured.length ? amount(measured.reduce((sum, point) => sum + point.used_pp, 0)) : '—';
  q('total').textContent = amount(data.total_used_pp);
  q('first').textContent = data.first_observed_at ? new Intl.DateTimeFormat('ko-KR', {
   timeZone: 'Asia/Seoul', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false
  }).format(new Date(data.first_observed_at * 1000)) : '아직 없음';
  const dailyMax = Math.max(1, ...visible.map(point => point.used_pp || 0)) * 1.12;
  const cumulativeMax = Math.max(1, ...visible.map(point => point.cumulative_pp || 0)) * 1.12;
  const width = WIDTH - LEFT - RIGHT, cell = width / span, height = BOTTOM - TOP;
  const x = day => LEFT + (day - rangeStart + .5) * cell;
  const nodes = [make('title', {id: 'history-chart-title'}, `일별 사용량과 누적 사용량: ${start}부터 ${end}`),
   make('desc', {id: 'history-chart-desc'}, '막대는 왼쪽 축의 일별 사용량, 선은 오른쪽 축의 수집 시작부터 누적량입니다. 단위는 퍼센트포인트입니다.')];
  nodes.push(make('text', {x: LEFT, y: 14, class: 'history-axis-label'}, '일별 %p'),
   make('text', {x: WIDTH - RIGHT, y: 14, 'text-anchor': 'end', class: 'history-axis-label'}, '수집 누적 %p'));
  for (let i = 0; i <= 4; i++) {
   const y = BOTTOM - height * i / 4;
   nodes.push(make('line', {x1: LEFT, x2: WIDTH - RIGHT, y1: y, y2: y, class: 'history-grid'}),
    make('text', {x: LEFT - 10, y: y + 4, 'text-anchor': 'end', class: 'history-tick'}, fmt(dailyMax * i / 4)),
    make('text', {x: WIDTH - RIGHT + 10, y: y + 4, class: 'history-tick history-cumulative-tick'}, fmt(cumulativeMax * i / 4)));
  }
  const labelEvery = Math.max(1, Math.ceil(span / Math.max(2, Math.floor(width / 70))));
  let path = '', joined = false, observedDays = 0;
  for (let index = 0; index < visible.length; index++) {
   const point = visible[index], center = x(ordinal(point.date));
   if (point.observations) observedDays++;
   if (point.used_pp !== null) {
    const barHeight = Math.max(1, point.used_pp / dailyMax * height);
    const bar = make('rect', {x: center - cell * .29, y: BOTTOM - barHeight, width: Math.max(.5, cell * .58),
     height: barHeight, class: 'history-bar' + (point.partial || point.in_progress ? ' history-partial' : '')});
    bar.append(make('title', {}, `${point.date}: ${amount(point.used_pp)}${point.partial ? ' · 부분 기록' : ''}`));
    nodes.push(bar);
   }
   if (finite(point.cumulative_pp)) {
    const y = BOTTOM - point.cumulative_pp / cumulativeMax * height;
    path += `${joined ? 'L' : 'M'}${center},${y} `;
    joined = true;
    nodes.push(make('circle', {cx: center, cy: y, r: span > 100 ? 1.8 : 3.5, class: 'history-point'}));
   } else joined = false;
   if (index % labelEvery === 0 || index === visible.length - 1) {
    nodes.push(make('text', {x: center, y: BOTTOM + 23, 'text-anchor': 'middle', class: 'history-tick'}, point.date.slice(5).replace('-', '/')));
   }
  }
  if (path) nodes.push(make('path', {d: path, class: 'history-line'}));
  if (!observedDays) nodes.push(make('text', {x: WIDTH / 2, y: 136, 'text-anchor': 'middle', class: 'history-empty'}, '이 기간에 수집된 기록이 없습니다'));
  chart.replaceChildren(...nodes);
  const rows = drag ? null : visible.slice().reverse().map(point => {
   const row = document.createElement('tr');
   const notes = point.observations ? `${point.observations}회${point.partial ? ' · 부분*' : ''}${point.in_progress ? ' · 수집 중' : ''}${point.resets ? ' · 주간 초기화' : ''}` : '기록 없음';
   for (const value of [point.date, fmt(point.used_pp), fmt(point.cumulative_pp), notes]) {
    const cell = document.createElement('td'); cell.textContent = value; row.append(cell);
   }
   return row;
  });
  if (rows) q('table').replaceChildren(...rows);
  if (failed) q('message').textContent = '기록을 새로 불러오지 못했습니다. 마지막으로 받은 데이터를 표시합니다.';
  else if (!data.first_observed_at) q('message').textContent = '첫 번째 정상 주간 사용량을 기다리고 있습니다. 주간 한도를 제공하는 계정만 기록됩니다.';
  else if (data.total_used_pp === null) q('message').textContent = '첫 관측값을 기준점으로 저장했습니다. 다음 정상 조회부터 증가분이 기록됩니다. 이전 날짜의 기록은 없습니다.';
  else q('message').textContent = `${span}일 표시 · ${observedDays}일 관측 · 마지막 수집 ${new Intl.DateTimeFormat('ko-KR', {timeZone: 'Asia/Seoul', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false}).format(new Date(data.last_observed_at * 1000))} KST · * 부분 기록, 오늘은 수집 중`;
 }

 async function refresh() {
  const ticket = ++generation;
  controller?.abort(); controller = new AbortController();
  const signal = controller.signal;
  try {
   const response = await fetch('/history' + (selectedAccount ? '?account=' + encodeURIComponent(selectedAccount) : ''), {cache: 'no-store', signal});
   if (!response.ok) throw Error();
   const meta = await response.json();
   if (ticket !== generation) return;
   selectedAccount = meta.account || '';
   const options = meta.accounts.map(account => {
    const option = document.createElement('option'); option.value = account.key; option.textContent = account.alias; return option;
   });
   q('account').replaceChildren(...options); q('account').value = selectedAccount;
   const today = ordinal(meta.today), first = meta.first_observed_at ? ordinal(kstDay(meta.first_observed_at)) : today;
   domainStart = Math.max(today - 3659, Math.min(today - 89, first)); domainEnd = today + 1;
   const query = new URLSearchParams({start: iso(domainStart), end: iso(today)});
   if (selectedAccount) query.set('account', selectedAccount);
   const full = await fetch('/history?' + query, {cache: 'no-store', signal});
   if (!full.ok) throw Error();
   const result = await full.json();
   if (ticket !== generation) return;
   data = result; failed = false;
   if (rangeStart === null || follow) [rangeStart, rangeEnd] = [today - 13, today + 1];
   else [rangeStart, rangeEnd] = clampRange(rangeStart, rangeEnd);
   root.dataset.provider = result.accounts.find(account => account.key === selectedAccount)?.provider || 'claude';
   overview(); render();
  } catch (error) {
   if (error.name === 'AbortError' || ticket !== generation) return;
   failed = true;
   if (data) render();
   else q('message').textContent = '사용량 기록을 불러오지 못했습니다. 잠시 후 다시 시도합니다.';
  }
 }
 q('account').addEventListener('change', () => {
  selectedAccount = q('account').value; data = null; q('tooltip').hidden = true;
  chart.replaceChildren(); q('overview').replaceChildren(); q('table').replaceChildren();
  for (const id of ['range-total', 'total', 'first']) q(id).textContent = '—';
  q('message').textContent = '선택한 계정의 기록을 불러오는 중입니다.';
  refresh();
 });
 q('reset').addEventListener('click', () => {if (!data) return; follow = true; change(domainEnd - 14, domainEnd, false)});
 q('zoom-in').addEventListener('click', () => change(rangeEnd - Math.max(1, Math.round((rangeEnd - rangeStart) / 2)), rangeEnd));
 q('zoom-out').addEventListener('click', () => change(rangeEnd - (rangeEnd - rangeStart) * 2, rangeEnd));
 for (const edge of ['start', 'end']) q(edge).addEventListener('change', () => {
  const day = ordinal(q(edge).value);
  if (!Number.isFinite(day)) {render(); return}
  if (edge === 'start') change(Math.min(day, rangeEnd - 1), rangeEnd);
  else change(rangeStart, Math.max(rangeStart + 1, day + 1));
 });
 function begin(event, kind, surface) {
  if (!data || event.button !== 0) return;
  event.preventDefault();
  drag = {id: event.pointerId, x: event.clientX, start: rangeStart, end: rangeEnd, kind, surface,
   scale: kind === 'chart' ? (WIDTH - LEFT - RIGHT) / WIDTH * chart.getBoundingClientRect().width : nav.getBoundingClientRect().width};
  surface.setPointerCapture(event.pointerId); root.classList.add('history-dragging');
 }
 chart.addEventListener('pointerdown', event => begin(event, 'chart', chart));
 nav.addEventListener('pointerdown', event => {
  if (!data) return;
  const kind = event.target.closest('#history-handle-start') ? 'start' : event.target.closest('#history-handle-end') ? 'end' : 'move';
  if (!selection.contains(event.target)) {
   const position = domainStart + (event.clientX - nav.getBoundingClientRect().left) / nav.getBoundingClientRect().width * (domainEnd - domainStart);
   const span = rangeEnd - rangeStart; change(position - span / 2, position + span / 2);
  }
  begin(event, kind, nav);
 });
 function move(event) {
  if (!drag || drag.id !== event.pointerId) return;
  const base = drag.kind === 'chart' ? drag.end - drag.start : domainEnd - domainStart;
  const offset = Math.round((event.clientX - drag.x) / drag.scale * base);
  if (drag.kind === 'start') change(Math.max(domainStart, Math.min(drag.end - 1, drag.start + offset)), drag.end);
  else if (drag.kind === 'end') change(drag.start, Math.max(drag.start + 1, Math.min(domainEnd, drag.end + offset)));
  else {
   const direction = drag.kind === 'chart' ? -1 : 1;
   change(drag.start + offset * direction, drag.end + offset * direction);
  }
 }
 function finish(event) {
  if (drag?.id !== event.pointerId) return;
  if (drag.surface.hasPointerCapture(event.pointerId)) drag.surface.releasePointerCapture(event.pointerId);
  drag = null; root.classList.remove('history-dragging');
  render();
 }
 for (const surface of [chart, nav]) {
  surface.addEventListener('pointermove', move);
  surface.addEventListener('pointerup', finish);
  surface.addEventListener('pointercancel', finish);
  surface.addEventListener('lostpointercapture', () => {drag = null; root.classList.remove('history-dragging')});
 }
 for (const edge of ['start', 'end']) q('handle-' + edge).addEventListener('keydown', event => {
  if (!data) return;
  const steps = {ArrowLeft: -1, ArrowRight: 1, ArrowDown: -1, ArrowUp: 1, PageDown: -7, PageUp: 7};
  if (!(event.key in steps) && event.key !== 'Home' && event.key !== 'End') return;
  event.preventDefault();
  const value = (edge === 'start' ? rangeStart : rangeEnd - 1) + (steps[event.key] || 0);
  if (edge === 'start') change(event.key === 'Home' ? domainStart : event.key === 'End' ? rangeEnd - 1 : Math.max(domainStart, Math.min(rangeEnd - 1, value)), rangeEnd);
  else change(rangeStart, event.key === 'Home' ? rangeStart + 1 : event.key === 'End' ? domainEnd : Math.max(rangeStart + 1, Math.min(domainEnd, value + 1)));
 });
 chart.addEventListener('pointermove', event => {
  if (drag || !data || !visible.length) return;
  const rect = chart.getBoundingClientRect(), logical = (event.clientX - rect.left) / rect.width * WIDTH;
  const index = Math.floor((logical - LEFT) / (WIDTH - LEFT - RIGHT) * (rangeEnd - rangeStart));
  const point = visible[index];
  if (!point || logical < LEFT || logical > WIDTH - RIGHT) {q('tooltip').hidden = true; return}
  q('tooltip').textContent = `${point.date} · 일별 ${amount(point.used_pp)} · 누적 ${amount(point.cumulative_pp)}${point.partial ? ' · 부분 기록*' : ''}`;
  q('tooltip').hidden = false;
 });
 chart.addEventListener('pointerleave', () => {q('tooltip').hidden = true});
 refresh();
 setInterval(() => {
  if (!document.hidden && !drag && ![q('start'), q('end'), q('account')].includes(document.activeElement)) refresh();
 }, 30000);
 document.addEventListener('visibilitychange', () => {if (!document.hidden) refresh()});
 new ResizeObserver(() => {if (data && !drag) render()}).observe(q('plot'));
})();
