"""
CRIO Appointment Service - Creates patients and appointments via CRIO API
Follows same patterns as V3 Dashboard scheduling flow
"""

import logging
import requests
from typing import Dict, Optional
from datetime import datetime, timedelta
from core.database import db

logger = logging.getLogger(__name__)


class CRIOAppointmentService:
    """Service for creating patients and appointments in CRIO"""

    PROXY_URL = "https://scheduling-dashboard-proxy-480267397633.us-central1.run.app"
    CLIENT_ID = "1194"  # DelRicht client ID

    def __init__(self):
        self.session = requests.Session()

    def create_patient_and_appointment(
        self,
        site_id: str,
        study_id: str,
        patient_name: str,
        patient_phone: str,
        patient_email: str,
        patient_dob: str,
        appointment_datetime: str,
        coordinator_email: str,
        session_id: str
    ) -> Dict:
        """
        Create patient in CRIO and schedule appointment

        Returns:
            {'success': True, 'appointment_id': '...', 'patient_id': '...', 'subject_id': '...'}
            or
            {'success': False, 'error': '...'}
        """
        try:
            # Step 1: Get CRIO session tokens
            tokens = self._get_shared_session_tokens()
            if not tokens:
                return {'success': False, 'error': 'No CRIO session available. Please ensure V3 Dashboard is logged in.'}

            # Step 2: Create patient in CRIO
            logger.info(f"📝 STEP 1: Creating patient in CRIO")
            patient_result = self._create_patient_in_crio(
                site_id=site_id,
                study_id=study_id,
                patient_name=patient_name,
                patient_phone=patient_phone,
                patient_email=patient_email,
                patient_dob=patient_dob,
                tokens=tokens
            )

            if not patient_result.get('success'):
                return patient_result

            patient_id = patient_result['patient_id']
            subject_id = patient_result['subject_id']

            logger.info(f"✅ Patient created - patientId: {patient_id}, subjectId: {subject_id}")

            # Step 3: Get recruitment visit ID for this study
            logger.info(f"📝 STEP 2: Getting recruitment visit ID for study {study_id}")
            visit_id = self._get_recruitment_visit_id(study_id, site_id, subject_id)

            if not visit_id:
                # Patient was created but we can't schedule appointment
                logger.warning(f"⚠️ No recruitment visit ID found - patient created but appointment skipped")
                return {
                    'success': True,
                    'patient_id': patient_id,
                    'subject_id': subject_id,
                    'appointment_id': None,
                    'warning': 'Patient created but appointment could not be scheduled (visit ID not found)'
                }

            logger.info(f"✅ Found recruitment visit ID: {visit_id}")

            # Step 4: Create appointment
            logger.info(f"📝 STEP 3: Creating appointment in CRIO calendar")
            appointment_result = self._create_appointment_in_crio(
                site_id=site_id,
                subject_id=subject_id,
                visit_id=visit_id,
                appointment_datetime=appointment_datetime,
                coordinator_email=coordinator_email,
                patient_name=patient_name,
                tokens=tokens
            )

            if not appointment_result.get('success'):
                return {
                    'success': False,
                    'error': appointment_result.get('error'),
                    'patient_id': patient_id,
                    'subject_id': subject_id,
                    'partial': True  # Patient was created
                }

            appointment_id = appointment_result['appointment_id']
            logger.info(f"✅ Appointment created - appointmentId: {appointment_id}")

            # Step 5: Store minimal reference in our database (audit trail)
            self._store_appointment_reference(
                session_id=session_id,
                crio_appointment_id=appointment_id,
                crio_patient_id=patient_id,
                site_id=site_id,
                study_id=study_id,
                appointment_datetime=appointment_datetime
            )

            return {
                'success': True,
                'appointment_id': appointment_id,
                'patient_id': patient_id,
                'subject_id': subject_id
            }

        except Exception as e:
            logger.error(f"❌ CRIO appointment creation failed: {e}", exc_info=True)
            return {'success': False, 'error': str(e)}

    def _create_patient_in_crio(
        self,
        site_id: str,
        study_id: str,
        patient_name: str,
        patient_phone: str,
        patient_email: str,
        patient_dob: str,
        tokens: Dict[str, str]
    ) -> Dict:
        """Create patient record in CRIO and enroll in study"""

        # Parse name
        name_parts = patient_name.strip().split()
        first_name = name_parts[0] if name_parts else "Unknown"
        last_name = name_parts[-1] if len(name_parts) > 1 else "Unknown"

        # Format DOB for CRIO (DD-MMM-YYYY)
        dob_formatted = self._format_date_for_crio(patient_dob)

        # Build CRIO patient payload
        patient_payload = {
            "siteId": site_id,
            "patientInfo": {
                "status": "AVAILABLE",
                "birthDate": dob_formatted,
                "patientContact": {
                    "firstName": first_name,
                    "lastName": last_name,
                    "email": patient_email,
                    "cellPhone": patient_phone
                }
            },
            "studies": [
                {
                    "studyId": study_id,
                    "subjectStatus": "PREQUALIFIED",  # Chatbot patients are pre-screened
                    "recruitmentStatus": "SCHEDULED_V1"  # Moving to scheduling
                }
            ]
        }

        logger.info(f"   Creating patient: {first_name} {last_name}")
        logger.info(f"   Study: {study_id}, Site: {site_id}")

        try:
            url = f"{self.PROXY_URL}/crio/production/patient?client_id={self.CLIENT_ID}"

            response = self.session.post(
                url,
                json=patient_payload,
                params={
                    'session_id': tokens['session_id'],
                    'csrf_token': tokens['csrf_token']
                },
                timeout=30
            )

            if response.status_code == 401:
                return {'success': False, 'error': 'CRIO session expired - please log into V3 Dashboard'}

            response.raise_for_status()
            data = response.json()

            # Extract patient ID and subject ID from response
            patient_id = data.get('patientInfo', {}).get('patientId')
            studies = data.get('studies', [])
            subject_id = studies[0].get('subjectId') if studies else None

            if not patient_id or not subject_id:
                logger.error(f"❌ Patient creation response missing IDs: {data}")
                return {'success': False, 'error': 'CRIO response missing patient or subject ID'}

            return {
                'success': True,
                'patient_id': patient_id,
                'subject_id': subject_id,
                'full_response': data
            }

        except requests.RequestException as e:
            logger.error(f"❌ CRIO patient creation failed: {e}")
            return {'success': False, 'error': f'CRIO API error: {str(e)}'}

    def _get_recruitment_visit_id(
        self,
        study_id: str,
        site_id: str,
        subject_id: str
    ) -> Optional[str]:
        """
        Get the studyVisitId for Recruitment/Screening visit

        Uses the visit-mappings discovery endpoint that V3 Dashboard uses
        """
        try:
            url = f"{self.PROXY_URL}/api/visit-mappings/discover/{study_id}"

            response = self.session.post(
                url,
                params={'site_id': site_id},
                timeout=10
            )

            if response.ok:
                data = response.json()
                if data.get('discovered'):
                    visit_id = data.get('recruitmentVisitId')
                    logger.info(f"   ✅ Auto-discovered recruitment visit ID: {visit_id}")
                    return visit_id

            # If discovery fails, log and return None
            logger.warning(f"   ⚠️ Could not discover recruitment visit ID for study {study_id}")
            return None

        except Exception as e:
            logger.error(f"❌ Visit ID discovery failed: {e}")
            return None

    def _create_appointment_in_crio(
        self,
        site_id: str,
        subject_id: str,
        visit_id: str,
        appointment_datetime: str,
        coordinator_email: str,
        patient_name: str,
        tokens: Dict[str, str]
    ) -> Dict:
        """Create appointment in CRIO calendar"""

        # Parse datetime
        if isinstance(appointment_datetime, str):
            dt = datetime.fromisoformat(appointment_datetime.replace('Z', '+00:00'))
        else:
            dt = appointment_datetime

        # Calculate end time (30 minutes later)
        end_dt = dt + timedelta(minutes=30)

        # Format for CRIO (DD-MMM-YYYY HH:MM in EST)
        start_formatted = self._format_datetime_for_crio(dt)
        end_formatted = self._format_datetime_for_crio(end_dt)

        # Build CRIO appointment payload (following V3 Dashboard pattern)
        appointment_payload = {
            "siteId": site_id,
            "subjectId": subject_id,
            "studyVisitId": visit_id,
            "appointmentId": "",  # Empty for new appointment
            "startDate": start_formatted,
            "endDate": end_formatted,
            "calendar": coordinator_email,
            "notes": f"Chatbot Booking - Patient: {patient_name} - Scheduled via AI assistant on {datetime.now().strftime('%d-%b-%Y %H:%M')}"
        }

        logger.info(f"   Scheduling appointment:")
        logger.info(f"   Time: {start_formatted} to {end_formatted}")
        logger.info(f"   Calendar: {coordinator_email}")

        try:
            url = f"{self.PROXY_URL}/crio/production/calendar/update-appointment"

            response = self.session.put(
                url,
                json=appointment_payload,
                params={
                    'session_id': tokens['session_id'],
                    'csrf_token': tokens['csrf_token'],
                    'client_id': self.CLIENT_ID
                },
                timeout=30
            )

            if response.status_code == 401:
                return {'success': False, 'error': 'CRIO session expired'}

            response.raise_for_status()
            data = response.json()

            # Extract appointment ID from response
            appointment_id = (
                data.get('appointmentId') or
                data.get('calendarAppointmentKey') or
                data.get('id')
            )

            if not appointment_id:
                logger.error(f"❌ Appointment creation response missing ID: {data}")
                return {'success': False, 'error': 'CRIO response missing appointment ID'}

            return {
                'success': True,
                'appointment_id': appointment_id,
                'full_response': data
            }

        except requests.RequestException as e:
            logger.error(f"❌ CRIO appointment creation failed: {e}")
            return {'success': False, 'error': f'CRIO API error: {str(e)}'}

    def _get_shared_session_tokens(self) -> Optional[Dict[str, str]]:
        """Get valid CRIO session tokens from shared database"""
        try:
            result = db.execute_query("""
                SELECT session_id, csrf_token, expires_at,
                       EXTRACT(EPOCH FROM (expires_at - NOW())) / 3600 as hours_remaining
                FROM crio_shared_session
                WHERE is_active = TRUE AND expires_at > NOW()
                ORDER BY authenticated_at DESC
                LIMIT 1
            """)

            if result:
                session = result[0]

                # Update usage tracking
                db.execute_update("""
                    UPDATE crio_shared_session
                    SET last_used_at = NOW(),
                        used_by_chatbot_count = used_by_chatbot_count + 1
                    WHERE is_active = TRUE AND expires_at > NOW()
                """)

                logger.info(f"✅ Using shared CRIO session (expires in {session['hours_remaining']:.1f} hours)")

                return {
                    'session_id': result[0]['session_id'],
                    'csrf_token': result[0]['csrf_token']
                }

            logger.warning("⚠️ No valid CRIO session available")
            return None

        except Exception as e:
            logger.error(f"❌ Failed to get CRIO session: {e}")
            return None

    def _store_appointment_reference(
        self,
        session_id: str,
        crio_appointment_id: str,
        crio_patient_id: str,
        site_id: str,
        study_id: str,
        appointment_datetime: str
    ):
        """Store minimal appointment reference in PostgreSQL for audit trail"""
        try:
            # Parse datetime
            if isinstance(appointment_datetime, str):
                dt = datetime.fromisoformat(appointment_datetime.replace('Z', '+00:00'))
            else:
                dt = appointment_datetime

            # Store in appointments table (CRIO is source of truth)
            db.execute_insert_returning("""
                INSERT INTO appointments
                (crio_appointment_id, crio_patient_id, session_id, site_id, study_id,
                 visit_id, coordinator_email, appointment_date, status, notes)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'scheduled', %s)
                RETURNING id
            """, (
                crio_appointment_id,
                crio_patient_id,
                session_id,
                site_id,
                study_id,
                'recruitment',  # Generic visit type
                '',  # Coordinator email not critical for audit
                dt,
                f"Created via chatbot on {datetime.now().strftime('%Y-%m-%d %H:%M')}"
            ))

            logger.info(f"✅ Stored appointment reference in PostgreSQL")

        except Exception as e:
            logger.error(f"❌ Failed to store appointment reference: {e}")
            # Don't fail the whole flow if audit storage fails

    def _format_date_for_crio(self, date_str: str) -> str:
        """
        Format date for CRIO API: DD-MMM-YYYY
        Input: YYYY-MM-DD
        Output: DD-MMM-YYYY (e.g., 15-Jan-1985)
        """
        try:
            dt = datetime.strptime(date_str, '%Y-%m-%d')
            return dt.strftime('%d-%b-%Y')
        except ValueError:
            logger.error(f"Invalid date format: {date_str}")
            return date_str

    def _format_datetime_for_crio(self, dt: datetime) -> str:
        """
        Format datetime for CRIO API: DD-MMM-YYYY HH:MM
        Output: DD-MMM-YYYY HH:MM (e.g., 15-Aug-2025 09:00)
        """
        return dt.strftime('%d-%b-%Y %H:%M')


# Singleton instance
crio_appointment_service = CRIOAppointmentService()
