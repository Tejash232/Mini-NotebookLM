"""Add hosted object-storage metadata and owner-scoped pgvector chunks.

Revision ID: 0002_supabase_pgvector_storage
Revises: 0001_initial_schema
"""
from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector


revision = "0002_supabase_pgvector_storage"
down_revision = "0001_initial_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name
    op.add_column("documents", sa.Column("storage_key", sa.Text(), nullable=True))
    if dialect == "sqlite":
        with op.batch_alter_table("documents") as batch_op:
            batch_op.create_unique_constraint("documents_owner_id_key", ["owner_id", "id"])
    else:
        op.create_unique_constraint("documents_owner_id_key", "documents", ["owner_id", "id"])
    if dialect == "postgresql":
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "document_chunks",
        sa.Column("id", sa.String(160), primary_key=True),
        sa.Column("owner_id", sa.String(64), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("document_id", sa.String(64), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(384).with_variant(sa.JSON(), "sqlite"), nullable=False),
        sa.ForeignKeyConstraint(
            ["owner_id", "document_id"], ["documents.owner_id", "documents.id"],
            name="document_chunks_owner_document_fkey", ondelete="CASCADE",
        ),
        sa.UniqueConstraint("owner_id", "document_id", "page_number", "chunk_index", name="document_chunks_position_key"),
    )
    op.create_index("document_chunks_owner_document_idx", "document_chunks", ["owner_id", "document_id"])
    op.create_index("document_chunks_owner_source_idx", "document_chunks", ["owner_id", "source"])
    if dialect == "postgresql":
        op.execute("CREATE INDEX document_chunks_embedding_hnsw_idx ON document_chunks USING hnsw (embedding vector_cosine_ops)")
    else:
        op.create_index("document_chunks_embedding_hnsw_idx", "document_chunks", ["embedding"])


def downgrade() -> None:
    op.drop_index("document_chunks_embedding_hnsw_idx", table_name="document_chunks")
    op.drop_index("document_chunks_owner_source_idx", table_name="document_chunks")
    op.drop_index("document_chunks_owner_document_idx", table_name="document_chunks")
    op.drop_table("document_chunks")
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("documents") as batch_op:
            batch_op.drop_constraint("documents_owner_id_key", type_="unique")
            batch_op.drop_column("storage_key")
    else:
        op.drop_constraint("documents_owner_id_key", "documents", type_="unique")
        op.drop_column("documents", "storage_key")
    # The shared vector extension is intentionally retained.
