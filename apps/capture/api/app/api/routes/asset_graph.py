"""
Asset Graph API endpoints.
Implements B-027 - Unified Asset Graph for Discovery Provenance.
"""

import logging
from typing import Dict, Any, List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ...db import get_db
from ...services.asset_graph_service import AssetGraphService
from ...models.session import Session as SessionModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["asset_graph"])


@router.get("/sessions/{session_id}/asset-graph")
async def get_session_asset_graph(
    session_id: str,
    db: Session = Depends(get_db)
) -> Dict[str, Any]:
    """
    Get the complete asset discovery graph for a session.
    
    Returns nodes and edges with discovery provenance metadata.
    """
    # Validate session exists
    session = db.query(SessionModel).filter(SessionModel.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    
    try:
        graph_service = AssetGraphService(db)
        graph_data = graph_service.get_session_graph(session_id)
        
        logger.info(f"Retrieved asset graph for session {session_id}: {graph_data['stats']['total_nodes']} nodes, {graph_data['stats']['total_edges']} edges")
        
        return {
            "success": True,
            "graph": graph_data
        }
        
    except Exception as e:
        logger.error(f"Failed to get asset graph for session {session_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to retrieve asset graph: {str(e)}")


@router.get("/sessions/{session_id}/asset-graph/node/{node_id}/ancestry")
async def get_node_ancestry(
    session_id: str,
    node_id: str,
    db: Session = Depends(get_db)
) -> Dict[str, Any]:
    """
    Get the ancestry path from root to specified node.
    
    Shows the discovery chain that led to this asset.
    """
    # Validate session exists
    session = db.query(SessionModel).filter(SessionModel.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    
    try:
        graph_service = AssetGraphService(db)
        ancestry = graph_service.get_node_ancestry(node_id)
        
        if not ancestry:
            raise HTTPException(status_code=404, detail="Node not found or no ancestry available")
        
        return {
            "success": True,
            "node_id": node_id,
            "ancestry": [graph_service._serialize_node(node) for node in ancestry],
            "discovery_path": [
                {
                    "url": node.url,
                    "asset_type": node.asset_type,
                    "discovery_depth": node.discovery_depth
                }
                for node in ancestry
            ]
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get ancestry for node {node_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to retrieve node ancestry: {str(e)}")


@router.get("/sessions/{session_id}/asset-graph/node/{node_id}/descendants")
async def get_node_descendants(
    session_id: str,
    node_id: str,
    max_depth: int = Query(default=10, ge=1, le=20, description="Maximum traversal depth"),
    db: Session = Depends(get_db)
) -> Dict[str, Any]:
    """
    Get all descendant nodes of specified node.
    
    Shows what assets were discovered from this node.
    """
    # Validate session exists
    session = db.query(SessionModel).filter(SessionModel.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    
    try:
        graph_service = AssetGraphService(db)
        descendants = graph_service.get_node_descendants(node_id, max_depth)
        
        return {
            "success": True,
            "node_id": node_id,
            "max_depth": max_depth,
            "descendants": [graph_service._serialize_node(node) for node in descendants],
            "count": len(descendants)
        }
        
    except Exception as e:
        logger.error(f"Failed to get descendants for node {node_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to retrieve node descendants: {str(e)}")


@router.get("/sessions/{session_id}/asset-graph/gaps")
async def get_discovery_gaps(
    session_id: str,
    db: Session = Depends(get_db)
) -> Dict[str, Any]:
    """
    Identify potential discovery gaps in the asset graph.
    
    Returns assets that were referenced but could not be fetched or processed.
    """
    # Validate session exists
    session = db.query(SessionModel).filter(SessionModel.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    
    try:
        graph_service = AssetGraphService(db)
        gaps = graph_service.find_discovery_gaps(session_id)
        
        # Categorize gaps by impact
        high_impact = [gap for gap in gaps if gap["potential_impact"] == "high"]
        medium_impact = [gap for gap in gaps if gap["potential_impact"] == "medium"] 
        low_impact = [gap for gap in gaps if gap["potential_impact"] == "low"]
        
        logger.info(f"Found {len(gaps)} discovery gaps for session {session_id}: {len(high_impact)} high impact, {len(medium_impact)} medium impact, {len(low_impact)} low impact")
        
        return {
            "success": True,
            "session_id": session_id,
            "gaps": gaps,
            "summary": {
                "total_gaps": len(gaps),
                "high_impact": len(high_impact),
                "medium_impact": len(medium_impact),
                "low_impact": len(low_impact)
            },
            "recommendations": _generate_gap_recommendations(gaps)
        }
        
    except Exception as e:
        logger.error(f"Failed to find discovery gaps for session {session_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to analyze discovery gaps: {str(e)}")


@router.get("/sessions/{session_id}/asset-graph/stats")
async def get_asset_graph_stats(
    session_id: str,
    db: Session = Depends(get_db)
) -> Dict[str, Any]:
    """
    Get summary statistics for the session's asset graph.
    """
    # Validate session exists
    session = db.query(SessionModel).filter(SessionModel.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    
    try:
        graph_service = AssetGraphService(db)
        graph_data = graph_service.get_session_graph(session_id)
        
        stats = graph_data["stats"]
        
        # Calculate additional metrics
        nodes = graph_data["nodes"]
        edges = graph_data["edges"]
        
        fetch_success_rate = 0
        if nodes:
            successful_fetches = len([n for n in nodes if n["fetch_status"] == "success"])
            fetch_success_rate = (successful_fetches / len(nodes)) * 100
        
        processing_success_rate = 0
        if nodes:
            successful_processing = len([n for n in nodes if n["processing_status"] == "success"])
            processing_success_rate = (successful_processing / len(nodes)) * 100
        
        max_depth = max([n["discovery_depth"] for n in nodes], default=0)
        
        return {
            "success": True,
            "session_id": session_id,
            "stats": {
                **stats,
                "fetch_success_rate": round(fetch_success_rate, 1),
                "processing_success_rate": round(processing_success_rate, 1),
                "max_discovery_depth": max_depth,
                "average_depth": round(sum(n["discovery_depth"] for n in nodes) / len(nodes), 1) if nodes else 0
            }
        }
        
    except Exception as e:
        logger.error(f"Failed to get asset graph stats for session {session_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to retrieve asset graph statistics: {str(e)}")


def _generate_gap_recommendations(gaps: List[Dict[str, Any]]) -> List[str]:
    """Generate recommendations based on discovery gaps."""
    recommendations = []
    
    high_impact_gaps = [gap for gap in gaps if gap["potential_impact"] == "high"]
    
    if high_impact_gaps:
        script_gaps = [gap for gap in high_impact_gaps if gap["asset_type"] == "script"]
        sourcemap_gaps = [gap for gap in high_impact_gaps if gap["asset_type"] == "sourcemap"]
        
        if script_gaps:
            recommendations.append(f"Consider manual analysis of {len(script_gaps)} unreachable JavaScript files that may contain additional endpoints or secrets")
        
        if sourcemap_gaps:
            recommendations.append(f"Investigate {len(sourcemap_gaps)} failed sourcemap fetches to improve source reconstruction coverage")
    
    auth_related_gaps = [gap for gap in gaps if "auth" in gap.get("fetch_error", "").lower() or "403" in gap.get("fetch_error", "") or "401" in gap.get("fetch_error", "")]
    if auth_related_gaps:
        recommendations.append(f"Consider authenticated scanning for {len(auth_related_gaps)} assets blocked by authentication")
    
    if not recommendations:
        recommendations.append("No significant discovery gaps found - good coverage achieved")
    
    return recommendations