"""Register Hoth without replacing existing planet identities.

Downgrade intentionally retains Hoth, even when unreferenced: the row may have
predated this migration and future or historical references must remain valid.
"""
from alembic import op

revision = "20261012_0004"
down_revision = "20261009_0003"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        INSERT INTO planets (slug, display_name, core3_zone_name, active)
        VALUES ('hoth', 'Hoth', 'hoth', true)
        ON CONFLICT (slug) DO UPDATE SET
            display_name = EXCLUDED.display_name,
            core3_zone_name = EXCLUDED.core3_zone_name,
            active = EXCLUDED.active
        WHERE planets.display_name IS DISTINCT FROM EXCLUDED.display_name
           OR planets.core3_zone_name IS DISTINCT FROM EXCLUDED.core3_zone_name
           OR planets.active IS DISTINCT FROM EXCLUDED.active
    """)


def downgrade():
    # Reference data cannot safely be attributed to this revision. Never delete
    # Hoth or undo canonical corrections that may now be used by other records.
    pass
