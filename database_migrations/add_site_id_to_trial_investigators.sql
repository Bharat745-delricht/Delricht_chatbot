-- Migration: Add site_id to trial_investigators for proper availability matching
-- Date: 2025-12-17
-- Purpose: Link trials directly to CRIO site_ids for accurate availability checks

-- Step 1: Add column
ALTER TABLE trial_investigators 
ADD COLUMN IF NOT EXISTS site_id VARCHAR(10);

-- Step 2: Create index for performance
CREATE INDEX IF NOT EXISTS idx_trial_investigators_site_id 
ON trial_investigators(site_id);

-- Step 3: Populate site_id by matching site_location to location_site_mappings
-- This maps investigators to sites based on their location

UPDATE trial_investigators ti
SET site_id = (
    SELECT lsm.site_id
    FROM location_site_mappings lsm
    WHERE ti.site_location ILIKE '%' || lsm.city_code || '%'
       OR ti.site_location ILIKE '%' || lsm.location_name || '%'
    ORDER BY lsm.is_default DESC, lsm.priority DESC
    LIMIT 1
)
WHERE ti.site_id IS NULL;

-- Step 4: Report results
DO $$
DECLARE
    total_count INTEGER;
    mapped_count INTEGER;
    unmapped_count INTEGER;
BEGIN
    SELECT COUNT(*) INTO total_count FROM trial_investigators;
    SELECT COUNT(*) INTO mapped_count FROM trial_investigators WHERE site_id IS NOT NULL;
    SELECT COUNT(*) INTO unmapped_count FROM trial_investigators WHERE site_id IS NULL;
    
    RAISE NOTICE '========================================';
    RAISE NOTICE 'MIGRATION RESULTS:';
    RAISE NOTICE '========================================';
    RAISE NOTICE 'Total investigators: %', total_count;
    RAISE NOTICE 'Mapped to sites: %', mapped_count;
    RAISE NOTICE 'Unmapped: %', unmapped_count;
    RAISE NOTICE '========================================';
END $$;

-- Step 5: Show sample mappings for verification
SELECT 
    ti.investigator_name,
    ti.site_location,
    ti.site_id,
    sc.site_name,
    sc.coordinator_email
FROM trial_investigators ti
LEFT JOIN site_coordinators sc ON ti.site_id = sc.site_id
WHERE ti.trial_id = 2  -- Gout trial
ORDER BY ti.investigator_name
LIMIT 10;
