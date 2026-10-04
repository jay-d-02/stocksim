# legacy — 이전 버전 (Flask)

v2(FastAPI + PostgreSQL + Redis)로 옮기기 전의 코드를 참고용으로 보관합니다. 실행 대상이 아닙니다.

- `flask_app/app.py` — Flask 앱. 실제 증권사 시세(KIS·키움·LS·FinanceDataReader)로 하는 모의투자와
  투자 게임이 한 앱에 있었고, 게임 상태 전체를 PostgreSQL JSONB 한 컬럼에 틱마다 통째로 저장했습니다.
- `flask_app/providers.py` — 증권사 API 여러 곳을 순서대로 시도하는 시세 조회 (API 키는 환경변수로만 받음)

v2에서 달라진 점과 이유는 저장소 루트의 README.md와 docs/ 를 보세요.
회원 계정은 `python -m scripts.import_legacy_users` 로 새 DB에 옮길 수 있습니다.
