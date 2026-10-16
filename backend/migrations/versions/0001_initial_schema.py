"""Create application persistence tables.

Revision ID: 0001_initial_schema
Revises:
"""
from alembic import op
import sqlalchemy as sa


revision = "0001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("identifier", sa.String(254), nullable=False),
        sa.Column("identifier_normalized", sa.String(254), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.UniqueConstraint("identifier_normalized", name="users_identifier_normalized_key"),
    )
    op.create_table(
        "sessions",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(64), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("expires_at", sa.Float(), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
    )
    op.create_index("sessions_expiry_idx", "sessions", ["expires_at"])
    op.create_table(
        "login_attempts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("address_key", sa.String(128), nullable=False),
        sa.Column("attempted_at", sa.Float(), nullable=False),
    )
    op.create_index("login_attempts_window_idx", "login_attempts", ["address_key", "attempted_at"])
    op.create_table(
        "documents",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("owner_id", sa.String(64), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("stored_path", sa.Text()),
        sa.Column("pages", sa.Integer(), nullable=False),
        sa.Column("chunks", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.UniqueConstraint("owner_id", "filename", name="documents_owner_filename_key"),
    )
    op.create_index("documents_owner_idx", "documents", ["owner_id"])
    op.create_table(
        "study_materials",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("owner_id", sa.String(64), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
    )
    op.create_index("materials_owner_kind_idx", "study_materials", ["owner_id", "kind", "created_at"])
    op.create_table(
        "quizzes",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("owner_id", sa.String(64), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("questions_json", sa.Text(), nullable=False),
        sa.Column("submission_json", sa.Text()),
        sa.Column("result_json", sa.Text()),
        sa.Column("created_at", sa.Float(), nullable=False),
    )
    op.create_index("quizzes_owner_idx", "quizzes", ["owner_id"])


def downgrade() -> None:
    op.drop_index("quizzes_owner_idx", table_name="quizzes")
    op.drop_table("quizzes")
    op.drop_index("materials_owner_kind_idx", table_name="study_materials")
    op.drop_table("study_materials")
    op.drop_index("documents_owner_idx", table_name="documents")
    op.drop_table("documents")
    op.drop_index("login_attempts_window_idx", table_name="login_attempts")
    op.drop_table("login_attempts")
    op.drop_index("sessions_expiry_idx", table_name="sessions")
    op.drop_table("sessions")
    op.drop_table("users")
