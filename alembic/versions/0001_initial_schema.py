"""initial_schema

Revision ID: 0001
Revises:
Create Date: 2026-04-20

Manually authored (no live DB available for autogenerate) from the
SQLAlchemy model definitions in api/app/models/.

Tables covered: sessions, files, file_analyses, dependencies,
source_maps, asset_nodes, asset_edges, jobs.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '0001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- sessions ---
    op.create_table(
        'sessions',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('name', sa.String(), nullable=True),
        sa.Column('source', sa.String(), nullable=False, server_default='extension'),
        sa.Column('version', sa.String(), nullable=False, server_default='3.0.0'),
    )

    # --- files ---
    op.create_table(
        'files',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('session_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('sessions.id'), nullable=False),
        sa.Column('url', sa.Text(), nullable=False),
        sa.Column('content_hash', sa.String(), nullable=False),
        sa.Column('content_type', sa.String(), nullable=True),
        sa.Column('content_encoding', sa.String(), nullable=True),
        sa.Column('content_length', sa.Integer(), nullable=False),
        sa.Column('captured_at', sa.DateTime(), nullable=False),
        sa.Column('file_metadata', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('stored_path', sa.Text(), nullable=False),
        sa.Column('map_path', sa.Text(), nullable=True),
        sa.Column('content_purged', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('content_purged_at', sa.DateTime(), nullable=True),
        sa.Column('purge_reason', sa.Text(), nullable=True),
        sa.UniqueConstraint('session_id', 'content_hash', name='files_session_content_unique'),
    )

    # --- file_analyses ---
    op.create_table(
        'file_analyses',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('file_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('files.id'), nullable=False, unique=True),
        sa.Column('session_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('sessions.id'), nullable=False),
        sa.Column('status', sa.String(), nullable=False, server_default='completed'),
        sa.Column('analysis', postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default='{}'),
        sa.Column('stats', postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default='{}'),
        sa.Column('extractors_used', postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default='[]'),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
    )

    # --- dependencies ---
    op.create_table(
        'dependencies',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('file_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('files.id'), nullable=False),
        sa.Column('dep_url', sa.Text(), nullable=False),
        sa.Column('resolved_url', sa.Text(), nullable=True),
        sa.Column('dep_type', sa.String(), nullable=True),
    )

    # --- source_maps ---
    op.create_table(
        'source_maps',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('file_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('files.id'), nullable=False),
        sa.Column('map_url', sa.Text(), nullable=True),
        sa.Column('detected_map_url', sa.Text(), nullable=True),
        sa.Column('stored_path', sa.Text(), nullable=True),
        sa.Column('parsed', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('processing_status', sa.String(), nullable=False, server_default='pending'),
        sa.Column('processing_error', sa.Text(), nullable=True),
        sa.Column('reconstructed_files_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('processed_at', sa.DateTime(), nullable=True),
        sa.Column('validation_state', sa.JSON(), nullable=True),
        sa.Column('content_purged', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('content_purged_at', sa.DateTime(), nullable=True),
        sa.Column('purge_reason', sa.Text(), nullable=True),
    )

    # --- asset_nodes ---
    op.create_table(
        'asset_nodes',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('session_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('sessions.id'), nullable=False),
        sa.Column('file_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('files.id'), nullable=True),
        sa.Column('url', sa.Text(), nullable=False),
        sa.Column('asset_type', sa.String(), nullable=False),
        sa.Column('content_hash', sa.String(), nullable=True),
        sa.Column('discovered_at', sa.DateTime(), nullable=False),
        sa.Column('discovery_depth', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('metadata', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('fetch_attempted', sa.String(), nullable=False, server_default='pending'),
        sa.Column('fetch_error', sa.Text(), nullable=True),
        sa.Column('processed', sa.String(), nullable=False, server_default='pending'),
        sa.Column('processing_error', sa.Text(), nullable=True),
    )
    op.create_index('idx_asset_nodes_session_id', 'asset_nodes', ['session_id'])
    op.create_index('idx_asset_nodes_file_id', 'asset_nodes', ['file_id'])
    op.create_index('idx_asset_nodes_url', 'asset_nodes', ['url'])
    op.create_index('idx_asset_nodes_discovery_depth', 'asset_nodes', ['discovery_depth'])

    # --- asset_edges ---
    op.create_table(
        'asset_edges',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('session_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('sessions.id'), nullable=False),
        sa.Column('source_node_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('asset_nodes.id'), nullable=False),
        sa.Column('target_node_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('asset_nodes.id'), nullable=False),
        sa.Column('discovery_method', sa.String(), nullable=False),
        sa.Column('discovered_at', sa.DateTime(), nullable=False),
        sa.Column('referer', sa.Text(), nullable=True),
        sa.Column('initiator', sa.Text(), nullable=True),
        sa.Column('context_metadata', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.create_index('idx_asset_edges_session_id', 'asset_edges', ['session_id'])
    op.create_index('idx_asset_edges_source_node_id', 'asset_edges', ['source_node_id'])
    op.create_index('idx_asset_edges_target_node_id', 'asset_edges', ['target_node_id'])
    op.create_index('idx_asset_edges_discovery_method', 'asset_edges', ['discovery_method'])

    # --- jobs ---
    op.create_table(
        'jobs',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('job_type', sa.String(32), nullable=False),
        sa.Column('session_id', sa.String(36), nullable=True, index=True),
        sa.Column('status', sa.String(32), nullable=False, server_default='queued'),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('started_at', sa.DateTime(), nullable=True),
        sa.Column('finished_at', sa.DateTime(), nullable=True),
        sa.Column('cancel_requested', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('cancel_requested_at', sa.DateTime(), nullable=True),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('state_json', sa.JSON(), nullable=False, server_default='{}'),
    )
    # NOTE: session_id above already declares index=True, which create_table emits as
    # ix_jobs_session_id. A second explicit create_index here is a duplicate that errors
    # ("relation already exists") on a from-scratch upgrade against a truly empty DB.


def downgrade() -> None:
    op.drop_index('ix_jobs_session_id', table_name='jobs')
    op.drop_table('jobs')
    op.drop_index('idx_asset_edges_discovery_method', table_name='asset_edges')
    op.drop_index('idx_asset_edges_target_node_id', table_name='asset_edges')
    op.drop_index('idx_asset_edges_source_node_id', table_name='asset_edges')
    op.drop_index('idx_asset_edges_session_id', table_name='asset_edges')
    op.drop_table('asset_edges')
    op.drop_index('idx_asset_nodes_discovery_depth', table_name='asset_nodes')
    op.drop_index('idx_asset_nodes_url', table_name='asset_nodes')
    op.drop_index('idx_asset_nodes_file_id', table_name='asset_nodes')
    op.drop_index('idx_asset_nodes_session_id', table_name='asset_nodes')
    op.drop_table('asset_nodes')
    op.drop_table('source_maps')
    op.drop_table('dependencies')
    op.drop_table('file_analyses')
    op.drop_table('files')
    op.drop_table('sessions')
