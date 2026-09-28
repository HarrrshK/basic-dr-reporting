"""Track durable synchronization commands for idempotency."""
from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if "processed_sync_operations" not in sa.inspect(bind).get_table_names():
        op.create_table("processed_sync_operations",
            sa.Column("operation_id", sa.String(length=36), primary_key=True),
            sa.Column("entity_type", sa.String(length=80), nullable=False),
            sa.Column("processed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
        op.create_index("ix_processed_sync_operations_entity_type", "processed_sync_operations", ["entity_type"])


def downgrade():
    op.drop_index("ix_processed_sync_operations_entity_type", table_name="processed_sync_operations")
    op.drop_table("processed_sync_operations")
