"""user nickname, experience, email

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-27 12:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('users', sa.Column('nickname', sa.Text(), nullable=True,
                                     comment='화면·명예의 전당에 보이는 이름 (중복 불가). 로그인 아이디는 남에게 보이지 않는다'))
    # 이미 가입한 회원은 아이디를 닉네임으로 (아이디가 중복 불가라 닉네임도 겹치지 않는다)
    op.execute("UPDATE users SET nickname = username")
    op.alter_column('users', 'nickname', nullable=False)
    op.add_column('users', sa.Column('experience', sa.Text(), server_default='beginner', nullable=False,
                                     comment='투자 경험: beginner 처음 / intermediate 해 봤음 / expert 자신 있음'))
    op.add_column('users', sa.Column('email', sa.Text(), nullable=True,
                                     comment='이메일 (선택, 소문자로 저장, 중복 불가). 비밀번호 찾기용'))
    op.create_unique_constraint('users_nickname_key', 'users', ['nickname'])
    op.create_unique_constraint('users_email_key', 'users', ['email'])
    op.create_check_constraint('experience_valid', 'users', "experience IN ('beginner', 'intermediate', 'expert')")


def downgrade():
    op.drop_constraint('experience_valid', 'users', type_='check')
    op.drop_constraint('users_email_key', 'users', type_='unique')
    op.drop_constraint('users_nickname_key', 'users', type_='unique')
    op.drop_column('users', 'email')
    op.drop_column('users', 'experience')
    op.drop_column('users', 'nickname')
