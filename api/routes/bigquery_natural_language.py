"""
BigQuery Natural Language Query API
Converts natural language questions to SQL and executes them against CRIO BigQuery data
"""

import json
import re
import time
from typing import Dict, List, Any, Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from google.cloud import bigquery
from core.services.gemini_service import gemini_service
import logging

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/bigquery", tags=["BigQuery Analytics"])

# BigQuery client for crio-pipe project
bigquery_client = bigquery.Client(project="crio-pipe")

class NaturalLanguageRequest(BaseModel):
    question: str
    context: Optional[str] = None

class BigQueryExecuteRequest(BaseModel):
    query: str

class BigQueryResult(BaseModel):
    query: str
    data: List[Dict[str, Any]]
    rowCount: int
    executionTime: int
    visualization: Optional[Dict[str, Any]] = None

class NaturalLanguageQueryResult(BaseModel):
    userQuestion: str
    generatedSQL: str
    result: BigQueryResult
    insights: List[str]
    suggestedFollowUp: List[str]

# Schema context for Gemini AI
CRIO_SCHEMA_CONTEXT = """
You are querying a CRIO clinical trial management system database with the following key tables:

CALENDAR_APPOINTMENT table:
- calendar_appointment_key (primary key)
- start, end (datetime) - appointment start/end times
- name (string) - appointment name/type
- site_key (integer) - DelRicht site identifier
- study_key (integer) - clinical study identifier
- subject_key (integer) - patient/subject identifier
- study_visit_key (integer) - specific visit type
- status (integer) - appointment status
- type (integer) - appointment type (1=Recruitment, 2=Screening, 3=Baseline, etc.)

ORGANIZATION table:
- organization_key (primary key) 
- name (string) - organization name ("Delricht Research" is key 1194)

CALENDAR table:
- calendar_key (primary key)
- name (string) - calendar name
- site_key (integer) - site identifier
- user_key (integer) - coordinator user ID

Key DelRicht Sites (site_key):
- 2327: Atlanta General Medicine
- 3863: Atlanta Psychiatry  
- 2054: Atlanta Dermatology
- 1266: BR Dermatology
- 3957: BR Psychiatry
- 2693: CHS General Medicine
- 4886: Cincinnati General Medicine
- 3466: Charlotte General Medicine
- 1867: Dallas General Medicine
- 1305: Tulsa General Medicine

Date functions: Use DATE() for date comparisons, DATETIME_SUB/ADD for date arithmetic.
Time filtering: WHERE DATE(start) >= DATE_SUB(CURRENT_DATE(), INTERVAL 7 DAY) for "last week"

Generate ONLY the SQL query without explanation or markdown formatting.
"""

def get_site_key_from_name(site_name: str) -> Optional[int]:
    """Convert site names to site_key values"""
    site_mapping = {
        'atlanta': [2327, 3863, 2054],  # ATL Gen Med, Psych, Derm
        'atl': [2327, 3863, 2054],
        'atlanta gen med': [2327],
        'atlanta general medicine': [2327],
        'atlanta psych': [3863],
        'atlanta psychiatry': [3863], 
        'atlanta dermatology': [2054],
        'br': [1266, 3957],  # BR Derm, Psych
        'br dermatology': [1266],
        'br psychiatry': [3957],
        'chs': [2693],
        'chs general medicine': [2693],
        'cincinnati': [4886],
        'cin': [4886],
        'charlotte': [3466],
        'clt': [3466],
        'dallas': [1867],
        'dal': [1867],
        'tulsa': [1305],
    }
    
    site_name_lower = site_name.lower()
    for name_pattern, site_keys in site_mapping.items():
        if name_pattern in site_name_lower:
            return site_keys[0] if len(site_keys) == 1 else None
    
    return None

def enhance_query_with_context(question: str) -> str:
    """Add contextual information to the user's question"""
    enhanced = question
    
    # Add time context
    time_keywords = ['next week', 'last week', 'this week', 'this month', 'today']
    for keyword in time_keywords:
        if keyword in question.lower():
            if 'next week' in question.lower():
                enhanced += " (next 7 days from current date)"
            elif 'last week' in question.lower():
                enhanced += " (previous 7 days from current date)"
            elif 'this week' in question.lower():
                enhanced += " (current week)"
            elif 'this month' in question.lower():
                enhanced += " (current month)"
            break
    
    # Add visit type context
    visit_types = {
        'recruitment': 'type = 1',
        'screening': 'type = 2', 
        'baseline': 'type = 3',
    }
    
    for visit_type in visit_types:
        if visit_type in question.lower():
            enhanced += f" (filter by appointment {visit_types[visit_type]})"
            break
    
    return enhanced

