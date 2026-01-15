"""
Serialized Criteria Extraction Processor
Separates inclusion and exclusion criteria extraction into individual API calls
to improve reliability and extraction completeness.
"""

import json
import logging
import asyncio
from typing import Dict, List, Any, Optional

from core.services.gemini_service import gemini_service

logger = logging.getLogger(__name__)

class SerializedCriteriaProcessor:
    """
    Processes criteria extraction in separate, focused API calls
    to improve reliability and reduce timeout issues.
    """
    
    def __init__(self):
        self.retry_attempts = 3
        self.retry_delay = 2  # seconds
        
    async def extract_criteria_serialized(self, text: str, method: str = "document_ai") -> Dict[str, Any]:
        """
        Extract criteria using serialized approach:
        1. Extract inclusion criteria only
        2. Extract exclusion criteria only
        3. Combine results
        """
        logger.info(f"🔄 Starting serialized criteria extraction (from {method})")
        
        # Use first 35,000 characters like current system
        text_chunk = text[:35000] if len(text) > 35000 else text
        
        results = {
            'success': False,
            'method': 'serialized_extraction',
            'source_method': method,
            'text_length': len(text_chunk),
            'inclusion_extraction': {},
            'exclusion_extraction': {},
            'combined_results': {
                'inclusion': [],
                'exclusion': []
            },
            'processing_stats': {
                'inclusion_success': False,
                'exclusion_success': False,
                'total_criteria': 0,
                'total_latency': 0,
                'api_calls_made': 0
            }
        }
        
        # Step 1: Extract inclusion criteria
        logger.info("📝 Extracting inclusion criteria...")
        inclusion_result = await self._extract_inclusion_criteria(text_chunk)
        results['inclusion_extraction'] = inclusion_result
        results['processing_stats']['inclusion_success'] = inclusion_result['success']
        results['processing_stats']['total_latency'] += inclusion_result.get('latency', 0)
        results['processing_stats']['api_calls_made'] += 1
        
        if inclusion_result['success']:
            results['combined_results']['inclusion'] = inclusion_result.get('criteria', [])
            logger.info(f"✅ Found {len(results['combined_results']['inclusion'])} inclusion criteria")
        else:
            logger.warning(f"❌ Inclusion criteria extraction failed: {inclusion_result.get('error')}")
        
        # Rate limiting between calls
        await asyncio.sleep(self.retry_delay)
        
        # Step 2: Extract exclusion criteria
        logger.info("📝 Extracting exclusion criteria...")
        exclusion_result = await self._extract_exclusion_criteria(text_chunk)
        results['exclusion_extraction'] = exclusion_result
        results['processing_stats']['exclusion_success'] = exclusion_result['success']
        results['processing_stats']['total_latency'] += exclusion_result.get('latency', 0)
        results['processing_stats']['api_calls_made'] += 1
        
        if exclusion_result['success']:
            results['combined_results']['exclusion'] = exclusion_result.get('criteria', [])
            logger.info(f"✅ Found {len(results['combined_results']['exclusion'])} exclusion criteria")
        else:
            logger.warning(f"❌ Exclusion criteria extraction failed: {exclusion_result.get('error')}")
        
        # Calculate final success and statistics
        results['processing_stats']['total_criteria'] = (
            len(results['combined_results']['inclusion']) + 
            len(results['combined_results']['exclusion'])
        )
        
        # Consider successful if at least one extraction succeeded
        results['success'] = (
            results['processing_stats']['inclusion_success'] or 
            results['processing_stats']['exclusion_success']
        )
        
        if results['success']:
            logger.info(f"🎯 Serialized extraction complete: {results['processing_stats']['total_criteria']} total criteria in {results['processing_stats']['total_latency']:.2f}s")
        else:
            logger.error("💥 Serialized extraction failed completely")
        
        return results
    
    async def _extract_inclusion_criteria(self, text: str) -> Dict[str, Any]:
        """Extract only inclusion criteria with focused prompt"""
        import time
        start_time = time.time()
        
        prompt = f"""Extract ONLY the inclusion criteria from this clinical trial protocol text.

Protocol text:
{text}

INSTRUCTIONS:
1. Look specifically for sections labeled "Inclusion Criteria:", "Participants are eligible", "Eligible participants", etc.
2. Extract EACH individual inclusion criterion as a separate item
3. Ignore exclusion criteria completely
4. Categorize each criterion appropriately

Return ONLY valid JSON in this exact format:
{{
  "inclusion_criteria": [
    {{"text": "Age ≥ 18 years", "category": "demographics"}},
    {{"text": "Documented medical history of condition", "category": "medical_history"}},
    {{"text": "Written informed consent obtained", "category": "study_procedures"}}
  ]
}}

Categories:
- demographics: age, gender, BMI requirements
- laboratory: lab values, biomarkers
- disease_specific: disease severity, duration, diagnosis  
- medical_history: past medical conditions, surgeries
- medications: prohibited/required medications
- reproductive: pregnancy, contraception requirements
- safety: allergies, contraindications
- study_procedures: consent, compliance requirements
- vital_signs: blood pressure, heart rate
- general: other requirements

Extract ALL inclusion criteria. Be comprehensive and thorough."""
        
        result = {
            'success': False,
            'latency': 0,
            'criteria': [],
            'raw_response': None,
            'error': None,
            'retry_attempts': 0
        }
        
        # Retry logic for inclusion criteria
        for attempt in range(self.retry_attempts):
            try:
                logger.info(f"  Inclusion attempt {attempt + 1}/{self.retry_attempts}...")
                
                response = await gemini_service.generate_text(prompt, max_tokens=3000)
                latency = time.time() - start_time
                
                if not response:
                    raise Exception("Empty response from Gemini")
                
                # Clean and parse response
                response = response.strip()
                if response.startswith('```'):
                    response = response.split('```')[1]
                    if response.startswith('json'):
                        response = response[4:]
                
                # Parse JSON
                data = json.loads(response)
                criteria = data.get('inclusion_criteria', [])
                
                if criteria:
                    result.update({
                        'success': True,
                        'latency': latency,
                        'criteria': criteria,
                        'raw_response': response[:500],
                        'retry_attempts': attempt + 1
                    })
                    logger.info(f"    ✅ Success: {len(criteria)} inclusion criteria extracted")
                    break
                else:
                    logger.warning(f"    ⚠️  No criteria found in response")
                    result['error'] = "No criteria found in response"
                    
            except json.JSONDecodeError as e:
                result['error'] = f"JSON parse error: {str(e)}"
                logger.warning(f"    🔍 JSON parse error (attempt {attempt + 1}): {e}")
                
            except asyncio.TimeoutError:
                result['error'] = "API timeout"
                logger.warning(f"    ⏱️  Timeout (attempt {attempt + 1})")
                
            except Exception as e:
                result['error'] = str(e)
                logger.warning(f"    💥 Error (attempt {attempt + 1}): {e}")
            
            # Wait before retry (except on last attempt)
            if attempt < self.retry_attempts - 1:
                await asyncio.sleep(self.retry_delay)
        
        result['latency'] = time.time() - start_time
        return result
    
    async def _extract_exclusion_criteria(self, text: str) -> Dict[str, Any]:
        """Extract only exclusion criteria with focused prompt"""
        import time
        start_time = time.time()
        
        prompt = f"""Extract ONLY the exclusion criteria from this clinical trial protocol text.

Protocol text:
{text}

INSTRUCTIONS:
1. Look specifically for sections labeled "Exclusion Criteria:", "Participants are excluded", "Not eligible participants", etc.
2. Extract EACH individual exclusion criterion as a separate item
3. Ignore inclusion criteria completely
4. Categorize each criterion appropriately

Return ONLY valid JSON in this exact format:
{{
  "exclusion_criteria": [
    {{"text": "Pregnancy or nursing", "category": "reproductive"}},
    {{"text": "Known hypersensitivity to study drug", "category": "safety"}},
    {{"text": "Current use of prohibited medications", "category": "medications"}}
  ]
}}

Categories:
- demographics: age, gender, BMI restrictions
- laboratory: lab value exclusions, biomarker limits
- disease_specific: disease contraindications, severity limits
- medical_history: past medical conditions that exclude
- medications: prohibited medications, drug interactions
- reproductive: pregnancy, nursing restrictions
- safety: allergies, contraindications, risk factors
- study_procedures: inability to comply, consent issues
- vital_signs: vital sign limitations
- general: other exclusionary factors

Extract ALL exclusion criteria. Be comprehensive and thorough."""
        
        result = {
            'success': False,
            'latency': 0,
            'criteria': [],
            'raw_response': None,
            'error': None,
            'retry_attempts': 0
        }
        
        # Retry logic for exclusion criteria
        for attempt in range(self.retry_attempts):
            try:
                logger.info(f"  Exclusion attempt {attempt + 1}/{self.retry_attempts}...")
                
                response = await gemini_service.generate_text(prompt, max_tokens=3000)
                latency = time.time() - start_time
                
                if not response:
                    raise Exception("Empty response from Gemini")
                
                # Clean and parse response
                response = response.strip()
                if response.startswith('```'):
                    response = response.split('```')[1]
                    if response.startswith('json'):
                        response = response[4:]
                
                # Parse JSON
                data = json.loads(response)
                criteria = data.get('exclusion_criteria', [])
                
                if criteria:
                    result.update({
                        'success': True,
                        'latency': latency,
                        'criteria': criteria,
                        'raw_response': response[:500],
                        'retry_attempts': attempt + 1
                    })
                    logger.info(f"    ✅ Success: {len(criteria)} exclusion criteria extracted")
                    break
                else:
                    logger.warning(f"    ⚠️  No criteria found in response")
                    result['error'] = "No criteria found in response"
                    
            except json.JSONDecodeError as e:
                result['error'] = f"JSON parse error: {str(e)}"
                logger.warning(f"    🔍 JSON parse error (attempt {attempt + 1}): {e}")
                
            except asyncio.TimeoutError:
                result['error'] = "API timeout"
                logger.warning(f"    ⏱️  Timeout (attempt {attempt + 1})")
                
            except Exception as e:
                result['error'] = str(e)
                logger.warning(f"    💥 Error (attempt {attempt + 1}): {e}")
            
            # Wait before retry (except on last attempt)
            if attempt < self.retry_attempts - 1:
                await asyncio.sleep(self.retry_delay)
        
        result['latency'] = time.time() - start_time
        return result
    
    async def compare_with_combined_approach(self, text: str, method: str = "document_ai") -> Dict[str, Any]:
        """
        Compare serialized approach with current combined approach
        for benchmarking purposes.
        """
        logger.info("🔄 Running comparison: Serialized vs Combined approach")
        
        # Test serialized approach
        serialized_result = await self.extract_criteria_serialized(text, method)
        
        await asyncio.sleep(3)  # Rate limiting between approaches
        
        # Test combined approach (current system)
        combined_result = await self._extract_combined_criteria(text, method)
        
        # Compare results
        comparison = {
            'serialized_approach': {
                'success': serialized_result['success'],
                'total_criteria': serialized_result['processing_stats']['total_criteria'],
                'inclusion_count': len(serialized_result['combined_results']['inclusion']),
                'exclusion_count': len(serialized_result['combined_results']['exclusion']),
                'total_latency': serialized_result['processing_stats']['total_latency'],
                'api_calls': serialized_result['processing_stats']['api_calls_made']
            },
            'combined_approach': {
                'success': combined_result['success'],
                'total_criteria': combined_result.get('total_criteria', 0),
                'inclusion_count': combined_result.get('inclusion_count', 0),
                'exclusion_count': combined_result.get('exclusion_count', 0),
                'total_latency': combined_result.get('latency', 0),
                'api_calls': 1
            },
            'improvement_metrics': {}
        }
        
        # Calculate improvement metrics
        s_total = comparison['serialized_approach']['total_criteria']
        c_total = comparison['combined_approach']['total_criteria']
        
        comparison['improvement_metrics'] = {
            'criteria_improvement_factor': s_total / c_total if c_total > 0 else float('inf'),
            'success_rate_comparison': {
                'serialized': comparison['serialized_approach']['success'],
                'combined': comparison['combined_approach']['success']
            },
            'latency_comparison': {
                'serialized': comparison['serialized_approach']['total_latency'],
                'combined': comparison['combined_approach']['total_latency'],
                'difference': comparison['serialized_approach']['total_latency'] - comparison['combined_approach']['total_latency']
            },
            'recommendation': 'serialized' if s_total > c_total * 1.5 else 'combined'
        }
        
        logger.info("📊 Comparison Results:")
        logger.info(f"  Serialized: {s_total} criteria in {comparison['serialized_approach']['total_latency']:.2f}s")
        logger.info(f"  Combined: {c_total} criteria in {comparison['combined_approach']['total_latency']:.2f}s")
        logger.info(f"  Improvement: {comparison['improvement_metrics']['criteria_improvement_factor']:.2f}x")
        logger.info(f"  Recommendation: {comparison['improvement_metrics']['recommendation']}")
        
        return comparison
    
    async def _extract_combined_criteria(self, text: str, method: str) -> Dict[str, Any]:
        """Test the current combined extraction approach for comparison"""
        import time
        start_time = time.time()
        
        text_chunk = text[:35000] if len(text) > 35000 else text
        
        prompt = f"""Extract ALL inclusion and exclusion criteria from this clinical trial protocol.

Protocol text:
{text_chunk}

Look for sections like "Inclusion Criteria:", "Exclusion Criteria:", etc.
Extract EVERY individual criterion as a separate item.

Return ONLY valid JSON:
{{
  "inclusion": [
    {{"text": "Age ≥ 18 years", "category": "demographics"}}
  ],
  "exclusion": [
    {{"text": "Pregnancy", "category": "reproductive"}}
  ]
}}

Categories: demographics, laboratory, disease_specific, medical_history, medications, reproductive, safety, study_procedures, vital_signs, general"""
        
        result = {
            'success': False,
            'latency': 0,
            'total_criteria': 0,
            'inclusion_count': 0,
            'exclusion_count': 0,
            'error': None
        }
        
        try:
            response = await gemini_service.generate_text(prompt, max_tokens=5000)
            latency = time.time() - start_time
            
            if response:
                # Clean and parse response
                response = response.strip()
                if response.startswith('```'):
                    response = response.split('```')[1]
                    if response.startswith('json'):
                        response = response[4:]
                
                data = json.loads(response)
                inclusion_count = len(data.get('inclusion', []))
                exclusion_count = len(data.get('exclusion', []))
                
                result.update({
                    'success': True,
                    'latency': latency,
                    'inclusion_count': inclusion_count,
                    'exclusion_count': exclusion_count,
                    'total_criteria': inclusion_count + exclusion_count
                })
                
            else:
                result.update({
                    'latency': latency,
                    'error': 'Empty response'
                })
                
        except Exception as e:
            result.update({
                'latency': time.time() - start_time,
                'error': str(e)
            })
        
        return result

# Create singleton instance
serialized_criteria_processor = SerializedCriteriaProcessor()