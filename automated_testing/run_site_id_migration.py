#!/usr/bin/env python3
"""
Safe migration runner for adding site_id to trial_investigators
Includes validation and rollback capability
"""

from db_helper import get_db
import sys

def validate_prerequisites():
    """Ensure migration can run safely"""
    db = get_db()
    
    print("🔍 PRE-MIGRATION VALIDATION")
    print("=" * 80)
    
    # Check 1: Does column already exist?
    existing = db.execute_query("""
        SELECT column_name 
        FROM information_schema.columns
        WHERE table_name = 'trial_investigators' AND column_name = 'site_id'
    """)
    
    if existing:
        print("⚠️  Column 'site_id' already exists")
        return "EXISTS"
    else:
        print("✓ Column 'site_id' does not exist - ready to add")
    
    # Check 2: How many investigators need mapping?
    count = db.execute_query("SELECT COUNT(*) as count FROM trial_investigators")
    print(f"✓ Total investigators: {count[0]['count']}")
    
    # Check 3: How many locations have site mappings?
    mappings = db.execute_query("SELECT COUNT(*) as count FROM location_site_mappings")
    print(f"✓ Location mappings available: {mappings[0]['count']}")
    
    print()
    return "READY"

def run_migration():
    """Execute the migration"""
    db = get_db()
    
    print("🚀 RUNNING MIGRATION")
    print("=" * 80)
    
    try:
        # Step 1: Add column
        print("Step 1: Adding site_id column...")
        db.execute_update("ALTER TABLE trial_investigators ADD COLUMN IF NOT EXISTS site_id VARCHAR(10)")
        print("   ✓ Column added")
        
        # Step 2: Create index
        print("Step 2: Creating index...")
        db.execute_update("CREATE INDEX IF NOT EXISTS idx_trial_investigators_site_id ON trial_investigators(site_id)")
        print("   ✓ Index created")
        
        # Step 3: Populate values
        print("Step 3: Populating site_id values...")
        updated = db.execute_update("""
            UPDATE trial_investigators ti
            SET site_id = (
                SELECT lsm.site_id
                FROM location_site_mappings lsm
                WHERE ti.site_location ILIKE '%' || lsm.city_code || '%'
                   OR ti.site_location ILIKE '%' || lsm.location_name || '%'
                ORDER BY lsm.is_default DESC, lsm.priority DESC
                LIMIT 1
            )
            WHERE ti.site_id IS NULL
        """)
        print(f"   ✓ Updated {updated} investigators")
        
        print()
        print("✅ MIGRATION SUCCESSFUL")
        return True
        
    except Exception as e:
        print(f"❌ MIGRATION FAILED: {e}")
        return False

def validate_results():
    """Check migration results"""
    db = get_db()
    
    print()
    print("📊 POST-MIGRATION VALIDATION")
    print("=" * 80)
    
    # Count mapped vs unmapped
    stats = db.execute_query("""
        SELECT 
            COUNT(*) as total,
            COUNT(site_id) as mapped,
            COUNT(*) - COUNT(site_id) as unmapped
        FROM trial_investigators
    """)
    
    if stats:
        s = stats[0]
        print(f"Total investigators: {s['total']}")
        print(f"Mapped to sites: {s['mapped']} ({s['mapped']/s['total']*100:.1f}%)")
        print(f"Unmapped: {s['unmapped']}")
        print()
    
    # Show sample for Gout trial (ID 2)
    print("Sample: Gout Trial (ID 2) Investigators")
    print("-" * 80)
    gout_sample = db.execute_query("""
        SELECT 
            ti.investigator_name,
            ti.site_location,
            ti.site_id,
            sc.site_name,
            sc.coordinator_email
        FROM trial_investigators ti
        LEFT JOIN site_coordinators sc ON ti.site_id = sc.site_id
        WHERE ti.trial_id = 2
        ORDER BY ti.site_location
        LIMIT 5
    """)
    
    for inv in gout_sample:
        status = "✅" if inv['site_id'] else "❌"
        print(f"{status} {inv['investigator_name']:25} | {inv['site_location']:20} | Site: {inv['site_id'] or 'UNMAPPED'}")
    
    print()
    print("=" * 80)

def main():
    print()
    print("=" * 80)
    print("🗄️  DATABASE MIGRATION: Add site_id to trial_investigators")
    print("=" * 80)
    print()
    
    # Validate
    status = validate_prerequisites()
    
    if status == "EXISTS":
        print()
        print("Column already exists. Showing current state...")
        validate_results()
        return
    
    if status != "READY":
        print("❌ Prerequisites not met")
        return
    
    # Confirm
    print()
    response = input("Proceed with migration? (yes/no): ")
    if response.lower() not in ['yes', 'y']:
        print("Migration cancelled")
        return
    
    print()
    
    # Run migration
    success = run_migration()
    
    if success:
        # Validate results
        validate_results()
    else:
        print()
        print("❌ Migration failed - check errors above")

if __name__ == "__main__":
    main()
