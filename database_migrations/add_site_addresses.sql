-- ============================================================================
-- Add Address Information to Site Coordinators Table
-- Enables appointment confirmation emails to include site addresses
-- ============================================================================

-- Add address columns
ALTER TABLE site_coordinators
ADD COLUMN IF NOT EXISTS address TEXT,
ADD COLUMN IF NOT EXISTS city VARCHAR(100),
ADD COLUMN IF NOT EXISTS state VARCHAR(2),
ADD COLUMN IF NOT EXISTS zip_code VARCHAR(10);

-- Add index for location lookups
CREATE INDEX IF NOT EXISTS idx_site_coordinators_location ON site_coordinators(city, state);

-- Add comments
COMMENT ON COLUMN site_coordinators.address IS 'Full street address of the site';
COMMENT ON COLUMN site_coordinators.city IS 'City where the site is located';
COMMENT ON COLUMN site_coordinators.state IS 'Two-letter state code';
COMMENT ON COLUMN site_coordinators.zip_code IS 'ZIP code for the site address';

-- ============================================================================
-- Populate addresses from Site Addresses.csv
-- ============================================================================

-- General Medicine Sites
UPDATE site_coordinators SET address = '3525 Prytania St. Suite 612', city = 'New Orleans', state = 'LA', zip_code = '70115' WHERE site_name = 'NO - General Medicine';
UPDATE site_coordinators SET address = '10100 S Mingo Rd', city = 'Tulsa', state = 'OK', zip_code = '74133' WHERE site_name = 'TUL - General Medicine';
UPDATE site_coordinators SET address = '16158 Airline Hwy, Suite 103', city = 'Prairieville', state = 'LA', zip_code = '70769' WHERE site_name = 'BR - General Medicine';
UPDATE site_coordinators SET address = '3916 LA-22, Suite 1', city = 'Mandeville', state = 'LA', zip_code = '70471' WHERE site_name = 'NS - General Medicine';
UPDATE site_coordinators SET address = '1050 South Coit Rd', city = 'Prosper', state = 'TX', zip_code = '75078' WHERE site_name = 'DAL - General Medicine';
UPDATE site_coordinators SET address = '17 Executive Park, Suite 480', city = 'Atlanta', state = 'GA', zip_code = '30329' WHERE site_name = 'ATL - General Medicine';
UPDATE site_coordinators SET address = '8970 Lorraine Road', city = 'Gulfport', state = 'MS', zip_code = '39503' WHERE site_name LIKE 'GU - General Medicine%';
UPDATE site_coordinators SET address = '3238 S National Ave', city = 'Springfield', state = 'MO', zip_code = '65807' WHERE site_name = 'SPR - General Medicine';
UPDATE site_coordinators SET address = '174 Saundersville Rd., Suite 303', city = 'Hendersonville', state = 'TN', zip_code = '37075' WHERE site_name = 'NAS - General Medicine';
UPDATE site_coordinators SET address = '1477 Tobias Gadson Blvd.', city = 'Charleston', state = 'SC', zip_code = '29407' WHERE site_name = 'CHS - General Medicine';
UPDATE site_coordinators SET address = '272 Lamp and Lantern Village', city = 'Town and Country', state = 'MO', zip_code = '63017' WHERE site_name = 'STL - General Medicine';
UPDATE site_coordinators SET address = '6000 Executive Blvd Ste 315', city = 'Rockville', state = 'MD', zip_code = '20852' WHERE site_name = 'BET - General Medicine';
UPDATE site_coordinators SET address = '6719 Fairview Road Suites A&B', city = 'Charlotte', state = 'NC', zip_code = '28210' WHERE site_name = 'CLT - General Medicine';
UPDATE site_coordinators SET address = '5701 West 119th St. Suite 240', city = 'Overland Park', state = 'KS', zip_code = '66209' WHERE site_name = 'OVP - General Medicine';
UPDATE site_coordinators SET address = '2908 Taylorsville Road', city = 'Louisville', state = 'KY', zip_code = '40205' WHERE site_name = 'LOU - General Medicine';
UPDATE site_coordinators SET address = '6499 Mason Montgomery Rd Ste C', city = 'Mason', state = 'OH', zip_code = '45040' WHERE site_name = 'CIN - General Medicine';
UPDATE site_coordinators SET address = '2162 N. Meridian St., Suite B', city = 'Indianapolis', state = 'IN', zip_code = '46202' WHERE site_name = 'IND - General Medicine';

