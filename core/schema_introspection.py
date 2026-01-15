"""
Dynamic database schema introspection for Gemini Clinical Trials Chatbot.

This module provides functionality to dynamically detect and document the current
database schema instead of relying on hardcoded definitions.
"""

import logging
from typing import Dict, List, Any, Optional
from core.database import db

logger = logging.getLogger(__name__)

class SchemaIntrospector:
    """Dynamically introspect database schema"""
    
    def get_all_tables(self) -> List[Dict[str, Any]]:
        """Get all tables in the database with metadata"""
        query = """
        SELECT 
            t.table_name,
            t.table_type,
            obj_description(c.oid) as table_comment
        FROM information_schema.tables t
        LEFT JOIN pg_class c ON c.relname = t.table_name
        WHERE t.table_schema = 'public' 
        AND t.table_type = 'BASE TABLE'
        ORDER BY t.table_name;
        """
        return db.execute_query(query) or []
    
    def get_table_columns(self, table_name: str) -> List[Dict[str, Any]]:
        """Get column information for a specific table"""
        query = """
        SELECT 
            c.column_name,
            c.data_type,
            c.is_nullable,
            c.column_default,
            c.character_maximum_length,
            c.numeric_precision,
            c.numeric_scale,
            pgd.description as column_comment
        FROM information_schema.columns c
        LEFT JOIN pg_class pgc ON pgc.relname = c.table_name
        LEFT JOIN pg_description pgd ON pgd.objoid = pgc.oid 
            AND pgd.objsubid = c.ordinal_position
        WHERE c.table_schema = 'public' 
        AND c.table_name = %s
        ORDER BY c.ordinal_position;
        """
        return db.execute_query(query, (table_name,)) or []
    
    def get_table_constraints(self, table_name: str) -> List[Dict[str, Any]]:
        """Get constraints (primary keys, foreign keys, etc.) for a table"""
        query = """
        SELECT 
            tc.constraint_name,
            tc.constraint_type,
            kcu.column_name,
            ccu.table_name AS foreign_table_name,
            ccu.column_name AS foreign_column_name
        FROM information_schema.table_constraints tc
        LEFT JOIN information_schema.key_column_usage kcu 
            ON tc.constraint_name = kcu.constraint_name
        LEFT JOIN information_schema.constraint_column_usage ccu 
            ON ccu.constraint_name = tc.constraint_name
        WHERE tc.table_schema = 'public' 
        AND tc.table_name = %s
        ORDER BY tc.constraint_type, tc.constraint_name;
        """
        return db.execute_query(query, (table_name,)) or []
    
    def get_table_indexes(self, table_name: str) -> List[Dict[str, Any]]:
        """Get indexes for a specific table"""
        try:
            query = """
            SELECT 
                i.indexname,
                i.indexdef,
                i.indexdef LIKE '%UNIQUE%' as is_unique,
                i.indexdef LIKE '%vector%' as is_vector_index
            FROM pg_indexes i
            WHERE i.schemaname = 'public' 
            AND i.tablename = %s
            ORDER BY i.indexname;
            """
            result = db.execute_query(query, (table_name,))
            return result or []
        except Exception as e:
            logger.error(f"Error getting indexes for table {table_name}: {e}")
            return []
    
    def get_complete_schema(self) -> Dict[str, Any]:
        """Get complete database schema information"""
        schema = {
            "database_info": self._get_database_info(),
            "tables": {}
        }
        
        tables = self.get_all_tables()
        for table in tables:
            table_name = table['table_name']
            try:
                schema["tables"][table_name] = {
                    "table_info": table,
                    "columns": self.get_table_columns(table_name),
                    "constraints": self.get_table_constraints(table_name),
                    "indexes": self.get_table_indexes(table_name),
                    # Skip row count for now to avoid potential issues
                    "row_count": None
                }
            except Exception as e:
                logger.error(f"Error processing table {table_name}: {e}")
                schema["tables"][table_name] = {"error": str(e)}
        
        return schema
    
    def _get_database_info(self) -> Dict[str, Any]:
        """Get general database information"""
        queries = {
            "version": "SELECT version()",
            "current_database": "SELECT current_database()",
            "current_user": "SELECT current_user",
            "extensions": """
                SELECT extname, extversion 
                FROM pg_extension 
                WHERE extname IN ('vector', 'pgvector')
            """
        }
        
        info = {}
        for key, query in queries.items():
            try:
                result = db.execute_query(query)
                if key == "extensions":
                    info[key] = result or []
                else:
                    # Safely access first result - RealDictRow objects behave like dictionaries
                    if result and isinstance(result, list) and len(result) > 0:
                        first_result = result[0]
                        # RealDictRow objects have dict-like behavior - get the first value
                        if hasattr(first_result, 'values') and callable(getattr(first_result, 'values')):
                            values = list(first_result.values())
                            info[key] = values[0] if values else None
                        else:
                            info[key] = first_result
                    else:
                        info[key] = None
            except Exception as e:
                logger.error(f"Schema introspection error for {key}: {e}")
                logger.error(f"Query was: {query}")
                logger.error(f"Result was: {result}")
                info[key] = None
        
        return info
    
    def _get_table_row_count(self, table_name: str) -> Optional[int]:
        """Get approximate row count for a table"""
        try:
            # Use parameterized query for safety (though table name can't be parameterized)
            query = "SELECT COUNT(*) as count FROM information_schema.tables WHERE table_name = %s"
            result = db.execute_query(query, (table_name,))
            if result and isinstance(result, list) and len(result) > 0:
                # This just returns 1 if table exists, let's get actual count differently
                pass
            
            # For actual row count, we need to use the table name directly (sanitized)
            # Only allow alphanumeric and underscores to prevent SQL injection
            import re
            if re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', table_name):
                query = f"SELECT COUNT(*) as count FROM {table_name}"
                result = db.execute_query(query)
                if result and isinstance(result, list) and len(result) > 0:
                    return result[0]['count']
            
            return 0
        except Exception as e:
            logger.warning(f"Could not get row count for {table_name}: {e}")
            return None
    
    def generate_markdown_schema(self) -> str:
        """Generate markdown documentation of current schema"""
        schema = self.get_complete_schema()
        
        markdown = "# Database Schema (Auto-Generated)\n\n"
        markdown += f"**Generated**: {schema['database_info']['current_database']}\n"
        markdown += f"**Database Version**: {schema['database_info']['version']}\n"
        markdown += f"**Current User**: {schema['database_info']['current_user']}\n\n"
        
        # Extensions
        if schema['database_info']['extensions']:
            markdown += "## Extensions\n"
            for ext in schema['database_info']['extensions']:
                markdown += f"- **{ext['extname']}** v{ext['extversion']}\n"
            markdown += "\n"
        
        # Tables
        markdown += "## Tables\n\n"
        for table_name, table_data in schema['tables'].items():
            markdown += f"### `{table_name}`\n"
            
            # Table info
            row_count = table_data['row_count']
            if row_count is not None:
                markdown += f"**Rows**: {row_count:,}\n\n"
            
            # Columns
            markdown += "#### Columns\n"
            markdown += "| Column | Type | Nullable | Default |\n"
            markdown += "|--------|------|----------|----------|\n"
            
            for col in table_data['columns']:
                col_type = col['data_type']
                if col['character_maximum_length']:
                    col_type += f"({col['character_maximum_length']})"
                
                nullable = "✓" if col['is_nullable'] == 'YES' else "✗"
                default = col['column_default'] or ""
                
                markdown += f"| `{col['column_name']}` | {col_type} | {nullable} | {default} |\n"
            
            # Constraints
            constraints = table_data['constraints']
            if constraints:
                markdown += "\n#### Constraints\n"
                for constraint in constraints:
                    if constraint['constraint_type'] == 'PRIMARY KEY':
                        markdown += f"- **Primary Key**: `{constraint['column_name']}`\n"
                    elif constraint['constraint_type'] == 'FOREIGN KEY':
                        markdown += f"- **Foreign Key**: `{constraint['column_name']}` → `{constraint['foreign_table_name']}.{constraint['foreign_column_name']}`\n"
                    elif constraint['constraint_type'] == 'UNIQUE':
                        markdown += f"- **Unique**: `{constraint['column_name']}`\n"
            
            # Indexes
            indexes = table_data['indexes']
            if indexes:
                markdown += "\n#### Indexes\n"
                for idx in indexes:
                    idx_type = ""
                    if idx['is_unique']:
                        idx_type += " (UNIQUE)"
                    if idx['is_vector_index']:
                        idx_type += " (VECTOR)"
                    
                    markdown += f"- **{idx['indexname']}**{idx_type}\n"
            
            markdown += "\n"
        
        return markdown
    
    def generate_json_schema(self) -> Dict[str, Any]:
        """Generate JSON schema documentation"""
        return self.get_complete_schema()

# Global instance
schema_introspector = SchemaIntrospector()