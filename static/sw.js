// 서비스 워커: 게임은 서버에서 진행되므로 화면·API는 항상 네트워크 우선.
// 정적 파일은 최신본을 받되 끊기면 저장본으로, CDN 라이브러리(버전 고정)는 저장본 우선.
// 로그인한 화면(HTML)과 API 응답은 저장하지 않는다 (다른 사람 정보가 남지 않도록).
const VERSION = 'v1';
const STATIC = `static-${VERSION}`, CDN = `cdn-${VERSION}`;
const SHELL = ['/offline', '/static/style.css', '/static/app.js', '/static/icons/icon-192.png'];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(STATIC).then(c => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', e => {
  e.waitUntil(caches.keys()
    .then(keys => Promise.all(keys.filter(k => ![STATIC, CDN].includes(k)).map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener('fetch', e => {
  const req = e.request, url = new URL(req.url);
  if (req.method !== 'GET') return;

  if (req.mode === 'navigate') {                         // 화면: 네트워크, 끊기면 오프라인 안내
    e.respondWith(fetch(req).catch(() => caches.match('/offline')));
    return;
  }
  if (url.origin === location.origin && url.pathname.startsWith('/static/')) {
    e.respondWith(fetch(req).then(res => {
      if (res.ok) { const copy = res.clone(); caches.open(STATIC).then(c => c.put(req, copy)); }
      return res;
    }).catch(() => caches.match(req)));
    return;
  }
  if (url.hostname === 'cdn.jsdelivr.net') {             // 주소에 버전이 있어 바뀌지 않음
    e.respondWith(caches.match(req).then(hit => hit || fetch(req).then(res => {
      if (res.ok) { const copy = res.clone(); caches.open(CDN).then(c => c.put(req, copy)); }
      return res;
    })));
  }
  // 그 밖(API 등)은 브라우저 기본 동작 = 항상 네트워크
});
