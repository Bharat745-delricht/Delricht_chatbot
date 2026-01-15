"""Minimal FastAPI application for Clinical Trials Chatbot"""
import os
import logging
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

# Load environment variables
load_dotenv()

# Import routes
from api.routes import chat, migration
from api.routes.dashboard import dashboard_router
from api.routes import gemini_chat
from api.routes import gemini_parse
from api.routes.visit_notifications import router as visit_notifications_router

# Testing new routes one at a time
from api.routes.protocols_unified import router as protocols_router
from api.routes.protocol_comparison import router as protocol_comparison_router
# from api.routes.bigquery_natural_language import router as bigquery_router
# from api.routes.visit_mappings import router as visit_mappings_router
from api.routes.site_coordinators import router as site_coordinators_router
from api.routes.sheets_export import router as sheets_export_router
from api.routes.sms_webhook import router as sms_router
from api.routes.reschedule_web_chat import router as reschedule_web_chat_router
from api.routes.trigger_reschedule_sms import router as trigger_reschedule_sms_router
from api.routes.lead_campaigns import router as lead_campaigns_router
from api.routes.crio_session_sync import router as crio_session_router
from api.routes.deployment_verification import router as deployment_verification_router
from api.routes.scheduled_reports import router as scheduled_reports_router

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