-- Dermatology Sites
UPDATE site_coordinators SET address = '3525 Prytania St. Suite 612', city = 'New Orleans', state = 'LA', zip_code = '70115' WHERE site_name = 'NO - Dermatology';
UPDATE site_coordinators SET address = '10154 Jefferson Hwy', city = 'Baton Rouge', state = 'LA', zip_code = '70809' WHERE site_name = 'BR - Dermatology';
UPDATE site_coordinators SET address = '521 Stonecrest Parkway Suite 201', city = 'Smyrna', state = 'TN', zip_code = '37167' WHERE site_name = 'NAS - Dermatology';
UPDATE site_coordinators SET address = '16759 Main Street, Suite 201', city = 'Wildwood', state = 'MO', zip_code = '63040' WHERE site_name = 'STL - Dermatology';
UPDATE site_coordinators SET address = '1030 S Coit Rd', city = 'Prosper', state = 'TX', zip_code = '75078' WHERE site_name = 'DAL - Dermatology';
UPDATE site_coordinators SET address = '570 Long Point Rd #200', city = 'Mt Pleasant', state = 'SC', zip_code = '29464' WHERE site_name = 'CHS - Dermatology';
UPDATE site_coordinators SET address = '17 Executive Park Drive NE Suite 115 & 290', city = 'Atlanta', state = 'GA', zip_code = '30329' WHERE site_name = 'ATL - Dermatology';
UPDATE site_coordinators SET address = '4565 E. Galbraith Road, Suite A', city = 'Cincinnati', state = 'OH', zip_code = '45236' WHERE site_name = 'CIN - Dermatology';

-- Ophthalmology Sites
UPDATE site_coordinators SET address = '8220 Naab Rd. Suite 200', city = 'Indianapolis', state = 'IN', zip_code = '46260' WHERE site_name = 'IND - Ophthalmology';
UPDATE site_coordinators SET address = '4628 Rye St.', city = 'Metairie', state = 'LA', zip_code = '70006' WHERE site_name = 'NO - Ophthalmology';

-- Urology Sites
UPDATE site_coordinators SET address = '3525 Prytania St. Suite 612', city = 'New Orleans', state = 'LA', zip_code = '70115' WHERE site_name = 'NO - Urology';

-- Psychiatry Sites
UPDATE site_coordinators SET address = '3525 Prytania St. Suite 308', city = 'New Orleans', state = 'LA', zip_code = '70115' WHERE site_name LIKE 'NO - Psych%';
UPDATE site_coordinators SET address = '5026 Tennyson Pkwy, Bldg. 7', city = 'Plano', state = 'TX', zip_code = '75024' WHERE site_name = 'DAL - Psych';
UPDATE site_coordinators SET address = '17 Executive Park Drive NE, Suite 480', city = 'Atlanta', state = 'GA', zip_code = '30329' WHERE site_name = 'ATL - Psych';
UPDATE site_coordinators SET address = '6719 Fairview Road Suites A&B', city = 'Charlotte', state = 'NC', zip_code = '28210' WHERE site_name = 'CLT - Psych';
UPDATE site_coordinators SET address = '6000 Executive Blvd, Suite 602', city = 'Rockville', state = 'MD', zip_code = '20852' WHERE site_name = 'BET - Psych';

-- Neurology Sites
UPDATE site_coordinators SET address = '10102 Park Rowe Ave. Suite 200', city = 'Baton Rouge', state = 'LA', zip_code = '70810' WHERE site_name = 'BR - Neuro Medical';

-- Set default value for any remaining sites without addresses
UPDATE site_coordinators SET
    address = 'Address available upon confirmation',
    city = 'Contact site',
    state = '',
    zip_code = ''
WHERE address IS NULL;

-- ============================================================================
-- Verification and Summary
-- ============================================================================

DO $$
DECLARE
    total_sites INTEGER;
    sites_with_address INTEGER;
    sites_without_address INTEGER;
BEGIN
    SELECT COUNT(*) INTO total_sites FROM site_coordinators;
    SELECT COUNT(*) INTO sites_with_address FROM site_coordinators WHERE address IS NOT NULL AND address != 'Address available upon confirmation';
    SELECT COUNT(*) INTO sites_without_address FROM site_coordinators WHERE address IS NULL OR address = 'Address available upon confirmation';

    RAISE NOTICE '✅ Site address migration complete';
    RAISE NOTICE '   Total sites: %', total_sites;
    RAISE NOTICE '   Sites with addresses: %', sites_with_address;
    RAISE NOTICE '   Sites needing addresses: %', sites_without_address;
END $$;
