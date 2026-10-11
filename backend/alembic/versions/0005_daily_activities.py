"""Add daily activity reports and backup change capture."""

from alembic import op
import sqlalchemy as sa

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "daily_activities",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("report_date", sa.Date(), nullable=False),
        sa.Column("activity_type", sa.String(length=40), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("report_date", name="uq_daily_activities_report_date"),
    )
    op.create_index("ix_daily_activities_report_date", "daily_activities", ["report_date"], unique=True)
    op.create_index("ix_daily_activities_activity_type", "daily_activities", ["activity_type"])
    op.execute(
        "CREATE TRIGGER field_reports_backup_change AFTER INSERT OR UPDATE OR DELETE ON daily_activities "
        "FOR EACH ROW EXECUTE FUNCTION field_reports_capture_backup_change()"
    )


def downgrade():
    op.execute("DROP TRIGGER IF EXISTS field_reports_backup_change ON daily_activities")
    op.drop_index("ix_daily_activities_activity_type", table_name="daily_activities")
    op.drop_index("ix_daily_activities_report_date", table_name="daily_activities")
    op.drop_table("daily_activities")