# Create FastAPI app
app = FastAPI(
    title="Clinical Trials Chatbot API",
    version="2.0.0",
    description="Simplified refactored version"
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Define API routes first
@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy"}

@app.get("/health/crio")
async def crio_health():
    """Check CRIO proxy service connectivity"""
    import requests
    try:
        # Test proxy service health
        proxy_url = "https://scheduling-dashboard-proxy-480267397633.us-central1.run.app"
        response = requests.get(f"{proxy_url}/health", timeout=10)

        if response.status_code == 200:
            proxy_data = response.json()
            return {
                "status": "healthy",
                "message": "CRIO proxy service is operational",
                "proxy": proxy_data,
                "note": "Patient creation and appointments use bearer token authentication via proxy"
            }
        else:
            return {
                "status": "degraded",
                "message": f"Proxy service returned {response.status_code}",
                "proxy_url": proxy_url
            }
    except Exception as e:
        return {
            "status": "unhealthy",
            "message": f"Cannot reach CRIO proxy service: {str(e)}",
            "error_type": type(e).__name__
        }

@app.get("/test/crio-sites")
async def test_crio_sites():
    """Test CRIO API by fetching sites list"""
    import requests
    try:
        proxy_url = "https://scheduling-dashboard-proxy-480267397633.us-central1.run.app"
        response = requests.get(f"{proxy_url}/crio/production/sites", timeout=30)

        if response.status_code == 200:
            sites_data = response.json()
            sites = sites_data.get('message', [])
            return {
                "success": True,
                "message": f"Successfully retrieved {len(sites)} sites from CRIO",
                "sample_sites": sites[:3] if sites else [],
                "total_sites": len(sites)
            }
        else:
            return {
                "success": False,
                "message": f"Failed to fetch sites: {response.status_code}",
                "response": response.text[:200]
            }
    except Exception as e:
        return {
            "success": False,
            "message": f"Error testing CRIO API: {str(e)}",
            "error_type": type(e).__name__
        }

@app.get("/api")
async def api_root():
    """API root endpoint"""
    return {"message": "Clinical Trials Chatbot API", "version": "2.0.0"}

@app.get("/favicon.ico")
async def favicon():
    """Prevent favicon.ico 404 errors"""
    from fastapi.responses import Response
    return Response(status_code=204)

@app.get("/debug/document-ai")
async def debug_document_ai():
    """Test Document AI configuration and imageless mode capabilities"""
    from core.services.production_document_processor import ProductionDocumentProcessor
    
    try:
        processor = ProductionDocumentProcessor()
        
        debug_info = {
            "document_ai_available": not processor.fallback_mode,
            "client_initialized": processor.client is not None,
            "project_id": processor.project_id,
            "location": processor.location, 
            "processor_id": processor.processor_id,
            "stats": processor.stats,
            "tiered_system_deployed": True,
            "deployment_time": "2025-08-14T15:00:00Z"
        }
        
        # Test processor availability
        if processor.client:
            try:
                processor_name = await processor._get_or_create_processor()
                debug_info["processor_name"] = processor_name
                debug_info["processor_accessible"] = processor_name is not None
            except Exception as e:
                debug_info["processor_error"] = str(e)
                debug_info["processor_accessible"] = False
        
        return debug_info
        
    except Exception as e:
        return {"error": str(e), "document_ai_available": False}

@app.get("/debug/test-imageless")
async def test_imageless_mode():
    """Test imageless mode configuration by simulating tiered processing"""
    from core.services.production_document_processor import ProductionDocumentProcessor
    try:
        from google.cloud import documentai_v1 as documentai
    except ImportError:
        return {"error": "Document AI not available", "imageless_supported": False}
    
    try:
        processor = ProductionDocumentProcessor()
        
        if processor.fallback_mode or not processor.client:
            return {"error": "Document AI not initialized", "imageless_supported": False}
        
        # Get processor info
        processor_name = await processor._get_or_create_processor()
        if not processor_name:
            return {"error": "No processor available", "imageless_supported": False}
        
        # Test configurations that would be used in tiered approach
        test_results = {
            "processor_name": processor_name,
            "standard_config": "No process_options (Tier 1)",
            "imageless_config": {
                "enable_native_pdf_parsing": True,
                "enable_image_quality_scores": False,
                "enable_math_ocr": False,  # Key for imageless mode
                "compute_style_info": False,
                "enable_selection_mark_detection": False
            },
            "tiered_system_ready": True,
            "expected_behavior": {
                "tier_1": "Standard Document AI (15-page limit)",
                "tier_2": "Imageless Document AI (30-page limit)",
                "tier_3": "PyPDF2 fallback (unlimited pages)"
            }
        }
        
        return test_results
        
    except Exception as e:
        return {"error": str(e), "imageless_supported": False}

@app.get("/debug") 
async def debug_info():
    """Debug endpoint to verify deployed code version and fixes"""
    import datetime
    from core.chat.answer_parser import AnswerParser
    
    # Test location parsing fix
    parser = AnswerParser()
    test_location = parser.parse_location("I'm in Tulsa")
    
    # Check if hardcoded trial ID fix is present
    try:
        from core.conversation.gemini_conversation_manager import GeminiConversationManager
        # Check if the fix is in the code by looking for context-based trial selection
        import inspect
        source = inspect.getsource(GeminiConversationManager._start_prescreening_with_explanations)
        has_trial_id_fix = "last_shown_trials" in source
    except:
        has_trial_id_fix = False
    
    # Test database connection
    db_connection_status = "unknown"
    try:
        from core.database import Database
        db_instance = Database()
        db_connection_status = "config_loaded"
        # Try a simple query
        result = db_instance.execute_query("SELECT 1 as test_value")
        if result and len(result) > 0:
            db_connection_status = "connected"
        else:
            db_connection_status = "empty_result"
    except Exception as e:
        db_connection_status = f"error: {str(e)}"
    
    # Test database schema
    tables_status = {}
    try:
        # Check if required tables exist
        table_checks = [
            ("chat_logs", "SELECT COUNT(*) FROM chat_logs LIMIT 1"),
            ("conversation_context", "SELECT COUNT(*) FROM conversation_context LIMIT 1"),
            ("prescreening_sessions", "SELECT COUNT(*) FROM prescreening_sessions LIMIT 1"),
            ("clinical_trials", "SELECT COUNT(*) FROM clinical_trials LIMIT 1")
        ]
        
        for table_name, query in table_checks:
            try:
                result = db_instance.execute_query(query)
                tables_status[table_name] = "exists" if result else "empty"
            except Exception as e:
                tables_status[table_name] = f"error: {str(e)}"
    except Exception as e:
        tables_status["error"] = str(e)
    
    # Investigation queries for timestamp debugging
    investigation_results = {}
    try:
        # Check recent chat_logs with timestamps
        recent_logs = db_instance.execute_query("""
            SELECT session_id, user_id, timestamp, 
                   CASE 
                     WHEN timestamp IS NULL THEN 'NULL'
                     ELSE timestamp::text
                   END as timestamp_status
            FROM chat_logs 
            ORDER BY id DESC 
            LIMIT 5
        """)
        investigation_results["recent_chat_logs"] = recent_logs
        
        # Check session_id patterns
        session_patterns = db_instance.execute_query("""
            SELECT 
                CASE 
                    WHEN session_id IS NULL THEN 'NULL'
                    WHEN session_id LIKE 'session_%' THEN 'session_prefix'
                    WHEN session_id ~ '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$' THEN 'uuid_format'
                    ELSE 'other'
                END as session_type,
                COUNT(*) as count
            FROM chat_logs
            GROUP BY session_type
            ORDER BY count DESC
        """)
        investigation_results["session_id_patterns"] = session_patterns
        
        # Check timestamp status across all records
        timestamp_status = db_instance.execute_query("""
            SELECT 
                COUNT(*) as total_records,
                COUNT(timestamp) as records_with_timestamp,
                COUNT(*) - COUNT(timestamp) as records_without_timestamp
            FROM chat_logs
        """)
        investigation_results["timestamp_status"] = timestamp_status
        
        # Check table schema for timestamp fields
        schema_info = db_instance.execute_query("""
            SELECT column_name, data_type, is_nullable, column_default
            FROM information_schema.columns 
            WHERE table_name = 'chat_logs' 
            AND column_name IN ('timestamp', 'created_at', 'updated_at')
            ORDER BY column_name
        """)
        investigation_results["timestamp_columns"] = schema_info
        
    except Exception as e:
        investigation_results["error"] = str(e)

    # Test email service configuration
    email_status = {}
    try:
        from core.services.email_service import email_service
        from config import settings
        email_status["provider"] = email_service.provider
        email_status["from_email"] = email_service.from_email
        email_status["dashboard_email"] = email_service.dashboard_email
        email_status["sendgrid_initialized"] = email_service.sendgrid is not None
        email_status["sendgrid_api_key_present"] = bool(settings.SENDGRID_API_KEY)
        email_status["sendgrid_api_key_length"] = len(settings.SENDGRID_API_KEY) if settings.SENDGRID_API_KEY else 0
        email_status["email_provider_setting"] = settings.EMAIL_PROVIDER
    except Exception as e:
        email_status["error"] = str(e)

    return {
        "deployment_time": datetime.datetime.now().isoformat(),
        "version": "2.0.0",
        "location_parsing_test": {
            "input": "I'm in Tulsa",
            "output": test_location,
            "expected": "Tulsa",
            "fix_working": test_location == "Tulsa"
        },
        "trial_id_fix_present": has_trial_id_fix,
        "environment": os.getenv("ENV", "unknown"),
        "database_host": os.getenv("DB_HOST", "unknown"),
        "database_connection": db_connection_status,
        "k_service": os.getenv("K_SERVICE", "not_set"),
        "instance_connection_name": os.getenv("INSTANCE_CONNECTION_NAME", "not_set"),
        "investigation": investigation_results,
        "database_tables": tables_status,
        "email_service": email_status
    }

@app.get("/debug/schema")
async def debug_schema():
    """Debug database schema and session_id relationships (legacy)"""
    from core.database import Database
    
    try:
        db_instance = Database()
        debug_info = {}
        
        # Check session_id formats in each table
        session_id_samples = {}
        
        tables_to_check = [
            "chat_logs",
            "conversation_context", 
            "prescreening_sessions"
        ]
        
        for table in tables_to_check:
            try:
                query = f"SELECT session_id, LENGTH(session_id), LEFT(session_id, 10) as prefix FROM {table} LIMIT 5"
                result = db_instance.execute_query(query)
                session_id_samples[table] = result
            except Exception as e:
                session_id_samples[table] = f"error: {str(e)}"
        
        debug_info["session_id_samples"] = session_id_samples
        
        # Check JOIN compatibility
        join_stats = {}
        
        # Check chat_logs without conversation_context
        try:
            query = """
                SELECT COUNT(*) as chat_logs_without_context
                FROM chat_logs cl 
                LEFT JOIN conversation_context cc ON cl.session_id = cc.session_id
                WHERE cc.session_id IS NULL
            """
            result = db_instance.execute_query(query)
            join_stats["chat_logs_without_context"] = result[0]["chat_logs_without_context"] if result else 0
        except Exception as e:
            join_stats["chat_logs_without_context"] = f"error: {str(e)}"
        
        # Check chat_logs without prescreening_sessions
        try:
            query = """
                SELECT COUNT(*) as chat_logs_without_prescreening
                FROM chat_logs cl
                LEFT JOIN prescreening_sessions ps ON cl.session_id = ps.session_id  
                WHERE ps.session_id IS NULL
            """
            result = db_instance.execute_query(query)
            join_stats["chat_logs_without_prescreening"] = result[0]["chat_logs_without_prescreening"] if result else 0
        except Exception as e:
            join_stats["chat_logs_without_prescreening"] = f"error: {str(e)}"
        
        # Check total counts
        try:
            query = "SELECT COUNT(*) as total FROM chat_logs"
            result = db_instance.execute_query(query)
            join_stats["total_chat_logs"] = result[0]["total"] if result else 0
        except Exception as e:
            join_stats["total_chat_logs"] = f"error: {str(e)}"
            
        try:
            query = "SELECT COUNT(*) as total FROM conversation_context"
            result = db_instance.execute_query(query)
            join_stats["total_conversation_context"] = result[0]["total"] if result else 0
        except Exception as e:
            join_stats["total_conversation_context"] = f"error: {str(e)}"
            
        try:
            query = "SELECT COUNT(*) as total FROM prescreening_sessions"
            result = db_instance.execute_query(query)
            join_stats["total_prescreening_sessions"] = result[0]["total"] if result else 0
        except Exception as e:
            join_stats["total_prescreening_sessions"] = f"error: {str(e)}"
        
        debug_info["join_stats"] = join_stats
        
        # Check chat_logs table structure
        try:
            query = "SELECT * FROM chat_logs LIMIT 3"
            result = db_instance.execute_query(query)
            debug_info["chat_logs_sample"] = result
        except Exception as e:
            debug_info["chat_logs_sample"] = f"error: {str(e)}"
        
        return debug_info
        
    except Exception as e:
        return {"error": str(e)}

@app.get("/api/schema")
async def get_current_schema():
    """Get current database schema dynamically"""
    from core.schema_introspection import schema_introspector
    
    try:
        return schema_introspector.get_complete_schema()
    except Exception as e:
        return {"error": f"Failed to introspect schema: {str(e)}"}

@app.get("/api/schema/markdown")
async def get_schema_markdown():
    """Get current database schema as markdown documentation"""
    from core.schema_introspection import schema_introspector
    
    try:
        markdown = schema_introspector.generate_markdown_schema()
        return {"markdown": markdown}
    except Exception as e:
        return {"error": f"Failed to generate schema markdown: {str(e)}"}

@app.get("/api/schema/tables")
async def get_tables():
    """Get list of all tables with basic info"""
    from core.schema_introspection import schema_introspector
    
    try:
        return {"tables": schema_introspector.get_all_tables()}
    except Exception as e:
        return {"error": f"Failed to get tables: {str(e)}"}

@app.get("/api/schema/tables/{table_name}")
async def get_table_details(table_name: str):
    """Get detailed information about a specific table"""
    from core.schema_introspection import schema_introspector
    
    try:
        return {
            "table_name": table_name,
            "columns": schema_introspector.get_table_columns(table_name),
            "constraints": schema_introspector.get_table_constraints(table_name),
            "indexes": schema_introspector.get_table_indexes(table_name)
        }
    except Exception as e:
        return {"error": f"Failed to get table details: {str(e)}"}

@app.get("/api/schema/tables/{table_name}/columns-simple")
async def get_table_columns_simple(table_name: str):
    """Simple endpoint to get just column names for a table"""
    from core.database import db
    
    try:
        columns = db.execute_query("""
            SELECT column_name, data_type, is_nullable 
            FROM information_schema.columns 
            WHERE table_schema = 'public' AND table_name = %s
            ORDER BY ordinal_position
        """, (table_name,))
        
        return {
            "table_name": table_name,
            "columns": columns or []
        }
    except Exception as e:
        return {"error": f"Failed to get columns for {table_name}: {str(e)}"}

@app.get("/debug/schema-test-detailed")
async def debug_schema_test_detailed():
    """Test each schema introspection function individually for clinical_trials"""
    from core.schema_introspection import schema_introspector
    
    results = {}
    
    # Test each function individually
    functions_to_test = [
        ("get_table_columns", lambda: schema_introspector.get_table_columns('clinical_trials')),
        ("get_table_constraints", lambda: schema_introspector.get_table_constraints('clinical_trials')),
        ("get_table_indexes", lambda: schema_introspector.get_table_indexes('clinical_trials')),
    ]
    
    for func_name, func in functions_to_test:
        try:
            result = func()
            results[func_name] = {
                "success": True,
                "result_length": len(result) if result else 0,
                "sample": result[0] if result and len(result) > 0 else None
            }
        except Exception as e:
            results[func_name] = {
                "success": False,
                "error": str(e),
                "error_type": type(e).__name__
            }
    
    return results

@app.get("/debug/schema-test-simple")
async def debug_schema_test_simple():
    """Test basic schema introspection without row counts"""
    from core.schema_introspection import schema_introspector
    
    try:
        # Test just getting tables without full details
        tables = schema_introspector.get_all_tables()
        
        # Test getting database info
        db_info = schema_introspector._get_database_info()
        
        # Test getting columns for one table
        columns = schema_introspector.get_table_columns('clinical_trials')
        
        return {
            "tables_count": len(tables),
            "first_table": tables[0] if tables else None,
            "database_info": db_info,
            "clinical_trials_columns": len(columns)
        }
    except Exception as e:
        return {
            "error": str(e),
            "error_type": type(e).__name__
        }

@app.get("/debug/schema-test")
async def debug_schema_test():
    """Debug schema introspection queries individually"""
    from core.database import db
    
    debug_results = {}
    
    # Test individual queries to isolate the issue
    test_queries = {
        "version": "SELECT version()",
        "current_database": "SELECT current_database()",
        "current_user": "SELECT current_user",
        "tables": """
            SELECT table_name, table_type
            FROM information_schema.tables 
            WHERE table_schema = 'public' 
            AND table_type = 'BASE TABLE'
            ORDER BY table_name
            LIMIT 3
        """,
        "constraints": """
            SELECT tc.constraint_name, tc.constraint_type
            FROM information_schema.table_constraints tc
            WHERE tc.table_schema = 'public' 
            AND tc.table_name = 'clinical_trials'
            LIMIT 3
        """
    }
    
    for key, query in test_queries.items():
        try:
            result = db.execute_query(query)
            debug_results[key] = {
                "success": True,
                "result_type": type(result).__name__,
                "result_length": len(result) if result else 0,
                "first_item_type": type(result[0]).__name__ if result and len(result) > 0 else None,
                "first_item": result[0] if result and len(result) > 0 else None
            }
        except Exception as e:
            debug_results[key] = {
                "success": False,
                "error": str(e),
                "error_type": type(e).__name__
            }
    
    return debug_results

@app.get("/debug/session-search/{session_id}")
async def debug_session_search(session_id: str):
    """Search for session across all possible locations"""
    from core.database import db
    
    results = {"session_id": session_id, "found_in": []}
    
    # Search in multiple tables and with LIKE patterns
    searches = [
        ("chat_logs_exact", f"SELECT COUNT(*) as count FROM chat_logs WHERE session_id = '{session_id}'"),
        ("chat_logs_like", f"SELECT COUNT(*) as count FROM chat_logs WHERE session_id LIKE '%{session_id}%'"),
        ("conversation_context_exact", f"SELECT COUNT(*) as count FROM conversation_context WHERE session_id = '{session_id}'"),
        ("conversation_context_like", f"SELECT COUNT(*) as count FROM conversation_context WHERE session_id LIKE '%{session_id}%'"),
        ("prescreening_sessions_exact", f"SELECT COUNT(*) as count FROM prescreening_sessions WHERE session_id = '{session_id}'"),
        ("prescreening_sessions_like", f"SELECT COUNT(*) as count FROM prescreening_sessions WHERE session_id LIKE '%{session_id}%'"),
        ("prescreening_answers_exact", f"SELECT COUNT(*) as count FROM prescreening_answers WHERE session_id = '{session_id}'"),
        ("prescreening_answers_like", f"SELECT COUNT(*) as count FROM prescreening_answers WHERE session_id LIKE '%{session_id}%'"),
    ]
    
    for search_name, query in searches:
        try:
            result = db.execute_query(query)
            count = result[0]["count"] if result else 0
            if count > 0:
                results["found_in"].append({"location": search_name, "count": count})
        except Exception as e:
            results["found_in"].append({"location": search_name, "error": str(e)})
    
    return results

@app.get("/debug/session-simple/{session_id}")
async def debug_session_simple(session_id: str):
    """Simple session debug to check if session exists"""
    from core.database import db
    
    try:
        # Check if session exists in chat_logs
        chat_count = db.execute_query("""
            SELECT COUNT(*) as count FROM chat_logs WHERE session_id = %s
        """, (session_id,))
        
        # Check conversation context
        context_exists = db.execute_query("""
            SELECT COUNT(*) as count FROM conversation_context WHERE session_id = %s
        """, (session_id,))
        
        # Check prescreening
        prescreening_count = db.execute_query("""
            SELECT COUNT(*) as count FROM prescreening_sessions WHERE session_id = %s
        """, (session_id,))
        
        return {
            "session_id": session_id,
            "chat_messages": chat_count[0]["count"] if chat_count else 0,
            "has_context": context_exists[0]["count"] > 0 if context_exists else False,
            "prescreening_sessions": prescreening_count[0]["count"] if prescreening_count else 0
        }
        
    except Exception as e:
        return {"error": str(e), "session_id": session_id}

@app.get("/debug/session/{session_id}")
async def debug_session(session_id: str):
    """Debug a specific session to analyze conversation flow and issues"""
    from core.database import db
    
    session_analysis = {
        "session_id": session_id,
        "chat_logs": [],
        "conversation_context": None,
        "prescreening_sessions": [],
        "prescreening_answers": [],
        "analysis": {}
    }
    
    try:
        # Get chat logs for this session
        chat_logs = db.execute_query("""
            SELECT id, session_id, user_id, user_message, bot_response, timestamp,
                   intent_detected, confidence_score, context_data, focus_condition, focus_location,
                   processing_time_ms
            FROM chat_logs 
            WHERE session_id = %s 
            ORDER BY timestamp ASC
        """, (session_id,))
        session_analysis["chat_logs"] = chat_logs or []
        
        # Get conversation context
        context = db.execute_query("""
            SELECT session_id, focus_condition, focus_location, current_state,
                   context_data, focus_trial_id, active, created_at, updated_at
            FROM conversation_context 
            WHERE session_id = %s
        """, (session_id,))
        session_analysis["conversation_context"] = context[0] if context else None
        
        # Get prescreening sessions
        prescreening_sessions = db.execute_query("""
            SELECT id, session_id, trial_id, condition, status, answered_questions,
                   total_questions, started_at, completed_at, eligible, eligibility_result
            FROM prescreening_sessions 
            WHERE session_id = %s 
            ORDER BY started_at DESC
        """, (session_id,))
        session_analysis["prescreening_sessions"] = prescreening_sessions or []
        
        # Get prescreening answers
        if prescreening_sessions:
            prescreening_answers = db.execute_query("""
                SELECT pa.id, pa.session_id, pa.criterion_id, pa.user_response,
                       pa.processed_response, pa.is_eligible, pa.answered_at,
                       tc.criterion_text, tc.criterion_type, tc.category
                FROM prescreening_answers pa
                LEFT JOIN trial_criteria tc ON pa.criterion_id = tc.id
                WHERE pa.session_id = %s 
                ORDER BY pa.answered_at ASC
            """, (session_id,))
            session_analysis["prescreening_answers"] = prescreening_answers or []
        
        # Analysis
        session_analysis["analysis"] = {
            "total_messages": len(session_analysis["chat_logs"]),
            "prescreening_sessions_count": len(session_analysis["prescreening_sessions"]),
            "prescreening_answers_count": len(session_analysis["prescreening_answers"]),
            "current_state": session_analysis["conversation_context"]["current_state"] if session_analysis["conversation_context"] else None,
            "last_activity": session_analysis["chat_logs"][-1]["timestamp"] if session_analysis["chat_logs"] else None,
            "prescreening_status": prescreening_sessions[0]["status"] if prescreening_sessions else None
        }
        
        return session_analysis
        
    except Exception as e:
        return {
            "error": f"Failed to analyze session {session_id}: {str(e)}",
            "session_id": session_id
        }

# Include routers
app.include_router(chat.router, prefix="/api", tags=["chat"])
app.include_router(gemini_chat.router, prefix="/api/gemini", tags=["gemini-chat"])
app.include_router(gemini_parse.router, prefix="/api/gemini", tags=["gemini-parse"])
app.include_router(protocols_router, prefix="/api/protocols", tags=["protocols"])
app.include_router(protocol_comparison_router, prefix="/api/protocols/comparison", tags=["protocol-comparison"])
app.include_router(migration.router, prefix="/api/migration", tags=["migration"])
app.include_router(dashboard_router, prefix="/api", tags=["dashboard"])
# app.include_router(bigquery_router, tags=["bigquery-analytics"])
# app.include_router(visit_mappings_router, tags=["visit-mappings"])
app.include_router(site_coordinators_router, tags=["site-coordinators"])
app.include_router(visit_notifications_router, tags=["visit-notifications"])
app.include_router(sheets_export_router, tags=["sheets-export"])
app.include_router(sms_router, tags=["sms"])
app.include_router(reschedule_web_chat_router, tags=["reschedule-web-chat"])
app.include_router(trigger_reschedule_sms_router, tags=["reschedule-trigger"])
app.include_router(lead_campaigns_router, tags=["lead-campaigns"])
app.include_router(crio_session_router, tags=["crio-session"])
app.include_router(deployment_verification_router, tags=["deployment-verification"])
app.include_router(scheduled_reports_router, tags=["scheduled-reports"])

# Mount static files last (after all routes are defined)
if os.path.exists("static"):
    app.mount("/static", StaticFiles(directory="static"), name="static")
    # Mount dashboard at root for easy access - this catches all remaining routes  
    app.mount("/", StaticFiles(directory="static/dashboard", html=True), name="dashboard")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)