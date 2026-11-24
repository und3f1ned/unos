"""The table standby_apps: the critical apps of the SD standby and their sync intervals
(system.sd-standby-critical-apps, ADR-0500).

Revision 0002, after 0001 (ADR-0444). IF NOT EXISTS, as the baseline: a database that create_all made
has the table already.
"""
from alembic import op
import sqlalchemy as sa


revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('standby_apps',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('app', sa.String(length=64), nullable=False),
    sa.Column('interval_hours', sa.Integer(), server_default='24', nullable=False),
    sa.Column('marked_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('app'),
    if_not_exists=True,
    )
