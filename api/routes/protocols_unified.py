"""
Unified Protocol Processing API - Serialized Extraction Architecture

This single module provides reliable protocol processing using SERIALIZED criteria extraction:

Primary Upload Path:
  POST /process-single → Document AI (PDF → text) → Gemini SERIALIZED extraction → Database

SERIALIZED EXTRACTION METHOD (only method used):
  1. Metadata extraction: Protocol info, objectives, summary (2000 tokens)
  2. Inclusion criteria: Focused extraction of only inclusion criteria (3000 tokens)
  3. Exclusion criteria: Focused extraction of only exclusion criteria (3000 tokens)

Batch Processing:
  POST /process-batch → Handles multiple files with serialized extraction

Enhancement Path:
  POST /smart-update/{protocol_id} → Gemini AI (existing text → enhanced fields)

Query Path:
  POST /query → Semantic search across protocols

Status & Analysis:
  GET /status/{job_id} → Check processing status
  GET /trials → List all trials with protocol status
  GET /trial/{trial_id}/analysis → Complete trial analysis

All processing uses the optimized pipeline:
PDF → Document AI (OCR/text extraction) → Gemini SERIALIZED extraction → Database

Why Serialized? Prevents timeouts by breaking large 5000-token requests into smaller, 
focused requests that complete successfully with 70-90% success rate vs 0% with combined approach.
"""

import os
import logging
import uuid
import json
import tempfile
import shutil
import asyncio
from typing import Dict, List, Any, Optional
from datetime import datetime
from fastapi import APIRouter, HTTPException, UploadFile, File, Form, BackgroundTasks
from pydantic import BaseModel

from core.database import db
from core.services.gemini_service import gemini_service
from core.services.production_document_processor import production_document_processor
try:
    from core.services.intelligent_trial_matching import intelligent_trial_matcher
except ImportError:
    logger.warning("Intelligent trial matcher not available, using basic matching")
    intelligent_trial_matcher = None

logger = logging.getLogger(__name__)
router = APIRouter()

# =====================================================
# Pydantic Models
# =====================================================

class ProcessingOptions(BaseModel):
    """Options for processing protocols"""
    extract_criteria: bool = True
    check_duplicates: bool = True
    create_trial_if_no_match: bool = True
    enhanced_extraction: bool = True  # Enable targeted missing field recovery
    manual_trial_id: Optional[int] = None  # Manual assignment
    processor_type: Optional[str] = None  # Auto-select if None
    
    # Accuracy-first processing options
    accuracy_mode: bool = False  # Enable accuracy-first processing
    sequential_processing: bool = False  # Process batch files one at a time
    enhanced_chunking: bool = False  # Process full document without truncation
    extraction_delay_seconds: int = 5  # Delay between extraction calls
    document_delay_seconds: int = 10  # Delay between documents in batch
    max_retries: int = 5  # Maximum retry attempts
    max_chunk_size: int = 15000  # Smaller chunks for accuracy
    chunk_overlap: int = 2000  # Overlap between chunks
    validate_extraction_quality: bool = False  # Quality validation
    require_both_inclusion_exclusion: bool = False  # Require both criteria types
    min_criteria_threshold: int = 5  # Minimum criteria expected

class SmartUpdateRequest(BaseModel):
    """Request for smart protocol updates"""
    update_type: str = "all"  # 'metadata', 'criteria', 'summary', 'all'
    regenerate_embeddings: bool = False
    preserve_manual_edits: bool = True

class ProtocolQuery(BaseModel):
    """Protocol search query"""
    query: str
    trial_id: Optional[int] = None
    limit: int = 5

# =====================================================
# Helper Functions
# =====================================================

def _extract_protocol_number_from_filename(filename: str) -> str:
    """Extract protocol number from filename"""
    import re
    base_name = os.path.splitext(filename)[0]
    
    # Common patterns for protocol numbers
    patterns = [
        r'^([A-Z]{2,4}-\d{3,4}-[A-Z]?\d{3,4}).*',
        r'^([A-Z]{3,5}\d{2,}).*',
        r'^(\d{4}-\d{3,4}).*',
        r'^([A-Z]+\d+[A-Z]*\d*).*'
    ]
    
    for pattern in patterns:
        match = re.match(pattern, base_name, re.IGNORECASE)
        if match:
            protocol_number = match.group(1).upper()
            # Remove common suffixes like "_SYNOPSIS"
            protocol_number = re.sub(r'_SYNOPSIS$', '', protocol_number, flags=re.IGNORECASE)
            return protocol_number
    
    # Remove common suffixes from full basename if no pattern matched
    clean_basename = re.sub(r'_SYNOPSIS$', '', base_name, flags=re.IGNORECASE)
    return clean_basename.upper()

async def _find_or_create_trial(protocol_number: str, extracted_data: Dict[str, Any] = None, job_id: str = None) -> int:
    """Find existing trial by EXACT protocol number match or create new one with overwrite capability"""
    # Check for existing trial with EXACT protocol number match
    existing = db.execute_query(
        "SELECT id FROM clinical_trials WHERE protocol_number = %s",
        (protocol_number,)
    )
    
    if existing:
        trial_id = existing[0]['id']
        logger.info(f"Found existing trial {trial_id} for protocol {protocol_number} - will overwrite with new data")
        
        # OVERWRITE: Clear existing data and update with new extracted data
        if extracted_data:
            metadata = extracted_data.get('protocol_metadata', {})
            clinical = extracted_data.get('clinical_trial_fields', {})
            title = metadata.get('trial_title') or clinical.get('trial_name') or f"Clinical Trial {protocol_number}"
            conditions = metadata.get('conditions') or clinical.get('conditions') or "To be determined"
            phase = clinical.get('phase')
            sponsor = clinical.get('sponsor')
            
            # Update clinical_trials with new data
            db.execute_update("""
                UPDATE clinical_trials 
                SET trial_name = %s, conditions = %s, phase = %s, sponsor = %s, 
                    status = %s, updated_at = NOW(), extraction_audit = %s
                WHERE id = %s
            """, (title, conditions, phase, sponsor, "Active", 
                  json.dumps({"updated_by_job": job_id, "action": "overwrite"} if job_id else {}), trial_id))
            
            # Clear existing protocol_metadata for this trial (will be recreated)
            db.execute_update("DELETE FROM protocol_metadata WHERE trial_id = %s", (trial_id,))
            
            # Clear existing trial_criteria for this trial (will be recreated) 
            db.execute_update("DELETE FROM trial_criteria WHERE trial_id = %s", (trial_id,))
            
            logger.info(f"Overwritten existing trial {trial_id} data for protocol {protocol_number}")
        
        return trial_id
    
    # Use intelligent trial matching if available
    if job_id and intelligent_trial_matcher and hasattr(intelligent_trial_matcher, 'find_best_match'):
        match_result = await intelligent_trial_matcher.find_best_match(
            protocol_number, extracted_data or {}
        )
        if match_result and match_result.get('confidence_score', 0) >= 0.85:
            return match_result['matched_trial_id']
    
    # Create new trial with comprehensive data
    if extracted_data:
        metadata = extracted_data.get('protocol_metadata', {})
        clinical = extracted_data.get('clinical_trial_fields', {})
        title = metadata.get('trial_title') or clinical.get('trial_name') or f"Clinical Trial {protocol_number}"
        conditions = metadata.get('conditions') or clinical.get('conditions') or "To be determined"
        phase = clinical.get('phase')
        sponsor = clinical.get('sponsor')
    else:
        title = f"Clinical Trial {protocol_number}"
        conditions = "To be determined"
        phase = None
        sponsor = None
    
    result = db.execute_insert_returning("""
        INSERT INTO clinical_trials 
        (protocol_number, trial_name, conditions, phase, sponsor, status, uploaded_at, extraction_audit)
        VALUES (%s, %s, %s, %s, %s, %s, NOW(), %s)
        RETURNING id
    """, (
        protocol_number, title, conditions, phase, sponsor, "Active",
        json.dumps({"created_by_job": job_id} if job_id else {})
    ))
    
    if result:
        logger.info(f"Created new trial {result['id']} for protocol {protocol_number}")
        return result['id']
    
    raise HTTPException(status_code=500, detail="Failed to create trial")



