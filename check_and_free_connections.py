#!/usr/bin/env python3
"""Check and free database connections"""

import psycopg2
import os

def main():
    conn = psycopg2.connect(
        host="34.56.137.172",
        database="gemini_chatbot_database",
        user="postgres",
        password=os.getenv("DB_PASS", "Delricht2017!")
    )
    conn.autocommit = True
    cursor = conn.cursor()

    # Check current connections
    cursor.execute("""
        SELECT count(*) FROM pg_stat_activity
        WHERE datname = 'gemini_chatbot_database'
    """)
    total = cursor.fetchone()[0]
    print(f"Total connections: {total}")

    # Get connection details
    cursor.execute("""
        SELECT pid, usename, application_name, client_addr, state,
               query_start, state_change
        FROM pg_stat_activity
        WHERE datname = 'gemini_chatbot_database'
        ORDER BY state_change DESC
        LIMIT 20
    """)
    print("\nActive connections:")
    for row in cursor.fetchall():
        print(f"  PID: {row[0]}, User: {row[1]}, App: {row[2]}, State: {row[4]}")

    # Terminate idle connections older than 5 minutes
    print("\nTerminating idle connections...")
    cursor.execute("""
        SELECT pg_terminate_backend(pid)
        FROM pg_stat_activity
        WHERE datname = 'gemini_chatbot_database'
          AND state = 'idle'
          AND pid <> pg_backend_pid()
          AND state_change < NOW() - INTERVAL '5 minutes'
    """)
    terminated = cursor.rowcount
    print(f"Terminated {terminated} idle connections")

    # Check remaining
    cursor.execute("""
        SELECT count(*) FROM pg_stat_activity
        WHERE datname = 'gemini_chatbot_database'
    """)
    remaining = cursor.fetchone()[0]
    print(f"\nConnections remaining: {remaining}")

    cursor.close()
    conn.close()

if __name__ == "__main__":
    main()
