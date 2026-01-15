#!/usr/bin/env python3
"""
Script to check Tyler's calendar events and see what's being parsed
Focuses on ATL - General Medicine (site_id: 2327)
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.database import db
from datetime import datetime, timedelta

def check_tyler_events():
    """Query recent Tyler events from CRIO data or logs"""

    print("=" * 80)
    print("CHECKING TYLER EVENTS AT ATL - GENERAL MEDICINE")
    print("=" * 80)
    print()

    # Tyler's user key
    tyler_user_key = "5540"
    tyler_email = "thastings@delricht.com"
    atl_site_id = "2327"

    # Check if we have any tables that store calendar events
    print("1. Checking database tables for calendar/event data...")
    print("-" * 80)

    tables_query = """
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = 'public'
        AND (
            table_name LIKE '%event%'
            OR table_name LIKE '%calendar%'
            OR table_name LIKE '%schedule%'
            OR table_name LIKE '%availability%'
            OR table_name LIKE '%tyler%'
        )
        ORDER BY table_name;
    """

    tables = db.execute_query(tables_query)
    if tables:
        print(f"Found {len(tables)} relevant tables:")
        for table in tables:
            print(f"  - {table[0]}")
        print()

        # Check each table for sample data
        for table in tables:
            table_name = table[0]
            print(f"2. Checking table: {table_name}")
            print("-" * 80)

            # Get column names
            cols_query = f"""
                SELECT column_name, data_type
                FROM information_schema.columns
                WHERE table_name = '{table_name}'
                ORDER BY ordinal_position;
            """
            cols = db.execute_query(cols_query)
            print(f"Columns: {', '.join([f'{c[0]} ({c[1]})' for c in cols])}")

            # Get sample data
            sample_query = f"SELECT * FROM {table_name} LIMIT 5;"
            try:
                sample = db.execute_query(sample_query)
                if sample:
                    print(f"Sample rows: {len(sample)}")
                    for row in sample[:2]:  # Show first 2 rows
                        print(f"  {row}")
                else:
                    print("  (no data)")
            except Exception as e:
                print(f"  Error querying: {e}")
            print()
    else:
        print("No calendar/event tables found in database.")
        print()

    # Check for any logs or activity records
    print("3. Checking for activity logs or pattern matching records...")
    print("-" * 80)

    log_tables_query = """
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = 'public'
        AND (table_name LIKE '%log%' OR table_name LIKE '%activity%')
        ORDER BY table_name;
    """

    log_tables = db.execute_query(log_tables_query)
    if log_tables:
        print(f"Found {len(log_tables)} log tables:")
        for table in log_tables:
            print(f"  - {table[0]}")
    else:
        print("No log tables found.")
    print()

    print("=" * 80)
    print("RECOMMENDATIONS:")
    print("=" * 80)
    print()
    print("Since calendar events come from CRIO API (not stored in our database),")
    print("you'll need to:")
    print()
    print("1. Check Tyler's actual CRIO calendar for ATL - General Medicine")
    print("2. Look at the V3 Dashboard console logs when viewing that site")
    print("3. Check the browser Network tab when loading availability")
    print()
    print("Common event title formats that might cause issues:")
    print("  ✓ '8 recruitment visits' (exact)        → Should match")
    print("  ✗ '8 recruitment visits ' (trailing)    → Won't match (space)")
    print("  ✗ ' 8 recruitment visits' (leading)     → Won't match (space)")
    print("  ✗ '8 recruitment visits (1283)' (extra) → Won't match (parentheses)")
    print("  ✗ '8 Recruitment Visits.' (period)      → Won't match (punctuation)")
    print()
    print("The flexible pattern we'll add will handle all of these cases.")
    print()

if __name__ == '__main__':
    try:
        check_tyler_events()
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