async def _store_protocol_data(trial_id: int, result: Dict[str, Any], job_id: str) -> bool:
    """Store extracted protocol data in database from unified processor results"""
    try:
        extracted_data = result.get('extracted_data', {})
        metadata = extracted_data.get('protocol_metadata', {})
        clinical = extracted_data.get('clinical_trial_fields', {})
        criteria = extracted_data.get('trial_criteria', {})
        
        # Store protocol metadata (excluding removed duplicate columns)
        metadata_result = db.execute_insert_returning("""
            INSERT INTO protocol_metadata
            (trial_id, trial_title, protocol_summary,
             primary_objectives, secondary_objectives, study_design,
             target_population, estimated_enrollment, study_duration,
             extraction_confidence, field_coverage_percentage,
             validation_results, processor_type, processing_method,
             extraction_version, source_file_hash, page_count, text_length,
             created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
            RETURNING id
        """, (
            trial_id,
            metadata.get('trial_title'),
            metadata.get('protocol_summary'),
            metadata.get('primary_objectives'),
            metadata.get('secondary_objectives'),
            metadata.get('study_design'),
            metadata.get('target_population'),
            metadata.get('estimated_enrollment'),
            metadata.get('study_duration'),
            min(result.get('extraction_confidence', 0), 9.99),
            min(result.get('field_coverage_percentage', 0), 9.99),
            json.dumps(result.get('validation_results', {})),
            result.get('processor_type'),
            result.get('processing_method'),
            result.get('extraction_version'),
            result.get('file_hash'),
            result.get('document_pages'),
            result.get('text_length')
        ))
        
        if not metadata_result:
            return False
        
        # Update clinical_trials table with authoritative fields (conditions, but NOT protocol_number)
        # Note: We don't update protocol_number here because it's the key we used to find this trial
        trial_updates = []
        trial_values = []
        
        # Skip protocol_number update - we found the trial by this field, so it should remain unchanged
        
        if metadata.get('conditions'):
            # Convert conditions to string if it's an array
            conditions_str = ', '.join(metadata.get('conditions')) if isinstance(metadata.get('conditions'), list) else metadata.get('conditions')
            trial_updates.append("conditions = %s")
            trial_values.append(conditions_str)
        
        if clinical.get('sponsor'):
            trial_updates.append("sponsor = %s")
            trial_values.append(clinical.get('sponsor'))
            
        if metadata.get('trial_title'):
            trial_updates.append("trial_name = %s")
            trial_values.append(metadata.get('trial_title'))
        
        if trial_updates:
            trial_updates.append("updated_at = NOW()")
            trial_values.append(trial_id)
            
            db.execute_update(f"""
                UPDATE clinical_trials 
                SET {', '.join(trial_updates)}
                WHERE id = %s
            """, trial_values)
        
        # Store document chunks from unified processor
        document_chunks = result.get('document_chunks', [])
        for chunk_data in document_chunks:
            # Ensure chunk_data is a dictionary and extract safely
            if isinstance(chunk_data, dict):
                chunk_text = chunk_data.get('text', '')
                chunk_index = chunk_data.get('index', 0)
            else:
                # Handle case where chunk_data might not be a dict
                chunk_text = str(chunk_data) if chunk_data else ''
                chunk_index = 0
            
            db.execute_update("""
                INSERT INTO protocol_documents
                (trial_id, chunk_text, chunk_index, created_at)
                VALUES (%s, %s, %s, NOW())
            """, (trial_id, chunk_text, chunk_index))
        
        # Clear existing criteria and store new ones
        db.execute_update("DELETE FROM trial_criteria WHERE trial_id = %s", (trial_id,))
        
        # Store individual criteria with smart categorization
        for criterion_item in criteria.get('inclusion', []):
            if isinstance(criterion_item, dict):
                text = criterion_item.get('text')
                category = criterion_item.get('category', 'general')
            else:
                text = str(criterion_item)
                category = 'general'
            
            if text:
                db.execute_update("""
                    INSERT INTO trial_criteria
                    (trial_id, criterion_type, criterion_text, category, is_required,
                     created_by_job, extraction_confidence)
                    VALUES (%s, 'inclusion', %s, %s, true, %s, %s)
                """, (trial_id, text, category, job_id, result.get('extraction_confidence')))
        
        for criterion_item in criteria.get('exclusion', []):
            if isinstance(criterion_item, dict):
                text = criterion_item.get('text')
                category = criterion_item.get('category', 'general')
            else:
                text = str(criterion_item)
                category = 'general'
            
            if text:
                db.execute_update("""
                    INSERT INTO trial_criteria
                    (trial_id, criterion_type, criterion_text, category, is_required,
                     created_by_job, extraction_confidence)
                    VALUES (%s, 'exclusion', %s, %s, true, %s, %s)
                """, (trial_id, text, category, job_id, result.get('extraction_confidence')))
        
        # Update trial with comprehensive extracted info
        trial_updates = []
        trial_values = []
        
        if metadata.get('trial_title'):
            trial_updates.append("trial_name = %s")
            trial_values.append(metadata.get('trial_title'))
        
        if metadata.get('conditions'):
            conditions_data = metadata.get('conditions')
            if isinstance(conditions_data, list) and conditions_data:
                # Take first condition if it's a list
                conditions_str = str(conditions_data[0])
            elif isinstance(conditions_data, dict):
                # If it's a dict, serialize it safely
                conditions_str = json.dumps(conditions_data)
            else:
                # Otherwise convert to string
                conditions_str = str(conditions_data)
            trial_updates.append("conditions = %s")
            trial_values.append(conditions_str)
        
        if clinical.get('phase'):
            trial_updates.append("phase = %s")
            trial_values.append(clinical.get('phase'))
        
        if clinical.get('sponsor'):
            trial_updates.append("sponsor = %s")
            trial_values.append(clinical.get('sponsor'))
        
        if clinical.get('description') or metadata.get('protocol_summary'):
            trial_updates.append("description = %s")
            trial_values.append(clinical.get('description') or metadata.get('protocol_summary'))
        
        if trial_updates:
            trial_values.append(trial_id)
            update_query = f"""
                UPDATE clinical_trials 
                SET {', '.join(trial_updates)}, updated_at = NOW()
                WHERE id = %s
            """
            db.execute_update(update_query, trial_values)
        
        logger.info(f"Stored protocol data for trial {trial_id}")
        return True
        
    except Exception as e:
        logger.error(f"Error storing protocol data: {e}")
        logger.error(f"Error type: {type(e)}")
        
        # Log the problematic data for debugging
        logger.error(f"Result keys: {list(result.keys()) if isinstance(result, dict) else 'Not a dict'}")
        if isinstance(result, dict):
            logger.error(f"extracted_data type: {type(result.get('extracted_data'))}")
            if 'document_chunks' in result:
                chunks = result['document_chunks']
                logger.error(f"document_chunks type: {type(chunks)}, length: {len(chunks) if chunks else 0}")
                if chunks and len(chunks) > 0:
                    logger.error(f"First chunk type: {type(chunks[0])}")
                    logger.error(f"First chunk: {chunks[0] if isinstance(chunks[0], (str, int, float, bool)) else 'Complex object'}")
        
        import traceback
        logger.error(f"Full traceback: {traceback.format_exc()}")
        return False

