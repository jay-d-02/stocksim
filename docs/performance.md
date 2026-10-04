# 성능 측정 기록

같은 PC(Windows + Docker Desktop)에서 5분 틱 요청 60회를 보내고 측정했다.
WAL은 `pg_current_wal_lsn()` 차이로 잰 PostgreSQL 쓰기량이다.

| 구조 | 틱당 WAL | 응답 중앙값 | p95 |
|---|---|---|---|
| v1: Flask + 게임 상태 전체를 JSONB 한 컬럼에 매 틱 UPDATE | 4.9 KB | 6.6 ms | 8.6 ms |
| v2 첫 버전: FastAPI + Redis (틱 요청에 JSON 본문) | 0.2 KB | 54.0 ms | 57.5 ms |
| **v2: 틱 요청을 본문 없이 (`?since=`)** | **0.2 KB** | **8.1 ms** | **11.2 ms** |

## 1. 쓰기량: 4.9 KB → 0.2 KB (약 1/25)

v1은 틱마다 게임 상태(압축 후 약 4.4 KB)를 담은 행을 통째로 UPDATE했다. PostgreSQL은 UPDATE를 새 행 버전으로
쓰기 때문에 WAL과 죽은 행(dead tuple)이 틱마다 쌓이고 VACUUM 부담이 된다. 30일 게임 한 판이 2,340틱이므로
한 판에 약 11 MB의 WAL이 나왔다.

v2는 자주 바뀌는 시장 상태를 Redis에 두고, PostgreSQL에는 틱마다 게임 행의 `current_tick`(정수 1개)만
갱신한다. 뉴스·체결·일봉은 생길 때만 INSERT한다.

## 2. 응답 시간 회귀: 54 ms → 8 ms

v2 첫 버전은 쓰기량은 줄었지만 응답이 8배 느려졌다. 원인을 좁혀 간 순서:

| 확인 | 결과 | 결론 |
|---|---|---|
| 서비스 코드만 컨테이너 안에서 측정 (`scripts/profile_tick.py`) | 3.6 ms (Redis 읽기·쓰기 0.6, 커밋 1.2) | 코드는 빠르다. HTTP 계층 문제 |
| 같은 요청을 컨테이너 안에서 | 1.9 ms | Windows 호스트 → 컨테이너 경로에서만 느리다 |
| uvicorn HTTP 파서(httptools·h11), 이벤트 루프(uvloop·asyncio) 교체 | 모두 46 ms | 서버 구현 문제가 아니다 |
| 본문 없는 POST, 본문을 읽지 않는 엔드포인트에 본문 보내기 | 1 ms 안팎 | 서버가 **요청 본문을 기다리는 동안** 지연된다 |
| 본문 검증에서 바로 실패하는 요청(422) | 46 ms | 핸들러와 무관, 본문 수신 자체가 늦다 |
| 같은 틱 요청을 본문 없이 | 51 → 7.4 ms | 확정 |

**원인**: 클라이언트가 요청 헤더와 본문을 따로 보내면, 본문은 Nagle 알고리즘 때문에 헤더의 ACK를 기다린다.
서버 쪽 리눅스 커널은 keep-alive로 재사용되는 연결에서 ACK를 지연(delayed ACK, 약 40 ms)한다.
v1의 gunicorn sync 워커는 요청마다 연결을 새로 열었고, 새 연결은 quick-ACK 모드로 시작해 이 문제가 드러나지 않았다.

**해결**: 틱은 초당 여러 번 오는 요청인데 보낼 값은 뉴스 번호 하나(`since`)뿐이다. 필터 성격의 값이라
쿼리 문자열이 더 맞기도 해서 `POST /games/{id}/tick?since=N`으로 바꿨다. 주문처럼 본문이 꼭 필요한 요청은
가끔 오므로 그대로 둔다. 운영 환경에서 Cloudflare 같은 프록시 뒤에 두면 클라이언트 TCP는 프록시에서 끝나
이 영향이 더 줄어든다.

## 재현

```bash
docker compose exec app python -m scripts.profile_tick      # 구간별 시간
```
