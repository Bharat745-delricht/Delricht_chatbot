#!/usr/bin/env python3
"""
Query specific sites (TUL, SPR, BET) from database
"""

import psycopg2
import os

# Database connection
DB_HOST = "34.56.137.172"
DB_NAME = "gemini_chatbot_database"
DB_USER = "postgres"
DB_PASS = os.environ.get('DB_PASS', 'Delricht2017!')

try:
    conn = psycopg2.connect(
        host=DB_HOST,
        database=DB_NAME,
        user=DB_USER,
        password=DB_PASS
    )

    cursor = conn.cursor()

    # Query for TUL, SPR, and BET Gen Med sites
    query = """
        SELECT site_id, site_name, coordinator_email, coordinator_user_key, is_active
        FROM site_coordinators
        WHERE
            site_name ILIKE '%TUL%'
            OR site_name ILIKE '%SPR%'
            OR (site_name ILIKE '%BET%' AND site_name ILIKE '%Gen%')
        ORDER BY site_name;
    """

    cursor.execute(query)
    sites = cursor.fetchall()

    print("=" * 80)
    print("TUL, SPR, and BET Gen Med Sites")
    print("=" * 80)
    print()

    tyler_user_key = "5540"

    for site in sites:
        site_id, site_name, email, user_key, is_active = site
        print(f"Site: {site_name}")
        print(f"  Site ID: {site_id}")
        print(f"  Coordinator Email: {email}")
        print(f"  Coordinator User Key: {user_key}")
        print(f"  Tyler User Key: {tyler_user_key}")
        print(f"  Active: {is_active}")

        if not user_key or user_key == '0':
            print(f"  ⚠️  WARNING: No coordinator user key configured!")
        else:
            print(f"  ✅ Configured correctly")
        print()

    cursor.close()
    conn.close()

    print("=" * 80)
    print("API Call Configuration")
    print("=" * 80)
    print()
    print("When V3 Dashboard calls CRIO API for these sites, it should include:")
    print(f"  - Tyler's user key: {tyler_user_key} (for capacity blocks)")
    print(f"  - Site coordinator user key (for patient visits)")
    print()
    print("If either is missing, that type of event won't be fetched.")
    print()

except Exception as e:
    print(f"Error: {e}")