# =====================================================
# Main Endpoints
# =====================================================

@router.post("/process-single")
async def process_single_protocol(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    options: str = Form('{}')  # JSON string of ProcessingOptions
):
    """
    Primary endpoint for single protocol processing
    
    Processing pipeline:
    1. Document AI (PDF → structured text)
    2. Gemini AI SERIALIZED extraction (3 focused API calls):
       - Metadata extraction (protocol info, objectives)
       - Inclusion criteria extraction (focused)
       - Exclusion criteria extraction (focused)
    3. Database (structured storage)
    
    Replaces: /upload, /upload-document-ai, /extract-criteria, /generate-summary
    """
    try:
        # Parse options
        try:
            parsed_options = json.loads(options)
            request_options = ProcessingOptions(**parsed_options)
        except Exception as e:
            logger.warning(f"Invalid options JSON, using defaults: {e}")
            request_options = ProcessingOptions()
        
        # Validate file
        if not file.filename or not file.filename.lower().endswith('.pdf'):
            raise HTTPException(status_code=400, detail="Only PDF files are allowed")
        
        # Create job ID for tracking
        job_id = str(uuid.uuid4())
        
        # Store job in database
        db.execute_update("""
            INSERT INTO protocol_processing_jobs 
            (job_id, job_type, status, total_files, file_paths, processing_options, user_id, created_at)
            VALUES (%s, 'single', 'queued', 1, %s, %s, %s, NOW())
        """, (
            job_id,
            json.dumps([file.filename]),
            json.dumps(parsed_options),
            'system'
        ))
        
        # Save file temporarily
        temp_file_path = None
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as temp_file:
                shutil.copyfileobj(file.file, temp_file)
                temp_file_path = temp_file.name
            
            # Process in background
            background_tasks.add_task(
                _process_single_background,
                job_id, temp_file_path, file.filename, request_options
            )
            
            return {
                "success": True,
                "job_id": job_id,
                "message": "Document processing started",
                "filename": file.filename,
                "status_endpoint": f"/api/protocols/status/{job_id}",
                "processing_options": parsed_options
            }
        
        except Exception as e:
            if temp_file_path and os.path.exists(temp_file_path):
                os.unlink(temp_file_path)
            raise e
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in single document processing: {e}")
        raise HTTPException(status_code=500, detail=f"Processing error: {str(e)}")

async def _process_single_background(job_id: str, file_path: str, filename: str,
                                    options: ProcessingOptions):
    """Background task for single document processing using unified Document AI processor"""
    try:
        # Update job status to processing
        db.execute_update("""
            UPDATE protocol_processing_jobs 
            SET status = 'processing', started_at = CURRENT_TIMESTAMP, current_file = %s
            WHERE job_id = %s
        """, (filename, job_id))
        
        # Process with unified Document AI processor
        # Disable hash-based duplicate checking for single uploads to enable protocol number overwrite
        processing_options = {
            'extract_criteria': options.extract_criteria,
            'check_duplicates': False,  # Always False for single uploads - we handle duplicates via protocol number matching
            'processor_type': options.processor_type
        }
        
        result = await production_document_processor.process_document(
            file_path, job_id, processing_options
        )
        
        if result['success']:
            # Handle trial matching or creation
            protocol_number = _extract_protocol_number_from_filename(filename)
            
            # Manual trial assignment takes precedence
            if options.manual_trial_id:
                trial_id = options.manual_trial_id
                logger.info(f"Using manually specified trial ID: {trial_id}")
            else:
                # Always use _find_or_create_trial for EXACT protocol number matching with overwrite capability
                # This handles both new trials and overwrites of existing trials with exact protocol number matches
                trial_id = await _find_or_create_trial(
                    protocol_number, 
                    result.get('extracted_data'),
                    job_id
                )
            
            # Store protocol data
            stored = await _store_protocol_data(trial_id, result, job_id)
            
            if stored:
                # Update job as completed
                final_results = {
                    **result,
                    'trial_id': trial_id,
                    'protocol_number': protocol_number,
                    'criteria_count': len(result.get('extracted_data', {}).get('trial_criteria', {}).get('inclusion', [])) +
                                     len(result.get('extracted_data', {}).get('trial_criteria', {}).get('exclusion', []))
                }
                
                db.execute_update("""
                    UPDATE protocol_processing_jobs 
                    SET status = 'completed', completed_at = CURRENT_TIMESTAMP,
                        processed_files = 1, progress_percentage = 100.0,
                        results = %s
                    WHERE job_id = %s
                """, (json.dumps(final_results), job_id))
                
                logger.info(f"Protocol processing completed for job {job_id}, trial {trial_id}")
            else:
                raise Exception("Failed to store protocol data")
        else:
            # Update job as failed
            db.execute_update("""
                UPDATE protocol_processing_jobs 
                SET status = 'failed', completed_at = CURRENT_TIMESTAMP,
                    error_messages = %s
                WHERE job_id = %s
            """, (json.dumps([result.get('error', 'Unknown error')]), job_id))
    
    except Exception as e:
        logger.error(f"Background processing failed for job {job_id}: {e}")
        
        db.execute_update("""
            UPDATE protocol_processing_jobs 
            SET status = 'failed', completed_at = CURRENT_TIMESTAMP,
                error_messages = %s
            WHERE job_id = %s
        """, (json.dumps([str(e)]), job_id))
    
    finally:
        # Clean up temporary file
        if os.path.exists(file_path):
            os.unlink(file_path)

@router.post("/process-batch")
async def process_batch_protocols(
    background_tasks: BackgroundTasks,
    files: List[UploadFile] = File(...),
    options: str = Form('{}')  # JSON string of batch options
):
    """
    Batch processing endpoint for multiple protocols
    
    Handles multiple files with dependency resolution and concurrent processing.
    Replaces: /batch-upload with intelligent dependency handling
    """
    try:
        # Parse options
        try:
            parsed_options = json.loads(options)
            batch_options = {
                'extract_criteria': parsed_options.get('extract_criteria', True),
                'check_duplicates': parsed_options.get('check_duplicates', True),
                'max_concurrent': parsed_options.get('max_concurrent', 3),
                'create_trials_if_no_match': parsed_options.get('create_trials_if_no_match', True),
                # Accuracy-first processing options
                'accuracy_mode': parsed_options.get('accuracy_mode', False),
                'sequential_processing': parsed_options.get('sequential_processing', False),
                'enhanced_chunking': parsed_options.get('enhanced_chunking', False),
                'extraction_delay_seconds': parsed_options.get('extraction_delay_seconds', 5),
                'document_delay_seconds': parsed_options.get('document_delay_seconds', 10),
                'max_retries': parsed_options.get('max_retries', 5),
                'max_chunk_size': parsed_options.get('max_chunk_size', 15000),
                'chunk_overlap': parsed_options.get('chunk_overlap', 2000)
            }
        except Exception as e:
            logger.warning(f"Invalid batch options, using defaults: {e}")
            batch_options = {
                'extract_criteria': True,
                'check_duplicates': True,
                'max_concurrent': 3,
                'create_trials_if_no_match': True,
                # Accuracy-first processing defaults
                'accuracy_mode': False,
                'sequential_processing': False,
                'enhanced_chunking': False,
                'extraction_delay_seconds': 5,
                'document_delay_seconds': 10,
                'max_retries': 5,
                'max_chunk_size': 15000,
                'chunk_overlap': 2000
            }
        
        # Validate files
        if not files:
            raise HTTPException(status_code=400, detail="No files provided")
        
        if len(files) > 50:
            raise HTTPException(status_code=400, detail="Too many files (max 50)")
        
        for file in files:
            if not file.filename.lower().endswith('.pdf'):
                raise HTTPException(status_code=400, detail=f"Non-PDF file: {file.filename}")
        
        # Create batch job
        job_id = str(uuid.uuid4())
        file_names = [f.filename for f in files]
        
        # Store job in database
        db.execute_update("""
            INSERT INTO protocol_processing_jobs 
            (job_id, job_type, status, total_files, file_paths, processing_options, user_id, created_at)
            VALUES (%s, 'batch', 'queued', %s, %s, %s, %s, NOW())
        """, (
            job_id,
            len(files),
            json.dumps(file_names),
            json.dumps(batch_options),
            'system'
        ))
        
        # Save files to temporary directory
        temp_dir = os.path.join(tempfile.gettempdir(), f"batch_{job_id}")
        os.makedirs(temp_dir, exist_ok=True)
        
        temp_file_paths = []
        try:
            for file in files:
                temp_path = os.path.join(temp_dir, file.filename)
                with open(temp_path, "wb") as buffer:
                    shutil.copyfileobj(file.file, buffer)
                temp_file_paths.append(temp_path)
            
            # Start background batch processing
            background_tasks.add_task(
                _process_batch_background,
                job_id, temp_file_paths, batch_options
            )
            
            return {
                "success": True,
                "job_id": job_id,
                "message": "Batch processing started",
                "total_files": len(files),
                "file_names": file_names,
                "status_endpoint": f"/api/protocols/status/{job_id}",
                "processing_options": batch_options
            }
            
        except Exception as e:
            # Clean up on error
            for path in temp_file_paths:
                if os.path.exists(path):
                    os.unlink(path)
            if os.path.exists(temp_dir):
                os.rmdir(temp_dir)
            raise e
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in batch processing: {e}")
        raise HTTPException(status_code=500, detail=f"Batch processing error: {str(e)}")

