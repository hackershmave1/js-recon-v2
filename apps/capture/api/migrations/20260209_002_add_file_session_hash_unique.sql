-- T-026: Add unique constraint on files(session_id, content_hash) for ingestion idempotency.
-- Safe to run multiple times on PostgreSQL.
-- Handles existing duplicate data by keeping first occurrence per session+hash.

-- Check if constraint already exists
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.table_constraints 
        WHERE constraint_name = 'files_session_content_unique' 
        AND table_name = 'files'
    ) THEN
        -- First, clean up any existing duplicates by keeping the earliest record per (session_id, content_hash)
        -- Handle foreign key constraints by deleting child records first
        WITH duplicate_files AS (
            SELECT id FROM files 
            WHERE id NOT IN (
                SELECT DISTINCT ON (session_id, content_hash) id
                FROM files 
                ORDER BY session_id, content_hash, captured_at ASC
            )
        )
        DELETE FROM dependencies WHERE file_id IN (SELECT id FROM duplicate_files);
        
        WITH duplicate_files AS (
            SELECT id FROM files 
            WHERE id NOT IN (
                SELECT DISTINCT ON (session_id, content_hash) id
                FROM files 
                ORDER BY session_id, content_hash, captured_at ASC
            )
        )
        DELETE FROM source_maps WHERE file_id IN (SELECT id FROM duplicate_files);
        
        WITH duplicate_files AS (
            SELECT id FROM files 
            WHERE id NOT IN (
                SELECT DISTINCT ON (session_id, content_hash) id
                FROM files 
                ORDER BY session_id, content_hash, captured_at ASC
            )
        )
        DELETE FROM file_analyses WHERE file_id IN (SELECT id FROM duplicate_files);
        
        -- Now delete the duplicate files
        DELETE FROM files 
        WHERE id NOT IN (
            SELECT DISTINCT ON (session_id, content_hash) id
            FROM files 
            ORDER BY session_id, content_hash, captured_at ASC
        );

        -- Add the unique constraint
        ALTER TABLE files
            ADD CONSTRAINT files_session_content_unique 
            UNIQUE (session_id, content_hash);
            
        RAISE NOTICE 'Added unique constraint files_session_content_unique';
    ELSE
        RAISE NOTICE 'Constraint files_session_content_unique already exists, skipping';
    END IF;
END
$$;