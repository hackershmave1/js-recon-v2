"""
Asset Graph Service for managing discovery provenance.
Implements B-027 - Unified Asset Graph for Discovery Provenance.
"""

import logging
from datetime import datetime
from typing import Dict, List, Any, Optional, Set
from urllib.parse import urljoin, urlparse

from sqlalchemy.orm import Session
from sqlalchemy import and_, or_
from sqlalchemy.exc import IntegrityError

from ..models.asset_graph import AssetNode, AssetEdge, DiscoveryMethod, AssetType
from ..models.session import Session as SessionModel
from ..models.file import File

logger = logging.getLogger(__name__)


class AssetGraphService:
    """Service for building and querying the asset discovery graph."""
    
    def __init__(self, db_session: Session):
        self.db = db_session
    
    def create_root_node(self, session_id: str, entry_url: str, asset_type: AssetType = AssetType.PAGE) -> AssetNode:
        """
        Create the root node for an asset discovery session.
        
        Args:
            session_id: Session UUID
            entry_url: Starting URL (page or script)
            asset_type: Type of the root asset
            
        Returns:
            Created AssetNode
        """
        logger.info(f"Creating root node for session {session_id}: {entry_url}")
        
        root_node = AssetNode(
            session_id=session_id,
            url=entry_url,
            asset_type=asset_type.value,
            discovery_depth=0,
            discovered_at=datetime.utcnow(),
            fetch_attempted="pending",
            processed="pending",
            asset_metadata={"is_root": True, "entry_point": True}
        )
        
        self.db.add(root_node)
        self.db.flush()  # Get the ID
        
        logger.info(f"Created root node {root_node.id} for {entry_url}")
        return root_node
    
    def add_discovered_asset(
        self, 
        session_id: str,
        source_node: AssetNode,
        target_url: str,
        asset_type: AssetType,
        discovery_method: DiscoveryMethod,
        context: Optional[Dict[str, Any]] = None,
        file_id: Optional[str] = None
    ) -> AssetNode:
        """
        Add a newly discovered asset to the graph.
        
        Args:
            session_id: Session UUID
            source_node: Node that discovered this asset
            target_url: URL of discovered asset
            asset_type: Type of discovered asset
            discovery_method: How it was discovered
            context: Additional context metadata
            file_id: Associated file ID if asset was stored
            
        Returns:
            Created or existing AssetNode
        """
        # Check if asset already exists in this session
        existing_node = self.db.query(AssetNode).filter(
            and_(
                AssetNode.session_id == session_id,
                AssetNode.url == target_url,
                AssetNode.asset_type == asset_type.value
            )
        ).first()
        
        if existing_node:
            logger.debug(f"Asset {target_url} already exists as node {existing_node.id}")
            target_node = existing_node
        else:
            # Create new node. Wrap the INSERT in a SAVEPOINT so that a concurrent
            # discoverer racing us on (session_id, url, asset_type) — both SELECT-miss
            # above, both INSERT — adopts the winner's node instead of silently
            # creating a duplicate (guarded by uq_asset_nodes_session_url_type). Inert
            # for the single-writer path; the surrounding transaction survives.
            target_node = AssetNode(
                session_id=session_id,
                file_id=file_id,
                url=target_url,
                asset_type=asset_type.value,
                discovery_depth=source_node.discovery_depth + 1,
                discovered_at=datetime.utcnow(),
                fetch_attempted="pending",
                processed="pending",
                asset_metadata=context or {}
            )

            try:
                with self.db.begin_nested():
                    self.db.add(target_node)
                    self.db.flush()  # Get the ID
                logger.info(f"Created new asset node {target_node.id} for {target_url}")
            except IntegrityError:
                # Lost the insert race: adopt the node the concurrent writer committed.
                target_node = self.db.query(AssetNode).filter(
                    and_(
                        AssetNode.session_id == session_id,
                        AssetNode.url == target_url,
                        AssetNode.asset_type == asset_type.value
                    )
                ).first()
                if target_node is None:
                    raise
                logger.debug(f"Adopted concurrently-created asset node {target_node.id} for {target_url}")
        
        # Create discovery edge (even if node existed, this might be a new discovery path)
        edge = AssetEdge(
            session_id=session_id,
            source_node_id=source_node.id,
            target_node_id=target_node.id,
            discovery_method=discovery_method.value,
            discovered_at=datetime.utcnow(),
            referer=source_node.url,
            initiator=f"{source_node.asset_type}_parser",
            context_metadata=context or {}
        )
        
        self.db.add(edge)
        logger.info(f"Created discovery edge: {source_node.id} --{discovery_method.value}--> {target_node.id}")
        
        return target_node
    
    def update_node_status(
        self,
        node_id: str,
        fetch_status: Optional[str] = None,
        fetch_error: Optional[str] = None,
        processing_status: Optional[str] = None,
        processing_error: Optional[str] = None,
        content_hash: Optional[str] = None,
        metadata_update: Optional[Dict[str, Any]] = None
    ) -> bool:
        """
        Update the status of an asset node.
        
        Args:
            node_id: Node UUID
            fetch_status: "success", "failed", or None to skip
            fetch_error: Error message if fetch failed
            processing_status: "success", "failed", or None to skip  
            processing_error: Error message if processing failed
            content_hash: Content hash for deduplication
            metadata_update: Additional metadata to merge
            
        Returns:
            True if node was updated, False if not found
        """
        node = self.db.query(AssetNode).filter(AssetNode.id == node_id).first()
        if not node:
            logger.warning(f"Asset node {node_id} not found for status update")
            return False
        
        updated = False
        
        if fetch_status:
            node.fetch_attempted = fetch_status
            updated = True
            
        if fetch_error:
            node.fetch_error = fetch_error
            updated = True
            
        if processing_status:
            node.processed = processing_status  
            updated = True
            
        if processing_error:
            node.processing_error = processing_error
            updated = True
            
        if content_hash:
            node.content_hash = content_hash
            updated = True
            
        if metadata_update:
            if node.asset_metadata:
                node.asset_metadata.update(metadata_update)
            else:
                node.asset_metadata = metadata_update
            updated = True
        
        if updated:
            logger.debug(f"Updated status for asset node {node_id}")
            
        return updated
    
    def link_node_to_file(self, node_id: str, file_id: str) -> bool:
        """
        Link an asset node to a stored file.
        
        Args:
            node_id: Asset node UUID
            file_id: File UUID
            
        Returns:
            True if linked successfully
        """
        node = self.db.query(AssetNode).filter(AssetNode.id == node_id).first()
        if not node:
            logger.warning(f"Asset node {node_id} not found for file linking")
            return False
            
        node.file_id = file_id
        logger.info(f"Linked asset node {node_id} to file {file_id}")
        return True
    
    def get_session_graph(self, session_id: str) -> Dict[str, Any]:
        """
        Get the complete asset graph for a session.
        
        Args:
            session_id: Session UUID
            
        Returns:
            Dictionary with nodes and edges
        """
        nodes = self.db.query(AssetNode).filter(AssetNode.session_id == session_id).all()
        edges = self.db.query(AssetEdge).filter(AssetEdge.session_id == session_id).all()
        
        return {
            "session_id": session_id,
            "nodes": [self._serialize_node(node) for node in nodes],
            "edges": [self._serialize_edge(edge) for edge in edges],
            "stats": {
                "total_nodes": len(nodes),
                "total_edges": len(edges),
                "nodes_by_type": self._count_nodes_by_type(nodes),
                "edges_by_method": self._count_edges_by_method(edges)
            }
        }
    
    def get_node_ancestry(self, node_id: str) -> List[AssetNode]:
        """
        Get the ancestry path from root to specified node.
        
        Args:
            node_id: Target node UUID
            
        Returns:
            List of nodes from root to target
        """
        ancestry = []
        current_node = self.db.query(AssetNode).filter(AssetNode.id == node_id).first()
        
        if not current_node:
            return ancestry
            
        # Build ancestry by following incoming edges back to root
        visited = set()
        while current_node and current_node.id not in visited:
            visited.add(current_node.id)
            ancestry.insert(0, current_node)  # Insert at beginning for correct order
            
            # Find parent (node with edge pointing to current_node)
            parent_edge = self.db.query(AssetEdge).filter(
                AssetEdge.target_node_id == current_node.id
            ).first()
            
            if parent_edge:
                current_node = parent_edge.source_node
            else:
                break  # Reached root or orphaned node
                
        return ancestry
    
    def get_node_descendants(self, node_id: str, max_depth: int = 10) -> List[AssetNode]:
        """
        Get all descendant nodes of specified node.
        
        Args:
            node_id: Source node UUID
            max_depth: Maximum traversal depth
            
        Returns:
            List of descendant nodes
        """
        descendants = []
        visited = set()
        queue = [(node_id, 0)]  # (node_id, depth)
        
        while queue:
            current_id, depth = queue.pop(0)
            
            if current_id in visited or depth >= max_depth:
                continue
                
            visited.add(current_id)
            
            # Get children
            child_edges = self.db.query(AssetEdge).filter(
                AssetEdge.source_node_id == current_id
            ).all()
            
            for edge in child_edges:
                child_node = edge.target_node
                if child_node.id not in visited:
                    descendants.append(child_node)
                    queue.append((child_node.id, depth + 1))
                    
        return descendants
    
    def find_discovery_gaps(self, session_id: str) -> List[Dict[str, Any]]:
        """
        Find potential discovery gaps (referenced but not fetched assets).
        
        Args:
            session_id: Session UUID
            
        Returns:
            List of gap analysis results
        """
        gaps = []
        
        # Find nodes that were discovered but fetch failed
        failed_nodes = self.db.query(AssetNode).filter(
            and_(
                AssetNode.session_id == session_id,
                AssetNode.fetch_attempted == "failed"
            )
        ).all()
        
        for node in failed_nodes:
            # Get discovery path to this failed node
            ancestry = self.get_node_ancestry(str(node.id))
            
            gaps.append({
                "node_id": str(node.id),
                "url": node.url,
                "asset_type": node.asset_type,
                "fetch_error": node.fetch_error,
                "discovery_depth": node.discovery_depth,
                "discovery_path": [{"url": n.url, "type": n.asset_type} for n in ancestry],
                "potential_impact": self._assess_gap_impact(node)
            })
            
        return gaps
    
    def _serialize_node(self, node: AssetNode) -> Dict[str, Any]:
        """Serialize asset node for API response."""
        return {
            "id": str(node.id),
            "url": node.url,
            "asset_type": node.asset_type,
            "discovery_depth": node.discovery_depth,
            "discovered_at": node.discovered_at.isoformat() if node.discovered_at else None,
            "fetch_status": node.fetch_attempted,
            "fetch_error": node.fetch_error,
            "processing_status": node.processed,
            "processing_error": node.processing_error,
            "content_hash": node.content_hash,
            "file_id": str(node.file_id) if node.file_id else None,
            "metadata": node.asset_metadata or {}
        }
    
    def _serialize_edge(self, edge: AssetEdge) -> Dict[str, Any]:
        """Serialize asset edge for API response."""
        return {
            "id": str(edge.id),
            "source_node_id": str(edge.source_node_id),
            "target_node_id": str(edge.target_node_id),
            "discovery_method": edge.discovery_method,
            "discovered_at": edge.discovered_at.isoformat() if edge.discovered_at else None,
            "referer": edge.referer,
            "initiator": edge.initiator,
            "context": edge.context_metadata or {}
        }
    
    def _count_nodes_by_type(self, nodes: List[AssetNode]) -> Dict[str, int]:
        """Count nodes by asset type."""
        counts = {}
        for node in nodes:
            counts[node.asset_type] = counts.get(node.asset_type, 0) + 1
        return counts
    
    def _count_edges_by_method(self, edges: List[AssetEdge]) -> Dict[str, int]:
        """Count edges by discovery method."""
        counts = {}
        for edge in edges:
            counts[edge.discovery_method] = counts.get(edge.discovery_method, 0) + 1
        return counts
    
    def _assess_gap_impact(self, node: AssetNode) -> str:
        """Assess the potential impact of a discovery gap."""
        if node.asset_type == AssetType.SOURCEMAP.value:
            return "high"  # Missing sourcemaps significantly impact analysis
        elif node.asset_type == AssetType.SCRIPT.value:
            return "high"  # Missing JS files are critical
        elif node.asset_type == AssetType.CHUNK.value:
            return "medium"  # Chunks might contain additional endpoints/secrets
        else:
            return "low"
