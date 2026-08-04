from datetime import datetime
from typing import Dict, Any, List
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ...db import get_db
from ...models import File as DbFile, Session as DbSession
from ...services.comprehensive_extractor import ComprehensiveExtractor
from ...services.security_utils import SecurityValidator
from ...services.http_fetcher import robust_fetcher

logger = logging.getLogger(__name__)

router = APIRouter()

class ComprehensiveAnalysisRequest(BaseModel):
    """Request model for comprehensive analysis"""
    content: str = Field(..., description="JavaScript content to analyze")
    url: str = Field(..., description="URL of the JavaScript file")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Additional metadata")
    options: Dict[str, Any] = Field(default_factory=dict, description="Analysis options")

class QuickAnalysisResponse(BaseModel):
    """Response model for quick analysis"""
    success: bool
    analysis: Dict[str, Any]
    processing_time_ms: int
    extractors_used: List[str]
    
class SessionAnalysisResponse(BaseModel):
    """Response model for session analysis"""
    session_id: str
    total_files: int
    analysis: Dict[str, Any]
    stats: Dict[str, Any]


class URLFetchAnalysisRequest(BaseModel):
    """Request model for URL-only analysis."""
    url: str = Field(..., description="URL of the JavaScript file to fetch and analyze")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Additional metadata")
    options: Dict[str, Any] = Field(default_factory=dict, description="Analysis options")
    analysis_type: str = Field(default="comprehensive", description="comprehensive or jsluice")
    timeout_seconds: int = Field(default=30, ge=1, le=120, description="HTTP fetch timeout in seconds")

@router.post("/api/analyze-comprehensive")
async def analyze_comprehensive(
    request: ComprehensiveAnalysisRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db)
) -> QuickAnalysisResponse:
    """
    Perform comprehensive JavaScript analysis using all available extractors.
    This combines jsluice, sourcemapper, and custom extraction logic.
    """
    start_time = datetime.utcnow()
    
    try:
        extractor = ComprehensiveExtractor()
        
        # Prepare metadata
        metadata = {
            'url': request.url,
            'contentType': 'application/javascript',
            'analysisTimestamp': start_time.isoformat(),
            **request.metadata
        }
        
        # Perform comprehensive analysis
        results = extractor.extract_all(request.content, metadata, options=request.options)
        
        processing_time = int((datetime.utcnow() - start_time).total_seconds() * 1000)
        
        return QuickAnalysisResponse(
            success=True,
            analysis=results,
            processing_time_ms=processing_time,
            extractors_used=results.get('extractors_used', [])
        )
        
    except Exception as e:
        logger.error(f"Comprehensive analysis failed: {e}")
        processing_time = int((datetime.utcnow() - start_time).total_seconds() * 1000)
        
        raise HTTPException(
            status_code=500,
            detail={
                "error": "Analysis failed",
                "message": str(e),
                "processing_time_ms": processing_time
            }
        )

@router.post("/api/analyze-jsluice")
async def analyze_jsluice(request: ComprehensiveAnalysisRequest) -> Dict[str, Any]:
    """
    Analyze JavaScript using only jsluice for URL and secret extraction.
    Faster than comprehensive analysis.
    """
    start_time = datetime.utcnow()
    
    try:
        extractor = ComprehensiveExtractor()
        
        if not extractor.jsluice:
            raise HTTPException(
                status_code=503, 
                detail="jsluice extractor not available"
            )
        
        # Extract URLs and secrets using jsluice
        urls = extractor.jsluice.extract_urls(
            request.content,
            request.url,
            resolve_urls=request.options.get("resolve_urls", True),
        )
        secrets = extractor.jsluice.extract_secrets(request.content)
        
        processing_time = int((datetime.utcnow() - start_time).total_seconds() * 1000)
        
        return {
            "success": True,
            "extractor": "jsluice",
            "analysis": {
                "urls": urls,
                "secrets": secrets
            },
            "stats": {
                "urls_found": len(urls),
                "secrets_found": len(secrets),
                "processing_time_ms": processing_time
            },
            "metadata": {
                "url": request.url,
                "content_size": len(request.content),
                "timestamp": start_time.isoformat()
            }
        }
        
    except Exception as e:
        logger.error(f"jsluice analysis failed: {e}")
        raise HTTPException(status_code=500, detail=f"jsluice analysis failed: {str(e)}")


