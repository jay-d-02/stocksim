"""진행 중인 게임 상태 저장소 (Redis)

엔진 상태(dict)를 게임마다 JSON 한 덩어리로 둔다. 5분 틱마다 통째로 바뀌는 값이라
PostgreSQL 대신 여기에 쓰고, DB에는 공개된 기록과 원장만 남긴다.

Redis는 캐시이지 원본이 아니다.
- 돈(예수금·보유·체결)은 PostgreSQL이 원본이고, 읽을 때마다 DB 값으로 맞춘다 (services.games.reconcile)
- 상태가 사라지면 PostgreSQL의 복구 지점(game_snapshots)에서 되살린다
"""
import json

import redis

from .config import settings

FINISHED_TTL = 7 * 24 * 3600      # 끝난 게임 상태는 일주일 뒤 자동 삭제 (기록은 DB에 있음)


class RuntimeStore:
    def __init__(self, client):
        self.r = client

    @staticmethod
    def _key(game_id):
        return f"game:{game_id}:state"

    def get(self, game_id):
        raw = self.r.get(self._key(game_id))
        return json.loads(raw) if raw else None

    def put(self, game_id, state):
        ttl = FINISHED_TTL if state.get("finished") else None
        self.r.set(self._key(game_id), json.dumps(state, separators=(",", ":"), ensure_ascii=False), ex=ttl)

    def delete(self, game_id):
        self.r.delete(self._key(game_id))

    def ping(self):
        return bool(self.r.ping())


class MemoryRuntimeStore(RuntimeStore):
    """테스트용: Redis 없이 같은 동작 (JSON 왕복까지 흉내 내서 직렬화 문제도 잡음)"""

    def __init__(self):
        self.data = {}

    def get(self, game_id):
        raw = self.data.get(game_id)
        return json.loads(raw) if raw else None

    def put(self, game_id, state):
        self.data[game_id] = json.dumps(state, ensure_ascii=False)

    def delete(self, game_id):
        self.data.pop(game_id, None)

    def ping(self):
        return True


_store = None


def get_store():
    global _store
    if _store is None:
        _store = RuntimeStore(redis.Redis.from_url(settings.redis_url, socket_timeout=2))
    return _store
