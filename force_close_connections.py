#!/usr/bin/env python3
"""Force close ALL idle database connections"""
import psycopg2
from config import settings

try:
    conn = psycopg2.connect(
        dbname='gemini_chatbot_database',
        user='postgres',
        password=settings.DB_PASS,
        host='34.56.137.172',
        port=5432
    )
    conn.autocommit = True
    cursor = conn.cursor()

    # Terminate ALL idle connections (except this one)
    cursor.execute("""
        SELECT pg_terminate_backend(pid)
        FROM pg_stat_activity
        WHERE datname = 'gemini_chatbot_database'
          AND state = 'idle'
          AND pid <> pg_backend_pid();
    """)

    terminated = cursor.rowcount
    print(f"✅ Terminated {terminated} idle connections")

    # Check remaining
    cursor.execute("""
        SELECT count(*) as total, state
        FROM pg_stat_activity
        WHERE datname='gemini_chatbot_database'
        GROUP BY state;
    """)

    print("\nRemaining connections:")
    for row in cursor.fetchall():
        print(f"  {row[0]} connections | State: {row[1]}")

    cursor.close()
    conn.close()

except Exception as e:
    print(f"❌ Error: {e}")
