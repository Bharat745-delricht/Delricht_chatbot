#!/usr/bin/env python3
"""
Add missing DelRicht sites to site_coordinators and assign orphaned trials
"""

from core.database import db

# DelRicht sites that need to be added
# Format: (site_code, location_city_state, site_type, coordinator_email)
MISSING_SITES = [
    ('CLT', 'Charlotte, NC', 'General Medicine', 'charlotte@delricht.com'),
    ('VIE', 'Vienna, VA', 'General Medicine', 'vienna@delricht.com'),
    ('HEN', 'Hendersonville, TN', 'General Medicine', 'hendersonville@delricht.com'),
    ('ROC', 'Rockville, MD', 'General Medicine', 'rockville@delricht.com'),
    ('SMY', 'Smyrna, TN', 'General Medicine', 'smyrna@delricht.com'),
    ('TC', 'Town and Country, MO', 'General Medicine', 'towncountry@delricht.com'),
    ('IND', 'Indianapolis, IN', 'General Medicine', 'indianapolis@delricht.com'),
    ('MTP', 'Mt. Pleasant, SC', 'General Medicine', 'mtpleasant@delricht.com'),
    ('PRO', 'Prosper, TX', 'General Medicine', 'prosper@delricht.com'),
]

def add_missing_sites():
    """Add missing sites to site_coordinators"""

    print("ADDING MISSING DELRICHT SITES")
    print("=" * 70)

    added_sites = []

    for site_code, location, site_type, email in MISSING_SITES:
        site_name = f"{site_code} - {site_type}"

        # Check if site already exists
        existing = db.execute_query("""
            SELECT site_id FROM site_coordinators
            WHERE site_name = %s
        """, (site_name,))

        if existing:
            print(f"⏭️  {site_name} already exists (ID: {existing[0]['site_id']})")
            continue

        # Find next available site_id (use 4-digit format starting at 5000)
        max_id = db.execute_query("""
            SELECT MAX(site_id::int) as max_id
            FROM site_coordinators
            WHERE site_id ~ '^[0-9]+$'
        """)

        next_id = str(int(max_id[0]['max_id']) + 1) if max_id and max_id[0]['max_id'] else '5000'

        # Insert new site
        db.execute_insert_returning("""
            INSERT INTO site_coordinators
            (site_id, site_name, coordinator_email, is_active)
            VALUES (%s, %s, %s, true)
            RETURNING id
        """, (next_id, site_name, email))

        print(f"✅ Added: {site_name} (ID: {next_id})")
        added_sites.append((next_id, site_name, location))

    return added_sites

def assign_orphaned_trials_to_new_sites(added_sites):
    """Assign orphaned trials to newly created sites"""

    print("\n" + "=" * 70)
    print("ASSIGNING ORPHANED TRIALS TO NEW SITES")
    print("=" * 70)

    fixed_count = 0

    for site_id, site_name, location in added_sites:
        # Find trials with this location
        orphaned = db.execute_query("""
            SELECT id, trial_id, investigator_name
            FROM trial_investigators
            WHERE site_id IS NULL
            AND site_location ILIKE %s
        """, (f"%{location}%",))

        if orphaned:
            print(f"\n{site_name} ({location}):")

            for record in orphaned:
                db.execute_update("""
                    UPDATE trial_investigators
                    SET site_id = %s
                    WHERE id = %s
                """, (site_id, record['id']))

                print(f"  ✅ Assigned trial {record['trial_id']} - {record['investigator_name']}")
                fixed_count += 1

    return fixed_count

if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "--confirm":
        added = add_missing_sites()

        if added:
            fixed = assign_orphaned_trials_to_new_sites(added)

            print("\n" + "=" * 70)
            print("SUMMARY:")
            print(f"  Sites added: {len(added)}")
            print(f"  Trials fixed: {fixed}")
            print("=" * 70)
        else:
            print("\n✅ All sites already exist")

    else:
        print("DRY RUN - Add --confirm to execute")
        print("\nThis will:")
        print("1. Add missing DelRicht sites to site_coordinators")
        print("2. Assign orphaned trials to those sites")
        print("\nRun: DB_PASS='xxx' python3 add_missing_sites.py --confirm")
