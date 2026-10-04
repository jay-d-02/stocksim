// 투자 공부: 요인 실험실 · 퀴즈 · 하위 탭 전환 (계산은 game.py 엔진과 같은 식)
(() => {
const $ = id => document.getElementById(id);
const { stocks: STOCKS, sectors: SECTORS, units: U, scenarios: SCEN, corp: CORP } = LEARN;
const CODES = Object.keys(STOCKS);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const pct = v => (v > 0 ? '+' : '') + v.toFixed(1) + '%';
const cls = v => v > 0.05 ? 'up' : (v < -0.05 ? 'down' : '');
const store = {
  get(k, d) { try { const v = localStorage.getItem(k); return v === null ? d : JSON.parse(v); } catch (e) { return d; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) {} },
};
const shuffle = a => { for (let i = a.length - 1; i > 0; i--) { const j = Math.floor(Math.random() * (i + 1)); [a[i], a[j]] = [a[j], a[i]]; } return a; };
const label = c => `${STOCKS[c].name} (${STOCKS[c].sector})`;

// ------------------------------------------------------------ 하위 탭
const TABS = ['lab', 'tips', 'quiz'];
function showTab(t) {
  if (t.startsWith('tip-') && $(t)) {          // #tip-04 → 투자 팁 탭을 열고 그 팁으로 이동
    const el = $(t);
    showTab('tips');
    el.classList.add('hl');
    setTimeout(() => el.scrollIntoView({ block: 'center' }), 0);
    return;
  }
  if (!TABS.includes(t)) t = 'lab';
  TABS.forEach(x => { $('tab-' + x).hidden = x !== t; });
  document.querySelectorAll('.lnav a[data-tab]').forEach(a => a.classList.toggle('on', a.dataset.tab === t));
  if (t === 'quiz' && !quiz.started) newRound();
}
document.querySelectorAll('.lnav a[data-tab]').forEach(a => a.onclick = e => {
  e.preventDefault(); history.replaceState(null, '', '#' + a.dataset.tab); showTab(a.dataset.tab);
});

// ------------------------------------------------------------ 요인 실험실
const lab = { dir: 1, p: 50, out: 'none', fx: 0, oil: 0, econ: 0, scen: null };
const ECON = { '-2': '침체', '-1': '둔화', '0': '보통', '1': '호조', '2': '호황' };
const FNAME = { rate: '금리', mkt: '시장 심리', fx: '환율', oil: '유가', econ: '경기' };

function surprise() {
  if (lab.out === 'none') return 0;
  const p = lab.p / 100;
  return lab.out === 'yes' ? lab.dir * (1 - p) : -lab.dir * p;   // 예상했던 만큼은 이미 반영됨
}
function compute() {
  const out = {};
  if (lab.scen) {
    for (const c of CODES) out[c] = { total: lab.scen.impacts[c] || 0, parts: null };
    return out;
  }
  const sp = surprise();
  for (const c of CODES) {
    const s = STOCKS[c], e = SECTORS[s.sector];
    const parts = {
      rate: e.rate * sp * U.rate, mkt: -sp * U.rate_mkt * s.beta,
      fx: e.fx * lab.fx / U.fx, oil: e.oil * lab.oil / U.oil, econ: e.econ * lab.econ * U.econ,
    };
    out[c] = { total: Object.values(parts).reduce((a, b) => a + b, 0), parts };
  }
  return out;
}

function renderLab() {
  // 컨트롤 표시
  const word = lab.dir > 0 ? '인상' : '인하';
  $('r-p-v').textContent = lab.p + '%';
  $('r-yes').textContent = '예상대로 ' + word;
  document.querySelectorAll('#r-dir button').forEach(b => b.classList.toggle('on', +b.dataset.v === lab.dir));
  document.querySelectorAll('#r-out button').forEach(b => b.classList.toggle('on', b.dataset.v === lab.out));
  $('fx-v').textContent = (lab.fx > 0 ? '+' : '') + lab.fx + '원';
  $('oil-v').textContent = (lab.oil > 0 ? '+' : '') + lab.oil + '달러';
  $('econ-v').textContent = ECON[lab.econ];
  const sp = surprise();
  $('r-surprise').innerHTML = lab.out === 'none' ? '' : lab.out === 'yes'
    ? `시장이 이미 <b>${lab.p}%</b>만큼 예상했기 때문에 실제 반응은 완전한 깜짝 ${word}의 <b>${100 - lab.p}%</b> 크기입니다.`
    : `시장은 ${lab.p}% 확률로 ${word}${lab.dir > 0 ? '을' : '를'} 기대했는데 동결됐습니다. 기대가 빗나간 만큼 <b>반대 방향</b>으로 ${lab.p}% 크기의 충격이 옵니다.`;
  document.querySelectorAll('#scen button').forEach(b => b.classList.toggle('on', lab.scen && b.dataset.k === lab.scen.key));

  const res = compute();
  const order = CODES.slice().sort((a, b) => res[b].total - res[a].total);
  const max = Math.max(5, ...order.map(c => Math.abs(res[c].total)));
  const lbl = new Set([...order.slice(0, 3), ...order.slice(-3)]);   // 값 라벨은 위·아래 3개만
  $('bars').innerHTML = order.map(c => {
    const v = res[c].total, w = Math.abs(v) / max * 50;
    const show = lbl.has(c) && Math.abs(v) >= 0.1;
    return `<div class="brow" data-c="${c}" tabindex="0">
      <span class="bname">${esc(STOCKS[c].name)}<small>${esc(STOCKS[c].sector)}</small></span>
      <span class="btrack"><i class="bfill ${v >= 0 ? 'pos' : 'neg'}" style="width:${w.toFixed(2)}%"></i>
        ${show ? `<em class="bval ${v >= 0 ? 'pos' : 'neg'}" style="${v >= 0 ? 'left' : 'right'}:calc(${50 + w}% + 6px)">${pct(v)}</em>` : ''}</span>
    </div>`;
  }).join('');
  document.querySelectorAll('.brow').forEach(r => {
    r.onmouseenter = r.onfocus = e => tip(r, res[r.dataset.c]);
    r.onmouseleave = r.onblur = () => { $('bar-tip').hidden = true; };
  });

  // 표 보기
  const cols = lab.scen ? [] : Object.keys(FNAME);
  $('lab-table').innerHTML = `<tr><th>종목</th>${cols.map(k => `<th>${FNAME[k]}</th>`).join('')}<th>합계</th></tr>` +
    order.map(c => `<tr><td>${esc(label(c))}</td>${cols.map(k => `<td>${pct(res[c].parts[k])}</td>`).join('')}` +
      `<td class="${cls(res[c].total)}"><b>${pct(res[c].total)}</b></td></tr>`).join('');

  // 해설
  const best = order[0], worst = order[order.length - 1];
  const moved = Math.abs(res[best].total) >= 0.1 || Math.abs(res[worst].total) >= 0.1;
  let why = '', title = '예상 주가 반응', sub = '왼쪽에서 요인을 움직이거나 아래 실제 사건을 골라 보세요.';
  if (lab.scen) {
    title = lab.scen.title; sub = '실제 게임에 나오는 사건입니다.';
    why = `<p>${esc(lab.scen.why)}</p><a href="${LEARN.factorsUrl}#${lab.scen.factor}">관련 요인 자세히 →</a>`;
  } else if (moved) {
    const lines = [];
    if (sp > 0) lines.push(['rate', `예상보다 금리가 <b>높아졌습니다</b>. 예대마진이 커지는 은행은 유리, 미래 가치가 깎이는 성장주(바이오·2차전지·게임)와 빚이 많은 건설은 불리합니다.`]);
    if (sp < 0) lines.push(['rate', `예상보다 금리가 <b>낮아졌습니다</b>. 성장주와 건설이 유리하고, 은행은 불리합니다.`]);
    if (lab.fx > 0) lines.push(['fx', '원화가 약해져 수출 기업(자동차·반도체·해운)은 같은 달러로 원화를 더 받고, 달러 비용이 큰 항공·식품은 불리합니다.']);
    if (lab.fx < 0) lines.push(['fx', '원화가 강해져 수출 기업의 원화 이익이 줄고, 달러로 연료·원료를 사는 항공·식품이 유리해집니다.']);
    if (lab.oil > 0) lines.push(['oil', '유가가 올라 정유사는 재고 가치가 오르고, 연료비가 큰 항공·해운은 비용이 늘어납니다.']);
    if (lab.oil < 0) lines.push(['oil', '유가가 내려 항공사의 연료비 부담이 줄고, 정유사는 재고 평가손실을 봅니다.']);
    if (lab.econ > 0) lines.push(['econ', '경기가 좋아져 해운·반도체·항공 같은 경기민감주가 크게 오르고, 통신·식품 같은 방어주는 덜 움직입니다.']);
    if (lab.econ < 0) lines.push(['econ', '경기가 나빠져 경기민감주가 크게 떨어지고, 매출이 꾸준한 방어주는 덜 떨어집니다.']);
    why = lines.map(([f, t]) => `<p>${t} <a href="${LEARN.factorsUrl}#${f}">왜?</a></p>`).join('');
    if (lines.length > 1) why += '<p class="muted">여러 요인이 겹치면 효과가 더해지거나 서로 상쇄됩니다. 표로 보기에서 요인별로 나눠 볼 수 있습니다.</p>';
  }
  if (moved) {
    why = `<div class="bw"><span>가장 유리 <b class="${cls(res[best].total)}">${esc(STOCKS[best].name)} ${pct(res[best].total)}</b></span>` +
      `<span>가장 불리 <b class="${cls(res[worst].total)}">${esc(STOCKS[worst].name)} ${pct(res[worst].total)}</b></span></div>` + why;
  }
  $('lab-title').textContent = title; $('lab-sub').textContent = sub;
  $('lab-why').innerHTML = why;
}
function tip(row, r) {
  const t = $('bar-tip'), c = row.dataset.c;
  t.innerHTML = `<b>${esc(label(c))}</b><div class="tt-total ${cls(r.total)}">${pct(r.total)}</div>` +
    (r.parts ? Object.entries(r.parts).filter(([, v]) => Math.abs(v) >= 0.05)
      .map(([k, v]) => `<div class="tt-row"><span>${FNAME[k]}</span><span>${pct(v)}</span></div>`).join('') : '') +
    `<div class="tt-row muted"><span>베타</span><span>${STOCKS[c].beta}</span></div>`;
  const b = row.getBoundingClientRect();
  t.hidden = false;
  t.style.top = (window.scrollY + b.top - t.offsetHeight - 6) + 'px';
  t.style.left = Math.min(window.scrollX + b.left + b.width / 2 - t.offsetWidth / 2, window.scrollX + innerWidth - t.offsetWidth - 12) + 'px';
}

const manual = () => { lab.scen = null; };
document.querySelectorAll('#r-dir button').forEach(b => b.onclick = () => { manual(); lab.dir = +b.dataset.v; renderLab(); });
document.querySelectorAll('#r-out button').forEach(b => b.onclick = () => { manual(); lab.out = b.dataset.v; renderLab(); });
[['r-p', 'p'], ['fx', 'fx'], ['oil', 'oil'], ['econ', 'econ']].forEach(([id, k]) =>
  $(id).oninput = e => { manual(); lab[k] = +e.target.value; renderLab(); });
function resetLab() {
  Object.assign(lab, { dir: 1, p: 50, out: 'none', fx: 0, oil: 0, econ: 0, scen: null });
  $('r-p').value = 50; $('fx').value = 0; $('oil').value = 0; $('econ').value = 0;
}
$('lab-reset').onclick = () => { resetLab(); renderLab(); };
$('scen').innerHTML = SCEN.map(s => `<button type="button" data-k="${s.key}"><i>${esc(s.tag)}</i>${esc(s.title)}</button>`).join('');
document.querySelectorAll('#scen button').forEach(b => b.onclick = () => {
  resetLab(); lab.scen = SCEN.find(s => s.key === b.dataset.k); renderLab();
  $('bars').scrollIntoView({ behavior: 'smooth', block: 'center' });
});

// ------------------------------------------------------------ 퀴즈
const quiz = { started: false, qs: [], i: 0, score: 0, answered: false };

// 금리 결정 결과가 종목에 주는 효과 (실험실과 같은 식)
function rateImpact(c, dir, p, happened) {
  const sp = happened ? dir * (1 - p) : -dir * p, s = STOCKS[c];
  return SECTORS[s.sector].rate * sp * U.rate - sp * U.rate_mkt * s.beta;
}
const code = sector => CODES.find(c => STOCKS[c].sector === sector);

function scenarioQuestions() {
  const qs = [];
  for (const s of SCEN) {
    const vals = CODES.map(c => [c, s.impacts[c] || 0]).sort((a, b) => b[1] - a[1]);
    for (const up of [true, false]) {
      const list = up ? vals : vals.slice().reverse();
      const [best, bv] = list[0];
      if (Math.abs(bv) < 2 || (up ? bv <= 0 : bv >= 0)) continue;
      const others = list.filter(([, v]) => Math.abs(v - bv) >= 2).map(([c]) => c);
      if (others.length < 3) continue;
      const choices = shuffle([best, ...shuffle(others).slice(0, 3)]);
      qs.push({
        q: `📰 <b>"${esc(s.title)}"</b><br>이 뉴스에 가장 크게 <b class="${up ? 'up' : 'down'}">${up ? '오를' : '떨어질'}</b> 종목은?`,
        choices: choices.map(label), answer: choices.indexOf(best),
        why: s.why + ' (' + choices.map(c => `${STOCKS[c].name} ${pct(s.impacts[c] || 0)}`).join(' · ') + ')',
        factor: s.factor,
      });
    }
  }
  return qs;
}
function corpQuestions() {
  return CORP.map(e => ({
    q: `📢 <b>"${esc(e.title)}"</b><br>A사 주가는 어떻게 될까요?`,
    choices: ['오를 가능성이 높다', '떨어질 가능성이 높다'], answer: e.move > 0 ? 0 : 1,
    why: e.why, factor: e.factor,
  }));
}
function conceptQuestions() {
  const bank = code('은행'), bio = code('바이오');
  const b90 = rateImpact(bank, 1, 0.9, true), bio80 = rateImpact(bio, 1, 0.8, false);
  const trip = Math.round(1_000_000 * (LEARN.fee * 2 + LEARN.tax));
  return [
    { q: '시장이 기준금리 <b>인상 확률 90%</b>를 예상했고, 실제로 인상됐습니다. 은행주는?',
      choices: ['크게 오른다', '조금 오른다', '떨어진다'], answer: 1,
      why: `이미 90%가 예상돼 가격에 들어가 있어서 남은 놀라움은 10%뿐입니다. 게임 계산으로 ${STOCKS[bank].name}는 약 ${pct(b90)}입니다.`, factor: 'priced' },
    { q: '시장이 <b>인상 확률 80%</b>를 예상했는데 금리가 <b>동결</b>됐습니다. 바이오주는?',
      choices: ['크게 오른다', '조금 떨어진다', '변화 없다'], answer: 0,
      why: `예상보다 금리가 낮게 나온 셈입니다. 금리에 가장 민감한 성장주가 크게 오릅니다. 게임 계산으로 ${STOCKS[bio].name}는 약 ${pct(bio80)}입니다.`, factor: 'priced' },
    { q: '증권가가 <b>"실적이 아주 좋을 것"</b>이라고 해서 며칠간 올랐던 회사가, 실제로 좋은 실적을 냈습니다. 주가는?',
      choices: ['크게 오른다', '거의 안 오른다', '반드시 폭락한다'], answer: 1,
      why: '좋은 실적은 이미 기대감으로 가격에 들어가 있습니다. 그래서 "소문에 사서 뉴스에 팔아라"라는 말이 있습니다. 반대로 기대에 못 미치면 크게 떨어집니다.', factor: 'priced' },
    { q: '베타가 <b>1.5</b>인 종목이 있습니다. 시장 전체가 <b>2%</b> 떨어지면 이 종목은 평균적으로?',
      choices: ['-1%', '-2%', '-3%', '-4%'], answer: 2,
      why: '베타는 시장이 1% 움직일 때 평균 몇 % 움직이는지입니다. 1.5 × 2% = 3%.', factor: 'sector' },
    { q: `<b>100만 원</b>어치를 사서 같은 가격에 바로 팔았습니다. 이 게임의 수수료·세금으로 나가는 돈은 약?`,
      choices: [`${Math.round(trip / 10).toLocaleString()}원`, `${trip.toLocaleString()}원`, `${(trip * 10).toLocaleString()}원`], answer: 1,
      why: `매수·매도 수수료 각 ${(LEARN.fee * 100).toFixed(3)}%에 매도 거래세 ${(LEARN.tax * 100).toFixed(1)}%가 붙어 약 ${trip.toLocaleString()}원입니다. 자주 사고팔수록 쌓입니다.`, factor: 'limit' },
    { q: '회사가 <b>유상증자</b>를 발표하면 보통 주가가 떨어지는 이유는?',
      choices: ['주식 수가 늘어 기존 주주의 몫이 줄어서', '회사가 이익을 너무 많이 내서', '배당이 늘어나서'], answer: 0,
      why: '새 주식을 찍으면 피자를 더 많은 조각으로 자르는 셈이라 한 조각(한 주)의 가치가 줄어듭니다.', factor: 'corp' },
    { q: '원·달러 환율이 크게 올랐습니다(원화 약세). 가장 유리한 업종은?',
      choices: ['항공', '자동차', '식품'], answer: 1,
      why: '수출 비중이 높은 자동차는 같은 달러를 벌어도 원화로 더 많이 받습니다. 항공·식품은 달러 비용이 늘어 불리합니다.', factor: 'fx' },
    { q: '출처를 알 수 없는 <b>"인수설"</b>로 주가가 급등 중입니다. 가장 현명한 행동은?',
      choices: ['지금이라도 최대한 산다', '공식 발표를 기다린다', '빚을 내서라도 산다'], answer: 1,
      why: '확인되지 않은 소문은 사실무근으로 끝날 때가 더 많고, 그러면 오른 만큼 한 번에 빠집니다. 게임에서도 인수설의 약 3분의 2는 사실무근입니다.', factor: 'sentiment' },
    { q: '인수설이 돈 뒤 <b>"인수 후보 기업이 정면 부인"</b>, <b>"매수는 대부분 개인, 기관은 차익 실현"</b>이라는 단서가 나왔습니다. 어떻게 볼까요?',
      choices: ['사실일 가능성이 높아졌다', '사실무근일 가능성이 높아졌다', '단서는 아무 의미가 없다'], answer: 1,
      why: '단서 하나는 틀릴 수 있지만, 서로 다른 곳에서 나온 두 단서가 같은 쪽을 가리키면 그쪽일 가능성이 커집니다. 게임에서도 인수설 단서가 모두 부정적이면 대부분 사실무근으로 끝납니다.', factor: 'sentiment' },
    { q: '유가가 급등할 때 <b>함께 들고 있으면</b> 충격을 줄여 주는 조합은?',
      choices: ['항공 + 해운', '정유 + 항공', '항공 + 식품'], answer: 1,
      why: '유가가 오르면 정유는 유리하고 항공은 불리합니다. 반대로 움직이는 둘을 섞으면 한쪽의 손실을 다른 쪽이 메워 줍니다(분산투자).', factor: 'oil' },
  ];
}
function newRound() {
  quiz.started = true;
  const pick = (a, n) => shuffle(a.slice()).slice(0, n);
  quiz.qs = shuffle([...pick(scenarioQuestions(), 5), ...pick(corpQuestions(), 2), ...pick(conceptQuestions(), 3)]);
  quiz.i = 0; quiz.score = 0;
  renderQ();
}
function renderQ() {
  const box = $('quiz');
  if (quiz.i >= quiz.qs.length) {
    const best = Math.max(store.get('learn.best', 0), quiz.score);
    store.set('learn.best', best);
    const msg = quiz.score >= 9 ? '투자 게임에서 바로 써먹어 보세요! 🎉' : quiz.score >= 6 ? '좋아요! 틀린 문제의 요인을 한 번 더 읽어 보세요.'
      : '"가격변동 요인"과 요인 실험실로 원리를 먼저 익혀 보세요.';
    box.innerHTML = `<div class="q-end"><div class="muted">결과</div><div class="q-score">${quiz.score} / ${quiz.qs.length}</div>
      <p>${msg}</p><p class="muted">최고 기록 ${best}점</p>
      <button type="button" class="go buy" id="q-again">새 문제로 다시 풀기</button></div>`;
    $('q-again').onclick = newRound;
    return;
  }
  const q = quiz.qs[quiz.i];
  quiz.answered = false;
  box.innerHTML = `<div class="q-top"><span>문제 ${quiz.i + 1} / ${quiz.qs.length}</span><span>맞힌 문제 ${quiz.score}</span></div>
    <div class="q-prog"><i style="width:${quiz.i / quiz.qs.length * 100}%"></i></div>
    <div class="q-q">${q.q}</div>
    <div class="q-choices">${q.choices.map((c, i) => `<button type="button" data-i="${i}">${esc(c)}</button>`).join('')}</div>
    <div class="q-fb" id="q-fb"></div>`;
  box.querySelectorAll('.q-choices button').forEach(b => b.onclick = () => answer(+b.dataset.i));
}
function answer(i) {
  if (quiz.answered) return;
  quiz.answered = true;
  const q = quiz.qs[quiz.i], ok = i === q.answer;
  if (ok) quiz.score++;
  document.querySelectorAll('.q-choices button').forEach((b, j) => {
    b.disabled = true;
    if (j === q.answer) b.classList.add('right');
    else if (j === i) b.classList.add('wrong');
  });
  $('q-fb').innerHTML = `<div class="q-res ${ok ? 'ok' : 'no'}">${ok ? '⭕ 정답!' : '❌ 아쉬워요'}</div>
    <p>${esc(q.why)}</p><a href="${LEARN.factorsUrl}#${q.factor}" target="_blank" rel="noopener">관련 요인 읽기 →</a>
    <button type="button" class="go buy" id="q-next">${quiz.i + 1 < quiz.qs.length ? '다음 문제' : '결과 보기'}</button>`;
  $('q-next').onclick = () => { quiz.i++; renderQ(); };
}

// ------------------------------------------------------------ 시작
renderLab();
showTab(location.hash.slice(1));
window.addEventListener('hashchange', () => showTab(location.hash.slice(1)));
})();
