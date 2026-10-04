// 모든 화면 공통: 앱(PWA) 설치, 서비스 워커 등록, 휴대폰 '더보기' 시트
(() => {
const $ = id => document.getElementById(id);
const store = {
  get(k, d) { try { const v = localStorage.getItem(k); return v === null ? d : JSON.parse(v); } catch (e) { return d; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) {} },
};

// ------------------------------------------------------------ 서비스 워커 (오프라인 안내 화면 + 정적 파일 캐시)
if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => navigator.serviceWorker.register('/sw.js').catch(() => {}));
}

// ------------------------------------------------------------ 설치 안내
const standalone = matchMedia('(display-mode: standalone)').matches || navigator.standalone === true;
const isIOS = /iphone|ipad|ipod/i.test(navigator.userAgent) ||
              (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
const phone = matchMedia('(max-width: 720px)').matches;
const bar = $('install-bar'), btns = document.querySelectorAll('.install-btn');
const snoozed = () => store.get('pwa.snooze', 0) > Date.now();
let deferred = null;

function offer(iosHelp) {
  if (standalone) return;
  btns.forEach(b => { b.hidden = false; });
  if (iosHelp) {
    $('install-msg').textContent = 'Safari 아래쪽 공유 버튼(□↑)을 누른 뒤 "홈 화면에 추가"를 고르세요.';
    bar.querySelector('.install-btn').hidden = true;      // iOS는 버튼으로 바로 설치할 수 없음
  }
  if (phone && !snoozed()) bar.hidden = false;
}
window.addEventListener('beforeinstallprompt', e => { e.preventDefault(); deferred = e; offer(false); });
if (isIOS) offer(true);
btns.forEach(b => b.onclick = async () => {
  if (deferred) {
    deferred.prompt();
    await deferred.userChoice.catch(() => {});
    deferred = null;
    bar.hidden = true;
  } else if (isIOS) {
    closeSheet(); bar.hidden = false;
  }
});
bar.querySelector('.install-x').onclick = () => {
  bar.hidden = true;
  store.set('pwa.snooze', Date.now() + 7 * 864e5);      // 일주일 동안 다시 안 띄움
};
window.addEventListener('appinstalled', () => { bar.hidden = true; btns.forEach(b => { b.hidden = true; }); });
if (standalone) document.documentElement.classList.add('standalone');

// ------------------------------------------------------------ 더보기 시트
const sheet = $('more-sheet'), more = $('more-btn');
function closeSheet() { if (sheet) { sheet.hidden = true; more.setAttribute('aria-expanded', 'false'); } }
if (sheet) {
  more.onclick = () => {
    sheet.hidden = !sheet.hidden;
    more.setAttribute('aria-expanded', String(!sheet.hidden));
  };
  sheet.onclick = e => { if (e.target === sheet) closeSheet(); };
  document.addEventListener('keydown', e => { if (e.key === 'Escape') closeSheet(); });
}
})();
