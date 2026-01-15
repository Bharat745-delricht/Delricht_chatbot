#!/usr/bin/env python3
"""Check and close idle database connections"""
import psycopg2
from config import settings

try:
    # Connect as superuser
    conn = psycopg2.connect(
        dbname='gemini_chatbot_database',
        user='postgres',
        password=settings.DB_PASS,
        host='34.56.137.172',
        port=5432
    )
    conn.autocommit = True
    cursor = conn.cursor()

    # Check total connections
    cursor.execute("""
        SELECT count(*) as total_connections, state, application_name
        FROM pg_stat_activity
        WHERE datname='gemini_chatbot_database'
        GROUP BY state, application_name
        ORDER BY count(*) DESC;
    """)

    print("Current Database Connections:")
    print("="*60)
    for row in cursor.fetchall():
        print(f"  {row[0]} connections | State: {row[1]} | App: {row[2]}")

    # Get total connection limit
    cursor.execute("SHOW max_connections;")
    max_conn = cursor.fetchone()[0]
    print(f"\n Max connections allowed: {max_conn}")

    # Close idle connections that are not from this script
    cursor.execute("""
        SELECT pg_terminate_backend(pid)
        FROM pg_stat_activity
        WHERE datname = 'gemini_chatbot_database'
          AND state = 'idle'
          AND pid <> pg_backend_pid()
          AND query_start < NOW() - INTERVAL '5 minutes';
    """)

    terminated = cursor.rowcount
    print(f"\n✅ Terminated {terminated} idle connections older than 5 minutes")

    cursor.close()
    conn.close()

except Exception as e:
    print(f"❌ Error: {e}")
