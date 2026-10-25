"""Skills are invoked as /name now: rewrite //name in stored skill text (built-in copies too).

Revision ID: f2b6c8d41a93
Revises: e8a1f3c5b702
"""

from alembic import op

revision = "f2b6c8d41a93"
down_revision = "e8a1f3c5b702"
branch_labels = None
depends_on = None

# //name as a word of its own (not inside https://… or a//b), the same rule as skills.INVOKE_RE.
_SQL = r"""regexp_replace({col}, '(^|[\s(])//([A-Za-z0-9][A-Za-z0-9_-]*)', '\1/\2', 'g')"""


def upgrade() -> None:
    for col in ("instructions", "description", "title"):
        op.execute(f"UPDATE assistant_skills SET {col} = {_SQL.format(col=col)} WHERE {col} ~ '//[A-Za-z0-9]'")


def downgrade() -> None:
    pass