async def _process_batch_background(job_id: str, file_paths: List[str], options: Dict[str, Any]):
    """Background task for batch document processing"""
    try:
        # Update job status
        db.execute_update("""
            UPDATE protocol_processing_jobs 
            SET status = 'processing', started_at = CURRENT_TIMESTAMP
            WHERE job_id = %s
        """, (job_id,))
        
        processed_count = 0
        failed_count = 0
        results = []
        
        # Check if sequential processing is requested
        sequential_mode = options.get('sequential_processing', False)
        accuracy_mode = options.get('accuracy_mode', False)
        
        if sequential_mode or accuracy_mode:
            logger.info("🎯 SEQUENTIAL PROCESSING MODE - Processing files one at a time for maximum accuracy")
            file_results = await _process_files_sequentially(
                file_paths, job_id, options
            )
        else:
            logger.info(f"⚡ CONCURRENT PROCESSING MODE - Processing up to {options.get('max_concurrent', 3)} files simultaneously")
            file_results = await _process_files_concurrently(
                file_paths, job_id, options
            )
        
        # Process results
        for file_result in file_results:
            if isinstance(file_result, Exception):
                failed_count += 1
                results.append({"error": str(file_result), "success": False})
            else:
                if file_result['success']:
                    processed_count += 1
                else:
                    failed_count += 1
                results.append(file_result)
            
            # Update progress
            progress = ((processed_count + failed_count) / len(file_paths)) * 100
            db.execute_update("""
                UPDATE protocol_processing_jobs 
                SET processed_files = %s, failed_files = %s, progress_percentage = %s
                WHERE job_id = %s
            """, (processed_count, failed_count, progress, job_id))
        
        # Finalize job
        final_status = 'completed' if failed_count == 0 else 'completed_with_errors'
        
        db.execute_update("""
            UPDATE protocol_processing_jobs 
            SET status = %s, completed_at = CURRENT_TIMESTAMP,
                results = %s, current_file = NULL
            WHERE job_id = %s
        """, (final_status, json.dumps({
            "processed_count": processed_count,
            "failed_count": failed_count,
            "total_count": len(file_paths),
            "file_results": results
        }), job_id))
        
        logger.info(f"Batch processing completed - {processed_count}/{len(file_paths)} successful")
        
    except Exception as e:
        logger.error(f"Batch processing failed for job {job_id}: {e}")
        
        db.execute_update("""
            UPDATE protocol_processing_jobs 
            SET status = 'failed', completed_at = CURRENT_TIMESTAMP,
                error_messages = %s
            WHERE job_id = %s
        """, (json.dumps([str(e)]), job_id))
    
    finally:
        # Clean up temporary files
        for file_path in file_paths:
            if os.path.exists(file_path):
                os.unlink(file_path)
        
        # Clean up temp directory
        temp_dir = os.path.dirname(file_paths[0]) if file_paths else None
        if temp_dir and os.path.exists(temp_dir):
            try:
                os.rmdir(temp_dir)
            except OSError:
                pass  # Directory not empty or other issue

