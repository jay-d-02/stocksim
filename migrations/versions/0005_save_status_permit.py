"""users.save_status (soft delete), withdrawn_at, permit (USER/ADMIN)

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-27 18:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = '0005'
down_revision = '0004'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('users', sa.Column('save_status', sa.Text(), server_default='Y', nullable=False,
                                     comment="Y 사용 중 / N 탈퇴. 로그인은 Y만 가능하고, N은 명예의 전당에서도 빠진다"))
    op.add_column('users', sa.Column('withdrawn_at', sa.DateTime(timezone=True), nullable=True,
                                     comment="탈퇴 시각 (save_status = 'N'일 때만)"))
    op.add_column('users', sa.Column('permit', sa.Text(), server_default='USER', nullable=False,
                                     comment='권한: USER 일반 회원 / ADMIN 관리자 (회원 정보 열람·권한 변경)'))
    op.alter_column('users', 'username', existing_type=sa.Text(), nullable=True,
                    comment='로그인 아이디 (3~30자, 중복 불가). 탈퇴하면 NULL',
                    existing_comment='로그인 아이디 (3~30자, 중복 불가)')
    op.alter_column('users', 'nickname', existing_type=sa.Text(), nullable=True,
                    comment='화면·명예의 전당에 보이는 이름 (중복 불가). 로그인 아이디는 남에게 보이지 않는다. 탈퇴하면 NULL',
                    existing_comment='화면·명예의 전당에 보이는 이름 (중복 불가). 로그인 아이디는 남에게 보이지 않는다')
    op.alter_column('users', 'password_hash', existing_type=sa.Text(), existing_nullable=False,
                    comment='비밀번호 해시 (werkzeug scrypt 형식, 평문 저장 안 함). 탈퇴하면 어떤 비밀번호와도 맞지 않는 값',
                    existing_comment='비밀번호 해시 (werkzeug scrypt 형식, 평문 저장 안 함)')
    op.create_table_comment('users', "회원. 탈퇴해도 행은 남기고(save_status = 'N') 개인정보만 지운다", existing_comment='회원')
    op.create_check_constraint('save_status_valid', 'users', "save_status IN ('Y', 'N')")
    op.create_check_constraint('permit_valid', 'users', "permit IN ('USER', 'ADMIN')")
    op.create_check_constraint('active_has_identity', 'users',
                               "save_status = 'N' OR (username IS NOT NULL AND nickname IS NOT NULL)")
    op.create_check_constraint('withdrawn_has_time', 'users', "(save_status = 'N') = (withdrawn_at IS NOT NULL)")


def downgrade():
    # 탈퇴 회원(아이디 NULL)이 있으면 되돌릴 수 없으므로 먼저 지운다
    op.execute("DELETE FROM users WHERE save_status = 'N'")
    for name in ('withdrawn_has_time', 'active_has_identity', 'permit_valid', 'save_status_valid'):
        op.drop_constraint(name, 'users', type_='check')
    op.create_table_comment('users', '회원', existing_comment="회원. 탈퇴해도 행은 남기고(save_status = 'N') 개인정보만 지운다")
    op.alter_column('users', 'password_hash', existing_type=sa.Text(), existing_nullable=False,
                    comment='비밀번호 해시 (werkzeug scrypt 형식, 평문 저장 안 함)')
    op.alter_column('users', 'nickname', existing_type=sa.Text(), nullable=False,
                    comment='화면·명예의 전당에 보이는 이름 (중복 불가). 로그인 아이디는 남에게 보이지 않는다')
    op.alter_column('users', 'username', existing_type=sa.Text(), nullable=False,
                    comment='로그인 아이디 (3~30자, 중복 불가)')
    op.drop_column('users', 'permit')
    op.drop_column('users', 'withdrawn_at')
    op.drop_column('users', 'save_status')
