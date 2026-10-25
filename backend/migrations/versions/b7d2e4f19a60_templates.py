"""templates: fixed text inserted in compose (template.*)

Revision ID: b7d2e4f19a60
Revises: a3f1c9d27e40
Create Date: 2026-09-30 21:30:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'b7d2e4f19a60'
down_revision: Union[str, Sequence[str], None] = 'a3f1c9d27e40'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'templates',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=200), nullable=False),
        sa.Column('shortcut', sa.String(length=48), server_default='', nullable=False),
        sa.Column('subject', sa.Text(), server_default='', nullable=False),
        sa.Column('body_html', sa.Text(), server_default='', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_templates_user_id', 'templates', ['user_id'], unique=False)
    op.create_index('uq_templates_user_shortcut', 'templates', ['user_id', 'shortcut'], unique=True,
                    postgresql_where=sa.text("shortcut <> ''"))


def downgrade() -> None:
    op.drop_index('uq_templates_user_shortcut', table_name='templates', postgresql_where=sa.text("shortcut <> ''"))
    op.drop_index('ix_templates_user_id', table_name='templates')
    op.drop_table('templates')
