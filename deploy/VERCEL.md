# Vercel 운영 안내

GitHub에 push하면 Vercel이 자동으로 배포한다. DB는 Neon(PostgreSQL), 게임 상태는 Upstash(Redis)를 쓴다.
셋 다 무료 플랜으로 시작할 수 있다. 리눅스 서버 운영(`deploy/LINUX.md`)과 같은 코드라서 둘 중 하나만 골라도 되고 둘 다 운영해도 된다.

## 구조

| | 리눅스 서버 (LINUX.md) | Vercel |
|---|---|---|
| 앱 | Docker 컨테이너 | `api/index.py` → 서버리스 함수 (`vercel.json`) |
| PostgreSQL | 컨테이너 | Neon |
| Redis | 컨테이너 | Upstash |
| 마이그레이션 | 앱이 켜질 때 자동 | 배포 전에 PC에서 `scripts.prepare_db` (3번) |
| 주소 | Tailscale Funnel | `https://프로젝트이름.vercel.app` 또는 내 도메인 |

게임 시간은 브라우저가 1.5초마다 보내는 tick 요청으로 흐르기 때문에 상시 켜진 서버가 없어도 된다.

## 무료 한도 (게임 한 판 = tick 요청 약 1,200번)

- Vercel Hobby: 비상업용만 가능. 함수 호출 수와 실행 시간에 월 한도가 있다
- Upstash 무료: 월 명령 수 한도가 있다 (tick 1번에 Redis 명령 몇 개)
- Neon 무료: 월 사용 시간 한도. 5분 동안 요청이 없으면 잠들고 첫 요청 때 1초쯤 걸려 깨어난다

정확한 수치는 각 서비스의 요금제 페이지에서 확인한다. 각 대시보드의 Usage 화면을 옮긴 뒤 1주일쯤 지켜본다.

## 1. 프로젝트 만들기 (Vercel)

1. https://vercel.com 에 GitHub 계정으로 가입 (Hobby)
2. **Add New → Project** → 이 저장소 **Import**
   - 저장소가 GitHub 조직 소유라서 목록에 안 보이면 개인 계정으로 fork해서 연결한다 (Hobby는 개인 저장소만 지원)
3. Framework Preset은 **Other**, 나머지 설정은 비워 둔다. `vercel.json`이 설정을 대신한다
4. 아직 **Deploy를 누르지 않는다** (DB를 먼저 만들어야 한다). 이미 눌렀다면 실패해도 상관없다

## 2. DB·Redis 연결 (Vercel 대시보드)

프로젝트 → **Storage** 탭:

1. **Neon** (Postgres) → Create. 리전은 **Singapore (ap-southeast-1)**
2. **Upstash for Redis** → Create. 리전은 **Singapore (ap-southeast-1)**
3. 두 서비스 모두 이 프로젝트에 연결(Connect)한다. 환경변수가 자동으로 들어간다

> 리전을 맞추는 이유: 요청 하나마다 DB를 여러 번 오가기 때문에 앱과 DB는 같은 곳에 있어야 빠르다.
> `vercel.json`의 `"regions": ["sin1"]`가 싱가포르다. DB를 다른 리전에 만들었다면 이 값도 맞춰 바꾼다
> (도쿄 `hnd1`, 서울 `icn1`).

**Settings → Environment Variables**에서 아래 두 가지를 확인한다:

- `DATABASE_URL_UNPOOLED`가 있어야 한다 (호스트 이름에 `-pooler`가 없는 직접 연결 주소).
  없으면 Neon 콘솔 → Connect에서 "Connection pooling"을 끈 주소를 복사해 `DATABASE_URL_UNPOOLED`로 추가한다
- `REDIS_URL` 또는 `KV_URL`이 `rediss://`로 시작해야 한다.
  REST 주소(`KV_REST_API_URL`)만 있으면 Upstash 콘솔에서 `rediss://...` 주소를 복사해 `REDIS_URL`로 추가한다

## 3. 환경변수 입력

Settings → Environment Variables (Production·Preview 모두 체크):

| 이름 | 값 |
|---|---|
| `SECRET_KEY` | `python -c "import secrets; print(secrets.token_urlsafe(48))"`로 만든 값. **서버 `.env`와 다른 값**으로 |
| `SECURE_COOKIES` | `true` |
| `PUBLIC_URL` | `https://프로젝트이름.vercel.app` (도메인을 붙였다면 그 주소) |
| `PRIVACY_CONTACT` | 개인정보 문의처 |
| `SMTP_HOST` `SMTP_PORT` `SMTP_USER` `SMTP_PASSWORD` `SMTP_FROM` | 서버 `.env`와 같게 (비밀번호 찾기 메일) |
| `NAVER_CLIENT_ID` `NAVER_CLIENT_SECRET` | 서버 `.env`와 같게 (오늘의 뉴스) |

