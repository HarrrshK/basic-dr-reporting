"""Add transactional change feed for asynchronous laptop backups."""
from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

TABLES = ("areas", "products", "import_batches", "doctors", "visits", "visit_products")


def upgrade():
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    if "backup_changes" not in existing:
        op.create_table(
            "backup_changes",
            sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
            sa.Column("table_name", sa.String(length=80), nullable=False),
            sa.Column("record_id", sa.String(length=160), nullable=False),
            sa.Column("operation", sa.String(length=16), nullable=False),
            sa.Column("row_data", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )
    indexes = {item["name"] for item in sa.inspect(op.get_bind()).get_indexes("backup_changes")}
    if "ix_backup_changes_table_name" not in indexes:
        op.create_index("ix_backup_changes_table_name", "backup_changes", ["table_name"])
    if "backup_agent_state" not in existing:
        op.create_table(
            "backup_agent_state",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("cursor", sa.BigInteger(), nullable=False, server_default="0"),
            sa.Column("bootstrap_complete", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_successful_backup", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_external_backup_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_error", sa.Text(), nullable=True),
            sa.Column("requested_at", sa.DateTime(timezone=True), nullable=True),
        )
    else:
        state_columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("backup_agent_state")}
        if "last_external_backup_at" not in state_columns:
            op.add_column("backup_agent_state", sa.Column("last_external_backup_at", sa.DateTime(timezone=True), nullable=True))
    op.execute("""
        CREATE FUNCTION field_reports_capture_backup_change() RETURNS trigger AS $$
        DECLARE
            source_row jsonb;
            record_key text;
        BEGIN
            source_row := CASE WHEN TG_OP = 'DELETE' THEN to_jsonb(OLD) ELSE to_jsonb(NEW) END;
            IF TG_TABLE_NAME = 'visit_products' THEN
                record_key := (source_row->>'visit_id') || ':' || (source_row->>'product_id');
            ELSE
                record_key := source_row->>'id';
            END IF;
            INSERT INTO backup_changes(table_name, record_id, operation, row_data)
            VALUES (TG_TABLE_NAME, record_key,
                    CASE WHEN TG_OP = 'DELETE' THEN 'delete' ELSE 'upsert' END,
                    CASE WHEN TG_OP = 'DELETE' THEN NULL ELSE source_row::json END);
            IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    for table_name in TABLES:
        op.execute(
            f"CREATE TRIGGER field_reports_backup_change AFTER INSERT OR UPDATE OR DELETE ON {table_name} "
            "FOR EACH ROW EXECUTE FUNCTION field_reports_capture_backup_change()"
        )
    op.drop_index("ix_processed_sync_operations_entity_type", table_name="processed_sync_operations")
    op.drop_table("processed_sync_operations")


def downgrade():
    for table_name in TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS field_reports_backup_change ON {table_name}")
    op.execute("DROP FUNCTION IF EXISTS field_reports_capture_backup_change()")
    op.drop_table("backup_agent_state")
    op.drop_index("ix_backup_changes_table_name", table_name="backup_changes")
    op.drop_table("backup_changes")
    op.create_table(
        "processed_sync_operations",
        sa.Column("operation_id", sa.String(length=36), primary_key=True),
        sa.Column("entity_type", sa.String(length=80), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_processed_sync_operations_entity_type", "processed_sync_operations", ["entity_type"])
