#!/usr/bin/env python3
"""
Explore database schema for contact collection data
"""
import sys
from core.database import db

def explore_schema():
    """Explore tables and their schemas"""

    # Get all tables
    tables_query = """
    SELECT table_name
    FROM information_schema.tables
    WHERE table_schema = 'public'
    ORDER BY table_name;
    """
    tables = db.execute_query(tables_query)
    print("\n=== All Tables ===")
    for table in tables:
        print(f"  - {table['table_name']}")

    # Check for contact-related tables
    contact_tables = [t['table_name'] for t in tables if 'contact' in t['table_name'].lower()]
    print(f"\n=== Contact-Related Tables ===")
    for table in contact_tables:
        print(f"  - {table}")

    # Check prescreening_sessions schema
    print("\n=== prescreening_sessions Schema ===")
    ps_schema = db.execute_query("""
        SELECT column_name, data_type
        FROM information_schema.columns
        WHERE table_name = 'prescreening_sessions'
        ORDER BY ordinal_position;
    """)
    for col in ps_schema:
        print(f"  - {col['column_name']}: {col['data_type']}")

    # Check conversation_contexts schema
    print("\n=== conversation_contexts Schema ===")
    cc_schema = db.execute_query("""
        SELECT column_name, data_type
        FROM information_schema.columns
        WHERE table_name = 'conversation_contexts'
        ORDER BY ordinal_position;
    """)
    for col in cc_schema:
        print(f"  - {col['column_name']}: {col['data_type']}")

    # Check patient_contact_info schema
    print("\n=== patient_contact_info Schema ===")
    pci_schema = db.execute_query("""
        SELECT column_name, data_type
        FROM information_schema.columns
        WHERE table_name = 'patient_contact_info'
        ORDER BY ordinal_position;
    """)
    for col in pci_schema:
        print(f"  - {col['column_name']}: {col['data_type']}")

    # Sample query to see contact data
    print("\n=== Sample Contact Data ===")
    sample = db.execute_query("""
        SELECT
            pci.session_id,
            pci.first_name,
            pci.last_name,
            pci.phone_number,
            pci.email,
            pci.created_at,
            ps.trial_id,
            ps.condition,
            ps.started_at
        FROM patient_contact_info pci
        LEFT JOIN prescreening_sessions ps ON pci.session_id = ps.session_id
        WHERE pci.phone_number IS NOT NULL
        ORDER BY pci.created_at DESC
        LIMIT 5;
    """)

    for row in sample:
        print(f"\n  Session: {row['session_id']}")
        print(f"  Name: {row['first_name']} {row['last_name']}")
        print(f"  Phone: {row['phone_number']}")
        print(f"  Email: {row['email']}")
        print(f"  Created: {row['created_at']}")
        print(f"  Started: {row['started_at']}")
        print(f"  Trial ID: {row['trial_id']}")
        print(f"  Condition: {row['condition']}")

if __name__ == "__main__":
    try:
        explore_schema()
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
