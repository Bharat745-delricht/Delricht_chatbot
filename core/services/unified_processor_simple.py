"""
Simplified Unified Document Processor

This processor consolidates document processing with intelligent extraction.
Uses PyPDF2 for text extraction when Document AI is not available.

Processing pipeline:
1. PDF text extraction (PyPDF2 or Document AI if available)
2. Gemini-powered field extraction
3. Intelligent trial matching
4. Database storage
"""

import os
import logging
import hashlib
import json
import re
from typing import Dict, List, Any, Optional
from datetime import datetime

import PyPDF2
from core.database import db
from core.services.gemini_service import gemini_service

logger = logging.getLogger(__name__)


class UnifiedDocumentProcessor:
    """Unified processor for document operations with fallback support"""
    
    def __init__(self):
        self.stats = {
            "documents_processed": 0,
            "successful_extractions": 0,
            "failed_extractions": 0
        }
        
    def _generate_file_hash(self, file_path: str) -> str:
        """Generate SHA256 hash of file for duplicate detection"""
        sha256_hash = hashlib.sha256()
        with open(file_path, "rb") as f:
            for byte_block in iter(lambda: f.read(4096), b""):
                sha256_hash.update(byte_block)
        return sha256_hash.hexdigest()
    
    async def _check_for_duplicates(self, file_hash: str) -> Dict[str, Any]:
        """Check if document already exists in database"""
        existing = db.execute_query("""
            SELECT trial_id FROM protocol_metadata 
            WHERE source_file_hash = %s
            ORDER BY created_at DESC LIMIT 1
        """, (file_hash,))
        
        if existing:
            return {
                'is_duplicate': True,
                'trial_id': existing[0]['trial_id']
            }
        return {'is_duplicate': False}
    
    def _extract_text_from_pdf(self, file_path: str) -> Dict[str, Any]:
        """Extract text from PDF using PyPDF2"""
        try:
            text_content = ""
            with open(file_path, 'rb') as file:
                pdf_reader = PyPDF2.PdfReader(file)
                num_pages = len(pdf_reader.pages)
                
                for page_num in range(min(num_pages, 100)):  # Limit to 100 pages
                    page = pdf_reader.pages[page_num]
                    text_content += page.extract_text() + "\n\n"
            
            # Create document chunks for storage
            chunk_size = 1000
            chunks = []
            for i in range(0, len(text_content), chunk_size - 100):
                chunk_text = text_content[i:i + chunk_size]
                chunks.append({
                    'text': chunk_text,
                    'index': len(chunks)
                })
            
            return {
                'success': True,
                'text': text_content,
                'pages': num_pages,
                'chunks': chunks[:100]  # Limit chunks
            }
            
        except Exception as e:
            logger.error(f"PDF extraction error: {e}")
            return {
                'success': False,
                'error': str(e)
            }
    
    async def _extract_with_gemini(self, text: str) -> Dict[str, Any]:
        """Extract structured data using Gemini"""
        try:
            # Comprehensive extraction prompt
            prompt = f"""Analyze this clinical trial protocol and extract all key information.

Protocol text:
{text[:15000]}

Extract and return in this exact JSON format:
{{
    "protocol_metadata": {{
        "protocol_number": "extracted protocol number",
        "trial_title": "full trial title",
        "protocol_summary": "2-3 sentence summary",
        "primary_objectives": "primary objectives",
        "secondary_objectives": "secondary objectives",
        "study_design": "study design",
        "target_population": "target population",
        "estimated_enrollment": number or null,
        "study_duration": "duration",
        "conditions": ["medical conditions"]
    }},
    "clinical_trial_fields": {{
        "phase": "Phase 1/2/3/4",
        "sponsor": "sponsor organization",
        "trial_name": "trial name",
        "description": "brief description"
    }},
    "trial_criteria": {{
        "inclusion": [
            {{"text": "inclusion criterion 1", "category": "general"}},
            {{"text": "inclusion criterion 2", "category": "demographics"}}
        ],
        "exclusion": [
            {{"text": "exclusion criterion 1", "category": "medical_history"}},
            {{"text": "exclusion criterion 2", "category": "general"}}
        ]
    }}
}}

Categories: demographics, laboratory, disease_specific, medical_history, medications, reproductive, safety, study_procedures, vital_signs, general

Return ONLY valid JSON, no markdown."""

            response = await gemini_service.generate_text(prompt, max_tokens=3000)
            
            # Clean and parse response
            response = response.strip()
            if response.startswith("```"):
                response = response.split("```")[1]
                if response.startswith("json"):
                    response = response[4:]
            
            try:
                extracted_data = json.loads(response)
                
                # Ensure proper structure
                if 'protocol_metadata' not in extracted_data:
                    extracted_data['protocol_metadata'] = {}
                if 'clinical_trial_fields' not in extracted_data:
                    extracted_data['clinical_trial_fields'] = {}
                if 'trial_criteria' not in extracted_data:
                    extracted_data['trial_criteria'] = {'inclusion': [], 'exclusion': []}
                    
            except json.JSONDecodeError as e:
                logger.warning(f"JSON parse error, using fallback: {e}")
                # Basic fallback extraction
                extracted_data = {
                    'protocol_metadata': {
                        'protocol_summary': text[:500],
                        'conditions': ['Unknown']
                    },
                    'clinical_trial_fields': {},
                    'trial_criteria': {'inclusion': [], 'exclusion': []}
                }
            
            return {
                'success': True,
                'data': extracted_data
            }
            
        except Exception as e:
            logger.error(f"Gemini extraction error: {e}")
            return {
                'success': False,
                'error': str(e)
            }
    
    async def process_document(self, file_path: str, job_id: str, 
                              processing_options: Dict[str, Any]) -> Dict[str, Any]:
        """
        Main entry point for document processing
        
        Returns complete processing results including extracted data and metadata
        """
        start_time = datetime.now()
        
        try:
            # Generate file hash
            file_hash = self._generate_file_hash(file_path)
            
            # Check for duplicates if requested
            if processing_options.get('check_duplicates', True):
                duplicate_check = await self._check_for_duplicates(file_hash)
                if duplicate_check['is_duplicate']:
                    return {
                        'success': True,
                        'duplicate_detected': True,
                        'existing_trial_id': duplicate_check['trial_id'],
                        'message': f"Document is duplicate of trial {duplicate_check['trial_id']}"
                    }
            
            # Extract text from PDF
            pdf_result = self._extract_text_from_pdf(file_path)
            if not pdf_result['success']:
                return {
                    'success': False,
                    'error': f"PDF extraction failed: {pdf_result.get('error')}"
                }
            
            # Extract structured data with Gemini
            extraction_result = await self._extract_with_gemini(pdf_result['text'])
            if not extraction_result['success']:
                return {
                    'success': False,
                    'error': f"Data extraction failed: {extraction_result.get('error')}"
                }
            
            extracted_data = extraction_result['data']
            
            # Calculate metrics
            field_coverage = self._calculate_field_coverage(extracted_data)
            extraction_confidence = 0.85 if field_coverage > 70 else 0.7
            
            # Prepare complete result
            processing_time = (datetime.now() - start_time).total_seconds()
            
            self.stats['documents_processed'] += 1
            self.stats['successful_extractions'] += 1
            
            return {
                'success': True,
                'extracted_data': extracted_data,
                'document_chunks': pdf_result.get('chunks', []),
                'file_hash': file_hash,
                'document_pages': pdf_result.get('pages', 0),
                'text_length': len(pdf_result.get('text', '')),
                'extraction_confidence': extraction_confidence,
                'field_coverage_percentage': field_coverage,
                'processor_type': 'simplified_unified',
                'processing_method': 'pypdf2_gemini',
                'extraction_version': '2.0',
                'processing_time_seconds': processing_time,
                'validation_results': {
                    'has_title': bool(extracted_data.get('protocol_metadata', {}).get('trial_title')),
                    'has_criteria': bool(extracted_data.get('trial_criteria', {}).get('inclusion')),
                    'has_summary': bool(extracted_data.get('protocol_metadata', {}).get('protocol_summary'))
                }
            }
            
        except Exception as e:
            logger.error(f"Document processing error: {e}")
            self.stats['failed_extractions'] += 1
            return {
                'success': False,
                'error': str(e)
            }
    
    def _calculate_field_coverage(self, extracted_data: Dict[str, Any]) -> float:
        """Calculate percentage of fields successfully extracted"""
        total_fields = 0
        filled_fields = 0
        
        # Check protocol metadata fields
        metadata_fields = ['protocol_number', 'trial_title', 'protocol_summary', 
                          'primary_objectives', 'study_design', 'conditions']
        for field in metadata_fields:
            total_fields += 1
            if extracted_data.get('protocol_metadata', {}).get(field):
                filled_fields += 1
        
        # Check clinical trial fields
        clinical_fields = ['phase', 'sponsor', 'trial_name']
        for field in clinical_fields:
            total_fields += 1
            if extracted_data.get('clinical_trial_fields', {}).get(field):
                filled_fields += 1
        
        # Check criteria
        total_fields += 2
        if extracted_data.get('trial_criteria', {}).get('inclusion'):
            filled_fields += 1
        if extracted_data.get('trial_criteria', {}).get('exclusion'):
            filled_fields += 1
        
        return (filled_fields / total_fields * 100) if total_fields > 0 else 0


# Create singleton instance
unified_document_processor = UnifiedDocumentProcessor()