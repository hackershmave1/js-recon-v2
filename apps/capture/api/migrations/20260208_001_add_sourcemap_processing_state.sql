-- Adds sourcemap processing state columns for source_maps.
-- Safe to run multiple times on PostgreSQL because of IF NOT EXISTS guards.

ALTER TABLE source_maps
    ADD COLUMN IF NOT EXISTS detected_map_url TEXT;

ALTER TABLE source_maps
    ADD COLUMN IF NOT EXISTS processing_status VARCHAR;

ALTER TABLE source_maps
    ADD COLUMN IF NOT EXISTS processing_error TEXT;

ALTER TABLE source_maps
    ADD COLUMN IF NOT EXISTS reconstructed_files_count INTEGER;

ALTER TABLE source_maps
    ADD COLUMN IF NOT EXISTS processed_at TIMESTAMP;

UPDATE source_maps
SET processing_status = COALESCE(processing_status, 'pending'),
    reconstructed_files_count = COALESCE(reconstructed_files_count, 0);

ALTER TABLE source_maps
    ALTER COLUMN processing_status SET DEFAULT 'pending';

ALTER TABLE source_maps
    ALTER COLUMN reconstructed_files_count SET DEFAULT 0;

ALTER TABLE source_maps
    ALTER COLUMN processing_status SET NOT NULL;

ALTER TABLE source_maps
    ALTER COLUMN reconstructed_files_count SET NOT NULL;