@router.post("/api/analyze-by-url")
async def analyze_by_url(request: URLFetchAnalysisRequest) -> Dict[str, Any]:
    """
    Fetch JavaScript by URL server-side, then run analysis.
    Supports comprehensive and jsluice-only modes.
    """
    start_time = datetime.utcnow()

    try:
        validated_url = SecurityValidator.validate_url(request.url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    if request.analysis_type not in {"comprehensive", "jsluice"}:
        raise HTTPException(status_code=400, detail="analysis_type must be 'comprehensive' or 'jsluice'")

    fetched = await fetch_javascript_from_url(validated_url, request.timeout_seconds)
    content = fetched["content"]
    final_url = fetched["final_url"]

    metadata = {
        "url": final_url,
        "fetchedUrl": validated_url,
        "contentType": fetched.get("content_type") or "application/javascript",
        "contentLength": fetched.get("content_length", len(content.encode("utf-8"))),
        "analysisTimestamp": start_time.isoformat(),
        "source": "dashboard_url_fetch",
        **request.metadata,
    }
    extractor = ComprehensiveExtractor()

    if request.analysis_type == "jsluice":
        if not extractor.jsluice:
            raise HTTPException(status_code=503, detail="jsluice extractor not available")

        try:
            urls = extractor.jsluice.extract_urls(
                content,
                final_url,
                resolve_urls=request.options.get("resolve_urls", True),
            )
            secrets = extractor.jsluice.extract_secrets(content)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"jsluice analysis failed: {exc}")

        processing_time = int((datetime.utcnow() - start_time).total_seconds() * 1000)
        return {
            "success": True,
            "analysis": {
                "urls": urls,
                "secrets": secrets,
            },
            "processing_time_ms": processing_time,
            "extractors_used": ["jsluice_urls", "jsluice_secrets"],
            "metadata": metadata,
        }

    try:
        results = extractor.extract_all(content, metadata, options=request.options)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Analysis failed: {exc}")

    processing_time = int((datetime.utcnow() - start_time).total_seconds() * 1000)
    return QuickAnalysisResponse(
        success=True,
        analysis=results,
        processing_time_ms=processing_time,
        extractors_used=results.get("extractors_used", []),
    ).model_dump()

@router.post("/api/process-sourcemap")
async def process_sourcemap(
    request: Dict[str, Any],
    background_tasks: BackgroundTasks
) -> Dict[str, Any]:
    """
    Process source map to reconstruct original files.
    """
    start_time = datetime.utcnow()
    
    try:
        extractor = ComprehensiveExtractor()
        
        if not extractor.sourcemapper:
            raise HTTPException(
                status_code=503,
                detail="sourcemapper not available"
            )
        
        js_url = request.get('js_url')
        sourcemap_url = request.get('sourcemap_url')
        sourcemap_content = request.get('sourcemap_content')
        
        if not js_url and not sourcemap_url:
            raise HTTPException(
                status_code=400,
                detail="Either js_url or sourcemap_url is required"
            )
        
        # Process source map
        if sourcemap_content:
            result = await extractor.sourcemapper.process_sourcemap_from_content(
                sourcemap_content
            )
        else:
            result = await extractor.sourcemapper.process_sourcemap_from_url(
                js_url,
                sourcemap_url,
                request.get('headers', {})
            )
        
        processing_time = int((datetime.utcnow() - start_time).total_seconds() * 1000)
        result['processing_time_ms'] = processing_time
        
        # If successful, optionally analyze reconstructed files in background
        if result['success'] and request.get('analyze_reconstructed', False):
            background_tasks.add_task(
                analyze_reconstructed_files,
                result['files']
            )
        
        return result
        
    except Exception as e:
        logger.error(f"Source map processing failed: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Source map processing failed: {str(e)}"
        )

