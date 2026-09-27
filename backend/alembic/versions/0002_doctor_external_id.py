"""Add stable external Doctor ID from the master workbook."""
from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("doctors")}
    if "external_id" not in columns:
        op.add_column("doctors", sa.Column("external_id", sa.String(length=80), nullable=True))
    indexes = {index["name"] for index in sa.inspect(bind).get_indexes("doctors")}
    if "ix_doctors_external_id" not in indexes:
        op.create_index("ix_doctors_external_id", "doctors", ["external_id"], unique=True)


def downgrade():
    op.drop_index("ix_doctors_external_id", table_name="doctors")
    op.drop_column("doctors", "external_id")
