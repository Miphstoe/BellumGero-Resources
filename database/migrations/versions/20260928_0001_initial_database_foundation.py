"""initial database foundation

Revision ID: 20260928_0001
Revises:
Create Date: 2026-09-28
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "20260928_0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


BIGINT_ID = sa.BigInteger()
JSONB = postgresql.JSONB(astext_type=sa.Text())


def id_column() -> sa.Column:
    return sa.Column("id", BIGINT_ID, sa.Identity(), primary_key=True)


def jsonb_column(name: str) -> sa.Column:
    return sa.Column(name, JSONB, nullable=False, server_default=sa.text("'{}'::jsonb"))


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    op.create_table(
        "source_systems",
        id_column(),
        sa.Column("code", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text()),
        sa.UniqueConstraint("code", name="uq_source_systems_code"),
    )
    op.create_table(
        "source_instances",
        id_column(),
        sa.Column("source_system_id", BIGINT_ID, sa.ForeignKey("source_systems.id"), nullable=False),
        sa.Column("code", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("external_id", sa.Text()),
        jsonb_column("metadata"),
        sa.UniqueConstraint("source_system_id", "code", name="uq_source_instances_system_code"),
    )
    op.create_table(
        "server_instances",
        id_column(),
        sa.Column("code", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("environment", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("retired_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("code", name="uq_server_instances_code"),
    )
    op.create_table(
        "server_epochs",
        id_column(),
        sa.Column("server_instance_id", BIGINT_ID, sa.ForeignKey("server_instances.id"), nullable=False),
        sa.Column("epoch_code", sa.Text(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("ended_at", sa.DateTime(timezone=True)),
        sa.Column("description", sa.Text()),
        sa.UniqueConstraint("server_instance_id", "epoch_code", name="uq_server_epochs_instance_code"),
    )
    op.create_table(
        "import_batches",
        id_column(),
        sa.Column("source_instance_id", BIGINT_ID, sa.ForeignKey("source_instances.id"), nullable=False),
        sa.Column("import_kind", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("tool_version", sa.Text()),
        sa.Column("source_revision", sa.Text()),
        jsonb_column("parameters"),
        jsonb_column("summary"),
    )
    op.create_table(
        "source_snapshots",
        id_column(),
        sa.Column("import_batch_id", BIGINT_ID, sa.ForeignKey("import_batches.id"), nullable=False),
        sa.Column("snapshot_kind", sa.Text(), nullable=False),
        sa.Column("external_path", sa.Text()),
        sa.Column("content_sha256", sa.Text()),
        sa.Column("byte_size", sa.BigInteger()),
        sa.Column("record_count", sa.Integer()),
        sa.Column("observed_at", sa.DateTime(timezone=True)),
        sa.Column("source_entered_at", sa.DateTime(timezone=True)),
        jsonb_column("metadata"),
        sa.UniqueConstraint("import_batch_id", "external_path", "content_sha256", name="uq_source_snapshots_batch_path_hash"),
    )
    op.create_table(
        "source_records",
        id_column(),
        sa.Column("source_snapshot_id", BIGINT_ID, sa.ForeignKey("source_snapshots.id")),
        sa.Column("record_type", sa.Text(), nullable=False),
        sa.Column("source_key", sa.Text()),
        sa.Column("parse_status", sa.Text(), nullable=False),
        sa.Column("parse_error", sa.Text()),
        sa.Column("payload_ref", sa.Text()),
        sa.Column("payload_hash", sa.Text()),
        sa.Column("normalized_payload", JSONB),
    )
    op.execute(
        "ALTER TABLE source_records ADD CONSTRAINT uq_source_records_snapshot_type_key "
        "UNIQUE NULLS NOT DISTINCT (source_snapshot_id, record_type, source_key)"
    )
    op.create_table(
        "anomalies",
        id_column(),
        sa.Column("source_record_id", BIGINT_ID, sa.ForeignKey("source_records.id")),
        sa.Column("entity_kind", sa.Text(), nullable=False),
        sa.Column("entity_id", sa.BigInteger()),
        sa.Column("anomaly_code", sa.Text(), nullable=False),
        sa.Column("severity", sa.Text(), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        jsonb_column("details"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("source_record_id", "anomaly_code", "entity_kind", "entity_id", name="uq_anomalies_record_code_entity"),
    )
    op.create_table(
        "planets",
        id_column(),
        sa.Column("slug", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("core3_zone_name", sa.Text()),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.UniqueConstraint("slug", name="uq_planets_slug"),
    )
    op.create_table(
        "source_planets",
        id_column(),
        sa.Column("source_instance_id", BIGINT_ID, sa.ForeignKey("source_instances.id"), nullable=False),
        sa.Column("source_planet_id", sa.Text(), nullable=False),
        sa.Column("source_planet_name", sa.Text(), nullable=False),
        sa.Column("planet_id", BIGINT_ID, sa.ForeignKey("planets.id")),
        jsonb_column("metadata"),
        sa.UniqueConstraint("source_instance_id", "source_planet_id", name="uq_source_planets_instance_source_id"),
    )
    op.create_table(
        "stat_definitions",
        sa.Column("code", sa.Text(), primary_key=True),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("core3_attribute_name", sa.Text()),
        sa.Column("description", sa.Text()),
        sa.Column("is_canonical", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )
    op.create_table(
        "resource_types",
        id_column(),
        sa.Column("slug", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("is_spawnable", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("core3_stf_name", sa.Text()),
        sa.Column("core3_display_class", sa.Text()),
        sa.Column("container_type", sa.Text()),
        sa.Column("inventory_type", sa.Text()),
        sa.Column("specific_planet_id", BIGINT_ID, sa.ForeignKey("planets.id")),
        jsonb_column("metadata"),
        sa.UniqueConstraint("slug", name="uq_resource_types_slug"),
    )
    op.create_table(
        "resource_type_edges",
        sa.Column("parent_type_id", BIGINT_ID, sa.ForeignKey("resource_types.id"), primary_key=True),
        sa.Column("child_type_id", BIGINT_ID, sa.ForeignKey("resource_types.id"), primary_key=True),
        sa.Column("source", sa.Text(), primary_key=True),
        sa.CheckConstraint("parent_type_id <> child_type_id", name="ck_resource_type_edges_no_self_edge"),
    )
    op.create_table(
        "resource_type_closure",
        sa.Column("ancestor_type_id", BIGINT_ID, sa.ForeignKey("resource_types.id"), primary_key=True),
        sa.Column("descendant_type_id", BIGINT_ID, sa.ForeignKey("resource_types.id"), primary_key=True),
        sa.Column("depth", sa.Integer(), nullable=False),
        sa.CheckConstraint("depth >= 0", name="ck_resource_type_closure_depth_nonnegative"),
    )
    op.create_index("ix_resource_type_closure_desc_ancestor", "resource_type_closure", ["descendant_type_id", "ancestor_type_id"])
    op.create_table(
        "source_resource_types",
        id_column(),
        sa.Column("source_instance_id", BIGINT_ID, sa.ForeignKey("source_instances.id"), nullable=False),
        sa.Column("source_type_key", sa.Text(), nullable=False),
        sa.Column("source_type_name", sa.Text()),
        sa.Column("canonical_type_id", BIGINT_ID, sa.ForeignKey("resource_types.id")),
        sa.Column("mapping_status", sa.Text(), nullable=False),
        sa.Column("mapping_confidence", sa.Text(), nullable=False),
        jsonb_column("metadata"),
        sa.UniqueConstraint("source_instance_id", "source_type_key", name="uq_source_resource_types_instance_key"),
    )
    op.create_table(
        "resource_type_stat_ranges",
        id_column(),
        sa.Column("resource_type_id", BIGINT_ID, sa.ForeignKey("resource_types.id")),
        sa.Column("source_resource_type_id", BIGINT_ID, sa.ForeignKey("source_resource_types.id")),
        sa.Column("stat_code", sa.Text(), sa.ForeignKey("stat_definitions.code"), nullable=False),
        sa.Column("min_value", sa.Integer()),
        sa.Column("max_value", sa.Integer()),
        sa.Column("is_applicable", sa.Boolean(), nullable=False),
        sa.Column("source_record_id", BIGINT_ID, sa.ForeignKey("source_records.id")),
        sa.CheckConstraint("num_nonnulls(resource_type_id, source_resource_type_id) = 1", name="ck_resource_type_stat_ranges_one_owner"),
        sa.CheckConstraint(
            "(is_applicable = false AND min_value IS NULL AND max_value IS NULL) OR "
            "(is_applicable = true AND ((min_value IS NULL AND max_value IS NULL) OR "
            "(min_value IS NOT NULL AND max_value IS NOT NULL AND min_value <= max_value)))",
            name="ck_resource_type_stat_ranges_applicability",
        ),
    )
    op.create_index(
        "uq_resource_type_stat_ranges_canonical",
        "resource_type_stat_ranges",
        ["resource_type_id", "stat_code"],
        unique=True,
        postgresql_where=sa.text("resource_type_id IS NOT NULL"),
    )
    op.create_index(
        "uq_resource_type_stat_ranges_source",
        "resource_type_stat_ranges",
        ["source_resource_type_id", "stat_code"],
        unique=True,
        postgresql_where=sa.text("source_resource_type_id IS NOT NULL"),
    )
    op.create_table(
        "resources",
        id_column(),
        sa.Column("public_id", postgresql.UUID(as_uuid=True), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("public_slug", sa.Text()),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("canonical_type_id", BIGINT_ID, sa.ForeignKey("resource_types.id")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("notes", sa.Text()),
        sa.UniqueConstraint("public_id", name="uq_resources_public_id"),
        sa.UniqueConstraint("public_slug", name="uq_resources_public_slug"),
    )
    op.create_table(
        "source_resources",
        id_column(),
        sa.Column("source_instance_id", BIGINT_ID, sa.ForeignKey("source_instances.id"), nullable=False),
        sa.Column("server_instance_id", BIGINT_ID, sa.ForeignKey("server_instances.id")),
        sa.Column("server_epoch_id", BIGINT_ID, sa.ForeignKey("server_epochs.id")),
        sa.Column("source_resource_id", sa.Text(), nullable=False),
        sa.Column("source_resource_name", sa.Text(), nullable=False),
        sa.Column("source_resource_type_id", BIGINT_ID, sa.ForeignKey("source_resource_types.id")),
        sa.Column("canonical_resource_id", BIGINT_ID, sa.ForeignKey("resources.id")),
        sa.Column("identity_status", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Text(), nullable=False),
        sa.Column("first_source_record_id", BIGINT_ID, sa.ForeignKey("source_records.id")),
        sa.Column("last_source_record_id", BIGINT_ID, sa.ForeignKey("source_records.id")),
        jsonb_column("metadata"),
    )
    op.execute(
        "ALTER TABLE source_resources ADD CONSTRAINT uq_source_resources_identity "
        "UNIQUE NULLS NOT DISTINCT (source_instance_id, server_epoch_id, source_resource_id)"
    )
    op.execute("CREATE INDEX ix_source_resources_instance_lower_name_expr ON source_resources (source_instance_id, lower(source_resource_name))")
    op.create_table(
        "resource_names",
        id_column(),
        sa.Column("canonical_resource_id", BIGINT_ID, sa.ForeignKey("resources.id")),
        sa.Column("source_resource_id", BIGINT_ID, sa.ForeignKey("source_resources.id")),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("normalized_name", sa.Text(), nullable=False),
        sa.Column("source_record_id", BIGINT_ID, sa.ForeignKey("source_records.id")),
        sa.UniqueConstraint("source_resource_id", "normalized_name", name="uq_resource_names_source_normalized"),
    )
    op.create_index("ix_resource_names_normalized", "resource_names", ["normalized_name"])
    op.create_table(
        "resource_stat_observations",
        id_column(),
        sa.Column("source_resource_id", BIGINT_ID, sa.ForeignKey("source_resources.id"), nullable=False),
        sa.Column("stat_code", sa.Text(), sa.ForeignKey("stat_definitions.code"), nullable=False),
        sa.Column("value", sa.Integer()),
        sa.Column("is_present", sa.Boolean(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True)),
        sa.Column("source_record_id", BIGINT_ID, sa.ForeignKey("source_records.id")),
        sa.Column("import_batch_id", BIGINT_ID, sa.ForeignKey("import_batches.id"), nullable=False),
        sa.CheckConstraint(
            "(is_present = true AND value IS NOT NULL) OR (is_present = false AND value IS NULL)",
            name="ck_resource_stat_observations_presence_value",
        ),
    )
    op.execute(
        "ALTER TABLE resource_stat_observations ADD CONSTRAINT uq_resource_stat_observations_source_stat_record "
        "UNIQUE NULLS NOT DISTINCT (source_resource_id, stat_code, source_record_id)"
    )
    op.create_index("ix_resource_stat_observations_stat_value", "resource_stat_observations", ["stat_code", "value"])
    op.create_table(
        "resource_type_memberships",
        sa.Column("source_resource_id", BIGINT_ID, sa.ForeignKey("source_resources.id"), primary_key=True),
        sa.Column("resource_type_id", BIGINT_ID, sa.ForeignKey("resource_types.id"), primary_key=True),
        sa.Column("membership_kind", sa.Text(), nullable=False),
        sa.Column("source_record_id", BIGINT_ID, sa.ForeignKey("source_records.id")),
    )
    op.create_table(
        "resource_observations",
        id_column(),
        sa.Column("source_resource_id", BIGINT_ID, sa.ForeignKey("source_resources.id"), nullable=False),
        sa.Column("source_record_id", BIGINT_ID, sa.ForeignKey("source_records.id")),
        sa.Column("observation_kind", sa.Text(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True)),
        sa.Column("source_entered_at", sa.DateTime(timezone=True)),
        sa.Column("source_unavailable_at", sa.DateTime(timezone=True)),
        sa.Column("confidence", sa.Text(), nullable=False),
        jsonb_column("details"),
    )
    op.create_index("ix_resource_observations_resource_kind_observed", "resource_observations", ["source_resource_id", "observation_kind", "observed_at"])
    op.create_table(
        "resource_planet_observations",
        id_column(),
        sa.Column("source_resource_id", BIGINT_ID, sa.ForeignKey("source_resources.id"), nullable=False),
        sa.Column("planet_id", BIGINT_ID, sa.ForeignKey("planets.id"), nullable=False),
        sa.Column("source_planet_id", BIGINT_ID, sa.ForeignKey("source_planets.id")),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True)),
        sa.Column("source_entered_at", sa.DateTime(timezone=True)),
        sa.Column("source_unavailable_at", sa.DateTime(timezone=True)),
        sa.Column("confidence", sa.Text(), nullable=False),
        sa.Column("source_record_id", BIGINT_ID, sa.ForeignKey("source_records.id")),
        sa.Column("import_batch_id", BIGINT_ID, sa.ForeignKey("import_batches.id"), nullable=False),
        jsonb_column("details"),
    )
    op.create_index("ix_resource_planet_observations_planet_state_observed", "resource_planet_observations", ["planet_id", "state", "observed_at"])
    op.create_index("ix_resource_planet_observations_resource_planet_observed", "resource_planet_observations", ["source_resource_id", "planet_id", "observed_at"])
    op.create_table(
        "resource_lifecycle_events",
        id_column(),
        sa.Column("source_resource_id", BIGINT_ID, sa.ForeignKey("source_resources.id"), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("event_time", sa.DateTime(timezone=True)),
        sa.Column("event_time_confidence", sa.Text(), nullable=False),
        sa.Column("planet_id", BIGINT_ID, sa.ForeignKey("planets.id")),
        sa.Column("source_record_id", BIGINT_ID, sa.ForeignKey("source_records.id")),
        sa.Column("import_batch_id", BIGINT_ID, sa.ForeignKey("import_batches.id"), nullable=False),
        jsonb_column("details"),
    )
    op.create_index("ix_resource_lifecycle_events_resource_type_time", "resource_lifecycle_events", ["source_resource_id", "event_type", "event_time"])
    op.create_table(
        "unresolved_source_resources",
        id_column(),
        sa.Column("source_instance_id", BIGINT_ID, sa.ForeignKey("source_instances.id"), nullable=False),
        sa.Column("import_batch_id", BIGINT_ID, sa.ForeignKey("import_batches.id"), nullable=False),
        sa.Column("source_name", sa.Text(), nullable=False),
        sa.Column("normalized_name", sa.Text(), nullable=False),
        sa.Column("result_status", sa.Text(), nullable=False),
        sa.Column("http_status", sa.Integer()),
        sa.Column("source_result_text", sa.Text()),
        sa.Column("source_record_id", BIGINT_ID, sa.ForeignKey("source_records.id")),
        jsonb_column("details"),
        sa.UniqueConstraint("source_instance_id", "normalized_name", "import_batch_id", name="uq_unresolved_source_resources_instance_name_batch"),
    )
    op.create_table(
        "resource_match_candidates",
        id_column(),
        sa.Column("left_source_resource_id", BIGINT_ID, sa.ForeignKey("source_resources.id"), nullable=False),
        sa.Column("right_source_resource_id", BIGINT_ID, sa.ForeignKey("source_resources.id"), nullable=False),
        sa.Column("score", sa.Numeric()),
        sa.Column("status", sa.Text(), nullable=False),
        jsonb_column("evidence"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("left_source_resource_id", "right_source_resource_id", name="uq_resource_match_candidates_pair"),
        sa.CheckConstraint("left_source_resource_id <> right_source_resource_id", name="ck_resource_match_candidates_not_same"),
    )
    op.create_index("ix_resource_match_candidates_status_score", "resource_match_candidates", ["status", "score"])
    op.create_table(
        "resource_identity_links",
        sa.Column("canonical_resource_id", BIGINT_ID, sa.ForeignKey("resources.id"), primary_key=True),
        sa.Column("source_resource_id", BIGINT_ID, sa.ForeignKey("source_resources.id"), primary_key=True),
        sa.Column("link_status", sa.Text(), nullable=False),
        sa.Column("confirmed_by", sa.Text(), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        jsonb_column("evidence"),
    )
    op.create_index(
        "uq_resource_identity_links_one_confirmed_source",
        "resource_identity_links",
        ["source_resource_id"],
        unique=True,
        postgresql_where=sa.text("link_status = 'confirmed'"),
    )
    op.create_table(
        "resource_identity_conflicts",
        id_column(),
        sa.Column("candidate_id", BIGINT_ID, sa.ForeignKey("resource_match_candidates.id")),
        sa.Column("conflict_type", sa.Text(), nullable=False),
        jsonb_column("details"),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )

    _seed_reference_data()


def _seed_reference_data() -> None:
    source_systems = sa.table(
        "source_systems",
        sa.column("code", sa.Text()),
        sa.column("name", sa.Text()),
        sa.column("description", sa.Text()),
    )
    op.bulk_insert(
        source_systems,
        [
            {"code": "galaxy_harvester", "name": "Galaxy Harvester", "description": "Historical Galaxy Harvester source data"},
            {"code": "core3", "name": "Core3", "description": "Bellum Gero Core3 server observations"},
        ],
    )
    op.execute(
        """
        INSERT INTO source_instances (source_system_id, code, display_name, external_id, metadata)
        SELECT id, 'galaxy-153', 'SWG Bellum Gero', '153', '{}'::jsonb
        FROM source_systems WHERE code = 'galaxy_harvester'
        """
    )
    op.execute(
        """
        INSERT INTO source_instances (source_system_id, code, display_name, external_id, metadata)
        SELECT id, 'bellum-gero-live', 'Bellum Gero Live', NULL, '{}'::jsonb
        FROM source_systems WHERE code = 'core3'
        """
    )
    server_instances = sa.table(
        "server_instances",
        sa.column("code", sa.Text()),
        sa.column("display_name", sa.Text()),
        sa.column("environment", sa.Text()),
    )
    op.bulk_insert(
        server_instances,
        [{"code": "bellum-gero-live", "display_name": "Bellum Gero Live", "environment": "live"}],
    )
    planets = sa.table(
        "planets",
        sa.column("slug", sa.Text()),
        sa.column("display_name", sa.Text()),
        sa.column("core3_zone_name", sa.Text()),
    )
    op.bulk_insert(
        planets,
        [
            {"slug": "corellia", "display_name": "Corellia", "core3_zone_name": "corellia"},
            {"slug": "dantooine", "display_name": "Dantooine", "core3_zone_name": "dantooine"},
            {"slug": "dathomir", "display_name": "Dathomir", "core3_zone_name": "dathomir"},
            {"slug": "endor", "display_name": "Endor", "core3_zone_name": "endor"},
            {"slug": "lok", "display_name": "Lok", "core3_zone_name": "lok"},
            {"slug": "naboo", "display_name": "Naboo", "core3_zone_name": "naboo"},
            {"slug": "rori", "display_name": "Rori", "core3_zone_name": "rori"},
            {"slug": "talus", "display_name": "Talus", "core3_zone_name": "talus"},
            {"slug": "tatooine", "display_name": "Tatooine", "core3_zone_name": "tatooine"},
            {"slug": "yavin4", "display_name": "Yavin 4", "core3_zone_name": "yavin4"},
        ],
    )
    stat_definitions = sa.table(
        "stat_definitions",
        sa.column("code", sa.Text()),
        sa.column("display_name", sa.Text()),
        sa.column("core3_attribute_name", sa.Text()),
        sa.column("description", sa.Text()),
    )
    op.bulk_insert(
        stat_definitions,
        [
            {"code": "CR", "display_name": "Cold Resistance", "core3_attribute_name": "res_cold_resist", "description": None},
            {"code": "CD", "display_name": "Conductivity", "core3_attribute_name": "res_conductivity", "description": None},
            {"code": "DR", "display_name": "Decay Resistance", "core3_attribute_name": "res_decay_resist", "description": None},
            {"code": "FL", "display_name": "Flavor", "core3_attribute_name": "res_flavor", "description": None},
            {"code": "HR", "display_name": "Heat Resistance", "core3_attribute_name": "res_heat_resist", "description": None},
            {"code": "MA", "display_name": "Malleability", "core3_attribute_name": "res_malleability", "description": None},
            {"code": "PE", "display_name": "Potential Energy", "core3_attribute_name": "res_potential_energy", "description": None},
            {"code": "OQ", "display_name": "Overall Quality", "core3_attribute_name": "res_quality", "description": None},
            {"code": "SR", "display_name": "Shock Resistance", "core3_attribute_name": "res_shock_resistance", "description": None},
            {"code": "UT", "display_name": "Unit Toughness", "core3_attribute_name": "res_toughness", "description": None},
            {"code": "ER", "display_name": "Entangle Resistance", "core3_attribute_name": None, "description": "Galaxy Harvester historical stat; no verified Core3 mapping yet."},
        ],
    )


def downgrade() -> None:
    op.drop_table("resource_identity_conflicts")
    op.drop_index("uq_resource_identity_links_one_confirmed_source", table_name="resource_identity_links")
    op.drop_table("resource_identity_links")
    op.drop_index("ix_resource_match_candidates_status_score", table_name="resource_match_candidates")
    op.drop_table("resource_match_candidates")
    op.drop_table("unresolved_source_resources")
    op.drop_index("ix_resource_lifecycle_events_resource_type_time", table_name="resource_lifecycle_events")
    op.drop_table("resource_lifecycle_events")
    op.drop_index("ix_resource_planet_observations_resource_planet_observed", table_name="resource_planet_observations")
    op.drop_index("ix_resource_planet_observations_planet_state_observed", table_name="resource_planet_observations")
    op.drop_table("resource_planet_observations")
    op.drop_index("ix_resource_observations_resource_kind_observed", table_name="resource_observations")
    op.drop_table("resource_observations")
    op.drop_table("resource_type_memberships")
    op.drop_index("ix_resource_stat_observations_stat_value", table_name="resource_stat_observations")
    op.drop_table("resource_stat_observations")
    op.drop_index("ix_resource_names_normalized", table_name="resource_names")
    op.drop_table("resource_names")
    op.execute("DROP INDEX IF EXISTS ix_source_resources_instance_lower_name_expr")
    op.drop_table("source_resources")
    op.drop_table("resources")
    op.drop_index("uq_resource_type_stat_ranges_source", table_name="resource_type_stat_ranges")
    op.drop_index("uq_resource_type_stat_ranges_canonical", table_name="resource_type_stat_ranges")
    op.drop_table("resource_type_stat_ranges")
    op.drop_table("source_resource_types")
    op.drop_index("ix_resource_type_closure_desc_ancestor", table_name="resource_type_closure")
    op.drop_table("resource_type_closure")
    op.drop_table("resource_type_edges")
    op.drop_table("resource_types")
    op.drop_table("stat_definitions")
    op.drop_table("source_planets")
    op.drop_table("planets")
    op.drop_table("anomalies")
    op.drop_table("source_records")
    op.drop_table("source_snapshots")
    op.drop_table("import_batches")
    op.drop_table("server_epochs")
    op.drop_table("server_instances")
    op.drop_table("source_instances")
    op.drop_table("source_systems")
    op.execute("DROP EXTENSION IF EXISTS pgcrypto")
