// 투자 게임 트레이딩 데스크: 시간 흐름, 세계지도 핀, 알림, 실시간 시세, 주문
(() => {
const $ = id => document.getElementById(id);
const fmt = n => Math.round(n).toLocaleString('ko-KR');
const sgn = n => (n > 0 ? '+' : '') + fmt(n);
const pct = n => (n > 0 ? '+' : '') + n.toFixed(2) + '%';
const cls = n => n > 0 ? 'up' : (n < 0 ? 'down' : '');
const esc = s => String(s ?? '').replace(/[&<>"']/g, c =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const store = {
  get(k, d) { try { const v = localStorage.getItem(k); return v === null ? d : JSON.parse(v); } catch (e) { return d; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) {} },
};

const STOCKS = INIT.stocks, CLOCKS = INIT.clocks, CODES = Object.keys(STOCKS);
const BASE_MS = 1500;                 // x1 속도에서 5분(틱 하나)이 흐르는 실제 시간
let S = INIT, intra = INIT.intra, lastId = 0;
let speed = 0, lastSpeed = 1, timer = null, busy = false, resumeAfterDetail = 0;
let sel = store.get('desk.sel', CODES[0]);
if (!STOCKS[sel]) sel = CODES[0];
const NEWS = new Map();

// 본문 없는 POST: 틱·다음 날은 since를 쿼리로 보낸다 (본문이 있으면 지연 ACK에 걸릴 수 있음, docs/performance.md)
async function post(url) {
  const r = await fetch(url, { method: 'POST' });
  if (!r.ok) throw new Error(r.status);
  return r.json();
}

// ------------------------------------------------------------ 분류·표시
function cat(n) {
  if (n.kind === 'info') return 'info';
  if (n.kind === 'preview') return 'sched';
  if (n.tag === '루머') return 'rumor';
  if (n.tag === '단서') return 'clue';
  return n.codes.length ? 'corp' : 'macro';
}
const quote = c => S.quotes[c];
const chgPct = c => (quote(c).cur - quote(c).prev) / quote(c).prev * 100;

// ------------------------------------------------------------ 상단 바
function renderTop() {
  $('d-day').textContent = `D${S.day} / ${S.max_day}`;
  $('d-clock').textContent = S.phase === 'open' ? S.clock : '15:30';
  const ph = S.finished ? ['게임 종료', 'end'] : S.phase === 'open' ? ['장중', 'open'] : ['장 마감', 'closed'];
  $('d-phase').textContent = ph[0];
  $('d-phase').className = 'dphase ' + ph[1];
  const done = (S.day - 1) * S.ticks + S.tick;
  $('d-prog').style.width = (done / (S.max_day * S.ticks) * 100).toFixed(2) + '%';

  const inds = [
    ['종합지수', S.index, S.index_prev, 2, '', 'supply'],
    ['기준금리', S.ind.rate, S.ind_open.rate, 2, '%', 'rate'],
    ['환율', S.ind.fx, S.ind_open.fx, 1, '원', 'fx'],
    ['유가', S.ind.oil, S.ind_open.oil, 2, '$', 'oil'],
  ];
  $('d-inds').innerHTML = inds.map(([l, v, p, d, u, f]) => {
    const df = v - p, c = Math.abs(df) < 1e-9 ? '' : cls(df);
    return `<a href="${URLS.factors}#${f}" target="_blank" rel="noopener"><span>${l}</span>` +
      `<b>${v.toLocaleString('ko-KR', { minimumFractionDigits: d, maximumFractionDigits: d })}${u}</b>` +
      `<small class="${c}">${c === 'up' ? '▲' : c === 'down' ? '▼' : '―'}${Math.abs(df).toFixed(d)}</small></a>`;
  }).join('');

  const pl = S.total - S.start_cash;
  $('d-total').textContent = fmt(S.total) + '원';
  $('d-pl').textContent = `${sgn(pl)} (${pct(pl / S.start_cash * 100)})`;
  $('d-pl').className = cls(pl);
  $('d-cash').textContent = '예수금 ' + fmt(S.cash) + '원';
  document.querySelectorAll('.dspeed button').forEach(b =>
    b.classList.toggle('on', +b.dataset.s === speed));
}

// ------------------------------------------------------------ 시세판
const rows = {};
function buildBoard() {
  const tb = document.querySelector('#board tbody');
  tb.innerHTML = '';
  for (const c of CODES) {
    const tr = document.createElement('tr');
    tr.innerHTML = `<td><b>${esc(STOCKS[c].name)}</b><small>${esc(STOCKS[c].sector)}</small></td>` +
      '<td class="cur"></td><td class="chg"></td><td class="qty"></td><td class="pl"></td>';
    tr.onclick = () => select(c, true);
    tb.appendChild(tr);
    rows[c] = { tr, last: null };
  }
}
function renderBoard() {
  for (const c of CODES) {
    const r = rows[c], q = quote(c), h = S.holdings[c], tr = r.tr;
    tr.classList.toggle('sel', c === sel);
    tr.querySelector('.cur').textContent = fmt(q.cur);
    const p = chgPct(c), chg = tr.querySelector('.chg');
    chg.textContent = pct(p); chg.className = 'chg ' + cls(p);
    tr.querySelector('.qty').textContent = h ? fmt(h.qty) : '';
    const pl = tr.querySelector('.pl');
    if (h) { const v = (q.cur - h.avg) / h.avg * 100; pl.textContent = pct(v); pl.className = 'pl ' + cls(v); }
    else { pl.textContent = ''; pl.className = 'pl'; }
    if (r.last !== null && r.last !== q.cur) {       // 가격이 바뀌면 잠깐 번쩍
      tr.classList.remove('fl-up', 'fl-down'); void tr.offsetWidth;
      tr.classList.add(q.cur > r.last ? 'fl-up' : 'fl-down');
    }
    r.last = q.cur;
  }
}

// ------------------------------------------------------------ 선택 종목 + 주문
let chart;
const PHONE = matchMedia('(max-width: 720px)');
function select(c, fromBoard) {
  sel = c; store.set('desk.sel', c);
  renderBoard(); renderStock(true);
  // 휴대폰에선 주문창이 시세판 아래에 있으니 바로 내려 줌
  if (fromBoard && PHONE.matches) document.querySelector('.dstock').scrollIntoView({ behavior: 'smooth', block: 'start' });
}
function maxBuy() { return Math.floor(S.cash / (quote(sel).cur * (1 + FEE))); }
function renderStock(reset) {
  const s = STOCKS[sel], q = quote(sel), p = chgPct(sel), h = S.holdings[sel];
  $('s-name').textContent = s.name;
  $('s-link').href = URLS.stock.replace('XXX', sel);
  $('s-cur').textContent = fmt(q.cur); $('s-cur').className = cls(p);
  $('s-chg').textContent = `${sgn(q.cur - q.prev)} (${pct(p)})`; $('s-chg').className = cls(p);
  $('s-sector').textContent = `${s.sector} · 베타 ${s.beta} · ${s.hq}`;
  $('s-info').innerHTML = (h ? `보유 <b>${fmt(h.qty)}주</b> · 평단 ${fmt(h.avg)} · ` +
      `<span class="${cls(q.cur - h.avg)}">${sgn((q.cur - h.avg) * h.qty)}원</span><br>` : '') +
    `매수 가능 ${fmt(maxBuy())}주 · 수수료 ${(FEE * 100).toFixed(3)}% · 매도 시 거래세 ${(TAX * 100).toFixed(1)}%`;
  const closed = S.phase !== 'open';
  $('buy').disabled = $('sell').disabled = closed;

  const data = intra[sel], color = p >= 0 ? '#FF5A63' : '#5B9BFF';
  if (!chart) {
    chart = new Chart($('s-chart'), {
      type: 'line',
      data: { labels: CLOCKS, datasets: [
        { data: [], borderWidth: 2, pointRadius: 0, tension: .1 },
        { data: [], borderColor: '#5E6B80', borderWidth: 1, borderDash: [4, 4], pointRadius: 0 } ] },
      options: { maintainAspectRatio: false, animation: false,
        plugins: { legend: { display: false },
          tooltip: { mode: 'index', intersect: false, filter: i => i.datasetIndex === 0,
                     callbacks: { label: c => fmt(c.parsed.y) + '원' } } },
        interaction: { mode: 'index', intersect: false },
        scales: { x: { ticks: { maxTicksLimit: 6, color: '#8C98AB' }, grid: { display: false } },
                  y: { ticks: { callback: v => fmt(v), color: '#8C98AB' }, grid: { color: '#1E2A3D' } } } },
    });
  }
  const ds = chart.data.datasets;
  ds[0].data = data.slice(); ds[0].borderColor = color;
  ds[1].data = CLOCKS.map(() => q.prev);
  chart.update('none');
  if (reset) $('qty').value = 1;
}
const newOrderId = () => (crypto.randomUUID ? crypto.randomUUID() : Date.now() + '-' + Math.random());
async function order(side) {
  const qty = parseInt($('qty').value) || 0;
  if (qty < 1) { note('수량을 1주 이상으로 입력하세요.', 'err'); return; }
  // 주문마다 고유 번호: 응답을 못 받아 다시 보내도 서버는 한 번만 체결한다
  const body = { stock_code: sel, side, qty, client_order_id: newOrderId() };
  let r;
  for (let attempt = 0; attempt < 2; attempt++) {
    try { r = await fetch(URLS.orders, { method: 'POST', headers: { 'Content-Type': 'application/json' },
                                          body: JSON.stringify(body) }); break; }
    catch (e) { if (attempt) { note('주문을 보내지 못했습니다.', 'err'); return; } }
  }
  const o = await r.json().catch(() => null);
  if (!o || !o.status) { note('주문을 처리하지 못했습니다.', 'err'); return; }
  if (o.status === 'REJECTED') note(o.reject_message, 'err');
  else {
    const t = o.trade, name = STOCKS[sel].name;
    note(`${name} ${fmt(qty)}주를 ${fmt(t.price)}원에 ${side === 'BUY' ? '매수' : '매도'}했습니다.` +
         (t.realized_pnl !== null ? ` 실현손익 ${sgn(t.realized_pnl)}원` : ''), 'ok');
  }
  try {
    const s = await fetch(`${URLS.state}?since=${lastId}`);
    if (s.ok) apply(await s.json());
  } catch (e) {}
}
$('buy').onclick = () => order('BUY');
$('sell').onclick = () => order('SELL');
// 25%·50%·최대는 매수 가능 수량 기준, '보유 전량'은 매도용
document.querySelectorAll('.s-order .qbtns button').forEach(b => b.onclick = () => {
  const n = b.dataset.all !== undefined ? (S.holdings[sel] ? S.holdings[sel].qty : 0)
                                        : Math.floor(maxBuy() * parseFloat(b.dataset.p));
  $('qty').value = Math.max(1, n);
});

// ------------------------------------------------------------ 세계지도
let proj, kproj, gPins, kPins;
async function initMap() {
  try {
    const topo = await (await fetch('https://cdn.jsdelivr.net/npm/world-atlas@2/countries-50m.json')).json();
    const all = topojson.feature(topo, topo.objects.countries).features.filter(f => f.id !== '010');
    const fc = { type: 'FeatureCollection', features: all };
    const W = 1000, H = 470, svg = d3.select('#world').attr('viewBox', `0 0 ${W} ${H}`);
    proj = d3.geoNaturalEarth1().rotate([-150, 0]).fitExtent([[4, 4], [W - 4, H - 4]], fc);
    const path = d3.geoPath(proj);
    svg.append('path').datum(d3.geoGraticule10()).attr('class', 'grat').attr('d', path);
    svg.append('g').selectAll('path').data(all).join('path')
      .attr('class', d => 'land' + (d.id === '410' ? ' kr' : '')).attr('d', path);
    gPins = svg.append('g');

    const KW = 150, KH = 180, ks = d3.select('#korea').attr('viewBox', `0 0 ${KW} ${KH}`);
    const kr = all.find(f => f.id === '410');
    kproj = d3.geoMercator().fitExtent([[12, 10], [KW - 12, KH - 10]], kr);
    ks.append('g').selectAll('path').data(all.filter(f => ['410', '408', '392', '156'].includes(f.id)))
      .join('path').attr('class', d => 'land' + (d.id === '410' ? ' kr' : '')).attr('d', d3.geoPath(kproj));
    kPins = ks.append('g');
    $('map-msg').hidden = true;
    drawPins();
  } catch (e) {
    $('map-msg').textContent = '지도를 불러오지 못했습니다. 알림 목록에서 사건을 확인하세요.';
  }
}
function addPin(n, live) {
  if (!proj || n.lat === undefined) return;
  const k = cat(n), jit = ((n.id * 37) % 7 - 3);
  const put = (g, p, r, jx) => {
    const [x, y] = p([n.lon + jit * jx, n.lat + jit * jx * 0.6]);
    const el = g.append('g').attr('class', `pin k-${k}${live ? ' live' : ''}`)
      .attr('transform', `translate(${x},${y})`).on('click', () => openDetail(n.id));
    el.append('circle').attr('class', 'pulse').attr('r', r);
    el.append('circle').attr('class', 'dot').attr('r', r);
    el.append('title').text(`${n.time} ${n.place} · ${n.title}`);
    if (live) el.append('text').attr('class', 'plabel').attr('x', r + 4).attr('y', 4).text(n.place);
  };
  if (n.kr) {
    put(kPins, kproj, 5, 0.05);
    if (live) put(gPins, proj, 6, 0);     // 세계지도의 한국 위치에도 표시
  } else {
    put(gPins, proj, 7, 0);
  }
}
function drawPins() {
  if (!proj) return;
  gPins.selectAll('*').remove(); kPins.selectAll('*').remove();
  for (const n of NEWS.values()) if (n.day === S.day) addPin(n, false);
}

// ------------------------------------------------------------ 알림
function feedItem(n) {
  const el = document.createElement('button');
  el.type = 'button'; el.className = 'fitem k-' + cat(n);
  el.innerHTML = `<span class="t-meta"><i>${esc(n.tag)}</i>D${n.day} ${esc(n.time)}${n.place ? ' · ' + esc(n.place) : ''}</span>` +
    `<b>${esc(n.title)}</b>`;
  el.onclick = () => openDetail(n.id);
  return el;
}
function addNews(n, live) {
  NEWS.set(n.id, n);
  const feed = $('feed');
  feed.prepend(feedItem(n));
  while (feed.children.length > 80) feed.lastChild.remove();
  if (n.day === S.day) addPin(n, live);
  if (!live) return;
  const t = feedItem(n);
  t.className = 'toast k-' + cat(n);
  t.onclick = () => { openDetail(n.id); t.remove(); };
  showToast(t);
  if (n.kind === 'event' && $('auto-pause').checked && speed > 0) {
    lastSpeed = speed; setSpeed(0);
    note('사건 발생! 일시정지했습니다. 판단이 끝나면 ▶를 누르세요.', 'info');
  }
}
function showToast(el) {
  const box = $('toasts');
  box.prepend(el);
  while (box.children.length > 4) box.lastChild.remove();
  setTimeout(() => el.classList.add('out'), 7000);
  setTimeout(() => el.remove(), 7500);
}
function note(text, kind) {
  const el = document.createElement('div');
  el.className = 'toast note ' + kind; el.textContent = text;
  showToast(el);
}
const upLabel = u => u.type === 'rate' ? '기준금리 결정'
  : STOCKS[u.code].name + ({ earn: ' 실적 발표', trial: ' 임상 결과', rumor: ' 인수설 답변' })[u.type];
// 같은 사건(예고 → 단서 → 결과)의 뉴스
const issueNews = id => [...NEWS.values()].filter(n => n.issue === id).sort((a, b) => a.id - b.id);
function renderUpcoming() {
  const up = S.upcoming.filter(u => u.day >= S.day);
  $('upcoming').innerHTML = up.length ? '<div class="up-h">예정된 일정</div>' + up.map(u => {
    const when = u.day === S.day ? (u.type === 'rate' ? '오늘 10:00' : '오늘 장중') : `${u.day - S.day}일 뒤`;
    const what = u.type === 'rate' ? `기준금리 결정 · ${u.dir > 0 ? '인상' : '인하'} 확률 ${Math.round(u.p * 100)}%`
      : u.type === 'earn' ? `${STOCKS[u.code].name} 실적 발표 · 기대 ${({ high: '높음', normal: '보통', low: '낮음' })[u.exp]}`
      : u.type === 'rumor' ? `${STOCKS[u.code].name} 인수설 조회공시 답변`
      : `${STOCKS[u.code].name} 임상 결과 발표`;
    const clues = u.issue ? issueNews(u.issue).filter(n => n.tag === '단서') : [];
    return `<div class="up-g"><div class="up-i"><span>${esc(what)}</span><small>${when}</small></div>` +
      (u.issue ? '<div class="up-c">' + (clues.length
        ? clues.map(n => `<button type="button" data-id="${n.id}"><i>🔎</i>${esc(n.title.replace('[단서] ', ''))}</button>`).join('')
        : '<span>🔎 아직 나온 단서가 없습니다. 발표 전까지 단서 뉴스가 나옵니다.</span>') + '</div>' : '') + '</div>';
  }).join('') : '';
  $('upcoming').querySelectorAll('[data-id]').forEach(b => b.onclick = () => openDetail(+b.dataset.id));
}

// ------------------------------------------------------------ 뉴스 상세
function openDetail(id) {
  const n = NEWS.get(id);
  if (!n) return;
  if (speed > 0) { resumeAfterDetail = speed; setSpeed(0); }
  $('detail-body').innerHTML =
    `<div class="t-meta k-${cat(n)}"><i>${esc(n.tag)}</i>D${n.day} ${esc(n.time)}${n.place ? ' · ' + esc(n.place) : ''}</div>` +
    `<h3>${esc(n.title)}</h3><p>${esc(n.body)}</p>` +
    (n.why ? `<details><summary>해설 보기 (먼저 스스로 판단해 보세요)</summary><p>${esc(n.why)}</p>` +
      (n.factor ? `<a href="${URLS.factors}#${n.factor}" target="_blank" rel="noopener">관련 요인 자세히 →</a>` : '') +
      '</details>' : '') +
    issueTrail(n) +
    (n.codes.length ? '<div class="d-codes">' + n.codes.map(c =>
      `<button type="button" class="btn-ghost" data-code="${c}">${esc(STOCKS[c].name)} 보기</button>`).join('') + '</div>' : '');
  $('detail-body').querySelectorAll('[data-code]').forEach(b =>
    b.onclick = () => { closeDetail(); select(b.dataset.code, true); });
  $('detail-body').querySelectorAll('.d-trail [data-id]').forEach(b => b.onclick = () => openDetail(+b.dataset.id));
  $('detail').hidden = false;
}
// 이 뉴스가 속한 사건의 흐름: 예고 → 단서들 → 결과. 발표 전이면 몇 개가 나왔는지와 기한을 함께
function issueTrail(n) {
  if (!n.issue) return '';
  const all = issueNews(n.issue);
  if (all.length < 2 && n.tag !== '단서') return '';
  const u = S.upcoming.find(x => x.issue === n.issue);
  const head = u ? `사건의 흐름 · ${esc(upLabel(u))} ${u.day === S.day ? '오늘' : `${u.day - S.day}일 뒤`}`
                 : '사건의 흐름';
  return `<div class="d-trail"><div class="up-h">${head}</div>` + all.map(x =>
    `<button type="button" class="k-${cat(x)}${x.id === n.id ? ' on' : ''}" data-id="${x.id}">` +
    `<span class="t-meta"><i>${esc(x.tag)}</i>D${x.day} ${esc(x.time)}</span>${esc(x.title)}</button>`).join('') +
    (u ? '<p class="muted">단서는 하나씩은 틀릴 수 있습니다. 여러 단서가 같은 쪽을 가리키는지, 차트가 어느 쪽으로 기우는지 함께 보세요.</p>' : '') +
    '</div>';
}
function closeDetail() {
  $('detail').hidden = true;
  if (resumeAfterDetail) { setSpeed(resumeAfterDetail); resumeAfterDetail = 0; }
}
$('detail').onclick = e => { if (e.target === $('detail') || e.target.dataset.close !== undefined) closeDetail(); };

// ------------------------------------------------------------ 장 마감 리포트
function showReport() {
  const r = S.report;
  if (!r) return;
  const d = r.total - r.open_total, tot = S.total - S.start_cash;
  const tomorrow = S.upcoming.filter(u => u.day === S.day + 1);
  $('report-body').innerHTML = S.finished ?
    `<div class="t-meta">게임 종료</div><h3>${S.max_day}거래일 완주!</h3>
     <div class="rep-big ${cls(tot)}">${fmt(S.total)}원 <small>${pct(tot / S.start_cash * 100)}</small></div>
     <p>최종 자산이 명예의 전당에 기록됐습니다. 이번 판에서 드러난 투자 습관을 확인해 보세요.</p>
     <a class="go buy rep-link" href="${URLS.style}">📊 내 투자 성향 보기</a>
     <form method="post" action="${URLS.newGame}" class="rep-new">
       <button class="btn-ghost" name="days" value="15">새 게임 15일</button>
       <button class="btn-ghost" name="days" value="30">새 게임 30일</button></form>
     <button type="button" class="btn-ghost rep-close">명예의 전당 보기</button>` :
    `<div class="t-meta">D${r.day} · 15:30</div><h3>장 마감</h3>
     <div class="rep-grid">
       <div>오늘 손익<b class="${cls(d)}">${sgn(d)}원</b></div>
       <div>총자산<b>${fmt(r.total)}원</b></div>
       <div>누적 수익률<b class="${cls(tot)}">${pct(tot / S.start_cash * 100)}</b></div>
       <div>오늘 사건<b>${r.events}건</b></div>
       <div>오늘 최고<b class="${cls(r.best_pct)}">${esc(STOCKS[r.best].name)} ${pct(r.best_pct)}</b></div>
       <div>오늘 최저<b class="${cls(r.worst_pct)}">${esc(STOCKS[r.worst].name)} ${pct(r.worst_pct)}</b></div>
     </div>
     ${tomorrow.length ? '<p class="muted">내일 일정: ' + tomorrow.map(upLabel).join(', ') + '</p>' : ''}
     <button type="button" class="go buy" id="next-day">다음 날 장 시작 ▶</button>
     <button type="button" class="btn-ghost rep-close">오늘 차트 더 보기</button>`;
  $('report').hidden = false;
  const nb = $('next-day');
  if (nb) nb.onclick = nextDay;
  $('report-body').querySelector('.rep-close').onclick = () => {
    $('report').hidden = true;
    if (S.finished) document.querySelector('.dhall').scrollIntoView({ behavior: 'smooth' });
  };
}
async function nextDay() {
  try {
    const snap = await post(`${URLS.next}?since=${lastId}`);
    $('report').hidden = true;
    apply(snap);
    setSpeed(lastSpeed || 1);
  } catch (e) { note('다음 날을 시작하지 못했습니다.', 'err'); }
}

// ------------------------------------------------------------ 시간 흐름
function setSpeed(s) {
  if (s > 0 && S.phase !== 'open') { showReport(); return; }
  speed = s;
  if (s > 0) lastSpeed = s;
  renderTop();
  schedule();
}
function schedule() {
  clearTimeout(timer);
  if (speed > 0 && S.phase === 'open') timer = setTimeout(doTick, BASE_MS / speed);
}
async function doTick() {
  if (busy) return;
  busy = true;
  try {
    apply(await post(`${URLS.tick}?since=${lastId}`));
  } catch (e) {
    speed = 0; renderTop();
    note('서버와 연결이 끊겼습니다. 다시 ▶를 눌러 보세요.', 'err');
  } finally { busy = false; }
  schedule();
}
function apply(snap) {
  const wasOpen = S.phase === 'open';
  if (snap.day !== S.day && proj) { gPins.selectAll('*').remove(); kPins.selectAll('*').remove(); }
  if (snap.intra) intra = snap.intra;
  else for (const c of CODES) {
    const a = intra[c];
    while (a.length < snap.tick + 1) a.push(snap.quotes[c].cur);
  }
  S = { ...S, ...snap };
  const fresh = snap.news.filter(n => n.id > lastId);
  for (const n of fresh) { addNews(n, true); lastId = Math.max(lastId, n.id); }
  renderTop(); renderBoard(); renderStock(); renderUpcoming();
  if (wasOpen && S.phase !== 'open') { speed = 0; renderTop(); showReport(); }
}

document.querySelectorAll('.dspeed button').forEach(b => b.onclick = () => setSpeed(+b.dataset.s));
document.addEventListener('keydown', e => {
  if (e.target.matches('input, textarea')) return;
  if (e.key === 'Escape' && !$('detail').hidden) closeDetail();
  if (e.code === 'Space') { e.preventDefault(); setSpeed(speed ? 0 : lastSpeed); }
});
// 다른 앱으로 가거나 화면이 꺼지면 멈춤 (돌아와서 ▶를 누르면 이어짐)
document.addEventListener('visibilitychange', () => {
  if (document.hidden && speed > 0) { lastSpeed = speed; setSpeed(0); }
});
$('auto-pause').checked = store.get('desk.autopause', true);
$('auto-pause').onchange = e => store.set('desk.autopause', e.target.checked);

// ------------------------------------------------------------ 시작
buildBoard();
for (const n of S.news) { addNews(n, false); lastId = Math.max(lastId, n.id); }
renderTop(); renderBoard(); renderStock(true); renderUpcoming();
initMap();
if (S.phase !== 'open') showReport();
else note(matchMedia('(pointer: coarse)').matches ? '▶를 누르면 시간이 흐릅니다. ⏸로 언제든 멈출 수 있어요.'
                                                    : '▶를 누르면 시간이 흐릅니다. 스페이스바로 일시정지할 수 있습니다.', 'info');
})();
