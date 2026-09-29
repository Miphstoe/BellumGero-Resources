from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    type_annotation_map = {
        dict[str, Any]: JSONB,
    }


def bigint_pk() -> Mapped[int]:
    return mapped_column(BigInteger, primary_key=True)


def jsonb_default() -> Mapped[dict[str, Any]]:
    return mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))


class SourceSystem(Base):
    __tablename__ = "source_systems"

    id: Mapped[int] = bigint_pk()
    code: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)


class SourceInstance(Base):
    __tablename__ = "source_instances"
    __table_args__ = (
        UniqueConstraint("source_system_id", "code", name="uq_source_instances_system_code"),
    )

    id: Mapped[int] = bigint_pk()
    source_system_id: Mapped[int] = mapped_column(ForeignKey("source_systems.id"), nullable=False)
    code: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    external_id: Mapped[str | None] = mapped_column(Text)
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb"))


class ServerInstance(Base):
    __tablename__ = "server_instances"

    id: Mapped[int] = bigint_pk()
    code: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    environment: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ServerEpoch(Base):
    __tablename__ = "server_epochs"
    __table_args__ = (
        UniqueConstraint("server_instance_id", "epoch_code", name="uq_server_epochs_instance_code"),
    )

    id: Mapped[int] = bigint_pk()
    server_instance_id: Mapped[int] = mapped_column(ForeignKey("server_instances.id"), nullable=False)
    epoch_code: Mapped[str] = mapped_column(Text, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    description: Mapped[str | None] = mapped_column(Text)


class ImportBatch(Base):
    __tablename__ = "import_batches"

    id: Mapped[int] = bigint_pk()
    source_instance_id: Mapped[int] = mapped_column(ForeignKey("source_instances.id"), nullable=False)
    import_kind: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    tool_version: Mapped[str | None] = mapped_column(Text)
    source_revision: Mapped[str | None] = mapped_column(Text)
    parameters: Mapped[dict[str, Any]] = jsonb_default()
    summary: Mapped[dict[str, Any]] = jsonb_default()


class SourceSnapshot(Base):
    __tablename__ = "source_snapshots"
    __table_args__ = (
        UniqueConstraint("import_batch_id", "external_path", "content_sha256", name="uq_source_snapshots_batch_path_hash"),
    )

    id: Mapped[int] = bigint_pk()
    import_batch_id: Mapped[int] = mapped_column(ForeignKey("import_batches.id"), nullable=False)
    snapshot_kind: Mapped[str] = mapped_column(Text, nullable=False)
    external_path: Mapped[str | None] = mapped_column(Text)
    content_sha256: Mapped[str | None] = mapped_column(Text)
    byte_size: Mapped[int | None] = mapped_column(BigInteger)
    record_count: Mapped[int | None] = mapped_column(Integer)
    observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_entered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb"))


class SourceRecord(Base):
    __tablename__ = "source_records"

    id: Mapped[int] = bigint_pk()
    source_snapshot_id: Mapped[int | None] = mapped_column(ForeignKey("source_snapshots.id"))
    record_type: Mapped[str] = mapped_column(Text, nullable=False)
    source_key: Mapped[str | None] = mapped_column(Text)
    parse_status: Mapped[str] = mapped_column(Text, nullable=False)
    parse_error: Mapped[str | None] = mapped_column(Text)
    payload_ref: Mapped[str | None] = mapped_column(Text)
    payload_hash: Mapped[str | None] = mapped_column(Text)
    normalized_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class Anomaly(Base):
    __tablename__ = "anomalies"
    __table_args__ = (
        UniqueConstraint("source_record_id", "anomaly_code", "entity_kind", "entity_id", name="uq_anomalies_record_code_entity"),
    )

    id: Mapped[int] = bigint_pk()
    source_record_id: Mapped[int | None] = mapped_column(ForeignKey("source_records.id"))
    entity_kind: Mapped[str] = mapped_column(Text, nullable=False)
    entity_id: Mapped[int | None] = mapped_column(BigInteger)
    anomaly_code: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[str] = mapped_column(Text, nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    details: Mapped[dict[str, Any]] = jsonb_default()
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))


