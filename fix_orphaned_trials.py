#!/usr/bin/env python3
"""
Fix orphaned trials by assigning proper site_ids to DelRicht sites
"""

from core.database import db

# Mapping of location text to DelRicht site_ids
DELRICHT_SITE_MAPPING = {
    'New Orleans': '1261',  # NO - General Medicine
    'Baton Rouge': '1265',  # BR - General Medicine
    'Tulsa': '1305',  # TUL - General Medicine
    'Dallas': '1867',  # DAL - General Medicine
    'Atlanta': '2327',  # ATL - General Medicine
    'Louisville': '3500',  # LOU - General Medicine
    'Overland Park': '1818',  # OVP - General Medicine
    'Mandeville': '3468',  # MAN - General Medicine
    'Charleston': '2693',  # CHS - General Medicine
    'Springfield': '2517',  # SPR - General Medicine
}

def fix_orphaned_delricht_trials():
    """Fix orphaned trials that belong to DelRicht sites"""

    print("FIXING ORPHANED TRIALS AT DELRICHT SITES")
    print("=" * 70)

    # Get all orphaned records
    orphaned = db.execute_query("""
        SELECT id, trial_id, investigator_name, site_location
        FROM trial_investigators
        WHERE site_id IS NULL
    """)

    print(f"Total orphaned records: {len(orphaned)}\n")

    fixed_count = 0
    skipped_count = 0

    for record in orphaned:
        site_location = record['site_location'] or ''

        # Try to match to DelRicht site
        matched_site_id = None
        for city, site_id in DELRICHT_SITE_MAPPING.items():
            if city.lower() in site_location.lower():
                matched_site_id = site_id
                break

        if matched_site_id:
            # Fix the record
            print(f"✅ Fixing Record {record['id']}:")
            print(f"   Location: {site_location}")
            print(f"   Assigning to: {matched_site_id}")

            db.execute_update("""
                UPDATE trial_investigators
                SET site_id = %s
                WHERE id = %s
            """, (matched_site_id, record['id']))

            fixed_count += 1
        else:
            # External site - skip
            skipped_count += 1
            if skipped_count <= 3:  # Show first few
                print(f"⏭️  Skipping Record {record['id']}: {site_location} (external site)")

    print("\n" + "=" * 70)
    print(f"SUMMARY:")
    print(f"  Fixed: {fixed_count} DelRicht site assignments")
    print(f"  Skipped: {skipped_count} external sites")
    print("=" * 70)

    # Verify
    remaining = db.execute_query("""
        SELECT COUNT(*) as count
        FROM trial_investigators
        WHERE site_id IS NULL
    """)

    print(f"\nRemaining orphaned records: {remaining[0]['count']}")

    if fixed_count > 0:
        print(f"\n✅ Fixed {fixed_count} trials!")
        print("These trials are now searchable and bookable.")

if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "--confirm":
        fix_orphaned_delricht_trials()
    else:
        print("DRY RUN - Add --confirm to execute fixes")
        print("\nThis will assign site_ids to orphaned trials at DelRicht sites")
        print("External site trials will be skipped")
        print("\nRun: DB_PASS='xxx' python3 fix_orphaned_trials.py --confirm")
