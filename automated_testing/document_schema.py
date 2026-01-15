#!/usr/bin/env python3
"""Document actual database schema to avoid column errors"""

from db_helper import get_db

def get_table_schema(table_name):
    """Get actual column names and types for a table"""
    db = get_db()
    
    schema = db.execute_query("""
        SELECT column_name, data_type, is_nullable
        FROM information_schema.columns
        WHERE table_name = %s
        ORDER BY ordinal_position
    """, (table_name,))
    
    return schema

def document_all_tables():
    """Document all important tables"""
    db = get_db()
    
    # Get list of tables
    tables = db.execute_query("""
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = 'public'
        AND table_type = 'BASE TABLE'
        ORDER BY table_name
    """)
    
    print("=" * 80)
    print("📚 DATABASE SCHEMA DOCUMENTATION")
    print("=" * 80)
    print(f"Database: gemini_chatbot_database")
    print(f"Total tables: {len(tables)}")
    print()
    
    # Focus on key tables
    key_tables = [
        'conversation_context',
        'chat_logs',
        'prescreening_sessions',
        'trial_investigators',
        'clinical_trials',
        'trial_criteria',
        'location_site_mappings',
        'site_coordinators',
        'patient_contact_info'
    ]
    
    for table_name in key_tables:
        print(f"📋 {table_name}")
        print("-" * 80)
        
        schema = get_table_schema(table_name)
        
        if schema:
            print(f"   Columns ({len(schema)}):")
            for col in schema:
                nullable = "NULL" if col['is_nullable'] == 'YES' else "NOT NULL"
                print(f"   • {col['column_name']:30} {col['data_type']:20} {nullable}")
        else:
            print(f"   ❌ Table not found")
        
        print()
    
    print("=" * 80)

if __name__ == "__main__":
    document_all_tables()
