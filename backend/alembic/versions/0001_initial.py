"""Initial doctor master and visit schema as originally deployed."""
from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

follow_up_status = sa.Enum("pending", "completed", "cancelled", name="followupstatus")


def upgrade():
    op.create_table(
        "areas",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=160), nullable=False),
    )
    op.create_index("ix_areas_name", "areas", ["name"], unique=True)

    op.create_table(
        "products",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False),
    )
    op.create_index("ix_products_name", "products", ["name"], unique=True)

    op.create_table(
        "import_batches",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("file_hash", sa.String(length=64), nullable=False),
        sa.Column("mapping", sa.JSON(), nullable=False),
        sa.Column("rows", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("summary", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_import_batches_file_hash", "import_batches", ["file_hash"])

    op.create_table(
        "doctors",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("external_id", sa.String(length=80), nullable=True),
        sa.Column("name", sa.String(length=240), nullable=False),
        sa.Column("normalized_name", sa.String(length=240), nullable=False),
        sa.Column("existing_specialty", sa.String(length=240), nullable=True),
        sa.Column("area_id", sa.Integer(), sa.ForeignKey("areas.id", ondelete="SET NULL"), nullable=True),
        sa.Column("hq", sa.String(length=160), nullable=True),
        sa.Column("category", sa.String(length=100), nullable=True),
        sa.Column("mobile", sa.String(length=40), nullable=True),
        sa.Column("normalized_mobile", sa.String(length=20), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("specialty_group", sa.String(length=160), nullable=True),
        sa.Column("doctor_status", sa.String(length=100), nullable=True),
        sa.Column("qualification", sa.String(length=160), nullable=True),
        sa.Column("gender", sa.String(length=40), nullable=True),
        sa.Column("clinic_hospital", sa.String(length=240), nullable=True),
        sa.Column("extra_data", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    for name, columns, unique in (
        ("ix_doctors_external_id", ["external_id"], True),
        ("ix_doctors_name", ["name"], False),
        ("ix_doctors_normalized_name", ["normalized_name"], False),
        ("ix_doctor_name_area", ["normalized_name", "area_id"], False),
        ("ix_doctor_mobile", ["normalized_mobile"], False),
        ("ix_doctors_hq", ["hq"], False),
        ("ix_doctors_category", ["category"], False),
        ("ix_doctors_active", ["active"], False),
        ("ix_doctors_specialty_group", ["specialty_group"], False),
        ("ix_doctors_doctor_status", ["doctor_status"], False),
        ("ix_doctors_qualification", ["qualification"], False),
        ("ix_doctors_gender", ["gender"], False),
        ("ix_doctors_clinic_hospital", ["clinic_hospital"], False),
    ):
        op.create_index(name, "doctors", columns, unique=unique)

    op.create_table(
        "visits",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("doctor_id", sa.Integer(), sa.ForeignKey("doctors.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("visit_date", sa.Date(), nullable=False),
        sa.Column("visit_time", sa.Time(), nullable=True),
        sa.Column("purpose", sa.String(length=240), nullable=True),
        sa.Column("outcome", sa.String(length=240), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("follow_up_required", sa.Boolean(), nullable=False),
        sa.Column("follow_up_date", sa.Date(), nullable=True),
        sa.Column("follow_up_reason", sa.Text(), nullable=True),
        sa.Column("follow_up_status", follow_up_status, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    for name, columns in (
        ("ix_visits_doctor_id", ["doctor_id"]),
        ("ix_visits_visit_date", ["visit_date"]),
        ("ix_visits_purpose", ["purpose"]),
        ("ix_visits_outcome", ["outcome"]),
        ("ix_visits_follow_up_required", ["follow_up_required"]),
        ("ix_visits_follow_up_date", ["follow_up_date"]),
        ("ix_visit_doctor_date", ["doctor_id", "visit_date"]),
    ):
        op.create_index(name, "visits", columns)

    op.create_table(
        "visit_products",
        sa.Column("visit_id", sa.Integer(), sa.ForeignKey("visits.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("products.id", ondelete="RESTRICT"), primary_key=True),
        sa.UniqueConstraint("visit_id", "product_id"),
    )

    op.create_table(
        "processed_sync_operations",
        sa.Column("operation_id", sa.String(length=36), primary_key=True),
        sa.Column("entity_type", sa.String(length=80), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_processed_sync_operations_entity_type", "processed_sync_operations", ["entity_type"])


def downgrade():
    op.drop_table("processed_sync_operations")
    op.drop_table("visit_products")
    for name in (
        "ix_visits_doctor_id", "ix_visits_visit_date", "ix_visits_purpose", "ix_visits_outcome",
        "ix_visits_follow_up_required", "ix_visits_follow_up_date", "ix_visit_doctor_date",
    ):
        op.drop_index(name, table_name="visits")
    op.drop_table("visits")
    for name in (
        "ix_doctors_external_id", "ix_doctors_name", "ix_doctors_normalized_name", "ix_doctor_name_area",
        "ix_doctor_mobile", "ix_doctors_hq", "ix_doctors_category",
        "ix_doctors_active", "ix_doctors_specialty_group", "ix_doctors_doctor_status",
        "ix_doctors_qualification", "ix_doctors_gender", "ix_doctors_clinic_hospital",
    ):
        op.drop_index(name, table_name="doctors")
    op.drop_table("doctors")
    op.drop_index("ix_import_batches_file_hash", table_name="import_batches")
    op.drop_table("import_batches")
    op.drop_index("ix_products_name", table_name="products")
    op.drop_table("products")
    op.drop_index("ix_areas_name", table_name="areas")
    op.drop_table("areas")
    follow_up_status.drop(op.get_bind(), checkfirst=True)
