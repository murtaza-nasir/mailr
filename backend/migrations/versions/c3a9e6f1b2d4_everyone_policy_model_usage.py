"""the built-in Everyone access policy, and per-user model use counts

- `access_policies` gets the row for group "*" (core/access.EVERYONE): every user in no group with
  a policy of its own gets the assistant with the server's local models. An existing row is
  left as the admin set it.
- `model_usage`: one row per user, day, feature, endpoint and model, with a count (intel/usage.py),
  for Settings → Ambrose → Model use.
- `instance_settings`: a household rule stored as `hosted` becomes `per_user`.

Re-runnable: the insert does nothing when the row exists, the table and index are made only when
missing.

Revision ID: c3a9e6f1b2d4
Revises: b7e4c2a9d051
Create Date: 2026-10-06 23:00:00

"""
import json
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'c3a9e6f1b2d4'
down_revision: Union[str, Sequence[str], None] = 'b7e4c2a9d051'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

EVERYONE_MODELS = {t: {"any": True, "allowed": [], "default": None} for t in ("chat", "classify")}
EVERYONE_FEATURES = {"assistant": True, "choose_models": False, "lab": False, "api_tokens": False,
                     "local_only_tokens": False, "hosted_models": False, "web_search": False, "calendar": False}


def upgrade() -> None:
    op.execute(sa.text(
        "INSERT INTO access_policies (group_name, description, models, features, needs) "
        "VALUES ('*', 'Everyone who is not in a group with a policy of its own.', CAST(:m AS jsonb), "
        "CAST(:f AS jsonb), '{}'::jsonb) ON CONFLICT (group_name) DO NOTHING"
    ).bindparams(m=json.dumps(EVERYONE_MODELS), f=json.dumps(EVERYONE_FEATURES)))
    bind = op.get_bind()
    if not sa.inspect(bind).has_table("model_usage"):
        op.create_table(
            "model_usage",
            # No foreign key: the count is written in a transaction of its own while the caller may
            # hold the user's row (FOR UPDATE would block the key-share lock a foreign key takes).
            # admin_routes.remove_user deletes a user's rows.
            sa.Column("user_id", sa.Integer, nullable=False),
            sa.Column("day", sa.Date, nullable=False),
            sa.Column("feature", sa.String(40), nullable=False),
            sa.Column("endpoint", sa.String(200), nullable=False),
            sa.Column("model", sa.String(200), nullable=False),
            sa.Column("local", sa.Boolean, nullable=False),
            sa.Column("calls", sa.Integer, nullable=False, server_default="0"),
            sa.PrimaryKeyConstraint("user_id", "day", "feature", "endpoint", "model", name="pk_model_usage"),
        )
    op.execute("CREATE INDEX IF NOT EXISTS ix_model_usage_day ON model_usage (day)")
    # The household rule has two values; a stored "hosted" (an earlier build of this branch) is per_user.
    op.execute("UPDATE instance_settings SET value = '\"per_user\"'::jsonb "
               "WHERE key = 'models.local_policy' AND value = '\"hosted\"'::jsonb")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS model_usage")
    op.execute("DELETE FROM access_policies WHERE group_name = '*'")
