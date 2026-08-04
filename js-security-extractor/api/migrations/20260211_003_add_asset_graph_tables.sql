-- Add asset graph tables for B-027 - Unified Asset Graph for Discovery Provenance
-- Safe to run multiple times on PostgreSQL because of IF NOT EXISTS guards.

-- Create asset_nodes table
CREATE TABLE IF NOT EXISTS asset_nodes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID NOT NULL,
    file_id UUID NULL,
    url TEXT NOT NULL,
    asset_type VARCHAR NOT NULL,
    content_hash VARCHAR NULL,
    discovered_at TIMESTAMP NOT NULL DEFAULT NOW(),
    discovery_depth INTEGER NOT NULL DEFAULT 0,
    metadata JSONB NULL,
    fetch_attempted VARCHAR NOT NULL DEFAULT 'pending',
    fetch_error TEXT NULL,
    processed VARCHAR NOT NULL DEFAULT 'pending',
    processing_error TEXT NULL,
    FOREIGN KEY (file_id) REFERENCES files(id),
    FOREIGN KEY (session_id) REFERENCES sessions(id)
);

-- Create asset_edges table
CREATE TABLE IF NOT EXISTS asset_edges (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID NOT NULL,
    source_node_id UUID NOT NULL,
    target_node_id UUID NOT NULL,
    discovery_method VARCHAR NOT NULL,
    discovered_at TIMESTAMP NOT NULL DEFAULT NOW(),
    referer TEXT NULL,
    initiator TEXT NULL,
    context_metadata JSONB NULL,
    FOREIGN KEY (session_id) REFERENCES sessions(id),
    FOREIGN KEY (source_node_id) REFERENCES asset_nodes(id),
    FOREIGN KEY (target_node_id) REFERENCES asset_nodes(id)
);

-- Create indexes for performance (only if they don't exist)
CREATE INDEX IF NOT EXISTS idx_asset_nodes_session_id ON asset_nodes(session_id);
CREATE INDEX IF NOT EXISTS idx_asset_nodes_file_id ON asset_nodes(file_id);
CREATE INDEX IF NOT EXISTS idx_asset_nodes_url ON asset_nodes(url);
CREATE INDEX IF NOT EXISTS idx_asset_nodes_discovery_depth ON asset_nodes(discovery_depth);

CREATE INDEX IF NOT EXISTS idx_asset_edges_session_id ON asset_edges(session_id);
CREATE INDEX IF NOT EXISTS idx_asset_edges_source_node_id ON asset_edges(source_node_id);
CREATE INDEX IF NOT EXISTS idx_asset_edges_target_node_id ON asset_edges(target_node_id);
CREATE INDEX IF NOT EXISTS idx_asset_edges_discovery_method ON asset_edges(discovery_method);