import json
import logging
from typing import Dict, Any
from datetime import datetime

from .celery_app import celery_app
from ..services.async_utils import run_coroutine_sync
from ..services.comprehensive_extractor import ComprehensiveExtractor

logger = logging.getLogger(__name__)

@celery_app.task(name="process_file_comprehensive")
def process_file_comprehensive(content: str, metadata: Dict[str, Any], options: Dict[str, Any] = None) -> Dict[str, Any]:
    """
    Enhanced file processing task using comprehensive extractor.
    This combines jsluice, sourcemapper, and custom analysis.
    """
    start_time = datetime.utcnow()
    
    try:
        extractor = ComprehensiveExtractor()
        
        # Perform comprehensive analysis
        result = extractor.extract_all(content, metadata)
        
        # Add processing metadata
        result['task_metadata'] = {
            'task_name': 'process_file_comprehensive',
            'start_time': start_time.isoformat(),
            'end_time': datetime.utcnow().isoformat(),
            'processing_time_ms': int((datetime.utcnow() - start_time).total_seconds() * 1000),
            'options': options or {}
        }
        
        logger.info(f"Comprehensive processing completed for {metadata.get('url', 'unknown')}: "
                   f"{result['stats']['total_endpoints']} endpoints, "
                   f"{result['stats']['total_secrets']} secrets")
        
        return result
        
    except Exception as e:
        logger.error(f"Comprehensive processing failed: {e}")
        return {
            'success': False,
            'error': str(e),
            'task_metadata': {
                'task_name': 'process_file_comprehensive',
                'start_time': start_time.isoformat(),
                'end_time': datetime.utcnow().isoformat(),
                'processing_time_ms': int((datetime.utcnow() - start_time).total_seconds() * 1000),
                'options': options or {}
            }
        }

@celery_app.task(name="process_sourcemap_task")
def process_sourcemap_task(js_url: str, sourcemap_url: str = None, options: Dict[str, Any] = None) -> Dict[str, Any]:
    """
    Dedicated source map processing task.
    """
    start_time = datetime.utcnow()
    
    try:
        extractor = ComprehensiveExtractor()
        
        if not extractor.sourcemapper:
            raise Exception("sourcemapper not available")
        
        # Process source map
        result = run_coroutine_sync(
            extractor.sourcemapper.process_sourcemap_from_url(
                js_url,
                sourcemap_url,
                options.get('headers', {}) if options else None,
            )
        )
        
        # Add task metadata
        result['task_metadata'] = {
            'task_name': 'process_sourcemap_task',
            'start_time': start_time.isoformat(),
            'end_time': datetime.utcnow().isoformat(),
            'processing_time_ms': int((datetime.utcnow() - start_time).total_seconds() * 1000),
            'js_url': js_url,
            'sourcemap_url': sourcemap_url
        }
        
        if result['success']:
            logger.info(f"Source map processing completed for {js_url}: "
                       f"{result['stats']['total_files']} files reconstructed")
        else:
            logger.warning(f"Source map processing failed for {js_url}: {result.get('error', 'Unknown error')}")
        
        return result
        
    except Exception as e:
        logger.error(f"Source map processing task failed: {e}")
        return {
            'success': False,
            'error': str(e),
            'files': [],
            'stats': {'total_files': 0, 'total_size': 0},
            'task_metadata': {
                'task_name': 'process_sourcemap_task',
                'start_time': start_time.isoformat(),
                'end_time': datetime.utcnow().isoformat(),
                'processing_time_ms': int((datetime.utcnow() - start_time).total_seconds() * 1000),
                'js_url': js_url,
                'sourcemap_url': sourcemap_url
            }
        }

@celery_app.task(name="batch_process_files")
def batch_process_files(files_data: list, options: Dict[str, Any] = None) -> Dict[str, Any]:
    """
    Process multiple files in batch for efficiency.
    """
    start_time = datetime.utcnow()
    
    try:
        extractor = ComprehensiveExtractor()
        results = []
        stats = {
            'total_files': len(files_data),
            'successful': 0,
            'failed': 0,
            'total_endpoints': 0,
            'total_secrets': 0
        }
        
        for i, file_data in enumerate(files_data):
            try:
                content = file_data.get('content', '')
                metadata = file_data.get('metadata', {})
                metadata['batch_index'] = i
                
                # Process file
                result = extractor.extract_all(content, metadata)
                results.append({
                    'index': i,
                    'success': True,
                    'result': result
                })
                
                stats['successful'] += 1
                stats['total_endpoints'] += result['stats'].get('total_endpoints', 0)
                stats['total_secrets'] += result['stats'].get('total_secrets', 0)
                
            except Exception as e:
                logger.error(f"Batch file {i} processing failed: {e}")
                results.append({
                    'index': i,
                    'success': False,
                    'error': str(e)
                })
                stats['failed'] += 1
        
        processing_time = int((datetime.utcnow() - start_time).total_seconds() * 1000)
        
        return {
            'success': True,
            'results': results,
            'stats': stats,
            'task_metadata': {
                'task_name': 'batch_process_files',
                'start_time': start_time.isoformat(),
                'end_time': datetime.utcnow().isoformat(),
                'processing_time_ms': processing_time,
                'options': options or {}
            }
        }
        
    except Exception as e:
        logger.error(f"Batch processing failed: {e}")
        return {
            'success': False,
            'error': str(e),
            'results': [],
            'stats': {'total_files': len(files_data), 'successful': 0, 'failed': len(files_data)},
            'task_metadata': {
                'task_name': 'batch_process_files',
                'start_time': start_time.isoformat(),
                'end_time': datetime.utcnow().isoformat(),
                'processing_time_ms': int((datetime.utcnow() - start_time).total_seconds() * 1000),
                'options': options or {}
            }
        }