@router.get("/api/sessions/{session_id}/comprehensive-analysis")
async def get_session_comprehensive_analysis(
    session_id: str,
    db: Session = Depends(get_db)
) -> SessionAnalysisResponse:
    """
    Get comprehensive analysis results for all files in a session.
    This combines results from all extractors and provides session-level insights.
    """
    try:
        # Get session
        session = db.query(DbSession).filter(DbSession.id == session_id).first()
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        
        # Get all files in session
        files = db.query(DbFile).filter(DbFile.session_id == session_id).all()
        
        if not files:
            raise HTTPException(status_code=404, detail="No files found in session")
        
        # Aggregate analysis results
        session_analysis = {
            "endpoints": [],
            "secrets": [],
            "dependencies": [],
            "sourcemap_results": [],
            "reconstructed_files": [],
            "security_patterns": []
        }
        
        session_stats = {
            "total_files": len(files),
            "files_with_analysis": 0,
            "files_with_sourcemaps": 0,
            "total_endpoints": 0,
            "total_secrets": 0,
            "total_dependencies": 0,
            "extractors_used": set()
        }
        
        for file_record in files:
            # Load analysis results if available
            if file_record.analysis_result:
                try:
                    analysis_data = file_record.analysis_result.analysis
                    session_stats["files_with_analysis"] += 1
                    
                    # Aggregate endpoints
                    if 'endpoints' in analysis_data:
                        for endpoint in analysis_data['endpoints']:
                            if isinstance(endpoint, dict):
                                endpoint_copy = dict(endpoint)
                            else:
                                endpoint_copy = {"value": endpoint}
                            endpoint_copy.setdefault("source_file_url", file_record.url)
                            endpoint_copy.setdefault("source_file_id", str(file_record.id))
                            session_analysis["endpoints"].append(endpoint_copy)
                        session_stats["extractors_used"].add("comprehensive")
                    
                    # Aggregate secrets  
                    if 'secrets' in analysis_data:
                        for secret in analysis_data['secrets']:
                            if isinstance(secret, dict):
                                secret_copy = dict(secret)
                            else:
                                secret_copy = {"value": secret}
                            secret_copy.setdefault("source_file_url", file_record.url)
                            secret_copy.setdefault("source_file_id", str(file_record.id))
                            session_analysis["secrets"].append(secret_copy)
                    
                    # Aggregate dependencies from analysis
                    if 'dependencies' in analysis_data:
                        for dep in analysis_data['dependencies']:
                            session_analysis["dependencies"].append({
                                "file_id": str(file_record.id),
                                "file_url": file_record.url,
                                "dependency": dep
                            })
                    
                    # Track extractors used
                    if file_record.analysis_result.extractors_used:
                        for extractor in file_record.analysis_result.extractors_used:
                            session_stats["extractors_used"].add(extractor)
                    
                except Exception as e:
                    logger.warning(f"Failed to process analysis for file {file_record.id}: {e}")
            
            # Load sourcemap results
            if file_record.source_map:
                session_analysis["sourcemap_results"].append({
                    "file_id": str(file_record.id),
                    "file_url": file_record.url,
                    "result": {
                        "status": file_record.source_map.processing_status,
                        "detected_url": file_record.source_map.detected_map_url,
                        "error": file_record.source_map.processing_error,
                        "reconstructed_files_count": file_record.source_map.reconstructed_files_count,
                        "processed_at": file_record.source_map.processed_at.isoformat() if file_record.source_map.processed_at else None
                    }
                })
                
                if file_record.source_map.processing_status in {'completed', 'completed_limited'}:
                    session_stats["files_with_sourcemaps"] += 1
                    session_stats["extractors_used"].add("sourcemapper")
                    
                    # Note: Reconstructed files would need to be loaded separately if needed
                    # For now, we just track that sourcemap processing was successful
            
            # Load dependencies from relationship
            if file_record.dependencies:
                for dep in file_record.dependencies:
                    session_analysis["dependencies"].append({
                        "file_id": str(file_record.id),
                        "file_url": file_record.url,
                        "dependency": {
                            "url": dep.dep_url,
                            "resolved_url": dep.resolved_url,
                            "type": dep.dep_type
                        }
                    })
        
        # Update final stats
        session_stats["total_endpoints"] = len(session_analysis["endpoints"])
        session_stats["total_secrets"] = len(session_analysis["secrets"])
        session_stats["total_dependencies"] = len(session_analysis["dependencies"])
        session_stats["extractors_used"] = list(session_stats["extractors_used"])
        
        # Add session-level insights
        session_analysis["insights"] = generate_session_insights(session_analysis, session_stats)
        
        return SessionAnalysisResponse(
            session_id=session_id,
            total_files=len(files),
            analysis=session_analysis,
            stats=session_stats
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Session comprehensive analysis failed: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Session analysis failed: {str(e)}"
        )

