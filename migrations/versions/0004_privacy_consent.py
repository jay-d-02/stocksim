"""privacy consent timestamp

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-27 15:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('users', sa.Column('privacy_agreed_at', sa.DateTime(timezone=True), nullable=True,
                                     comment='개인정보 수집·이용 동의 시각. 동의 절차 전에 가입한 회원은 NULL'))


def downgrade():
    op.drop_column('users', 'privacy_agreed_at')