class Planet(Base):
    __tablename__ = "planets"

    id: Mapped[int] = bigint_pk()
    slug: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    core3_zone_name: Mapped[str | None] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))


class SourcePlanet(Base):
    __tablename__ = "source_planets"
    __table_args__ = (
        UniqueConstraint("source_instance_id", "source_planet_id", name="uq_source_planets_instance_source_id"),
    )

    id: Mapped[int] = bigint_pk()
    source_instance_id: Mapped[int] = mapped_column(ForeignKey("source_instances.id"), nullable=False)
    source_planet_id: Mapped[str] = mapped_column(Text, nullable=False)
    source_planet_name: Mapped[str] = mapped_column(Text, nullable=False)
    planet_id: Mapped[int | None] = mapped_column(ForeignKey("planets.id"))
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb"))


class StatDefinition(Base):
    __tablename__ = "stat_definitions"

    code: Mapped[str] = mapped_column(Text, primary_key=True)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    core3_attribute_name: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    is_canonical: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))


class ResourceType(Base):
    __tablename__ = "resource_types"

    id: Mapped[int] = bigint_pk()
    slug: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    is_spawnable: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    core3_stf_name: Mapped[str | None] = mapped_column(Text)
    core3_display_class: Mapped[str | None] = mapped_column(Text)
    container_type: Mapped[str | None] = mapped_column(Text)
    inventory_type: Mapped[str | None] = mapped_column(Text)
    specific_planet_id: Mapped[int | None] = mapped_column(ForeignKey("planets.id"))
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb"))


class ResourceTypeEdge(Base):
    __tablename__ = "resource_type_edges"
    __table_args__ = (
        CheckConstraint("parent_type_id <> child_type_id", name="ck_resource_type_edges_no_self_edge"),
    )

    parent_type_id: Mapped[int] = mapped_column(ForeignKey("resource_types.id"), primary_key=True)
    child_type_id: Mapped[int] = mapped_column(ForeignKey("resource_types.id"), primary_key=True)
    source: Mapped[str] = mapped_column(Text, primary_key=True)


class ResourceTypeClosure(Base):
    __tablename__ = "resource_type_closure"
    __table_args__ = (
        CheckConstraint("depth >= 0", name="ck_resource_type_closure_depth_nonnegative"),
        Index("ix_resource_type_closure_desc_ancestor", "descendant_type_id", "ancestor_type_id"),
    )

    ancestor_type_id: Mapped[int] = mapped_column(ForeignKey("resource_types.id"), primary_key=True)
    descendant_type_id: Mapped[int] = mapped_column(ForeignKey("resource_types.id"), primary_key=True)
    depth: Mapped[int] = mapped_column(Integer, nullable=False)


class SourceResourceType(Base):
    __tablename__ = "source_resource_types"
    __table_args__ = (
        UniqueConstraint("source_instance_id", "source_type_key", name="uq_source_resource_types_instance_key"),
    )

    id: Mapped[int] = bigint_pk()
    source_instance_id: Mapped[int] = mapped_column(ForeignKey("source_instances.id"), nullable=False)
    source_type_key: Mapped[str] = mapped_column(Text, nullable=False)
    source_type_name: Mapped[str | None] = mapped_column(Text)
    canonical_type_id: Mapped[int | None] = mapped_column(ForeignKey("resource_types.id"))
    mapping_status: Mapped[str] = mapped_column(Text, nullable=False)
    mapping_confidence: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb"))


