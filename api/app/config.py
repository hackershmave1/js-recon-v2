from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql://jsextractor:changeme123@localhost:5432/js_extractor"
    storage_path: str = "/var/lib/js-extractor/storage"
    sourcemap_processing_timeout_seconds: int = 30
    sourcemap_max_size_bytes: int = 50 * 1024 * 1024
    sourcemap_max_reconstructed_files: int = 1000
    file_content_ttl_days: int = 30
    sourcemap_content_ttl_days: int = 30
    cleanup_max_deletions_per_run: int = 500
    api_key: str | None = None
    
    # Smart analysis trigger settings
    smart_analysis_enabled: bool = True
    smart_analysis_min_file_size: int = 50 * 1024  # 50KB
    smart_analysis_with_sourcemaps: bool = True
    smart_analysis_api_pattern_threshold: int = 3
    smart_analysis_secret_pattern_threshold: int = 1
    smart_analysis_minified_js_threshold: float = 0.8  # Lines longer than 80 chars as fraction
    
    # Chunked regex processing settings
    regex_chunk_enabled: bool = True
    regex_chunk_size_threshold: int = 1024 * 1024  # 1MB - files larger than this get chunked
    regex_chunk_size: int = 100 * 1024  # 100KB chunks
    regex_chunk_overlap: int = 5 * 1024  # 5KB overlap between chunks
    regex_chunk_timeout: int = 5  # 5 seconds timeout per chunk
    regex_chunk_max_chunks: int = 50  # Maximum number of chunks to prevent abuse
    
    # Endpoint sanitization settings
    endpoint_sanitization_enabled: bool = True
    endpoint_filter_domains: bool = True
    endpoint_filter_extensions: bool = True
    
    # HTTP fetch hardening settings
    fetch_retry_enabled: bool = True
    fetch_max_retries: int = 3
    fetch_retry_backoff: float = 1.0  # Base delay in seconds (exponential backoff)
    fetch_max_response_size: int = 100 * 1024 * 1024  # 100MB response size cap
    fetch_timeout_seconds: int = 30
    fetch_connect_timeout_seconds: int = 10
    fetch_user_agent: str = "JS-Security-Extractor/3.0"

    class Config:
        env_prefix = ""
        case_sensitive = False


settings = Settings()
