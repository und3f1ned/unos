"""The baseline: the schema of unos.db as create_all made it before the migrations (ADR-0444).

Every table and index is created only where it is missing (IF NOT EXISTS). A new database gets all
of them. A database that create_all made before the migrations, as the dev device's, has them and
keeps them as they are, so this revision only records itself: the database is taken as the
baseline, not recreated. A backup made by create_all before one of these tables existed gets it, as
create_all gave it. Never edit this file: a change of the schema is a revision of its own.
"""
from alembic import op
import sqlalchemy as sa


revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('app_resource_limits',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('app', sa.String(length=64), nullable=False),
    sa.Column('container', sa.String(length=128), nullable=False),
    sa.Column('memory_bytes', sa.BigInteger(), nullable=True),
    sa.Column('cpu_quota', sa.Integer(), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('container'),
    if_not_exists=True,
    )
    op.create_index('ix_app_resource_limits_app', 'app_resource_limits', ['app'], unique=False, if_not_exists=True)

    op.create_table('dashboard_layouts',
    sa.Column('user_id', sa.Integer(), autoincrement=False, nullable=False),
    sa.Column('layout', sa.Text(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now(), nullable=True),
    sa.PrimaryKeyConstraint('user_id'),
    if_not_exists=True,
    )
    op.create_table('iscsi_targets',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=32), nullable=False),
    sa.Column('uid', sa.String(length=32), nullable=False),
    sa.Column('iqn', sa.String(length=223), nullable=False),
    sa.Column('pool', sa.String(length=63), nullable=False),
    sa.Column('volume', sa.String(length=63), nullable=False),
    sa.Column('size_gib', sa.Integer(), nullable=False),
    sa.Column('chap_user', sa.String(length=64), nullable=False),
    sa.Column('mutual', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('iqn'),
    sa.UniqueConstraint('name'),
    sa.UniqueConstraint('uid'),
    if_not_exists=True,
    )
    op.create_table('login_attempts',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('username', sa.String(), nullable=False),
    sa.Column('ip_address', sa.String(), nullable=True),
    sa.Column('user_agent', sa.String(), nullable=True),
    sa.Column('success', sa.Boolean(), nullable=True),
    sa.Column('failure_reason', sa.String(), nullable=True),
    sa.Column('timestamp', sa.DateTime(), server_default=sa.func.now(), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    if_not_exists=True,
    )
    op.create_index('ix_login_attempts_id', 'login_attempts', ['id'], unique=False, if_not_exists=True)
    op.create_index('ix_login_attempts_username', 'login_attempts', ['username'], unique=False, if_not_exists=True)

    op.create_table('smb_bind_interfaces',
    sa.Column('name', sa.String(length=15), nullable=False),
    sa.PrimaryKeyConstraint('name'),
    if_not_exists=True,
    )
    op.create_table('smb_shares',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=32, collation='NOCASE'), nullable=False),
    sa.Column('path', sa.Text(), nullable=False),
    sa.Column('comment', sa.Text(), server_default='', nullable=False),
    sa.Column('browseable', sa.Boolean(), server_default='1', nullable=False),
    sa.Column('guest_ok', sa.Boolean(), server_default='0', nullable=False),
    sa.Column('import_notes', sa.Text(), nullable=True),
    sa.Column('version', sa.Integer(), server_default='1', nullable=False),
    sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('name'),
    if_not_exists=True,
    )
    op.create_table('smb_state',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('next_uid', sa.Integer(), server_default='70000', nullable=False),
    sa.Column('shares_imported_at', sa.DateTime(), nullable=True),
    sa.Column('import_report', sa.Text(), nullable=True),
    sa.Column('pending_import_source', sa.String(length=8), nullable=True),
    sa.CheckConstraint("pending_import_source IS NULL OR pending_import_source IN ('live')", name='ck_smb_state_pending_import_source'),
    sa.CheckConstraint('id = 1', name='ck_smb_state_singleton'),
    sa.PrimaryKeyConstraint('id'),
    if_not_exists=True,
    )
    op.create_table('smb_uid_names',
    sa.Column('uid', sa.Integer(), autoincrement=False, nullable=False),
    sa.Column('linux_name', sa.String(length=32), nullable=False),
    sa.CheckConstraint('uid BETWEEN 70000 AND 79999', name='ck_smb_uid_names_uid_range'),
    sa.PrimaryKeyConstraint('uid', 'linux_name'),
    if_not_exists=True,
    )
    op.create_table('smb_uids',
    sa.Column('uid', sa.Integer(), autoincrement=False, nullable=False),
    sa.Column('linux_name', sa.String(length=32), nullable=False),
    sa.CheckConstraint('uid BETWEEN 70000 AND 79999', name='ck_smb_uids_uid_range'),
    sa.PrimaryKeyConstraint('uid'),
    if_not_exists=True,
    )
    op.create_table('two_factor_requirement',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('grace_days', sa.Integer(), nullable=False),
    sa.Column('enabled_at', sa.DateTime(), nullable=False),
    sa.Column('enabled_by', sa.String(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    if_not_exists=True,
    )
    op.create_table('user_groups',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=32, collation='NOCASE'), nullable=False),
    sa.Column('version', sa.Integer(), server_default='1', nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('name'),
    sqlite_autoincrement=True,
    if_not_exists=True,
    )
    op.create_table('user_themes',
    sa.Column('user_id', sa.Integer(), autoincrement=False, nullable=False),
    sa.Column('theme', sa.Text(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now(), nullable=True),
    sa.PrimaryKeyConstraint('user_id'),
    if_not_exists=True,
    )
    op.create_table('usernames_taken',
    sa.Column('username', sa.String(), nullable=False),
    sa.Column('taken_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
    sa.PrimaryKeyConstraint('username'),
    if_not_exists=True,
    )
    op.create_table('users',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('username', sa.String(), nullable=False),
    sa.Column('hashed_password', sa.String(), nullable=False),
    sa.Column('is_admin', sa.Boolean(), nullable=True),
    sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=True),
    sa.Column('updated_at', sa.DateTime(), nullable=True),
    sa.Column('totp_secret', sa.String(), nullable=True),
    sa.Column('totp_enabled', sa.Boolean(), nullable=True),
    sa.Column('recovery_codes', sa.Text(), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    if_not_exists=True,
    )
    op.create_index('ix_users_id', 'users', ['id'], unique=False, if_not_exists=True)
    op.create_index('ix_users_username', 'users', ['username'], unique=True, if_not_exists=True)

    op.create_table('api_tokens',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(), nullable=False),
    sa.Column('token_hash', sa.String(), nullable=False),
    sa.Column('token_prefix', sa.String(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=True),
    sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=True),
    sa.Column('last_used_at', sa.DateTime(), nullable=True),
    sa.Column('expires_at', sa.DateTime(), nullable=True),
    sa.Column('permissions', sa.String(), nullable=True),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id'),
    if_not_exists=True,
    )
    op.create_index('ix_api_tokens_id', 'api_tokens', ['id'], unique=False, if_not_exists=True)
    op.create_index('ix_api_tokens_token_hash', 'api_tokens', ['token_hash'], unique=True, if_not_exists=True)

    op.create_table('iscsi_initiators',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('target_id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=223), nullable=False),
    sa.ForeignKeyConstraint(['target_id'], ['iscsi_targets.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('target_id', 'name', name='uq_iscsi_initiators_target_name'),
    if_not_exists=True,
    )
    op.create_index('ix_iscsi_initiators_target_id', 'iscsi_initiators', ['target_id'], unique=False, if_not_exists=True)

    op.create_table('security_keys',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(), nullable=False),
    sa.Column('credential_id', sa.String(), nullable=False),
    sa.Column('public_key', sa.LargeBinary(), nullable=False),
    sa.Column('algorithm', sa.Integer(), nullable=False),
    sa.Column('sign_count', sa.Integer(), nullable=False),
    sa.Column('rp_id', sa.String(), nullable=False),
    sa.Column('origin', sa.String(), nullable=False),
    sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=True),
    sa.Column('last_used_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('credential_id'),
    if_not_exists=True,
    )
    op.create_table('smb_accounts',
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('linux_name', sa.String(length=32), nullable=False),
    sa.Column('uid', sa.Integer(), nullable=True),
    sa.Column('enabled', sa.Boolean(), server_default='0', nullable=False),
    sa.Column('password_state', sa.String(length=8), server_default='needed', nullable=False),
    sa.Column('password_set_at', sa.DateTime(), nullable=True),
    sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=True),
    sa.CheckConstraint("password_state IN ('set', 'needed')", name='ck_smb_accounts_password_state'),
    sa.CheckConstraint('uid IS NULL OR uid BETWEEN 70000 AND 79999', name='ck_smb_accounts_uid_range'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('user_id'),
    sa.UniqueConstraint('linux_name'),
    sa.UniqueConstraint('uid'),
    if_not_exists=True,
    )
    op.create_table('smb_share_access',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('share_id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('username', sa.String(length=64), nullable=False),
    sa.Column('level', sa.String(length=8), nullable=False),
    sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
    sa.CheckConstraint("level IN ('read', 'write')", name='ck_smb_share_access_level'),
    sa.ForeignKeyConstraint(['share_id'], ['smb_shares.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('share_id', 'user_id', name='uq_smb_share_access_share_user'),
    if_not_exists=True,
    )
    op.create_index('ix_smb_share_access_user_id', 'smb_share_access', ['user_id'], unique=False, if_not_exists=True)

    op.create_table('smb_share_group_access',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('share_id', sa.Integer(), nullable=False),
    sa.Column('group_id', sa.Integer(), nullable=False),
    sa.Column('level', sa.String(length=8), nullable=False),
    sa.CheckConstraint("level IN ('read', 'write')", name='ck_smb_share_group_access_level'),
    sa.ForeignKeyConstraint(['group_id'], ['user_groups.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['share_id'], ['smb_shares.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('share_id', 'group_id', name='uq_smb_share_group_access_share_group'),
    if_not_exists=True,
    )
    op.create_index('ix_smb_share_group_access_group_id', 'smb_share_group_access', ['group_id'], unique=False, if_not_exists=True)

    op.create_table('smb_share_options',
    sa.Column('share_id', sa.Integer(), nullable=False),
    sa.Column('encryption_required', sa.Boolean(), server_default='0', nullable=False),
    sa.Column('time_machine', sa.Boolean(), server_default='0', nullable=False),
    sa.Column('previous_versions', sa.Boolean(), server_default='0', nullable=False),
    sa.ForeignKeyConstraint(['share_id'], ['smb_shares.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('share_id'),
    if_not_exists=True,
    )
    op.create_table('user_group_members',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('group_id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('username', sa.String(length=64), nullable=False),
    sa.ForeignKeyConstraint(['group_id'], ['user_groups.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('group_id', 'user_id', name='uq_user_group_members_group_user'),
    if_not_exists=True,
    )
    op.create_index('ix_user_group_members_user_id', 'user_group_members', ['user_id'], unique=False, if_not_exists=True)

    op.create_table('user_sessions',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('expires_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sqlite_with_rowid=False,
    if_not_exists=True,
    )
