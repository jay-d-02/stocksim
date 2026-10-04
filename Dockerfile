# ---- 공통: 런타임 의존성
FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TZ=Asia/Seoul
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

# ---- 테스트: 개발 의존성 + 코드 (docker compose --profile test run --rm tests)
FROM base AS test
COPY requirements-dev.txt .
RUN pip install -r requirements-dev.txt
COPY . .
CMD ["pytest"]

# ---- 실행 (기본 타깃)
FROM base AS app
COPY . .
RUN useradd --create-home --uid 10001 app && chown -R app /app
USER app
EXPOSE 8000
# DB가 없으면 만들고 → 마이그레이션 → 서버 시작 (워커 2개가 같은 게임을 다뤄도 DB 행 잠금으로 직렬화됨)
CMD ["sh", "-c", "python -m scripts.ensure_db && alembic upgrade head && exec uvicorn server.main:app --host 0.0.0.0 --port 8000 --workers 2 --proxy-headers --forwarded-allow-ips='*'"]
