"""
Asset Graph models for tracking discovery provenance.
Implements B-027 - Unified Asset Graph for Discovery Provenance.
"""

import uuid
from datetime import datetime
from enum import Enum

from sqlalchemy import Column, DateTime, ForeignKey, String, Text, Integer, Index
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship

from ..db import Base


class DiscoveryMethod(str, Enum):
    """Enumeration of asset discovery methods."""
    INITIAL_PAGE = "initial_page"           # Starting page/entry point
    SCRIPT_TAG = "script_tag"              # Found via <script src="...">
    IMPORT_STATEMENT = "import_statement"   # Found via import/require in JS
    SOURCEMAP_REFERENCE = "sourcemap_reference"  # Found via sourceMappingURL
    SOURCEMAP_HEADER = "sourcemap_header"   # Found via SourceMap HTTP header
    CHUNK_REFERENCE = "chunk_reference"     # Found via webpack chunk reference
    FETCH_CALL = "fetch_call"              # Found via fetch()/axios() call
    MANUAL_UPLOAD = "manual_upload"        # Manually uploaded by user
    RECONSTRUCTED = "reconstructed"        # Reconstructed from sourcemap
    CRAWLER_DISCOVERED = "crawler_discovered"  # Found by automated crawler


class AssetType(str, Enum):
    """Enumeration of asset types in the discovery graph."""
    PAGE = "page"                   # HTML page (root node)
    SCRIPT = "script"              # JavaScript file
    STYLESHEET = "stylesheet"       # CSS file
    SOURCEMAP = "sourcemap"        # Source map file
    CHUNK = "chunk"                # Webpack/bundler chunk
    RECONSTRUCTED_SOURCE = "reconstructed_source"  # Source reconstructed from map
    OTHER = "other"                # Other asset type


class AssetNode(Base):
    """
    Represents a node in the asset discovery graph.
    Each node represents a discoverable asset (page, script, sourcemap, etc.)
    """
    __tablename__ = "asset_nodes"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id = Column(UUID(as_uuid=True), ForeignKey("sessions.id"), nullable=False)
    file_id = Column(UUID(as_uuid=True), ForeignKey("files.id"), nullable=True)  # Null for non-file assets
    
    # Asset identification
    url = Column(Text, nullable=False)
    asset_type = Column(String, nullable=False)  # AssetType enum value
    content_hash = Column(String, nullable=True)  # For content-based deduplication
    
    # Discovery metadata
    discovered_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    discovery_depth = Column(Integer, nullable=False, default=0)  # Depth from root (0 = entry point)
    
    # Asset metadata
    # NOTE: SQLAlchemy Declarative reserves the attribute name `metadata`,
    # so we map the DB column `metadata` to a safe Python attribute.
    asset_metadata = Column("metadata", JSONB, nullable=True)  # Additional asset-specific metadata
    
    # Status tracking
    fetch_attempted = Column(String, nullable=False, default="pending")  # pending, success, failed
    fetch_error = Column(Text, nullable=True)
    processed = Column(String, nullable=False, default="pending")  # pending, success, failed
    processing_error = Column(Text, nullable=True)
    
    # Relationships
    session = relationship("Session", back_populates="asset_nodes")
    file = relationship("File", back_populates="asset_node", uselist=False)
    
    # Graph relationships (self-referential through edges)
    outgoing_edges = relationship(
        "AssetEdge", 
        foreign_keys="AssetEdge.source_node_id",
        back_populates="source_node",
        cascade="all, delete-orphan"
    )
    incoming_edges = relationship(
        "AssetEdge",
        foreign_keys="AssetEdge.target_node_id", 
        back_populates="target_node",
        cascade="all, delete-orphan"
    )

    # Indexes for performance
    __table_args__ = (
        Index('idx_asset_nodes_session_id', 'session_id'),
        Index('idx_asset_nodes_file_id', 'file_id'),
        Index('idx_asset_nodes_url', 'url'),
        Index('idx_asset_nodes_discovery_depth', 'discovery_depth'),
    )

    def __repr__(self):
        return f"<AssetNode {self.asset_type}:{self.url[:50]}>"


class AssetEdge(Base):
    """
    Represents a directed edge in the asset discovery graph.
    Each edge represents a discovery relationship between two assets.
    """
    __tablename__ = "asset_edges"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id = Column(UUID(as_uuid=True), ForeignKey("sessions.id"), nullable=False)
    
    # Graph relationship
    source_node_id = Column(UUID(as_uuid=True), ForeignKey("asset_nodes.id"), nullable=False)
    target_node_id = Column(UUID(as_uuid=True), ForeignKey("asset_nodes.id"), nullable=False)
    
    # Discovery metadata
    discovery_method = Column(String, nullable=False)  # DiscoveryMethod enum value
    discovered_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    
    # Context information
    referer = Column(Text, nullable=True)  # HTTP referer if available
    initiator = Column(Text, nullable=True)  # What initiated the discovery (parser, crawler, etc.)
    context_metadata = Column(JSONB, nullable=True)  # Additional context (line number, etc.)
    
    # Relationships
    session = relationship("Session", back_populates="asset_edges")
    source_node = relationship(
        "AssetNode", 
        foreign_keys=[source_node_id],
        back_populates="outgoing_edges"
    )
    target_node = relationship(
        "AssetNode",
        foreign_keys=[target_node_id], 
        back_populates="incoming_edges"
    )

    # Indexes for performance
    __table_args__ = (
        Index('idx_asset_edges_session_id', 'session_id'),
        Index('idx_asset_edges_source_node_id', 'source_node_id'),
        Index('idx_asset_edges_target_node_id', 'target_node_id'),
        Index('idx_asset_edges_discovery_method', 'discovery_method'),
    )

    def __repr__(self):
        return f"<AssetEdge {self.source_node_id} --{self.discovery_method}--> {self.target_node_id}>"