`DATABASE_URL`, `REDIS_URL` 같은 DB 주소는 2번에서 자동으로 들어갔으니 직접 넣지 않는다.

## 4. DB 준비 (윈도우 PC, 처음 한 번 + 마이그레이션이 추가될 때마다)

Neon의 직접 연결 주소(`DATABASE_URL_UNPOOLED` 값)를 복사한다. Vercel 환경변수 화면에서 눈 모양 아이콘을 누르면 보인다.

### 기존 회원·기록을 옮길 때 (선택)

서버에서 덤프를 떠서 PC로 가져온다 (`LINUX.md` 4번과 같은 방법):

```bash
# 서버
cd ~/stocksim
docker compose exec -T db sh -c 'pg_dump -U $POSTGRES_USER -d market_sim -Fc -f /tmp/stocksim.dump'
docker cp stocksim-db:/tmp/stocksim.dump .
```

```powershell
# PC (PowerShell, 프로젝트 폴더)
scp -i 개인키 ubuntu@서버IP:~/stocksim/stocksim.dump .
docker run --rm -v "${PWD}:/w" postgres:17-alpine pg_restore --no-owner --no-acl -d "Neon직접연결주소" /w/stocksim.dump
```

옮긴 다음부터는 서버와 Vercel의 데이터가 따로 쌓인다. 완전히 옮기는 경우라면 덤프를 뜨기 전에 서버를 공지 후 멈춘다.

### 마이그레이션 + 종목 마스터

Docker Desktop을 켜고 PowerShell에서 실행한다:

```powershell
docker compose build app
docker compose run --rm --no-deps -e DATABASE_URL="Neon직접연결주소" app python -m scripts.prepare_db
```

"마이그레이션·종목 마스터 완료"가 나오면 끝이다. 데이터를 옮긴 경우에도 실행한다 (이미 적용된 부분은 건너뛴다).

## 5. 배포

Vercel → Deployments → **Redeploy** (또는 GitHub에 push).
그 다음부터는 아래처럼 자동으로 배포된다.

- `main` 브랜치에 push → 실제 주소(Production)에 배포
- 다른 브랜치에 push, PR 생성 → 미리보기 주소(Preview)에 배포. **Preview도 같은 DB를 쓴다**

확인할 것:
- `https://주소/health` 접속
- 가입 → 게임 시작 → 몇 틱 진행 → 매수 → 새로고침해도 그대로인지
- 휴대폰에서 홈 화면에 추가 (PWA)

## 6. 도메인 (선택)

Settings → Domains → 도메인 입력 → 안내대로 DNS 레코드를 추가한다. HTTPS는 자동으로 적용된다.
도메인을 바꿨으면 `PUBLIC_URL`도 함께 바꾸고 Redeploy한다.

## 운영

| 할 일 | 방법 |
|---|---|
| 업데이트 | push. `migrations/versions/`에 파일이 추가됐다면 **push 전에** 4번 마이그레이션 먼저 |
| 로그 | 프로젝트 → Logs |
| 되돌리기 | Deployments → 이전 배포 → **Instant Rollback** (DB는 되돌아가지 않음) |
| 백업 | Neon은 무료 플랜에서도 최근 몇 시간 이내 시점으로 복원(Restore)할 수 있다. 오래 보관하려면 아래처럼 직접 덤프 |
| 실제 뉴스 지금 가져오기 | '오늘의 뉴스' 화면을 열면 30분마다 알아서 가져온다 |

```powershell
# 백업 (PC)
docker run --rm -v "${PWD}:/w" postgres:17-alpine pg_dump -Fc -f /w/stocksim-vercel.dump "Neon직접연결주소"
```

## 안 될 때

| 증상 | 원인·해결 |
|---|---|
| 빌드 실패 "No entrypoint" / 프레임워크 감지 오류 | Settings → Build and Deployment → Framework Preset을 **Other**로 바꾸고 Redeploy |
| 500 오류, 로그에 `relation ... does not exist` | 4번 마이그레이션을 실행하지 않음 |
| 500 오류, 로그에 Redis 연결 오류 | `REDIS_URL`이 `rediss://`(s 두 개)인지 확인 |
| 로그인해도 바로 풀림 | `SECRET_KEY`가 비었거나 Production·Preview에 서로 다른 값이 들어감 |
| 처음 접속이 1~2초 느림 | Neon이 쉬다가 깨어나는 시간. 정상 |
| 시간이 9시간 어긋남 | `DATABASE_URL_UNPOOLED`가 없어 풀러 주소로 붙음 (풀러는 시간대 설정을 무시함). 2번 확인 |