@router.post("/api/batch-analyze")
async def batch_analyze(
    files: List[ComprehensiveAnalysisRequest],
    background_tasks: BackgroundTasks,
    options: Dict[str, Any] = {}
) -> Dict[str, Any]:
    """
    Analyze multiple JavaScript files in batch.
    Returns immediate results for quick analysis and queues comprehensive analysis.
    """
    start_time = datetime.utcnow()
    
    try:
        extractor = ComprehensiveExtractor()
        batch_results = []
        
        for i, file_request in enumerate(files):
            try:
                # Quick local analysis for immediate feedback
                metadata = {
                    'url': file_request.url,
                    'batch_index': i,
                    **file_request.metadata
                }
                
                # Basic analysis (fast)
                quick_result = extractor.extract_all(file_request.content, metadata)
                batch_results.append({
                    'index': i,
                    'url': file_request.url,
                    'success': True,
                    'quick_analysis': quick_result
                })
                
                # Schedule a best-effort background pass without requiring a
                # separate task queue runtime.
                background_tasks.add_task(
                    process_file_comprehensive_background,
                    file_request.content,
                    metadata,
                    options
                )
                
            except Exception as e:
                batch_results.append({
                    'index': i,
                    'url': file_request.url,
                    'success': False,
                    'error': str(e)
                })
        
        processing_time = int((datetime.utcnow() - start_time).total_seconds() * 1000)
        
        return {
            "success": True,
            "batch_size": len(files),
            "results": batch_results,
            "stats": {
                "successful": len([r for r in batch_results if r['success']]),
                "failed": len([r for r in batch_results if not r['success']]),
                "processing_time_ms": processing_time
            },
            "note": "Comprehensive analysis queued in background"
        }
        
    except Exception as e:
        logger.error(f"Batch analysis failed: {e}")
        raise HTTPException(status_code=500, detail=f"Batch analysis failed: {str(e)}")

# Helper functions


def process_file_comprehensive_background(content: str, metadata: Dict[str, Any], options: Dict[str, Any] | None = None) -> None:
    """Run a background comprehensive analysis pass for batch requests."""
    start_time = datetime.utcnow()
    try:
        extractor = ComprehensiveExtractor()
        result = extractor.extract_all(content, metadata, options=options or {})
        processing_time = int((datetime.utcnow() - start_time).total_seconds() * 1000)
        stats = result.get("stats", {})
        logger.info(
            "Background comprehensive analysis completed for %s: %s endpoints, %s secrets in %sms",
            metadata.get("url", "unknown"),
            stats.get("total_endpoints", 0),
            stats.get("total_secrets", 0),
            processing_time,
        )
    except Exception as exc:
        logger.error("Background comprehensive analysis failed for %s: %s", metadata.get("url", "unknown"), exc)

async def analyze_reconstructed_files(files: List[Dict[str, Any]]):
    """Background task to analyze reconstructed source files"""
    try:
        extractor = ComprehensiveExtractor()
        
        for file_info in files:
            if file_info['type'] == 'javascript':
                metadata = {
                    'url': f"reconstructed://{file_info['path']}",
                    'source': 'reconstructed',
                    'original_path': file_info['path']
                }
                
                # Analyze reconstructed file
                analysis = extractor.extract_all(file_info['content'], metadata)
                
                # Store or log results as needed
                logger.info(f"Analyzed reconstructed file {file_info['path']}: "
                          f"{len(analysis.get('analysis', {}).get('endpoints', []))} endpoints, "
                          f"{len(analysis.get('analysis', {}).get('secrets', []))} secrets")
                
    except Exception as e:
        logger.error(f"Reconstructed file analysis failed: {e}")

