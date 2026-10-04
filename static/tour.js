// 게임 화면 사용법 안내: 화면 요소를 하나씩 비추며 설명한다.
// 초보(투자 경험 = 처음이에요)는 첫 방문 때 자동으로, 누구나 '❓ 사용법' 버튼으로 다시 볼 수 있다.
(() => {
const STEPS = [
  { el: '.dclock', title: '게임 속 시계', text: '하루는 09:00에 열려 15:30에 닫혀요. 30거래일 동안 가상자금으로 투자합니다. 지금은 시간이 멈춰 있어요.' },
  { el: '.dspeed', title: '시간 흐르게 하기', text: '▶ x1을 누르면 5분씩 시간이 흘러요. x2·x4는 더 빠르게. 스페이스바로 언제든 멈출 수 있어요.' },
  { el: '.dfeed', title: '알림: 여기가 제일 중요해요', text: '금리·환율·실적 같은 사건이 뉴스로 떠요. 위쪽 "예정된 일정"은 이틀 뒤에 있을 발표를 미리 알려 줘요. 뉴스를 누르면 해설이 나와요.' },
  { el: '.dmap', title: '세계 상황판', text: '사건이 어디서 터졌는지 지도에 핀으로 보여 줘요. 해외 사건도 국내 종목에 영향을 줘요.' },
  { el: '.dinds', title: '시장 지표', text: '종합지수·금리·환율·유가예요. 누르면 이 값이 오를 때 어떤 업종이 좋고 나쁜지 설명을 볼 수 있어요.' },
  { el: '.dboard', title: '시세판', text: '12개 가상 종목의 지금 가격이에요. 종목을 누르면 오른쪽(휴대폰은 아래) 주문 창이 그 종목으로 바뀌어요.' },
  { el: '.dstock', title: '사고팔기', text: '수량을 정하고 매수·매도를 누르면 지금 가격으로 바로 체결돼요. 살 때 수수료, 팔 때 세금이 붙어요.' },
  { el: '.dauto', title: '처음엔 이걸 켜 두세요', text: '사건이 터지면 시간이 자동으로 멈춰서, 천천히 뉴스를 읽고 판단할 수 있어요. 이제 ▶ x1을 눌러 시작해 보세요!' },
];

const cfg = typeof TOUR !== 'undefined' ? TOUR : {};      // game.html 이 넣어 줌 (const라 window 속성은 아님)
const key = 'tour.done.' + (cfg.user || '');
const done = () => { try { return localStorage.getItem(key) === '1'; } catch (e) { return false; } };
const markDone = () => { try { localStorage.setItem(key, '1'); } catch (e) {} };

let i = 0, box = null;
const visible = s => { const el = document.querySelector(s.el); return el && el.getClientRects().length ? el : null; };

function place() {
  const s = STEPS[i], el = visible(s);
  if (!el) return;
  const r = el.getBoundingClientRect(), pad = 6;
  const hole = box.querySelector('.tour-hole'), card = box.querySelector('.tour-card');
  Object.assign(hole.style, { top: r.top - pad + 'px', left: r.left - pad + 'px', width: r.width + pad * 2 + 'px', height: r.height + pad * 2 + 'px' });
  const cw = card.offsetWidth, ch = card.offsetHeight, vw = innerWidth, vh = innerHeight;
  let top = r.bottom + 14;                                   // 아래에 자리가 없으면 위, 그래도 없으면 화면 안쪽
  if (top + ch > vh - 12) top = r.top - ch - 14;
  if (top < 12) top = Math.min(vh - ch - 12, Math.max(12, r.top + 12));
  const left = Math.min(vw - cw - 16, Math.max(16, r.left));
  Object.assign(card.style, { top: top + 'px', left: left + 'px' });
}

function render() {
  const s = STEPS[i], last = i === STEPS.length - 1;
  box.querySelector('.tour-card').innerHTML = `
    <small>${i + 1} / ${STEPS.length}</small><h3 id="tour-title">${s.title}</h3><p>${s.text}</p>
    <div class="tour-dots">${STEPS.map((_, k) => `<i class="${k === i ? 'on' : ''}"></i>`).join('')}</div>
    <div class="tour-btns">
      <button type="button" class="skip">${last ? '' : '건너뛰기'}</button>
      ${i ? '<button type="button" class="nav prev">이전</button>' : ''}
      <button type="button" class="nav next">${last ? '시작하기' : '다음'}</button>
    </div>`;
  const el = visible(s);
  if (el) el.scrollIntoView({ block: 'center', behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' });
  setTimeout(place, 300);
  place();
  box.querySelector('.next').focus();
}

function go(d) {
  do { i += d; } while (i > 0 && i < STEPS.length && !visible(STEPS[i]));   // 화면에 없는 요소는 건너뜀
  if (i >= STEPS.length || i < 0) return close();
  render();
}

function close() {
  markDone();
  if (box) box.remove();
  box = null;
  removeEventListener('resize', place);
  removeEventListener('scroll', place, true);
  document.removeEventListener('keydown', onKey, true);
}

function onKey(e) {
  if (!box) return;
  if (e.key === 'Escape') { e.preventDefault(); close(); }
  else if (e.key === 'ArrowRight' || e.key === 'Enter') { e.preventDefault(); e.stopPropagation(); go(1); }
  else if (e.key === 'ArrowLeft') { e.preventDefault(); go(-1); }
  else if (e.key === ' ') { e.preventDefault(); e.stopPropagation(); }  // 안내 중엔 스페이스바로 시간이 흐르지 않게
}

function open() {
  if (box) return;
  i = 0;
  box = document.createElement('div');
  box.className = 'tour';
  box.setAttribute('role', 'dialog');
  box.setAttribute('aria-modal', 'true');
  box.setAttribute('aria-labelledby', 'tour-title');
  box.innerHTML = '<div class="tour-hole"></div><div class="tour-card"></div>';
  box.addEventListener('click', e => {
    if (e.target.closest('.next')) go(1);
    else if (e.target.closest('.prev')) go(-1);
    else if (e.target.closest('.skip')) close();
  });
  document.body.appendChild(box);
  addEventListener('resize', place);
  addEventListener('scroll', place, true);
  document.addEventListener('keydown', onKey, true);
  render();
}

document.querySelectorAll('[data-tour]').forEach(b => b.addEventListener('click', open));
if (cfg.auto && !done()) setTimeout(open, 600);           // 지도·시세판이 자리 잡은 뒤
})();