async def generate_sql_from_question(question: str) -> str:
    """Use Gemini AI to convert natural language to SQL"""
    try:
        enhanced_question = enhance_query_with_context(question)
        
        prompt = f"""{CRIO_SCHEMA_CONTEXT}

User Question: {enhanced_question}

Generate a SQL query for BigQuery using the crio-pipe.crio_data dataset.
Return ONLY the SQL query without any explanation or markdown formatting."""

        response = await gemini_service.generate_text(prompt)
        
        # Clean up the response - remove markdown formatting if present
        sql_query = response.strip()
        if sql_query.startswith('```sql'):
            sql_query = sql_query[6:]
        if sql_query.startswith('```'):
            sql_query = sql_query[3:]
        if sql_query.endswith('```'):
            sql_query = sql_query[:-3]
        
        sql_query = sql_query.strip()
        
        # Add dataset prefix if not present
        if 'crio_data.' not in sql_query and 'crio-pipe.crio_data.' not in sql_query:
            sql_query = re.sub(
                r'\bFROM\s+(\w+)',
                r'FROM `crio-pipe.crio_data.\1`',
                sql_query,
                flags=re.IGNORECASE
            )
            sql_query = re.sub(
                r'\bJOIN\s+(\w+)',
                r'JOIN `crio-pipe.crio_data.\1`',
                sql_query,
                flags=re.IGNORECASE
            )
        
        return sql_query
        
    except Exception as e:
        logger.error(f"Error generating SQL from question: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to generate SQL: {str(e)}")

def execute_bigquery(sql: str) -> BigQueryResult:
    """Execute SQL query against BigQuery"""
    try:
        start_time = time.time()
        
        # Execute the query
        query_job = bigquery_client.query(sql)
        results = query_job.result()
        
        execution_time = int((time.time() - start_time) * 1000)
        
        # Convert results to list of dictionaries
        data = []
        for row in results:
            row_dict = {}
            for key, value in row.items():
                # Handle different data types
                if hasattr(value, 'isoformat'):  # datetime objects
                    row_dict[key] = value.isoformat()
                elif isinstance(value, (int, float, str, bool)) or value is None:
                    row_dict[key] = value
                else:
                    row_dict[key] = str(value)
            data.append(row_dict)
        
        return BigQueryResult(
            query=sql,
            data=data,
            rowCount=len(data),
            executionTime=execution_time
        )
        
    except Exception as e:
        logger.error(f"BigQuery execution error: {e}")
        raise HTTPException(status_code=400, detail=f"Query execution failed: {str(e)}")

async def generate_insights(question: str, result: BigQueryResult) -> List[str]:
    """Generate insights about the query results using Gemini AI"""
    try:
        if result.rowCount == 0:
            return ["No data found for the specified criteria."]
        
        # Sample first few rows for insight generation
        sample_data = result.data[:3]
        
        prompt = f"""Analyze the following BigQuery results for a CRIO scheduling system query.

Original Question: {question}
Query: {result.query}
Row Count: {result.rowCount}
Sample Data: {json.dumps(sample_data, indent=2)}

Generate 2-3 brief, actionable insights about this data. Focus on:
- Key numbers or trends
- Operational implications  
- Notable patterns

Return as a JSON array of insight strings."""

        response = await gemini_service.generate_text(prompt)
        
        # Parse the JSON response
        try:
            insights = json.loads(response)
            return insights if isinstance(insights, list) else [response]
        except json.JSONDecodeError:
            # Fallback to basic insights
            return [f"Found {result.rowCount} records matching your criteria."]
            
    except Exception as e:
        logger.warning(f"Error generating insights: {e}")
        return [f"Query returned {result.rowCount} results."]

async def generate_followup_questions(question: str, result: BigQueryResult) -> List[str]:
    """Generate suggested follow-up questions"""
    try:
        prompt = f"""Based on this CRIO scheduling query and results, suggest 3 relevant follow-up questions.

Original Question: {question}
Result Count: {result.rowCount}

Generate natural language questions that would provide additional insights.
Return as a JSON array of question strings."""

        response = await gemini_service.generate_text(prompt)
        
        try:
            questions = json.loads(response)
            return questions if isinstance(questions, list) else []
        except json.JSONDecodeError:
            return []
            
    except Exception as e:
        logger.warning(f"Error generating follow-up questions: {e}")
        return []

@router.post("/natural-language", response_model=NaturalLanguageQueryResult)
async def query_natural_language(request: NaturalLanguageRequest):
    """Process natural language query and return results with insights"""
    try:
        # Generate SQL from natural language
        sql_query = await generate_sql_from_question(request.question)
        logger.info(f"Generated SQL: {sql_query}")
        
        # Execute the query
        result = execute_bigquery(sql_query)
        
        # Generate insights and follow-up questions
        insights = await generate_insights(request.question, result)
        followup = await generate_followup_questions(request.question, result)
        
        return NaturalLanguageQueryResult(
            userQuestion=request.question,
            generatedSQL=sql_query,
            result=result,
            insights=insights,
            suggestedFollowUp=followup
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Natural language query error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/execute", response_model=BigQueryResult)
async def execute_query(request: BigQueryExecuteRequest):
    """Execute raw SQL query against BigQuery"""
    try:
        result = execute_bigquery(request.query)
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Query execution error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/health")
async def health_check():
    """Health check for BigQuery service"""
    try:
        # Test query
        test_query = "SELECT COUNT(*) as count FROM `crio-pipe.crio_data.calendar_appointment` LIMIT 1"
        query_job = bigquery_client.query(test_query)
        query_job.result()
        
        return {
            "status": "healthy",
            "service": "BigQuery Natural Language API",
            "project": "crio-pipe",
            "timestamp": time.time()
        }
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"BigQuery service unavailable: {str(e)}")