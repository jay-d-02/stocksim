"""Vercel 진입점: 모든 요청을 FastAPI 앱 하나로 받는다 (vercel.json의 rewrites, deploy/VERCEL.md)"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from server.main import app  # noqa: E402,F401