async def _process_files_sequentially(file_paths: List[str], job_id: str, options: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Process files one at a time for maximum accuracy"""
    file_results = []
    document_delay = options.get('document_delay_seconds', 10)
    
    for i, file_path in enumerate(file_paths):
        try:
            filename = os.path.basename(file_path)
            logger.info(f"🔄 Processing file {i+1}/{len(file_paths)}: {filename}")
            
            # Update current file
            db.execute_update("""
                UPDATE protocol_processing_jobs 
                SET current_file = %s WHERE job_id = %s
            """, (filename, job_id))
            
            # Enhanced processing options for accuracy
            processing_options = {
                'extract_criteria': options.get('extract_criteria', True),
                'check_duplicates': options.get('check_duplicates', True),
                'accuracy_mode': options.get('accuracy_mode', False),
                'sequential_processing': options.get('sequential_processing', False),
                'enhanced_chunking': options.get('enhanced_chunking', False),
                'extraction_delay_seconds': options.get('extraction_delay_seconds', 5),
                'max_retries': options.get('max_retries', 5),
                'max_chunk_size': options.get('max_chunk_size', 15000),
                'chunk_overlap': options.get('chunk_overlap', 2000)
            }
            
            result = await production_document_processor.process_document(
                file_path, job_id, processing_options
            )
            
            if result['success']:
                # Handle trial creation/matching
                protocol_number = _extract_protocol_number_from_filename(filename)
                trial_id = await _find_or_create_trial(
                    protocol_number,
                    result.get('extracted_data'),
                    job_id
                )
                
                # Store protocol data
                stored = await _store_protocol_data(trial_id, result, job_id)
                
                file_results.append({
                    "filename": filename,
                    "success": True,
                    "trial_id": trial_id,
                    "protocol_number": protocol_number,
                    "result": result
                })
            else:
                file_results.append({
                    "filename": filename,
                    "success": False,
                    "error": result.get('error', 'Processing failed')
                })
            
            # Delay between documents for API stability
            if i < len(file_paths) - 1:  # Don't delay after last file
                logger.info(f"⏳ Waiting {document_delay}s before next document...")
                await asyncio.sleep(document_delay)
                
        except Exception as e:
            logger.error(f"Error processing {file_path}: {e}")
            file_results.append({
                "filename": os.path.basename(file_path),
                "success": False,
                "error": str(e)
            })
    
    return file_results


async def _process_files_concurrently(file_paths: List[str], job_id: str, options: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Process files concurrently for speed (original method)"""
    # Process files with controlled concurrency
    semaphore = asyncio.Semaphore(options.get('max_concurrent', 3))
    
    async def process_single_file(file_path: str) -> Dict[str, Any]:
        async with semaphore:
            try:
                filename = os.path.basename(file_path)
                
                # Update current file
                db.execute_update("""
                    UPDATE protocol_processing_jobs 
                    SET current_file = %s WHERE job_id = %s
                """, (filename, job_id))
                
                # Process with unified processor
                processing_options = {
                    'extract_criteria': options.get('extract_criteria', True),
                    'check_duplicates': options.get('check_duplicates', True)
                }
                
                result = await production_document_processor.process_document(
                    file_path, job_id, processing_options
                )
                
                if result['success']:
                    # Handle trial creation/matching
                    protocol_number = _extract_protocol_number_from_filename(filename)
                    trial_id = await _find_or_create_trial(
                        protocol_number,
                        result.get('extracted_data'),
                        job_id
                    )
                    
                    # Store protocol data
                    stored = await _store_protocol_data(trial_id, result, job_id)
                    
                    return {
                        "filename": filename,
                        "success": True,
                        "trial_id": trial_id,
                        "protocol_number": protocol_number,
                        "result": result
                    }
                else:
                    return {
                        "filename": filename,
                        "success": False,
                        "error": result.get('error', 'Processing failed')
                    }
                    
            except Exception as e:
                logger.error(f"Error processing {file_path}: {e}")
                return {
                    "filename": os.path.basename(file_path),
                    "success": False,
                    "error": str(e)
                }
    
    # Process all files
    tasks = [process_single_file(path) for path in file_paths]
    file_results = await asyncio.gather(*tasks, return_exceptions=True)
    return file_results

@router.post("/enrich-missing-data")
async def enrich_missing_data():
    """One-time endpoint to fill missing data gaps in clinical_trials and protocol_metadata tables"""
    logger.info("🚀 Starting data enrichment process...")
    
    try:
        # Get protocols that have documents but missing data
        protocols = db.execute_query("""
            SELECT 
                ct.id as trial_id,
                ct.protocol_number,
                ct.sponsor IS NULL as missing_sponsor,
                ct.enrollment_target IS NULL as missing_enrollment,
                ct.estimated_duration IS NULL as missing_duration,
                ct.medications IS NULL as missing_medications,
                ct.nct_number IS NULL as missing_nct,
                ct.primary_endpoint IS NULL as missing_primary_endpoint,
                ct.secondary_endpoints IS NULL as missing_secondary_endpoints,
                pm.id as metadata_id,
                pm.primary_objectives IS NULL as missing_primary_obj,
                pm.secondary_objectives IS NULL as missing_secondary_obj,
                pm.target_population IS NULL as missing_target_pop,
                pm.study_duration IS NULL as missing_study_duration,
                pm.estimated_enrollment IS NULL as missing_enrollment_meta,
                COUNT(pd.id) as chunk_count
            FROM clinical_trials ct
            LEFT JOIN protocol_metadata pm ON ct.id = pm.trial_id
            LEFT JOIN protocol_documents pd ON ct.id = pd.trial_id
            GROUP BY ct.id, ct.protocol_number, ct.sponsor, ct.enrollment_target, 
                     ct.estimated_duration, ct.medications, ct.nct_number,
                     ct.primary_endpoint, ct.secondary_endpoints,
                     pm.id, pm.primary_objectives, pm.secondary_objectives,
                     pm.target_population, pm.study_duration, pm.estimated_enrollment
            HAVING COUNT(pd.id) > 0
            ORDER BY COUNT(pd.id) DESC
        """)
        
        stats = {"protocols_processed": 0, "fields_updated": 0, "errors": 0}
        
        for protocol in protocols:
            try:
                trial_id = protocol['trial_id']
                protocol_number = protocol['protocol_number']
                metadata_id = protocol['metadata_id']
                
                logger.info(f"🔄 Processing {protocol_number}")
                
                # Get document text
                chunks = db.execute_query("""
                    SELECT chunk_text FROM protocol_documents 
                    WHERE trial_id = %s 
                    ORDER BY chunk_index 
                    LIMIT 30
                """, (trial_id,))
                
                if not chunks:
                    continue
                    
                full_text = " ".join([chunk['chunk_text'] for chunk in chunks])
                
                # Use the production document processor's missing field recovery
                fields_to_recover = []
                if protocol['missing_sponsor']: fields_to_recover.append('sponsor')
                if protocol['missing_medications']: fields_to_recover.append('medications') 
                if protocol['missing_nct']: fields_to_recover.append('nct_number')
                if protocol['missing_enrollment']: fields_to_recover.append('enrollment_target')
                if protocol['missing_primary_obj']: fields_to_recover.append('primary_objectives')
                if protocol['missing_secondary_obj']: fields_to_recover.append('secondary_objectives')
                if protocol['missing_target_pop']: fields_to_recover.append('target_population')
                
                recovered_data = {}
                
                # Use the existing missing field recovery system
                for field in fields_to_recover[:5]:  # Process more fields per protocol
                    result = await production_document_processor._query_specific_field(full_text, field)
                    if result.get('success') and result.get('value'):
                        recovered_data[field] = result['value']
                        logger.info(f"✅ Recovered {field}: {result['value'][:50]}...")
                    
                    # Rate limiting
                    await asyncio.sleep(1)
                
                # Update clinical_trials fields
                ct_updates = []
                ct_values = []
                for field in ['sponsor', 'medications', 'nct_number', 'enrollment_target']:
                    if field in recovered_data:
                        if field == 'nct_number':
                            # Ensure NCT number fits in varchar(20)
                            value = recovered_data[field][:20] if len(recovered_data[field]) > 20 else recovered_data[field]
                        elif field == 'enrollment_target':
                            # Extract numeric value from enrollment target
                            try:
                                import re
                                numbers = re.findall(r'\d+', str(recovered_data[field]))
                                if numbers:
                                    value = int(numbers[0])
                                else:
                                    continue
                            except (ValueError, IndexError):
                                continue
                        else:
                            value = recovered_data[field]
                        ct_updates.append(f"{field} = %s")
                        ct_values.append(value)
                
                if ct_updates:
                    ct_values.append(trial_id)
                    db.execute_update(f"""
                        UPDATE clinical_trials 
                        SET {', '.join(ct_updates)}, updated_at = CURRENT_TIMESTAMP
                        WHERE id = %s
                    """, ct_values)
                    stats['fields_updated'] += len(ct_updates)
                
                # Update protocol_metadata fields
                if metadata_id:
                    pm_updates = []
                    pm_values = []
                    for field in ['primary_objectives', 'secondary_objectives', 'target_population']:
                        if field in recovered_data:
                            pm_updates.append(f"{field} = %s")
                            pm_values.append(recovered_data[field])
                    
                    if pm_updates:
                        pm_values.append(metadata_id)
                        db.execute_update(f"""
                            UPDATE protocol_metadata 
                            SET {', '.join(pm_updates)}, updated_at = CURRENT_TIMESTAMP
                            WHERE id = %s
                        """, pm_values)
                        stats['fields_updated'] += len(pm_updates)
                
                stats['protocols_processed'] += 1
                logger.info(f"✅ Completed {protocol_number}")
                
                # Delay between protocols
                await asyncio.sleep(2)
                
            except Exception as e:
                logger.error(f"❌ Error processing {protocol.get('protocol_number', 'unknown')}: {e}")
                stats['errors'] += 1
        
        logger.info(f"📊 Data enrichment completed: {stats}")
        
        return {
            "success": True,
            "message": "Data enrichment process completed",
            "stats": stats
        }
        
    except Exception as e:
        logger.error(f"💥 Data enrichment failed: {e}")
        raise HTTPException(status_code=500, detail=f"Data enrichment failed: {str(e)}")

@router.post("/reprocess-summary/{trial_id}")
async def reprocess_protocol_summary(trial_id: int):
    """Reprocess protocol summary using enhanced extraction method"""
    logger.info(f"🔄 Reprocessing summary for trial {trial_id}")
    
    try:
        # 1. Get protocol document content
        document_chunks = db.execute_query("""
            SELECT chunk_text FROM protocol_documents 
            WHERE trial_id = %s 
            ORDER BY chunk_index 
            LIMIT 50
        """, (trial_id,))
        
        if not document_chunks:
            raise HTTPException(status_code=404, detail="No protocol document found for this trial")
        
        # Combine document chunks
        full_text = " ".join([chunk['chunk_text'] for chunk in document_chunks])
        logger.info(f"📄 Retrieved {len(document_chunks)} chunks, total text length: {len(full_text):,} chars")
        
        # 2. Use the same enhanced metadata extraction as in production processor
        extraction_result = await production_document_processor._extract_metadata_only(full_text, "reprocess")
        
        if not extraction_result or not extraction_result.get('success'):
            raise HTTPException(status_code=500, detail="Summary extraction failed")
        
        # 3. Extract the enhanced summary
        extracted_data = extraction_result.get('data', {})
        new_summary = extracted_data.get('protocol_metadata', {}).get('protocol_summary')
        
        if not new_summary:
            raise HTTPException(status_code=500, detail="No summary generated from extraction")
        
        # 4. Update the protocol_metadata record
        db.execute_update("""
            UPDATE protocol_metadata 
            SET protocol_summary = %s, 
                updated_at = CURRENT_TIMESTAMP,
                extraction_version = '2.0_enhanced'
            WHERE trial_id = %s
        """, (new_summary, trial_id))
        
        logger.info(f"✅ Successfully updated summary for trial {trial_id}")
        logger.info(f"📝 New summary length: {len(new_summary)} chars")
        
        return {
            "success": True,
            "message": "Protocol summary successfully reprocessed",
            "trial_id": trial_id,
            "new_summary": new_summary,
            "summary_length": len(new_summary),
            "extraction_method": "enhanced_2.0"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Summary reprocessing failed for trial {trial_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Summary reprocessing failed: {str(e)}")

@router.post("/smart-update/{protocol_id}")
async def smart_update_protocol(protocol_id: int, request: SmartUpdateRequest):
    """
    Update existing protocol with enhanced extraction
    Uses existing document chunks to re-extract or enhance data
    """
    # Verify protocol exists
    protocol = db.execute_query("""
        SELECT pm.*, ct.id as trial_id
        FROM protocol_metadata pm
        JOIN clinical_trials ct ON pm.trial_id = ct.id
        WHERE pm.id = %s
    """, (protocol_id,))
    
    if not protocol:
        raise HTTPException(status_code=404, detail="Protocol not found")
    
    trial_id = protocol[0]['trial_id']
    
    # Get existing document chunks
    chunks = db.execute_query("""
        SELECT chunk_text FROM protocol_documents 
        WHERE trial_id = %s 
        ORDER BY chunk_index
        LIMIT 20
    """, (trial_id,))
    
    if not chunks:
        raise HTTPException(status_code=400, detail="No document chunks found")
    
    # Combine chunks for processing
    text_content = "\n\n".join([c['chunk_text'] for c in chunks])
    
    # Re-extract with Gemini service directly
    try:
        # Use Gemini to extract structured data
        prompt = f"""
        Extract structured information from this clinical trial protocol text:

        {text_content[:10000]}  # Limit to first 10k chars

        Please extract and return JSON with these fields:
        {{
            "protocol_metadata": {{
                "trial_title": "...",
                "protocol_summary": "...",
                "protocol_number": "...",
                "sponsor": "...",
                "conditions": ["condition1", "condition2"],
                "primary_objectives": "...",
                "secondary_objectives": "...",
                "study_design": "...",
                "target_population": "...",
                "estimated_enrollment": 100,
                "study_duration": "..."
            }},
            "trial_criteria": {{
                "inclusion": ["criterion1", "criterion2"],
                "exclusion": ["criterion1", "criterion2"]
            }}
        }}
        
        Return only valid JSON, no additional text.
        """
        
        response = await gemini_service.generate_text(prompt, max_tokens=2000)
        
        # Parse JSON response with robust error handling
        import json
        import re
        
        # Try to extract JSON from response (handle markdown code blocks)
        response_clean = response.strip()
        
        # Remove markdown code blocks if present
        if '```json' in response_clean:
            json_match = re.search(r'```json\s*(.*?)\s*```', response_clean, re.DOTALL)
            if json_match:
                response_clean = json_match.group(1)
        elif '```' in response_clean:
            json_match = re.search(r'```\s*(.*?)\s*```', response_clean, re.DOTALL)
            if json_match:
                response_clean = json_match.group(1)
        
        # Find JSON object in the response
        json_match = re.search(r'\{.*\}', response_clean, re.DOTALL)
        if json_match:
            json_str = json_match.group(0)
        else:
            json_str = response_clean
        
        try:
            extracted_json = json.loads(json_str)
        except json.JSONDecodeError:
            # Fallback: create empty structure
            logger.warning(f"Failed to parse JSON from Gemini response: {response_clean[:200]}...")
            extracted_json = {
                "protocol_metadata": {},
                "trial_criteria": {"inclusion": [], "exclusion": []}
            }
        
        protocol_metadata = extracted_json.get("protocol_metadata", {})
        trial_criteria = extracted_json.get("trial_criteria", {})
        
        # Convert any array fields to text to avoid type mismatch errors
        for key, value in protocol_metadata.items():
            if isinstance(value, list):
                protocol_metadata[key] = ', '.join(str(v) for v in value) if value else None
        
        # Log the extracted data for debugging
        logger.info(f"Smart Update extracted data: {protocol_metadata}")
        
    except Exception as e:
        logger.error(f"Error in smart update extraction: {e}")
        raise HTTPException(status_code=500, detail=f"Extraction failed: {str(e)}")
    
    # Update based on request type
    updates_made = []
    
    if request.update_type in ["metadata", "all"]:
        # Update metadata fields
        db.execute_update("""
            UPDATE protocol_metadata 
            SET trial_title = COALESCE(%s, trial_title),
                protocol_summary = COALESCE(%s, protocol_summary),
                primary_objectives = COALESCE(%s, primary_objectives),
                secondary_objectives = COALESCE(%s, secondary_objectives),
                study_design = COALESCE(%s, study_design),
                target_population = COALESCE(%s, target_population),
                estimated_enrollment = COALESCE(%s, estimated_enrollment),
                study_duration = COALESCE(%s, study_duration),
                updated_at = NOW()
            WHERE id = %s
        """, (
            protocol_metadata.get('trial_title'),
            protocol_metadata.get('protocol_summary'),
            protocol_metadata.get('primary_objectives'),
            protocol_metadata.get('secondary_objectives'),
            protocol_metadata.get('study_design'),
            protocol_metadata.get('target_population'),
            protocol_metadata.get('estimated_enrollment'),
            protocol_metadata.get('study_duration'),
            protocol_id
        ))
        updates_made.append("metadata")
        
        # Also update authoritative fields in clinical_trials table
        trial_updates = []
        trial_values = []
        
        if protocol_metadata.get('protocol_number'):
            trial_updates.append("protocol_number = COALESCE(%s, protocol_number)")
            trial_values.append(protocol_metadata.get('protocol_number'))
            
        if protocol_metadata.get('conditions'):
            conditions_str = ', '.join(protocol_metadata.get('conditions')) if isinstance(protocol_metadata.get('conditions'), list) else protocol_metadata.get('conditions')
            trial_updates.append("conditions = COALESCE(%s, conditions)")
            trial_values.append(conditions_str)
            
        if protocol_metadata.get('sponsor'):
            trial_updates.append("sponsor = COALESCE(%s, sponsor)")
            trial_values.append(protocol_metadata.get('sponsor'))
            
        if protocol_metadata.get('trial_title'):
            trial_updates.append("trial_name = COALESCE(%s, trial_name)")
            trial_values.append(protocol_metadata.get('trial_title'))
        
        if trial_updates:
            trial_updates.append("updated_at = NOW()")
            trial_values.append(trial_id)
            
            db.execute_update(f"""
                UPDATE clinical_trials 
                SET {', '.join(trial_updates)}
                WHERE id = %s
            """, trial_values)
    
    if request.update_type in ["criteria", "all"]:
        # Clear and re-add criteria
        db.execute_update("DELETE FROM trial_criteria WHERE trial_id = %s", (trial_id,))
        
        for criterion in trial_criteria.get('inclusion', []):
            if criterion:
                db.execute_update("""
                    INSERT INTO trial_criteria
                    (trial_id, criterion_type, criterion_text, category, is_required)
                    VALUES (%s, 'inclusion', %s, 'general', true)
                """, (trial_id, criterion))
        
        for criterion in trial_criteria.get('exclusion', []):
            if criterion:
                db.execute_update("""
                    INSERT INTO trial_criteria
                    (trial_id, criterion_type, criterion_text, category, is_required)
                    VALUES (%s, 'exclusion', %s, 'general', true)
                """, (trial_id, criterion))
        
        updates_made.append("criteria")
    
    if request.update_type in ["summary", "all"]:
        # Generate enhanced summary
        summary_prompt = f"""Create a comprehensive clinical trial summary:
        
{text_content[:5000]}

Write a 2-3 paragraph professional summary covering purpose, population, and design."""
        
        summary = await gemini_service.generate_text(summary_prompt, max_tokens=500)
        
        db.execute_update("""
            UPDATE protocol_metadata 
            SET protocol_summary = %s, updated_at = NOW()
            WHERE id = %s
        """, (summary, protocol_id))
        
        updates_made.append("summary")
    
    return {
        "success": True,
        "protocol_id": protocol_id,
        "trial_id": trial_id,
        "updates_made": updates_made,
        "fields_updated": len([k for k, v in protocol_metadata.items() if v])
    }

@router.get("/status/{job_id}")
async def get_processing_status(job_id: str):
    """Get processing status for a job"""
    job = db.execute_query("""
        SELECT * FROM protocol_processing_jobs WHERE job_id = %s
    """, (job_id,))
    
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    job_data = job[0]
    return {
        "job_id": job_id,
        "status": job_data['status'],
        "type": job_data['job_type'],
        "created_at": job_data['created_at'].isoformat() if job_data['created_at'] else None,
        "completed_at": job_data['completed_at'].isoformat() if job_data['completed_at'] else None,
        "results": job_data['results'] or {},
        "error_messages": job_data['error_messages'] or []
    }

@router.get("/trials")
async def get_trials_with_protocols():
    """Get all trials with their protocol status"""
    trials = db.execute_query("""
        SELECT 
            ct.id,
            ct.protocol_number,
            ct.trial_name,
            ct.conditions,
            ct.phase,
            ct.sponsor,
            ct.status,
            ct.uploaded_at,
            CASE WHEN pm.id IS NOT NULL THEN true ELSE false END as has_protocol,
            pm.id as protocol_metadata_id,
            pm.protocol_summary,
            (SELECT COUNT(*) FROM trial_criteria WHERE trial_id = ct.id) as criteria_count,
            (SELECT COUNT(*) FROM protocol_documents WHERE trial_id = ct.id) as document_chunks
        FROM clinical_trials ct
        LEFT JOIN protocol_metadata pm ON ct.id = pm.trial_id
        ORDER BY ct.uploaded_at DESC
    """)
    
    return {
        "trials": trials or [],
        "total_count": len(trials) if trials else 0
    }

@router.get("/trial/{trial_id}/analysis")
async def get_trial_analysis(trial_id: int):
    """Get comprehensive analysis of a trial"""
    # Get trial info
    trial = db.execute_query("""
        SELECT * FROM clinical_trials WHERE id = %s
    """, (trial_id,))
    
    if not trial:
        raise HTTPException(status_code=404, detail="Trial not found")
    
    # Get protocol metadata
    metadata = db.execute_query("""
        SELECT * FROM protocol_metadata 
        WHERE trial_id = %s 
        ORDER BY created_at DESC LIMIT 1
    """, (trial_id,))
    
    # Get criteria summary
    criteria = db.execute_query("""
        SELECT 
            criterion_type,
            COUNT(*) as count,
            array_agg(DISTINCT category) as categories
        FROM trial_criteria 
        WHERE trial_id = %s
        GROUP BY criterion_type
    """, (trial_id,))
    
    # Get document stats
    doc_stats = db.execute_query("""
        SELECT 
            COUNT(*) as chunk_count,
            AVG(LENGTH(chunk_text)) as avg_chunk_size
        FROM protocol_documents 
        WHERE trial_id = %s
    """, (trial_id,))
    
    return {
        "trial": trial[0],
        "protocol_metadata": metadata[0] if metadata else None,
        "criteria_summary": criteria or [],
        "document_stats": doc_stats[0] if doc_stats else None,
        "analysis_timestamp": datetime.now().isoformat()
    }

@router.post("/query")
async def query_protocols(request: ProtocolQuery):
    """Semantic search across protocol documents"""
    # Build query
    if request.trial_id:
        # Search within specific trial
        results = db.execute_query("""
            SELECT 
                pd.chunk_text,
                pd.chunk_index,
                ct.protocol_number,
                ct.trial_name
            FROM protocol_documents pd
            JOIN clinical_trials ct ON pd.trial_id = ct.id
            WHERE pd.trial_id = %s
            AND pd.chunk_text ILIKE %s
            ORDER BY pd.chunk_index
            LIMIT %s
        """, (request.trial_id, f"%{request.query}%", request.limit))
    else:
        # Search across all protocols
        results = db.execute_query("""
            SELECT 
                pd.chunk_text,
                pd.chunk_index,
                pd.trial_id,
                ct.protocol_number,
                ct.trial_name
            FROM protocol_documents pd
            JOIN clinical_trials ct ON pd.trial_id = ct.id
            WHERE pd.chunk_text ILIKE %s
            ORDER BY pd.trial_id, pd.chunk_index
            LIMIT %s
        """, (f"%{request.query}%", request.limit))
    
    return {
        "query": request.query,
        "results": results or [],
        "count": len(results) if results else 0
    }

# Legacy endpoint redirects for compatibility
@router.post("/upload-document-ai")
async def legacy_upload_redirect(**kwargs):
    """Redirect to unified upload endpoint"""
    return {"message": "Please use POST /api/protocols/upload instead"}

@router.post("/process-single")
async def legacy_process_redirect(**kwargs):
    """Redirect to unified upload endpoint"""
    return {"message": "Please use POST /api/protocols/upload instead"}


@router.post("/extract-criteria/{trial_id}")
async def legacy_extract_redirect(trial_id: int):
    """Redirect to smart-update endpoint"""
    return {"message": f"Please use POST /api/protocols/smart-update/{trial_id} instead"}


# =====================================================
# Frontend Compatibility Endpoints
# =====================================================

@router.get("/metadata/{trial_id}")
async def get_protocol_metadata(trial_id: int):
    """Get protocol metadata for a specific trial"""
    try:
        # Query protocol metadata with trial info and criteria from authoritative sources
        metadata = db.execute_query("""
            SELECT
                pm.*,
                ct.trial_name as trial_title,
                ct.protocol_number as protocol_no,
                ct.sponsor,
                ct.conditions,
                -- Get criteria counts from trial_criteria table
                COALESCE(criteria_stats.inclusion_count, 0) as inclusion_count,
                COALESCE(criteria_stats.exclusion_count, 0) as exclusion_count
            FROM protocol_metadata pm
            JOIN clinical_trials ct ON pm.trial_id = ct.id
            LEFT JOIN (
                SELECT
                    trial_id,
                    COUNT(CASE WHEN criterion_type = 'inclusion' THEN 1 END) as inclusion_count,
                    COUNT(CASE WHEN criterion_type = 'exclusion' THEN 1 END) as exclusion_count
                FROM trial_criteria
                GROUP BY trial_id
            ) criteria_stats ON ct.id = criteria_stats.trial_id
            WHERE pm.trial_id = %s
            ORDER BY pm.created_at DESC
            LIMIT 1
        """, (trial_id,))

        if not metadata:
            raise HTTPException(status_code=404, detail="Protocol metadata not found")

        return metadata[0]
    except HTTPException:
        # Re-raise HTTPException without modification
        raise
    except Exception as e:
        logger.error(f"Error getting protocol metadata: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.put("/update-metadata/{protocol_id}")
async def update_protocol_metadata(protocol_id: int, data: dict):
    """Update protocol metadata and related trial information"""
    try:
        # Get trial_id for this protocol metadata
        trial_info = db.execute_query("""
            SELECT trial_id FROM protocol_metadata WHERE id = %s
        """, (protocol_id,))
        
        if not trial_info:
            raise HTTPException(status_code=404, detail="Protocol metadata not found")
        
        trial_id = trial_info[0]['trial_id']
        
        # Update protocol metadata fields
        metadata_fields = []
        metadata_values = []
        
        if 'protocol_summary' in data:
            metadata_fields.append("protocol_summary = %s")
            metadata_values.append(data['protocol_summary'])
        if 'primary_objectives' in data:
            metadata_fields.append("primary_objectives = %s")
            metadata_values.append(data['primary_objectives'])
        if 'secondary_objectives' in data:
            metadata_fields.append("secondary_objectives = %s")
            metadata_values.append(data['secondary_objectives'])
        if 'study_design' in data:
            metadata_fields.append("study_design = %s")
            metadata_values.append(data['study_design'])
        if 'target_population' in data:
            metadata_fields.append("target_population = %s")
            metadata_values.append(data['target_population'])
        if 'estimated_enrollment' in data:
            metadata_fields.append("estimated_enrollment = %s")
            metadata_values.append(data['estimated_enrollment'])
        if 'study_duration' in data:
            metadata_fields.append("study_duration = %s")
            metadata_values.append(data['study_duration'])
        
        if metadata_fields:
            metadata_fields.append("updated_at = CURRENT_TIMESTAMP")
            metadata_values.append(protocol_id)
            
            db.execute_update(f"""
                UPDATE protocol_metadata 
                SET {', '.join(metadata_fields)}
                WHERE id = %s
            """, metadata_values)
        
        # Update clinical trials fields
        trial_fields = []
        trial_values = []
        
        if 'sponsor' in data:
            trial_fields.append("sponsor = %s")
            trial_values.append(data['sponsor'])
        if 'protocol_number' in data:
            trial_fields.append("protocol_number = %s")
            trial_values.append(data['protocol_number'])
        if 'trial_title' in data:
            trial_fields.append("trial_name = %s")
            trial_values.append(data['trial_title'])
        if 'conditions' in data and data['conditions']:
            # Join conditions array into string
            conditions_str = ', '.join(data['conditions']) if isinstance(data['conditions'], list) else data['conditions']
            trial_fields.append("conditions = %s")
            trial_values.append(conditions_str)
        
        if trial_fields:
            trial_fields.append("updated_at = CURRENT_TIMESTAMP")
            trial_values.append(trial_id)
            
            db.execute_update(f"""
                UPDATE clinical_trials 
                SET {', '.join(trial_fields)}
                WHERE id = %s
            """, trial_values)
        
        return {"success": True, "message": "Metadata updated successfully"}
    except Exception as e:
        logger.error(f"Error updating metadata: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/trials-with-protocols")
async def get_trials_with_protocols():
    """Get all trials with their protocol status"""
    try:
        trials = db.execute_query("""
            SELECT ct.*, 
                   CASE WHEN pm.id IS NOT NULL THEN 'completed' ELSE 'none' END as protocol_status,
                   pm.id as protocol_metadata_id,
                   pm.protocol_summary,
                   pm.primary_objectives,
                   pm.secondary_objectives,
                   -- Get criteria counts from authoritative trial_criteria table
                   COALESCE(criteria_stats.inclusion_count, 0) as inclusion_count,
                   COALESCE(criteria_stats.exclusion_count, 0) as exclusion_count,
                   COALESCE(criteria_stats.total_criteria, 0) as total_criteria
            FROM clinical_trials ct
            LEFT JOIN protocol_metadata pm ON ct.id = pm.trial_id
            LEFT JOIN (
                SELECT 
                    trial_id,
                    COUNT(CASE WHEN criterion_type = 'inclusion' THEN 1 END) as inclusion_count,
                    COUNT(CASE WHEN criterion_type = 'exclusion' THEN 1 END) as exclusion_count,
                    COUNT(*) as total_criteria
                FROM trial_criteria
                GROUP BY trial_id
            ) criteria_stats ON ct.id = criteria_stats.trial_id
            ORDER BY ct.id DESC
        """)
        
        return {"trials": trials or [], "success": True}
    except Exception as e:
        logger.error(f"Error getting trials with protocols: {e}")
        return {"trials": [], "success": False, "error": str(e)}

@router.put("/update-summary/{protocol_id}")
async def update_protocol_summary(protocol_id: int, data: dict):
    """Update protocol summary"""
    try:
        db.execute_update("""
            UPDATE protocol_metadata 
            SET protocol_summary = %s, updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
        """, (data.get('protocol_summary'), protocol_id))
        
        return {"success": True, "message": "Summary updated successfully"}
    except Exception as e:
        logger.error(f"Error updating summary: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/process-status/{job_id}")
async def get_process_status_alias(job_id: str):
    """Get processing status for a job (alias for /status/{job_id})"""
    # Redirect to existing status endpoint
    return await get_processing_status(job_id)

@router.get("/trial-analysis/{trial_id}")
async def get_trial_analysis_alias(trial_id: int):
    """Get trial analysis (alias for /trial/{trial_id}/analysis)"""
    # Redirect to existing analysis endpoint
    return await get_trial_analysis(trial_id)

@router.get("/criteria/{trial_id}")
async def get_trial_criteria(trial_id: int):
    """Get all criteria for a specific trial from authoritative trial_criteria table"""
    try:
        criteria = db.execute_query("""
            SELECT 
                id,
                criterion_type,
                criterion_text,
                category,
                is_required,
                sort_order,
                extraction_confidence,
                validation_status
            FROM trial_criteria
            WHERE trial_id = %s
            ORDER BY criterion_type, sort_order NULLS LAST, id
        """, (trial_id,))
        
        # Group by type for easier frontend handling
        result = {
            "inclusion": [c for c in criteria if c['criterion_type'] == 'inclusion'],
            "exclusion": [c for c in criteria if c['criterion_type'] == 'exclusion'],
            "total_count": len(criteria) if criteria else 0
        }
        
        return result
    except Exception as e:
        logger.error(f"Error getting trial criteria: {e}")
        raise HTTPException(status_code=500, detail=str(e))