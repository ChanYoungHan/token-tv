'use strict';
/* Account series share dates and axes; each account retains its own quota units. */
(() => {
 const root = document.querySelector('#usage-history');
 if (!root) return;
 if (document.documentElement.hasAttribute('data-demo')) {root.hidden = true; return}
 const q = id => document.getElementById('history-' + id);
 const chart = q('chart'), nav = q('navigator'), selection = q('selection');
 const NS = 'http://www.w3.org/2000/svg', DAY = 86400000, LEFT = 58, RIGHT = 66, TOP = 26, BOTTOM = 252;
 let WIDTH = 900;
 const ordinal = value => Math.round(Date.parse(value + 'T00:00:00Z') / DAY);
 const iso = day => new Date(day * DAY).toISOString().slice(0, 10);
 const finite = value => typeof value === 'number' && Number.isFinite(value);
 const fmt = value => finite(value) ? new Intl.NumberFormat('ko-KR', {maximumFractionDigits:3}).format(value) : '—';
 const amount = value => finite(value) ? fmt(value) + ' %p' : '—';
 const kstDay = epoch => new Date(epoch * 1000 + 9 * 3600000).toISOString().slice(0,10);
 const timeLabel = epoch => epoch ? new Intl.DateTimeFormat('ko-KR', {timeZone:'Asia/Seoul', month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit', hour12:false}).format(new Date(epoch * 1000)) : '아직 없음';
 const make = (tag, attrs = {}, text) => {
  const node = document.createElementNS(NS, tag);
  for (const [key,value] of Object.entries(attrs)) node.setAttribute(key,value);
  if (text !== undefined) node.textContent = text;
  return node;
 };
 const element = (tag, cls, text) => {const node=document.createElement(tag); if(cls) node.className=cls; if(text!==undefined) node.textContent=text; return node};
 let data = null, rangeStart = null, rangeEnd = null, domainStart = 0, domainEnd = 0;
 let generation = 0, controller = null, drag = null, follow = true;
 let visible = [], visibleSeries = [], failed = false;
 const hiddenAccounts = new Set();
 function colors(accounts) {
  const palette=['#ffad66','#5fd8ff','#c5a3ff','#65e2ba','#ff8faf','#e6d66e'], used=new Set(), result=new Map();
  for (const account of [...accounts].sort((a,b)=>a.key.localeCompare(b.key))) {
   let index={claude:0,codex:1,grok:2}[account.provider] ?? 3;
   while(used.has(index)) index++;
   used.add(index); result.set(account.key,palette[index] || `hsl(${(index*137.508)%360} 78% 72%)`);
  }
  return result;
 }
 function clampRange(start,end) {
  const size=Math.max(1,Math.min(domainEnd-domainStart,Math.round(end-start)));
  start=Math.max(domainStart,Math.min(domainEnd-size,Math.round(start)));
  return [start,start+size];
 }
 function change(start,end,manual=true) {
  if(!data) return;
  [rangeStart,rangeEnd]=clampRange(start,end);
  if(manual) follow=false;
  q('tooltip').hidden=true; render();
 }
 function updateNavigator() {
  const length=domainEnd-domainStart;
  selection.style.left=((rangeStart-domainStart)/length*100)+'%';
  selection.style.width=((rangeEnd-rangeStart)/length*100)+'%';
  for(const edge of ['start','end']) {
   const handle=q('handle-'+edge), value=edge==='start'?rangeStart:rangeEnd-1;
   handle.setAttribute('aria-valuemin',String(edge==='start'?domainStart:rangeStart));
   handle.setAttribute('aria-valuemax',String(edge==='start'?rangeEnd-1:domainEnd-1));
   handle.setAttribute('aria-valuenow',String(value)); handle.setAttribute('aria-valuetext',iso(value));
  }
 }
 function activeSeries() {return data.series.filter(series=>!hiddenAccounts.has(series.key))}
 function legend() {
  const nodes=data.series.map(series=>{
   const button=element('button','history-account-toggle'); button.type='button'; button.dataset.key=series.key;
   button.style.setProperty('--series-color',series.color);
   button.setAttribute('aria-pressed',String(!hiddenAccounts.has(series.key)));
   button.setAttribute('aria-label',series.alias+' 그래프 표시');
   button.append(element('i','history-account-dot'),element('span','',series.alias));
   button.addEventListener('click',()=>{
    if(hiddenAccounts.has(series.key)) hiddenAccounts.delete(series.key);
    else if(activeSeries().length>1) hiddenAccounts.add(series.key);
    for(const item of q('legend').querySelectorAll('button')) item.setAttribute('aria-pressed',String(!hiddenAccounts.has(item.dataset.key)));
    q('tooltip').hidden=true; render();
   });
   return button;
  });
  q('legend').replaceChildren(...nodes);
 }
 function overview() {
  const nodes=[], series=activeSeries(), width=900/(domainEnd-domainStart);
  let maximum=1;
  for(const item of series) for(const point of item.days) maximum=Math.max(maximum,point.used_pp||0);
  series.forEach((item,seriesIndex)=>{
   const group=make('g',{style:`--series-color:${item.color}`});
   for(const point of item.days) {
    if(point.used_pp===null) continue;
    const height=Math.max(1,point.used_pp/maximum*32), slot=width*.8/series.length;
    group.append(make('rect',{x:(ordinal(point.date)-domainStart)*width+width*.1+seriesIndex*slot,
     y:41-height,width:Math.max(.3,slot*.85),height,class:'history-mini-bar'}));
   }
   nodes.push(group);
  });
  q('overview').replaceChildren(...nodes);
 }
 function render() {
  if(!data) return;
  WIDTH=Math.max(260,Math.round(chart.clientWidth||900)); chart.setAttribute('viewBox',`0 0 ${WIDTH} 300`);
  const start=iso(rangeStart), end=iso(rangeEnd-1), span=rangeEnd-rangeStart;
  visibleSeries=activeSeries().map(series=>({...series,points:series.days.filter(point=>point.date>=start&&point.date<=end)}));
  visible=visibleSeries[0]?.points||[];
  q('start').value=start; q('end').value=end; q('start').min=iso(domainStart); q('start').max=end;
  q('end').min=start; q('end').max=iso(domainEnd-1);
  q('zoom-in').disabled=span<=1; q('zoom-out').disabled=span>=domainEnd-domainStart;
  updateNavigator();
  const stats=data.series.map(series=>{
   const measured=series.days.filter(point=>point.date>=start&&point.date<=end&&point.used_pp!==null);
   const card=element('div','history-account-stat'); card.style.setProperty('--series-color',series.color);
   card.dataset.muted=String(hiddenAccounts.has(series.key));
   const title=element('div','history-account-title'); title.append(element('i','history-account-dot'),element('strong','',series.alias));
   const metrics=element('div','history-account-metrics');
   for(const [label,value] of [['선택 기간',measured.length?amount(measured.reduce((sum,p)=>sum+p.used_pp,0)):'—'],['수집 누적',amount(series.total_used_pp)]]) {
    const item=element('div'); item.append(element('span','',label),element('strong','',value)); metrics.append(item);
   }
   card.append(title,metrics,element('p','history-account-first','첫 관측 '+timeLabel(series.first_observed_at)+' KST'));
   return card;
  });
  q('stats').replaceChildren(...stats);
  let dailyMax=1,cumulativeMax=1,observedDays=0;
  for(const series of visibleSeries) for(const point of series.points) {
   dailyMax=Math.max(dailyMax,point.used_pp||0); cumulativeMax=Math.max(cumulativeMax,point.cumulative_pp||0);
  }
  dailyMax*=1.12; cumulativeMax*=1.12;
  const width=WIDTH-LEFT-RIGHT,cell=width/span,height=BOTTOM-TOP;
  const nodes=[make('title',{id:'history-chart-title'},`전체 계정의 일별 사용량과 누적 사용량: ${start}부터 ${end}`),
   make('desc',{id:'history-chart-desc'},'계정별 색상으로 구분합니다. 막대는 왼쪽 축의 일별 사용량, 선은 오른쪽 축의 수집 누적량입니다. 계정별 값은 합산하지 않습니다.')];
  nodes.push(make('text',{x:LEFT,y:14,class:'history-axis-label'},'일별 %p'),make('text',{x:WIDTH-RIGHT,y:14,'text-anchor':'end',class:'history-axis-label'},'수집 누적 %p'));
  for(let i=0;i<=4;i++) {
   const y=BOTTOM-height*i/4;
   nodes.push(make('line',{x1:LEFT,x2:WIDTH-RIGHT,y1:y,y2:y,class:'history-grid'}),
    make('text',{x:LEFT-10,y:y+4,'text-anchor':'end',class:'history-tick'},fmt(dailyMax*i/4)),
    make('text',{x:WIDTH-RIGHT+10,y:y+4,class:'history-tick'},fmt(cumulativeMax*i/4)));
  }
  const every=Math.max(1,Math.ceil(span/Math.max(2,Math.floor(width/75))));
  for(let index=0;index<visible.length;index++) {
   if(visibleSeries.some(series=>series.points[index]?.observations)) observedDays++;
   if(index%every===0 || (index===visible.length-1&&index%every>every/2)) nodes.push(make('text',{
    x:LEFT+(index+.5)*cell,y:BOTTOM+23,'text-anchor':'middle',class:'history-tick'},visible[index].date.slice(5).replace('-','/')));
  }
  visibleSeries.forEach((series,seriesIndex)=>{
   const group=make('g',{style:`--series-color:${series.color}`,'data-history-account':series.key});
   let path='',joined=false;
   const slot=cell*.72/visibleSeries.length;
   for(let index=0;index<series.points.length;index++) {
    const point=series.points[index],center=LEFT+(index+.14)*cell+(seriesIndex+.5)*slot;
    if(point.used_pp!==null) {
     const barHeight=Math.max(1,point.used_pp/dailyMax*height);
     group.append(make('rect',{x:center-slot*.43,y:BOTTOM-barHeight,width:Math.max(.4,slot*.86),height:barHeight,
      class:'history-bar'+(point.partial||point.in_progress?' history-partial':'')}));
    }
    if(finite(point.cumulative_pp)) {
     const y=BOTTOM-point.cumulative_pp/cumulativeMax*height;
     path+=`${joined?'L':'M'}${center},${y} `; joined=true;
     group.append(make('circle',{cx:center,cy:y,r:span>100?1.8:3.5,class:'history-point'}));
    } else joined=false;
   }
   if(path) group.append(make('path',{d:path,class:'history-line'}));
   nodes.push(group);
  });
  if(!observedDays) nodes.push(make('text',{x:WIDTH/2,y:136,'text-anchor':'middle',class:'history-empty'},'이 기간에 수집된 기록이 없습니다'));
  chart.replaceChildren(...nodes);
  if(!drag) {
   const rows=[];
   for(let index=visible.length-1;index>=0;index--) for(const series of visibleSeries) {
    const point=series.points[index]; if(!point) continue;
    const row=element('tr');
    const account=element('td'),badge=element('span','history-table-account'); badge.style.setProperty('--series-color',series.color);
    badge.append(element('i','history-account-dot'),element('span','',series.alias)); account.append(badge);
    row.append(element('td','',point.date),account);
    const notes=point.observations?`${point.observations}회${point.partial?' · 부분*':''}${point.in_progress?' · 수집 중':''}${point.resets?' · 주간 초기화':''}`:'기록 없음';
    for(const value of [fmt(point.used_pp),fmt(point.cumulative_pp),notes]) row.append(element('td','',value));
    rows.push(row);
   }
   q('table').replaceChildren(...rows);
  }
  const firsts=data.series.filter(series=>series.first_observed_at), last=Math.max(0,...data.series.map(series=>series.last_observed_at||0));
  if(failed) q('message').textContent='기록을 새로 불러오지 못했습니다. 마지막으로 받은 데이터를 표시합니다.';
  else if(!firsts.length) q('message').textContent='첫 정상 주간 사용량을 기다립니다. 계정별로 정상 조회된 값부터 기록합니다.';
  else q('message').textContent=`${visibleSeries.length}개 계정 · ${span}일 표시 · ${observedDays}일 관측 · 마지막 수집 ${timeLabel(last)} KST · * 부분 기록, 오늘은 수집 중`;
  overview();
 }
 async function refresh() {
  const ticket=++generation; controller?.abort(); controller=new AbortController(); const signal=controller.signal;
  try {
   const response=await fetch('/history',{cache:'no-store',signal}); if(!response.ok) throw Error();
   const meta=await response.json(); if(ticket!==generation) return;
   const today=ordinal(meta.today), firsts=meta.accounts.filter(account=>account.first_observed_at).map(account=>ordinal(kstDay(account.first_observed_at)));
   const nextStart=Math.max(today-3659,Math.min(today-89,...firsts)), nextEnd=today+1;
   const colorMap=colors(meta.accounts);
   const series=await Promise.all(meta.accounts.map(async account=>{
    const query=new URLSearchParams({account:account.key,start:iso(nextStart),end:iso(today)});
    const response=await fetch('/history?'+query,{cache:'no-store',signal}); if(!response.ok) throw Error();
    const detail=await response.json(); return {...account,...detail,key:account.key,alias:account.alias,color:colorMap.get(account.key)};
   }));
   if(ticket!==generation) return;
   data={series}; domainStart=nextStart; domainEnd=nextEnd; failed=false;
   for(const key of hiddenAccounts) if(!series.some(item=>item.key===key)) hiddenAccounts.delete(key);
   if(series.length&&series.every(item=>hiddenAccounts.has(item.key))) hiddenAccounts.delete(series[0].key);
   if(rangeStart===null||follow) [rangeStart,rangeEnd]=[today-13,today+1];
   else [rangeStart,rangeEnd]=clampRange(rangeStart,rangeEnd);
   legend(); render();
  } catch(error) {
   if(error.name==='AbortError'||ticket!==generation) return;
   failed=true; if(data) render(); else q('message').textContent='사용량 기록을 불러오지 못했습니다. 잠시 후 다시 시도합니다.';
  }
 }
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
  const rows = [element('strong', '', point.date)];
  for (const series of visibleSeries) {
   const item = series.points[index], row = element('div', 'history-tooltip-row');
   row.style.setProperty('--series-color', series.color);
   row.append(element('i', 'history-account-dot'), element('span', '', `${series.alias} · 일별 ${amount(item.used_pp)} · 누적 ${amount(item.cumulative_pp)}${item.partial ? ' · 부분*' : ''}`));
   rows.push(row);
  }
  q('tooltip').replaceChildren(...rows);
  q('tooltip').hidden = false;
 });
 chart.addEventListener('pointerleave', () => {q('tooltip').hidden = true});
 refresh();
 setInterval(() => {
  if (!document.hidden && !drag && ![q('start'), q('end')].includes(document.activeElement)) refresh();
 }, 30000);
 document.addEventListener('visibilitychange', () => {if (!document.hidden) refresh()});
 new ResizeObserver(() => {if (data && !drag) render()}).observe(q('plot'));
})();