def generate_session_insights(analysis: Dict[str, Any], stats: Dict[str, Any]) -> Dict[str, Any]:
    """Generate session-level insights from aggregated analysis"""
    insights = {
        "security_score": "unknown",
        "risk_factors": [],
        "recommendations": [],
        "technology_stack": [],
        "coverage": {}
    }
    
    # Calculate security score based on findings
    risk_score = 0
    
    # Check for high-risk secrets
    high_risk_secrets = [s for s in analysis["secrets"] if s.get('confidence') == 'high']
    if high_risk_secrets:
        risk_score += len(high_risk_secrets) * 10
        insights["risk_factors"].append(f"Found {len(high_risk_secrets)} high-confidence secrets")
    
    # Check for external endpoints
    external_endpoints = [e for e in analysis["endpoints"] if e.get('url', '').startswith('http')]
    if len(external_endpoints) > 10:
        risk_score += 5
        insights["risk_factors"].append(f"Many external API calls ({len(external_endpoints)})")
    
    # Determine security score
    if risk_score == 0:
        insights["security_score"] = "low_risk"
    elif risk_score < 20:
        insights["security_score"] = "medium_risk"
    else:
        insights["security_score"] = "high_risk"
    
    # Coverage analysis
    insights["coverage"] = {
        "files_analyzed": f"{stats['files_with_analysis']}/{stats['total_files']}",
        "sourcemap_coverage": f"{stats['files_with_sourcemaps']}/{stats['total_files']}",
        "extractors_used": stats["extractors_used"]
    }
    
    # Basic recommendations
    if stats['files_with_sourcemaps'] < stats['total_files']:
        insights["recommendations"].append("Enable source maps for better analysis coverage")
    
    if 'jsluice' not in stats['extractors_used']:
        insights["recommendations"].append("Install jsluice for enhanced URL/secret extraction")
    
    return insights


async def fetch_javascript_from_url(url: str, timeout_seconds: int) -> Dict[str, Any]:
    """Fetch JavaScript content from URL using hardened HTTP fetcher."""
    headers = {
        "Accept": "application/javascript, text/javascript, application/x-javascript, text/plain, */*",
    }
    
    # Create fetcher with custom timeout
    fetcher = robust_fetcher.__class__(
        timeout_seconds=timeout_seconds,
        connect_timeout_seconds=min(timeout_seconds // 3, 10)
    )
    
    result = await fetcher.fetch_text(url, headers=headers, check_content_type=True)
    
    if not result.success:
        # Map fetch errors to HTTP exceptions
        if result.error_type == "fetch_timeout":
            raise HTTPException(status_code=504, detail="Timed out while fetching JavaScript URL")
        elif result.error_type == "connect_timeout":
            raise HTTPException(status_code=504, detail="Connection timed out while fetching JavaScript URL")
        elif result.error_type == "binary_content":
            raise HTTPException(status_code=400, detail="URL does not contain JavaScript content")
        elif result.error_type == "response_too_large":
            raise HTTPException(status_code=413, detail=f"JavaScript file too large: {result.error_message}")
        elif result.error_type in ("fetch_4xx", "fetch_5xx"):
            status_code = result.status_code or 502
            raise HTTPException(
                status_code=502 if status_code >= 500 else 404,
                detail=f"Failed to fetch URL (HTTP {status_code})"
            )
        elif result.error_type == "invalid_url":
            raise HTTPException(status_code=400, detail="Invalid URL format")
        else:
            # Network errors, unexpected errors, etc.
            raise HTTPException(status_code=502, detail=f"Failed to fetch URL: {result.error_message}")
    
    # Validate content using existing security validator
    try:
        SecurityValidator.validate_js_content(result.content)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Fetched content rejected: {exc}")

    return {
        "content": result.content,
        "final_url": result.final_url or url,
        "content_type": result.content_type,
        "content_length": result.content_length or len(result.content.encode('utf-8')),
    }
