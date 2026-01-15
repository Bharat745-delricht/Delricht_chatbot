#!/usr/bin/env python3
"""
Standardized Database Access Helper
Ensures consistent DB connection across all scripts
"""

import os
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

def load_env():
    """Load environment variables from .env file"""
    env_path = Path(__file__).parent.parent / '.env'
    
    if not env_path.exists():
        print(f"⚠️  Warning: .env file not found at {env_path}")
        return False
    
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                os.environ[key] = value
    
    return True

def get_db():
    """Get database instance with environment loaded"""
    # Load environment variables
    load_env()
    
    # Verify critical variables are set
    required = ['DB_PASS', 'DB_HOST', 'DB_NAME', 'DB_USER']
    missing = [var for var in required if not os.getenv(var)]
    
    if missing:
        print(f"❌ Missing environment variables: {', '.join(missing)}")
        print(f"   Check .env file")
        sys.exit(1)
    
    # Import and return db instance
    from core.database import db
    return db

def validate_connection():
    """Test database connection"""
    db = get_db()
    
    try:
        result = db.execute_query("SELECT 1 as test")
        if result and result[0]['test'] == 1:
            print("✅ Database connection successful")
            return True
    except Exception as e:
        print(f"❌ Database connection failed: {e}")
        return False

if __name__ == "__main__":
    # Test when run directly
    print("Testing database helper...")
    print()
    
    load_env()
    print(f"✓ Environment loaded from .env")
    print(f"  DB_HOST: {os.getenv('DB_HOST')}")
    print(f"  DB_NAME: {os.getenv('DB_NAME')}")
    print(f"  DB_USER: {os.getenv('DB_USER')}")
    print(f"  DB_PASS: {'*' * len(os.getenv('DB_PASS', ''))}")
    print()
    
    validate_connection()
