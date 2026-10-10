"""Revision-scoped offline creature harvesting catalog."""
from alembic import op

revision = "20261009_0003"
down_revision = "20260929_0002"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE TABLE creature_identities (
	id BIGSERIAL NOT NULL,
	repository TEXT NOT NULL,
	template_key TEXT NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (repository, template_key)
)
""")
    op.execute("""CREATE TABLE creature_revisions (
	id BIGSERIAL NOT NULL,
	repository TEXT NOT NULL,
	revision TEXT NOT NULL,
	manifest_hash TEXT NOT NULL,
	status TEXT NOT NULL,
	imported_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	summary JSONB DEFAULT '{}'::jsonb NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (repository, revision)
)
""")
    op.execute("""CREATE TABLE harvest_categories (
	code TEXT NOT NULL,
	PRIMARY KEY (code),
	CHECK (code IN ('hide','meat','bone','milk'))
)
""")
    op.execute("""CREATE TABLE creature_catalog_activations (
	repository TEXT NOT NULL,
	revision_id BIGINT NOT NULL,
	PRIMARY KEY (repository),
	FOREIGN KEY(revision_id) REFERENCES creature_revisions (id)
)
""")
    op.execute("""CREATE TABLE creature_source_files (
	id BIGSERIAL NOT NULL,
	revision_id BIGINT NOT NULL,
	path TEXT NOT NULL,
	sha256 TEXT NOT NULL,
	role TEXT NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (revision_id, path),
	FOREIGN KEY(revision_id) REFERENCES creature_revisions (id)
)
""")
    op.execute("""CREATE TABLE creature_definitions (
	id BIGSERIAL NOT NULL,
	revision_id BIGINT NOT NULL,
	creature_id BIGINT NOT NULL,
	source_file_id BIGINT NOT NULL,
	line INTEGER NOT NULL,
	level INTEGER,
	parent_symbol TEXT,
	validation_status TEXT NOT NULL,
	field_provenance JSONB DEFAULT '{}'::jsonb NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (revision_id, creature_id),
	FOREIGN KEY(revision_id) REFERENCES creature_revisions (id),
	FOREIGN KEY(creature_id) REFERENCES creature_identities (id),
	FOREIGN KEY(source_file_id) REFERENCES creature_source_files (id)
)
""")
    op.execute("""CREATE TABLE creature_import_diagnostics (
	id BIGSERIAL NOT NULL,
	revision_id BIGINT NOT NULL,
	source_file_id BIGINT,
	line INTEGER NOT NULL,
	code TEXT NOT NULL,
	details JSONB DEFAULT '{}'::jsonb NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(revision_id) REFERENCES creature_revisions (id),
	FOREIGN KEY(source_file_id) REFERENCES creature_source_files (id)
)
""")
    op.execute("""CREATE TABLE creature_spawn_nodes (
	id BIGSERIAL NOT NULL,
	revision_id BIGINT NOT NULL,
	kind TEXT NOT NULL,
	key TEXT NOT NULL,
	source_file_id BIGINT NOT NULL,
	line INTEGER NOT NULL,
	planet_id BIGINT,
	details JSONB DEFAULT '{}'::jsonb NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (revision_id, kind, key),
	CHECK (kind IN ('region','group','lair','static')),
	FOREIGN KEY(revision_id) REFERENCES creature_revisions (id),
	FOREIGN KEY(source_file_id) REFERENCES creature_source_files (id),
	FOREIGN KEY(planet_id) REFERENCES planets (id)
)
""")
    op.execute("""CREATE TABLE creature_harvests (
	definition_id BIGINT NOT NULL,
	category TEXT NOT NULL,
	raw_lookup TEXT NOT NULL,
	base_amount INTEGER NOT NULL,
	PRIMARY KEY (definition_id, category),
	CHECK (base_amount >= 0),
	FOREIGN KEY(definition_id) REFERENCES creature_definitions (id),
	FOREIGN KEY(category) REFERENCES harvest_categories (code)
)
""")
    op.execute("""CREATE TABLE creature_names (
	definition_id BIGINT NOT NULL,
	kind TEXT NOT NULL,
	value TEXT NOT NULL,
	PRIMARY KEY (definition_id, kind),
	FOREIGN KEY(definition_id) REFERENCES creature_definitions (id)
)
""")
    op.execute("""CREATE TABLE creature_spawn_evidence (
	id BIGSERIAL NOT NULL,
	definition_id BIGINT NOT NULL,
	planet_id BIGINT NOT NULL,
	region_id BIGINT NOT NULL,
	lair_id BIGINT NOT NULL,
	confidence TEXT NOT NULL,
	conditions JSONB DEFAULT '{}'::jsonb NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (definition_id, planet_id, region_id, lair_id),
	FOREIGN KEY(definition_id) REFERENCES creature_definitions (id),
	FOREIGN KEY(planet_id) REFERENCES planets (id),
	FOREIGN KEY(region_id) REFERENCES creature_spawn_nodes (id),
	FOREIGN KEY(lair_id) REFERENCES creature_spawn_nodes (id)
)
""")
    op.execute("""CREATE TABLE creature_spawn_relationships (
	id BIGSERIAL NOT NULL,
	parent_id BIGINT NOT NULL,
	child_id BIGINT,
	definition_id BIGINT,
	conditions JSONB DEFAULT '{}'::jsonb NOT NULL,
	PRIMARY KEY (id),
	CHECK ((child_id IS NULL) <> (definition_id IS NULL)),
	FOREIGN KEY(parent_id) REFERENCES creature_spawn_nodes (id),
	FOREIGN KEY(child_id) REFERENCES creature_spawn_nodes (id),
	FOREIGN KEY(definition_id) REFERENCES creature_definitions (id)
)
""")
    op.execute("""CREATE TABLE harvest_class_mappings (
	definition_id BIGINT NOT NULL,
	category TEXT NOT NULL,
	resource_type_id BIGINT NOT NULL,
	method TEXT NOT NULL,
	PRIMARY KEY (definition_id, category, resource_type_id),
	FOREIGN KEY(definition_id, category) REFERENCES creature_harvests (definition_id, category),
	FOREIGN KEY(resource_type_id) REFERENCES resource_types (id)
)
""")
    op.execute("INSERT INTO harvest_categories(code) VALUES ('hide'),('meat'),('bone'),('milk')")
    op.create_index('ix_creature_definitions_revision_status', 'creature_definitions', ['revision_id', 'validation_status'])
    op.create_index('ix_creature_harvests_category_lookup', 'creature_harvests', ['category', 'raw_lookup'])
    op.create_index('ix_creature_spawn_evidence_planet_definition', 'creature_spawn_evidence', ['planet_id', 'definition_id'])
    op.create_index('ix_creature_spawn_relationships_parent', 'creature_spawn_relationships', ['parent_id'])


def downgrade():
    op.drop_table('harvest_class_mappings')
    op.drop_table('creature_spawn_relationships')
    op.drop_table('creature_spawn_evidence')
    op.drop_table('creature_names')
    op.drop_table('creature_harvests')
    op.drop_table('creature_spawn_nodes')
    op.drop_table('creature_import_diagnostics')
    op.drop_table('creature_definitions')
    op.drop_table('creature_source_files')
    op.drop_table('creature_catalog_activations')
    op.drop_table('harvest_categories')
    op.drop_table('creature_revisions')
    op.drop_table('creature_identities')
