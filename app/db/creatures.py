"""Additive, revision-scoped creature catalog. Resource availability stays elsewhere."""
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, Index, Integer, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column
from app.db.models import Base, bigint_pk, jsonb_default


class CreatureRevision(Base):
    __tablename__ = "creature_revisions"
    __table_args__ = (UniqueConstraint("repository", "revision"),)
    id: Mapped[int] = bigint_pk()
    repository: Mapped[str] = mapped_column(Text)
    revision: Mapped[str] = mapped_column(Text)
    manifest_hash: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    summary: Mapped[dict[str, Any]] = jsonb_default()


class CreatureCatalogActivation(Base):
    __tablename__ = "creature_catalog_activations"
    repository: Mapped[str] = mapped_column(Text, primary_key=True)
    revision_id: Mapped[int] = mapped_column(ForeignKey("creature_revisions.id"))


class CreatureSourceFile(Base):
    __tablename__ = "creature_source_files"
    __table_args__ = (UniqueConstraint("revision_id", "path"),)
    id: Mapped[int] = bigint_pk()
    revision_id: Mapped[int] = mapped_column(ForeignKey("creature_revisions.id"))
    path: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(Text)


class CreatureIdentity(Base):
    __tablename__ = "creature_identities"
    __table_args__ = (UniqueConstraint("repository", "template_key"),)
    id: Mapped[int] = bigint_pk()
    repository: Mapped[str] = mapped_column(Text)
    template_key: Mapped[str] = mapped_column(Text)


class CreatureDefinition(Base):
    __tablename__ = "creature_definitions"
    __table_args__ = (UniqueConstraint("revision_id", "creature_id"),
                     Index("ix_creature_definitions_revision_status", "revision_id", "validation_status"))
    id: Mapped[int] = bigint_pk()
    revision_id: Mapped[int] = mapped_column(ForeignKey("creature_revisions.id"))
    creature_id: Mapped[int] = mapped_column(ForeignKey("creature_identities.id"))
    source_file_id: Mapped[int] = mapped_column(ForeignKey("creature_source_files.id"))
    line: Mapped[int] = mapped_column(Integer)
    level: Mapped[int | None] = mapped_column(Integer)
    parent_symbol: Mapped[str | None] = mapped_column(Text)
    validation_status: Mapped[str] = mapped_column(Text)
    field_provenance: Mapped[dict[str, Any]] = jsonb_default()


class CreatureName(Base):
    __tablename__ = "creature_names"
    definition_id: Mapped[int] = mapped_column(ForeignKey("creature_definitions.id"), primary_key=True)
    kind: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[str] = mapped_column(Text)


class HarvestCategory(Base):
    __tablename__ = "harvest_categories"
    code: Mapped[str] = mapped_column(Text, primary_key=True)
    __table_args__ = (CheckConstraint("code IN ('hide','meat','bone','milk')"),)


class CreatureHarvest(Base):
    __tablename__ = "creature_harvests"
    __table_args__ = (CheckConstraint("base_amount >= 0"), Index("ix_creature_harvests_category_lookup", "category", "raw_lookup"))
    definition_id: Mapped[int] = mapped_column(ForeignKey("creature_definitions.id"), primary_key=True)
    category: Mapped[str] = mapped_column(ForeignKey("harvest_categories.code"), primary_key=True)
    raw_lookup: Mapped[str] = mapped_column(Text)
    base_amount: Mapped[int] = mapped_column(Integer)


class HarvestClassMapping(Base):
    __tablename__ = "harvest_class_mappings"
    definition_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    category: Mapped[str] = mapped_column(Text, primary_key=True)
    resource_type_id: Mapped[int] = mapped_column(ForeignKey("resource_types.id"), primary_key=True)
    method: Mapped[str] = mapped_column(Text)
    __table_args__ = (ForeignKeyConstraint(["definition_id", "category"], ["creature_harvests.definition_id", "creature_harvests.category"]),)


class SpawnNode(Base):
    """Revision-scoped region/group/lair; geometry is retained without fake waypoints."""
    __tablename__ = "creature_spawn_nodes"
    __table_args__ = (UniqueConstraint("revision_id", "kind", "key"), CheckConstraint("kind IN ('region','group','lair','static')"))
    id: Mapped[int] = bigint_pk()
    revision_id: Mapped[int] = mapped_column(ForeignKey("creature_revisions.id"))
    kind: Mapped[str] = mapped_column(Text)
    key: Mapped[str] = mapped_column(Text)
    source_file_id: Mapped[int] = mapped_column(ForeignKey("creature_source_files.id"))
    line: Mapped[int] = mapped_column(Integer)
    planet_id: Mapped[int | None] = mapped_column(ForeignKey("planets.id"))
    details: Mapped[dict[str, Any]] = jsonb_default()


class SpawnRelationship(Base):
    __tablename__ = "creature_spawn_relationships"
    id: Mapped[int] = bigint_pk()
    parent_id: Mapped[int] = mapped_column(ForeignKey("creature_spawn_nodes.id"))
    child_id: Mapped[int | None] = mapped_column(ForeignKey("creature_spawn_nodes.id"))
    definition_id: Mapped[int | None] = mapped_column(ForeignKey("creature_definitions.id"))
    __table_args__ = (CheckConstraint("(child_id IS NULL) <> (definition_id IS NULL)"),
                     Index("ix_creature_spawn_relationships_parent", "parent_id"))
    conditions: Mapped[dict[str, Any]] = jsonb_default()


class CreatureSpawnEvidence(Base):
    __tablename__ = "creature_spawn_evidence"
    __table_args__ = (UniqueConstraint("definition_id", "planet_id", "region_id", "lair_id"),
                     Index("ix_creature_spawn_evidence_planet_definition", "planet_id", "definition_id"))
    id: Mapped[int] = bigint_pk()
    definition_id: Mapped[int] = mapped_column(ForeignKey("creature_definitions.id"))
    planet_id: Mapped[int] = mapped_column(ForeignKey("planets.id"))
    region_id: Mapped[int] = mapped_column(ForeignKey("creature_spawn_nodes.id"))
    lair_id: Mapped[int] = mapped_column(ForeignKey("creature_spawn_nodes.id"))
    confidence: Mapped[str] = mapped_column(Text)
    conditions: Mapped[dict[str, Any]] = jsonb_default()


class CreatureImportDiagnostic(Base):
    __tablename__ = "creature_import_diagnostics"
    id: Mapped[int] = bigint_pk()
    revision_id: Mapped[int] = mapped_column(ForeignKey("creature_revisions.id"))
    source_file_id: Mapped[int | None] = mapped_column(ForeignKey("creature_source_files.id"))
    line: Mapped[int] = mapped_column(Integer)
    code: Mapped[str] = mapped_column(Text)
    details: Mapped[dict[str, Any]] = jsonb_default()