class ResourceTypeStatRange(Base):
    __tablename__ = "resource_type_stat_ranges"
    __table_args__ = (
        CheckConstraint("num_nonnulls(resource_type_id, source_resource_type_id) = 1", name="ck_resource_type_stat_ranges_one_owner"),
        CheckConstraint(
            "(is_applicable = false AND min_value IS NULL AND max_value IS NULL) OR "
            "(is_applicable = true AND ((min_value IS NULL AND max_value IS NULL) OR "
            "(min_value IS NOT NULL AND max_value IS NOT NULL AND min_value <= max_value)))",
            name="ck_resource_type_stat_ranges_applicability",
        ),
    )

    id: Mapped[int] = bigint_pk()
    resource_type_id: Mapped[int | None] = mapped_column(ForeignKey("resource_types.id"))
    source_resource_type_id: Mapped[int | None] = mapped_column(ForeignKey("source_resource_types.id"))
    stat_code: Mapped[str] = mapped_column(ForeignKey("stat_definitions.code"), nullable=False)
    min_value: Mapped[int | None] = mapped_column(Integer)
    max_value: Mapped[int | None] = mapped_column(Integer)
    is_applicable: Mapped[bool] = mapped_column(Boolean, nullable=False)
    source_record_id: Mapped[int | None] = mapped_column(ForeignKey("source_records.id"))


