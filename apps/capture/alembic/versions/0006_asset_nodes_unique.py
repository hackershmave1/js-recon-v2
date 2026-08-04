"""asset_nodes unique constraint

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-04

Adds ``uq_asset_nodes_session_url_type`` UNIQUE (session_id, url, asset_type) so
concurrent discovery can no longer create duplicate nodes for the same asset (see
api/app/services/asset_graph_service.py :: add_discovered_asset, which now guards
its INSERT with a SAVEPOINT + re-SELECT that relies on this constraint).

The constraint cannot be added while duplicates exist, so the upgrade first
collapses any existing duplicate groups — keeping the earliest id per
(session_id, url, asset_type) — after repointing asset_edges that reference a
soon-to-be-deleted duplicate onto the kept node (asset_edges.source_node_id /
target_node_id FK-reference asset_nodes.id with no ON DELETE, so a bare delete
would raise a foreign-key violation).
"""
from alembic import op


revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Repoint edges off duplicate nodes onto the keeper (earliest id per group).
    op.execute(
        """
        WITH ranked AS (
            SELECT
                id,
                first_value(id) OVER (
                    PARTITION BY session_id, url, asset_type ORDER BY id
                ) AS keeper_id
            FROM asset_nodes
        )
        UPDATE asset_edges e
        SET source_node_id = r.keeper_id
        FROM ranked r
        WHERE e.source_node_id = r.id AND r.id <> r.keeper_id
        """
    )
    op.execute(
        """
        WITH ranked AS (
            SELECT
                id,
                first_value(id) OVER (
                    PARTITION BY session_id, url, asset_type ORDER BY id
                ) AS keeper_id
            FROM asset_nodes
        )
        UPDATE asset_edges e
        SET target_node_id = r.keeper_id
        FROM ranked r
        WHERE e.target_node_id = r.id AND r.id <> r.keeper_id
        """
    )

    # 2. Delete the duplicate nodes, keeping the earliest id per group.
    op.execute(
        """
        DELETE FROM asset_nodes a
        USING (
            SELECT
                id,
                row_number() OVER (
                    PARTITION BY session_id, url, asset_type ORDER BY id
                ) AS rn
            FROM asset_nodes
        ) d
        WHERE a.id = d.id AND d.rn > 1
        """
    )

    # 3. Now that each (session_id, url, asset_type) is unique, enforce it.
    op.create_unique_constraint(
        "uq_asset_nodes_session_url_type",
        "asset_nodes",
        ["session_id", "url", "asset_type"],
    )


def downgrade() -> None:
    # The dedup in upgrade() is not reversible (collapsed rows are gone); dropping
    # the constraint restores the pre-0006 schema shape.
    op.drop_constraint("uq_asset_nodes_session_url_type", "asset_nodes", type_="unique")
