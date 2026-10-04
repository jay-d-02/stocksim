"""PostgreSQL 연결 (SQLAlchemy 2.0, psycopg 3)

엔드포인트는 동기 함수로 두고 FastAPI가 스레드 풀에서 실행한다. 드라이버가 동기이고,
요청 하나가 짧은 트랜잭션 하나라서 async로 얻는 이득보다 코드 단순함이 크다.
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import settings


class Base(DeclarativeBase):
    pass


engine = create_engine(settings.database_url, pool_size=5, max_overflow=5, pool_pre_ping=True,
                       connect_args={"options": "-c timezone=Asia/Seoul"})
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def get_session():
    """요청 하나 = 세션 하나. 커밋은 서비스가 명시적으로, 예외면 롤백"""
    with SessionLocal() as s:
        try:
            yield s
        except Exception:
            s.rollback()
            raise