class Resource(Base):
    __tablename__ = "resources"

    id: Mapped[int] = bigint_pk()
    public_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, unique=True, server_default=text("gen_random_uuid()"))
    public_slug: Mapped[str | None] = mapped_column(Text, unique=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    canonical_type_id: Mapped[int | None] = mapped_column(ForeignKey("resource_types.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    status: Mapped[str] = mapped_column(Text, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)


class SourceResource(Base):
    __tablename__ = "source_resources"
    __table_args__ = (
        Index("ix_source_resources_instance_lower_name", "source_instance_id", text("lower(source_resource_name)")),
    )

    id: Mapped[int] = bigint_pk()
    source_instance_id: Mapped[int] = mapped_column(ForeignKey("source_instances.id"), nullable=False)
    server_instance_id: Mapped[int | None] = mapped_column(ForeignKey("server_instances.id"))
    server_epoch_id: Mapped[int | None] = mapped_column(ForeignKey("server_epochs.id"))
    source_resource_id: Mapped[str] = mapped_column(Text, nullable=False)
    source_resource_name: Mapped[str] = mapped_column(Text, nullable=False)
    source_resource_type_id: Mapped[int | None] = mapped_column(ForeignKey("source_resource_types.id"))
    canonical_resource_id: Mapped[int | None] = mapped_column(ForeignKey("resources.id"))
    identity_status: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[str] = mapped_column(Text, nullable=False)
    first_source_record_id: Mapped[int | None] = mapped_column(ForeignKey("source_records.id"))
    last_source_record_id: Mapped[int | None] = mapped_column(ForeignKey("source_records.id"))
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb"))


class ResourceName(Base):
    __tablename__ = "resource_names"
    __table_args__ = (
        UniqueConstraint("source_resource_id", "normalized_name", name="uq_resource_names_source_normalized"),
        Index("ix_resource_names_normalized", "normalized_name"),
    )

    id: Mapped[int] = bigint_pk()
    canonical_resource_id: Mapped[int | None] = mapped_column(ForeignKey("resources.id"))
    source_resource_id: Mapped[int | None] = mapped_column(ForeignKey("source_resources.id"))
    name: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_name: Mapped[str] = mapped_column(Text, nullable=False)
    source_record_id: Mapped[int | None] = mapped_column(ForeignKey("source_records.id"))


class ResourceStatObservation(Base):
    __tablename__ = "resource_stat_observations"
    __table_args__ = (
        CheckConstraint(
            "(is_present = true AND value IS NOT NULL) OR (is_present = false AND value IS NULL)",
            name="ck_resource_stat_observations_presence_value",
        ),
        Index("ix_resource_stat_observations_stat_value", "stat_code", "value"),
    )

    id: Mapped[int] = bigint_pk()
    source_resource_id: Mapped[int] = mapped_column(ForeignKey("source_resources.id"), nullable=False)
    stat_code: Mapped[str] = mapped_column(ForeignKey("stat_definitions.code"), nullable=False)
    value: Mapped[int | None] = mapped_column(Integer)
    is_present: Mapped[bool] = mapped_column(Boolean, nullable=False)
    observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_record_id: Mapped[int | None] = mapped_column(ForeignKey("source_records.id"))
    import_batch_id: Mapped[int] = mapped_column(ForeignKey("import_batches.id"), nullable=False)


class ResourceTypeMembership(Base):
    __tablename__ = "resource_type_memberships"

    source_resource_id: Mapped[int] = mapped_column(ForeignKey("source_resources.id"), primary_key=True)
    resource_type_id: Mapped[int] = mapped_column(ForeignKey("resource_types.id"), primary_key=True)
    membership_kind: Mapped[str] = mapped_column(Text, nullable=False)
    source_record_id: Mapped[int | None] = mapped_column(ForeignKey("source_records.id"))


class ResourceObservation(Base):
    __tablename__ = "resource_observations"
    __table_args__ = (
        Index("ix_resource_observations_resource_kind_observed", "source_resource_id", "observation_kind", "observed_at"),
    )

    id: Mapped[int] = bigint_pk()
    source_resource_id: Mapped[int] = mapped_column(ForeignKey("source_resources.id"), nullable=False)
    source_record_id: Mapped[int | None] = mapped_column(ForeignKey("source_records.id"))
    observation_kind: Mapped[str] = mapped_column(Text, nullable=False)
    observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_entered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_unavailable_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    confidence: Mapped[str] = mapped_column(Text, nullable=False)
    details: Mapped[dict[str, Any]] = jsonb_default()


class ResourcePlanetObservation(Base):
    __tablename__ = "resource_planet_observations"
    __table_args__ = (
        Index("ix_resource_planet_observations_planet_state_observed", "planet_id", "state", "observed_at"),
        Index("ix_resource_planet_observations_resource_planet_observed", "source_resource_id", "planet_id", "observed_at"),
    )

    id: Mapped[int] = bigint_pk()
    source_resource_id: Mapped[int] = mapped_column(ForeignKey("source_resources.id"), nullable=False)
    planet_id: Mapped[int] = mapped_column(ForeignKey("planets.id"), nullable=False)
    source_planet_id: Mapped[int | None] = mapped_column(ForeignKey("source_planets.id"))
    state: Mapped[str] = mapped_column(Text, nullable=False)
    observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_entered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_unavailable_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    confidence: Mapped[str] = mapped_column(Text, nullable=False)
    source_record_id: Mapped[int | None] = mapped_column(ForeignKey("source_records.id"))
    import_batch_id: Mapped[int] = mapped_column(ForeignKey("import_batches.id"), nullable=False)
    details: Mapped[dict[str, Any]] = jsonb_default()


class ResourceLifecycleEvent(Base):
    __tablename__ = "resource_lifecycle_events"
    __table_args__ = (
        Index("ix_resource_lifecycle_events_resource_type_time", "source_resource_id", "event_type", "event_time"),
    )

    id: Mapped[int] = bigint_pk()
    source_resource_id: Mapped[int] = mapped_column(ForeignKey("source_resources.id"), nullable=False)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    event_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    event_time_confidence: Mapped[str] = mapped_column(Text, nullable=False)
    planet_id: Mapped[int | None] = mapped_column(ForeignKey("planets.id"))
    source_record_id: Mapped[int | None] = mapped_column(ForeignKey("source_records.id"))
    import_batch_id: Mapped[int] = mapped_column(ForeignKey("import_batches.id"), nullable=False)
    details: Mapped[dict[str, Any]] = jsonb_default()


class Core3LiveSnapshotImport(Base):
    __tablename__ = "core3_live_snapshot_imports"
    __table_args__ = (
        UniqueConstraint("source_instance_id", "captured_at", name="uq_core3_live_snapshot_imports_instance_capture"),
        Index("ix_core3_live_snapshot_imports_instance_capture", "source_instance_id", "captured_at"),
        Index("ix_core3_live_snapshot_imports_instance_advanced", "source_instance_id", "advanced_current"),
    )

    id: Mapped[int] = bigint_pk()
    source_instance_id: Mapped[int] = mapped_column(ForeignKey("source_instances.id"), nullable=False)
    source_snapshot_id: Mapped[int] = mapped_column(ForeignKey("source_snapshots.id"), nullable=False, unique=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    content_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    complete: Mapped[bool] = mapped_column(Boolean, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    advanced_current: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb"))


class CurrentResourceAvailability(Base):
    __tablename__ = "current_resource_availability"
    __table_args__ = (
        Index("ix_current_resource_availability_instance_active", "source_instance_id", "is_active"),
        Index("ix_current_resource_availability_current_snapshot", "current_source_snapshot_id"),
    )

    source_resource_id: Mapped[int] = mapped_column(ForeignKey("source_resources.id"), primary_key=True)
    source_instance_id: Mapped[int] = mapped_column(ForeignKey("source_instances.id"), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False)
    last_seen_source_snapshot_id: Mapped[int | None] = mapped_column(ForeignKey("source_snapshots.id"))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    current_source_snapshot_id: Mapped[int] = mapped_column(ForeignKey("source_snapshots.id"), nullable=False)
    current_as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    absent_source_snapshot_id: Mapped[int | None] = mapped_column(ForeignKey("source_snapshots.id"))
    absent_as_of: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    details: Mapped[dict[str, Any]] = jsonb_default()


class UnresolvedSourceResource(Base):
    __tablename__ = "unresolved_source_resources"
    __table_args__ = (
        UniqueConstraint("source_instance_id", "normalized_name", "import_batch_id", name="uq_unresolved_source_resources_instance_name_batch"),
    )

    id: Mapped[int] = bigint_pk()
    source_instance_id: Mapped[int] = mapped_column(ForeignKey("source_instances.id"), nullable=False)
    import_batch_id: Mapped[int] = mapped_column(ForeignKey("import_batches.id"), nullable=False)
    source_name: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_name: Mapped[str] = mapped_column(Text, nullable=False)
    result_status: Mapped[str] = mapped_column(Text, nullable=False)
    http_status: Mapped[int | None] = mapped_column(Integer)
    source_result_text: Mapped[str | None] = mapped_column(Text)
    source_record_id: Mapped[int | None] = mapped_column(ForeignKey("source_records.id"))
    details: Mapped[dict[str, Any]] = jsonb_default()


class ResourceMatchCandidate(Base):
    __tablename__ = "resource_match_candidates"
    __table_args__ = (
        UniqueConstraint("left_source_resource_id", "right_source_resource_id", name="uq_resource_match_candidates_pair"),
        CheckConstraint("left_source_resource_id <> right_source_resource_id", name="ck_resource_match_candidates_not_same"),
        Index("ix_resource_match_candidates_status_score", "status", "score"),
    )

    id: Mapped[int] = bigint_pk()
    left_source_resource_id: Mapped[int] = mapped_column(ForeignKey("source_resources.id"), nullable=False)
    right_source_resource_id: Mapped[int] = mapped_column(ForeignKey("source_resources.id"), nullable=False)
    score: Mapped[float | None] = mapped_column(Numeric)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    evidence: Mapped[dict[str, Any]] = jsonb_default()
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))


class ResourceIdentityLink(Base):
    __tablename__ = "resource_identity_links"

    canonical_resource_id: Mapped[int] = mapped_column(ForeignKey("resources.id"), primary_key=True)
    source_resource_id: Mapped[int] = mapped_column(ForeignKey("source_resources.id"), primary_key=True)
    link_status: Mapped[str] = mapped_column(Text, nullable=False)
    confirmed_by: Mapped[str] = mapped_column(Text, nullable=False)
    confirmed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    evidence: Mapped[dict[str, Any]] = jsonb_default()


class ResourceIdentityConflict(Base):
    __tablename__ = "resource_identity_conflicts"

    id: Mapped[int] = bigint_pk()
    candidate_id: Mapped[int | None] = mapped_column(ForeignKey("resource_match_candidates.id"))
    conflict_type: Mapped[str] = mapped_column(Text, nullable=False)
    details: Mapped[dict[str, Any]] = jsonb_default()
    status: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
