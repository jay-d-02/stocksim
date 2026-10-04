"""API 공통 의존성"""
from fastapi import Depends
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_session
from ..runtime import get_store
from ..services.games import GameService


def runtime_store():
    return get_store()


def game_service(db: Session = Depends(get_session), store=Depends(runtime_store)):
    return GameService(db, store, settings)
