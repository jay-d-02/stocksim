"""real_news: 실제 뉴스 (NAVER API HUB 뉴스 검색) 제목·요약·링크와 자동 분류한 요인

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-04 18:00:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0006'
down_revision = '0005'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'real_news',
        sa.Column('id', sa.BigInteger(), primary_key=True, comment='뉴스 ID'),
        sa.Column('link', sa.Text(), nullable=False, comment='원문 주소 (언론사 링크, 없으면 네이버 뉴스 링크). 중복 판단 기준'),
        sa.Column('title', sa.Text(), nullable=False, comment='제목 (HTML 태그 제거)'),
        sa.Column('summary', sa.Text(), nullable=False, comment='검색 결과의 짧은 요약 (2~3줄)'),
        sa.Column('source', sa.Text(), nullable=False, comment='원문 도메인 (예: www.yna.co.kr)'),
        sa.Column('query', sa.Text(), nullable=False, comment='이 뉴스를 찾은 검색어'),
        sa.Column('published_at', sa.DateTime(timezone=True), nullable=False, comment='기사 시각'),
        sa.Column('factors', postgresql.JSONB(), nullable=False,
                  comment='자동 분류한 요인 {rate|fx|oil|econ: 1 오름 / -1 내림 / 0 그대로}'),
        sa.Column('fetched_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False,
                  comment='가져온 시각'),
        sa.UniqueConstraint('link', name='real_news_link_key'),
        comment='실제 뉴스 제목·요약·링크와 자동 분류한 요인. 본문은 저장하지 않는다 (저작권). 30일 지나면 지움',
    )
    op.create_index('real_news_recent', 'real_news', [sa.text('published_at DESC')])


def downgrade():
    op.drop_index('real_news_recent', table_name='real_news')
    op.drop_table('real_news')
