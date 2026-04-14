"""
Tests for B-027 - Unified Asset Graph for Discovery Provenance

Tests asset discovery tracking, graph construction, and provenance queries.
"""

import pytest
from unittest.mock import Mock
from datetime import datetime
import uuid

from app.services.asset_graph_service import AssetGraphService
from app.models.asset_graph import AssetNode, AssetEdge, DiscoveryMethod, AssetType


class TestAssetGraphService:
    """Test asset graph service functionality."""

    def setup_method(self):
        """Set up test fixtures."""
        self.mock_db = Mock()
        self.service = AssetGraphService(self.mock_db)
        self.session_id = str(uuid.uuid4())

    def test_create_root_node(self):
        """Test creating a root node for the asset graph."""
        entry_url = "https://wishandwash.co.il/app.js"
        
        # Mock database interactions
        self.mock_db.add = Mock()
        self.mock_db.flush = Mock()
        
        # Create root node
        root_node = self.service.create_root_node(
            self.session_id, entry_url, AssetType.SCRIPT
        )
        
        assert root_node.session_id == self.session_id
        assert root_node.url == entry_url
        assert root_node.asset_type == AssetType.SCRIPT.value
        assert root_node.discovery_depth == 0
        assert root_node.asset_metadata["is_root"] == True
        assert root_node.fetch_attempted == "pending"
        
        self.mock_db.add.assert_called_once_with(root_node)
        self.mock_db.flush.assert_called_once()

    def test_add_discovered_asset_new(self):
        """Test adding a newly discovered asset."""
        # Create source node
        source_node = AssetNode(
            id=uuid.uuid4(),
            session_id=self.session_id,
            url="https://wishandwash.co.il/app.js",
            asset_type=AssetType.SCRIPT.value,
            discovery_depth=0
        )
        
        target_url = "https://wishandwash.co.il/config.json"
        
        # Mock database queries
        self.mock_db.query().filter().first.return_value = None  # Asset doesn't exist
        self.mock_db.add = Mock()
        self.mock_db.flush = Mock()
        
        # Add discovered asset
        target_node = self.service.add_discovered_asset(
            self.session_id,
            source_node, 
            target_url,
            AssetType.OTHER,
            DiscoveryMethod.IMPORT_STATEMENT,
            {"line": 5, "context": "require('./config.json')"}
        )
        
        assert target_node.url == target_url
        assert target_node.asset_type == AssetType.OTHER.value
        assert target_node.discovery_depth == 1  # source_depth + 1
        assert target_node.asset_metadata["line"] == 5
        
        # Should add both node and edge
        assert self.mock_db.add.call_count == 2

    def test_add_discovered_asset_existing(self):
        """Test adding an asset that already exists (creates new edge only)."""
        # Create source node
        source_node = AssetNode(
            id=uuid.uuid4(),
            session_id=self.session_id,
            url="https://wishandwash.co.il/app.js", 
            asset_type=AssetType.SCRIPT.value,
            discovery_depth=0
        )
        
        # Create existing target node
        existing_node = AssetNode(
            id=uuid.uuid4(),
            session_id=self.session_id,
            url="https://wishandwash.co.il/config.json",
            asset_type=AssetType.OTHER.value,
            discovery_depth=1
        )
        
        # Mock database queries
        self.mock_db.query().filter().first.return_value = existing_node
        self.mock_db.add = Mock()
        
        # Add discovered asset
        target_node = self.service.add_discovered_asset(
            self.session_id,
            source_node,
            "https://wishandwash.co.il/config.json",
            AssetType.OTHER,
            DiscoveryMethod.FETCH_CALL
        )
        
        assert target_node == existing_node
        # Should only add edge, not new node
        self.mock_db.add.assert_called_once()

    def test_update_node_status(self):
        """Test updating node status information."""
        node_id = str(uuid.uuid4())
        mock_node = AssetNode(
            id=node_id,
            fetch_attempted="pending",
            processed="pending",
            asset_metadata={"existing": "data"}
        )
        
        self.mock_db.query().filter().first.return_value = mock_node
        
        # Update node status
        result = self.service.update_node_status(
            node_id,
            fetch_status="success",
            processing_status="failed",
            processing_error="Parse error",
            content_hash="abc123",
            metadata_update={"new": "info"}
        )
        
        assert result == True
        assert mock_node.fetch_attempted == "success"
        assert mock_node.processed == "failed"
        assert mock_node.processing_error == "Parse error"
        assert mock_node.content_hash == "abc123"
        assert mock_node.asset_metadata["existing"] == "data"  # Preserved
        assert mock_node.asset_metadata["new"] == "info"  # Added

    def test_update_node_status_not_found(self):
        """Test updating status for non-existent node."""
        self.mock_db.query().filter().first.return_value = None
        
        result = self.service.update_node_status("nonexistent", fetch_status="success")
        
        assert result == False

    def test_link_node_to_file(self):
        """Test linking an asset node to a stored file."""
        node_id = str(uuid.uuid4())
        file_id = str(uuid.uuid4())
        
        mock_node = AssetNode(id=node_id, file_id=None)
        self.mock_db.query().filter().first.return_value = mock_node
        
        result = self.service.link_node_to_file(node_id, file_id)
        
        assert result == True
        assert mock_node.file_id == file_id

    def test_get_session_graph(self):
        """Test retrieving complete session graph."""
        # Mock nodes and edges
        mock_nodes = [
            AssetNode(
                id=uuid.uuid4(),
                session_id=self.session_id,
                url="https://wishandwash.co.il/app.js",
                asset_type=AssetType.SCRIPT.value,
                discovery_depth=0,
                discovered_at=datetime.utcnow(),
                fetch_attempted="success",
                processed="success"
            ),
            AssetNode(
                id=uuid.uuid4(),
                session_id=self.session_id,
                url="https://wishandwash.co.il/config.json",
                asset_type=AssetType.OTHER.value,
                discovery_depth=1,
                discovered_at=datetime.utcnow(),
                fetch_attempted="success", 
                processed="success"
            )
        ]
        
        mock_edges = [
            AssetEdge(
                id=uuid.uuid4(),
                session_id=self.session_id,
                source_node_id=mock_nodes[0].id,
                target_node_id=mock_nodes[1].id,
                discovery_method=DiscoveryMethod.IMPORT_STATEMENT.value,
                discovered_at=datetime.utcnow()
            )
        ]
        
        self.mock_db.query().filter().all.side_effect = [mock_nodes, mock_edges]
        
        # Get session graph
        graph = self.service.get_session_graph(self.session_id)
        
        assert graph["session_id"] == self.session_id
        assert len(graph["nodes"]) == 2
        assert len(graph["edges"]) == 1
        assert graph["stats"]["total_nodes"] == 2
        assert graph["stats"]["total_edges"] == 1
        assert graph["stats"]["nodes_by_type"][AssetType.SCRIPT.value] == 1
        assert graph["stats"]["nodes_by_type"][AssetType.OTHER.value] == 1
        assert graph["stats"]["edges_by_method"][DiscoveryMethod.IMPORT_STATEMENT.value] == 1

    def test_get_node_ancestry(self):
        """Test getting ancestry path for a node."""
        # Create mock nodes representing a discovery chain
        root_id = uuid.uuid4()
        parent_id = uuid.uuid4()
        target_id = uuid.uuid4()
        
        root_node = AssetNode(
            id=root_id,
            url="https://wishandwash.co.il/index.html",
            asset_type=AssetType.PAGE.value,
            discovery_depth=0
        )
        
        parent_node = AssetNode(
            id=parent_id,
            url="https://wishandwash.co.il/app.js",
            asset_type=AssetType.SCRIPT.value,
            discovery_depth=1
        )
        
        target_node = AssetNode(
            id=target_id,
            url="https://wishandwash.co.il/config.json",
            asset_type=AssetType.OTHER.value,
            discovery_depth=2
        )
        
        # Mock edges
        parent_edge = AssetEdge(
            source_node_id=root_id,
            target_node_id=parent_id,
            source_node=root_node
        )
        
        target_edge = AssetEdge(
            source_node_id=parent_id,
            target_node_id=target_id,
            source_node=parent_node
        )
        
        # Mock database calls
        self.mock_db.query().filter().first.side_effect = [
            target_node,   # Initial target lookup
            target_edge,   # Edge to parent
            parent_edge,   # Edge to root  
            None           # No more parents (root reached)
        ]
        
        # Get ancestry
        ancestry = self.service.get_node_ancestry(str(target_id))
        
        assert len(ancestry) == 3
        assert ancestry[0] == root_node     # Root first
        assert ancestry[1] == parent_node   # Parent second
        assert ancestry[2] == target_node   # Target last

    def test_find_discovery_gaps(self):
        """Test finding discovery gaps (failed fetches)."""
        failed_node = AssetNode(
            id=uuid.uuid4(),
            session_id=self.session_id,
            url="https://wishandwash.co.il/missing.js",
            asset_type=AssetType.SCRIPT.value,
            discovery_depth=2,
            fetch_attempted="failed",
            fetch_error="404 Not Found"
        )
        
        self.mock_db.query().filter().all.return_value = [failed_node]
        
        # Mock ancestry method
        self.service.get_node_ancestry = Mock(return_value=[
            AssetNode(url="https://wishandwash.co.il/index.html", asset_type=AssetType.PAGE.value),
            AssetNode(url="https://wishandwash.co.il/app.js", asset_type=AssetType.SCRIPT.value),
            failed_node
        ])
        
        # Find gaps
        gaps = self.service.find_discovery_gaps(self.session_id)
        
        assert len(gaps) == 1
        gap = gaps[0]
        assert gap["url"] == "https://wishandwash.co.il/missing.js"
        assert gap["asset_type"] == AssetType.SCRIPT.value
        assert gap["fetch_error"] == "404 Not Found"
        assert gap["discovery_depth"] == 2
        assert gap["potential_impact"] == "high"  # Scripts are high impact
        assert len(gap["discovery_path"]) == 3

    def test_serialize_node(self):
        """Test node serialization for API responses."""
        node = AssetNode(
            id=uuid.uuid4(),
            url="https://wishandwash.co.il/test.js",
            asset_type=AssetType.SCRIPT.value,
            discovery_depth=1,
            discovered_at=datetime(2026, 2, 11, 10, 30, 0),
            fetch_attempted="success",
            processed="failed",
            processing_error="Syntax error",
            content_hash="abc123",
            file_id=uuid.uuid4(),
            asset_metadata={"test": "data"}
        )
        
        serialized = self.service._serialize_node(node)
        
        assert serialized["id"] == str(node.id)
        assert serialized["url"] == "https://wishandwash.co.il/test.js"
        assert serialized["asset_type"] == AssetType.SCRIPT.value
        assert serialized["discovery_depth"] == 1
        assert serialized["discovered_at"] == "2026-02-11T10:30:00"
        assert serialized["fetch_status"] == "success"
        assert serialized["processing_status"] == "failed"
        assert serialized["processing_error"] == "Syntax error"
        assert serialized["content_hash"] == "abc123"
        assert serialized["file_id"] == str(node.file_id)
        assert serialized["metadata"]["test"] == "data"

    def test_serialize_edge(self):
        """Test edge serialization for API responses."""
        source_id = uuid.uuid4()
        target_id = uuid.uuid4()
        
        edge = AssetEdge(
            id=uuid.uuid4(),
            source_node_id=source_id,
            target_node_id=target_id,
            discovery_method=DiscoveryMethod.SCRIPT_TAG.value,
            discovered_at=datetime(2026, 2, 11, 10, 30, 0),
            referer="https://wishandwash.co.il/index.html",
            initiator="html_parser",
            context_metadata={"tag": "script"}
        )
        
        serialized = self.service._serialize_edge(edge)
        
        assert serialized["id"] == str(edge.id)
        assert serialized["source_node_id"] == str(source_id)
        assert serialized["target_node_id"] == str(target_id)
        assert serialized["discovery_method"] == DiscoveryMethod.SCRIPT_TAG.value
        assert serialized["discovered_at"] == "2026-02-11T10:30:00"
        assert serialized["referer"] == "https://wishandwash.co.il/index.html"
        assert serialized["initiator"] == "html_parser"
        assert serialized["context"]["tag"] == "script"

    def test_assess_gap_impact(self):
        """Test gap impact assessment logic."""
        # High impact: script
        script_node = AssetNode(asset_type=AssetType.SCRIPT.value)
        assert self.service._assess_gap_impact(script_node) == "high"
        
        # High impact: sourcemap
        sourcemap_node = AssetNode(asset_type=AssetType.SOURCEMAP.value)
        assert self.service._assess_gap_impact(sourcemap_node) == "high"
        
        # Medium impact: chunk
        chunk_node = AssetNode(asset_type=AssetType.CHUNK.value)
        assert self.service._assess_gap_impact(chunk_node) == "medium"
        
        # Low impact: other
        other_node = AssetNode(asset_type=AssetType.OTHER.value)
        assert self.service._assess_gap_impact(other_node) == "low"


class TestDiscoveryMethodEnum:
    """Test DiscoveryMethod enumeration."""
    
    def test_enum_values(self):
        """Test that all expected discovery methods are defined."""
        expected_methods = [
            "initial_page", "script_tag", "import_statement", "sourcemap_reference",
            "sourcemap_header", "chunk_reference", "fetch_call", "manual_upload",
            "reconstructed", "crawler_discovered"
        ]
        
        for method in expected_methods:
            assert hasattr(DiscoveryMethod, method.upper())
            assert getattr(DiscoveryMethod, method.upper()).value == method


class TestAssetTypeEnum:
    """Test AssetType enumeration."""
    
    def test_enum_values(self):
        """Test that all expected asset types are defined."""
        expected_types = [
            "page", "script", "stylesheet", "sourcemap", "chunk",
            "reconstructed_source", "other"
        ]
        
        for asset_type in expected_types:
            assert hasattr(AssetType, asset_type.upper())
            assert getattr(AssetType, asset_type.upper()).value == asset_type
