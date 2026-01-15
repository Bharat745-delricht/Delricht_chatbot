"""
Database-based debug logging for when Cloud Run logs don't work
"""

from core.database import db
from datetime import datetime
import json

def log_debug(session_id: str, event: str, data: dict = None):
    """Log debug info to database for analysis"""
    try:
        db.execute_update("""
            INSERT INTO debug_logs
            (session_id, event, data, created_at)
            VALUES (%s, %s, %s, NOW())
        """, (session_id, event, json.dumps(data) if data else '{}'))
    except:
        # If table doesn't exist, fail silently
        pass

# Create table if doesn't exist
def init_debug_table():
    try:
        db.execute_update("""
            CREATE TABLE IF NOT EXISTS debug_logs (
                id SERIAL PRIMARY KEY,
                session_id VARCHAR(100),
                event VARCHAR(200),
                data TEXT,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)
    except:
        pass
