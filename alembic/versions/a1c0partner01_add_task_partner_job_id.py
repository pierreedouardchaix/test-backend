"""add tasks.partner_job_id (partner correlation id)

Revision ID: a1c0partner01
Revises: 6ed3eaf0aa0e
Create Date: 2026-07-05 18:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'a1c0partner01'
down_revision: Union[str, Sequence[str], None] = '6ed3eaf0aa0e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('tasks', sa.Column('partner_job_id', sa.String(), nullable=True))
    # Unique so an incoming webhook resolves to exactly one task. Postgres treats
    # NULLs as distinct, so the many tasks without a partner job id don't collide.
    op.create_index(op.f('ix_tasks_partner_job_id'), 'tasks', ['partner_job_id'], unique=True)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_tasks_partner_job_id'), table_name='tasks')
    op.drop_column('tasks', 'partner_job_id')
