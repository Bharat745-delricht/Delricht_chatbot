"""Database connection module with connection pooling"""
import os
import logging
import psycopg2
from psycopg2 import pool
from psycopg2.extras import RealDictCursor
from contextlib import contextmanager
from typing import Optional, Dict, List, Any

logger = logging.getLogger(__name__)

class Database:
    """Database connection manager with connection pooling"""

    def __init__(self):
        self.connection_params = self._get_connection_params()
        self.connection_pool = None
        self._initialize_pool()

    def _get_connection_params(self) -> dict:
        """Get database connection parameters"""
        # Use direct IP connection for both Cloud Run and local development
        # Cloud SQL socket connection was causing issues in Cloud Run
        return {
            'dbname': os.getenv('DB_NAME', 'gemini_chatbot_database'),
            'user': os.getenv('DB_USER', 'postgres'),
            'password': os.getenv('DB_PASS'),
            'host': os.getenv('DB_HOST', '34.56.137.172'),  # Production DB IP
            'port': os.getenv('DB_PORT', '5432'),
            'cursor_factory': RealDictCursor,
            # Connection timeout: Fail fast if can't connect within 10 seconds
            'connect_timeout': 10,
            # Query timeout: Cancel queries taking longer than 30 seconds
            'options': '-c statement_timeout=30000'
        }

    def _initialize_pool(self):
        """Initialize connection pool"""
        try:
            # Create a threaded connection pool
            # minconn: Minimum connections to keep open (always warm)
            # maxconn: Maximum connections allowed (prevent overwhelming DB)
            # Note: PostgreSQL max_connections = 50, so we use max 20 to leave room for scaling
            self.connection_pool = pool.ThreadedConnectionPool(
                minconn=3,  # Keep 3 connections always warm
                maxconn=20,  # Max 20 connections (DB limit is 50, allows room for multiple instances)
                **self.connection_params
            )
            logger.info("Database connection pool initialized (min=3, max=20)")
        except Exception as e:
            logger.error(f"Failed to initialize connection pool: {str(e)}")
            raise

    def _is_connection_alive(self, conn) -> bool:
        """Check if a connection is still alive and usable"""
        try:
            # Simple query to test connection
            with conn.cursor() as cursor:
                cursor.execute("SELECT 1")
            return True
        except Exception:
            return False

    @contextmanager
    def get_connection(self):
        """Get a database connection from the pool with stale connection handling"""
        conn = None
        conn_is_bad = False
        try:
            # Get connection from pool (blocks if all connections are in use)
            conn = self.connection_pool.getconn()

            # Validate connection is still alive (handles server-side disconnects)
            if not self._is_connection_alive(conn):
                logger.warning("Got stale connection from pool, discarding and getting fresh one")
                conn_is_bad = True
                self.connection_pool.putconn(conn, close=True)  # Discard bad connection
                conn = self.connection_pool.getconn()  # Get fresh connection
                conn_is_bad = False

            yield conn
            conn.commit()
        except pool.PoolError as e:
            logger.error(f"Connection pool error: {str(e)}")
            raise
        except (psycopg2.OperationalError, psycopg2.InterfaceError) as e:
            # Handle connection errors (SSL closed, connection reset, etc.)
            conn_is_bad = True
            logger.error(f"Connection error (will discard connection): {str(e)}")
            raise
        except Exception as e:
            if conn:
                try:
                    conn.rollback()
                except Exception:
                    conn_is_bad = True  # Can't rollback, connection is bad
            logger.error(f"Database error: {str(e)}")
            raise
        finally:
            if conn:
                # Return connection to pool, close if it's bad
                self.connection_pool.putconn(conn, close=conn_is_bad)

    def execute_query(self, query: str, params: Optional[tuple] = None, max_retries: int = 2) -> List[Dict[str, Any]]:
        """Execute a SELECT query and return results with automatic retry on connection errors"""
        last_error = None
        for attempt in range(max_retries + 1):
            try:
                with self.get_connection() as conn:
                    with conn.cursor() as cursor:
                        cursor.execute(query, params)
                        return cursor.fetchall()
            except (psycopg2.OperationalError, psycopg2.InterfaceError) as e:
                last_error = e
                if attempt < max_retries:
                    logger.warning(f"Query failed (attempt {attempt + 1}/{max_retries + 1}), retrying: {str(e)}")
                    continue
                raise
        raise last_error

    def execute_update(self, query: str, params: Optional[tuple] = None, max_retries: int = 2) -> int:
        """Execute an INSERT/UPDATE/DELETE query with automatic retry on connection errors"""
        last_error = None
        for attempt in range(max_retries + 1):
            try:
                with self.get_connection() as conn:
                    with conn.cursor() as cursor:
                        cursor.execute(query, params)
                        return cursor.rowcount
            except (psycopg2.OperationalError, psycopg2.InterfaceError) as e:
                last_error = e
                if attempt < max_retries:
                    logger.warning(f"Update failed (attempt {attempt + 1}/{max_retries + 1}), retrying: {str(e)}")
                    continue
                raise
        raise last_error

    def execute_insert_returning(self, query: str, params: Optional[tuple] = None, max_retries: int = 2) -> Optional[Dict[str, Any]]:
        """Execute an INSERT query with RETURNING clause with automatic retry"""
        last_error = None
        for attempt in range(max_retries + 1):
            try:
                with self.get_connection() as conn:
                    with conn.cursor() as cursor:
                        cursor.execute(query, params)
                        result = cursor.fetchone()
                        return result
            except (psycopg2.OperationalError, psycopg2.InterfaceError) as e:
                last_error = e
                if attempt < max_retries:
                    logger.warning(f"Insert failed (attempt {attempt + 1}/{max_retries + 1}), retrying: {str(e)}")
                    continue
                raise
        raise last_error

    def cleanup_idle_connections(self):
        """Clean up idle connections in the database (not the pool)"""
        try:
            # Terminate database connections that have been idle for >5 minutes
            # This is a safety mechanism to prevent connection exhaustion
            query = """
                SELECT pg_terminate_backend(pid)
                FROM pg_stat_activity
                WHERE datname = current_database()
                  AND state = 'idle'
                  AND state_change < now() - interval '5 minutes'
                  AND pid <> pg_backend_pid()
                  AND application_name NOT LIKE '%psql%'
            """
            with self.get_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(query)
                    result = cursor.fetchall()
                    terminated = sum(1 for r in result if r.get('pg_terminate_backend', False))
                    if terminated > 0:
                        logger.info(f"Cleaned up {terminated} idle database connections")
                    return terminated
        except Exception as e:
            logger.error(f"Error cleaning up idle connections: {str(e)}")
            return 0

    def close_all_connections(self):
        """Close all connections in the pool (call on shutdown)"""
        if self.connection_pool:
            self.connection_pool.closeall()
            logger.info("All database connections closed")

# Create a singleton instance
db = Database()
