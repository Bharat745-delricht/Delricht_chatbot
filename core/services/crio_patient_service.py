"""
CRIO Patient & Appointment Service
Uses the EXACT same proxy endpoints and data formats as V3 Dashboard
Mirrors the working implementation from clinical-scheduling-dashboard-v3
"""

from typing import Dict, Optional, List
import requests
import logging
from datetime import datetime
from core.database import db

logger = logging.getLogger(__name__)


class CRIOPatientService:
    """
    Service for creating patients and booking appointments in CRIO
    Uses the same patterns as PatientSchedulingModal.tsx from V3 Dashboard
    """

    # Use the SAME proxy URL as V3 Dashboard
    PROXY_URL = "https://scheduling-dashboard-proxy-480267397633.us-central1.run.app"
    CLIENT_ID = "1194"  # DelRicht client ID

    def __init__(self):
        self.session = requests.Session()

    def create_patient(
        self,
        session_id: str,
        contact_info: Dict,
        site_id: str,
        study_id: str
    ) -> Optional[Dict]:
        """
        Create patient in CRIO using EXACT same format as V3 Dashboard

        Args:
            session_id: Conversation session ID (for tracking)
            contact_info: {
                first_name, last_name, email, phone_number,
                date_of_birth, gender
            }
            site_id: CRIO site ID (e.g., "2327")
            study_id: CRIO study ID

        Returns:
            {
                'patient_id': str,      # CRIO patient ID
                'subject_id': str,      # Subject ID for appointments
                'study_id': str,
                'full_response': dict   # Complete CRIO response
            }
        """

        # Format date for CRIO (dd-MMM-yyyy format like "15-AUG-1990")
        dob = datetime.strptime(contact_info['date_of_birth'], '%Y-%m-%d')
        dob_crio = self._format_date_for_crio(dob)

        # Build CRIO patient payload - EXACT same structure as V3 Dashboard
        # See: PatientSchedulingModal.tsx lines 645-689
        patient_data = {
            'siteId': site_id,
            'patientInfo': {
                'externalId': f"CHATBOT_{int(datetime.now().timestamp() * 1000)}",
                'birthDate': dob_crio,
                'status': 'AVAILABLE',
                'gender': contact_info['gender'],  # 'M' or 'F'
                'sex': contact_info['gender'],
                'notes': f"Patient from chatbot session {session_id}",
                'doNotCall': False,
                'doNotEmail': False,
                'doNotText': False,
                'patientContact': {
                    'firstName': contact_info['first_name'],
                    'middleName': '',
                    'lastName': contact_info['last_name'],
                    'email': contact_info['email'],
                    'cellPhone': contact_info['phone_number'],
                    'homePhone': '',
                    'address1': '',
                    'address2': '',
                    'city': '',
                    'state': '',
                    'postalCode': '',
                    'countryCode': 'US'
                }
            },
            'studies': [{
                'studyId': study_id,
                'subjectStatus': 'INTERESTED',
                'recruitmentStatus': 'PROSPECT'
            }]
        }

        try:
            # Call proxy service - SAME endpoint as V3 Dashboard
            endpoint = f"{self.PROXY_URL}/crio/production/patient?client_id={self.CLIENT_ID}"

            logger.info(f"📋 Creating CRIO patient for session {session_id}")
            logger.info(f"   Site ID: {site_id}, Study ID: {study_id}")
            logger.info(f"   Patient: {contact_info['first_name']} {contact_info['last_name']}")

            response = self.session.post(
                endpoint,
                json=patient_data,
                timeout=30
            )

            if response.status_code != 200:
                logger.error(f"❌ CRIO patient creation failed: {response.status_code}")
                logger.error(f"   Response: {response.text[:500]}")
                return None

            response_data = response.json()
            logger.info(f"✅ CRIO patient created successfully")

            # Extract patient data - SAME logic as V3 Dashboard (lines 714-734)
            patient_id = (
                response_data.get('patientInfo', {}).get('patientId') or
                response_data.get('patientId') or
                response_data.get('id') or
                response_data.get('patientInfo', {}).get('id')
            )

            # Extract enrolled study data (critical for appointments)
            enrolled_study = next(
                (s for s in response_data.get('studies', []) if s.get('studyId') == study_id),
                None
            )

            subject_id = enrolled_study.get('subjectId') if enrolled_study else None

            logger.info(f"   Patient ID: {patient_id}")
            logger.info(f"   Subject ID: {subject_id}")

            if not patient_id:
                logger.error("❌ Patient creation succeeded but no patient ID in response")
                return None

            if not subject_id:
                logger.warning("⚠️  No subject ID found - appointments may not work")

            # Save mapping to database
            self._save_patient_mapping(
                session_id, contact_info, patient_id, site_id, study_id
            )

            return {
                'patient_id': patient_id,
                'subject_id': subject_id,
                'study_id': study_id,
                'full_response': response_data
            }

        except requests.RequestException as e:
            logger.error(f"❌ Network error creating CRIO patient: {e}")
            return None
        except Exception as e:
            logger.error(f"❌ Unexpected error creating CRIO patient: {e}", exc_info=True)
            return None

    def book_appointment(
        self,
        patient_id: str,
        subject_id: str,
        session_id: str,
        site_id: str,
        study_id: str,
        visit_id: str,
        coordinator_email: str,
        appointment_datetime: datetime,
        duration: int = 60
    ) -> Optional[str]:
        """
        Book appointment in CRIO

        NOTE: CRIO appointment creation requires subjectId (not patientId)
        This is returned from the patient creation response

        Args:
            patient_id: CRIO patient ID
            subject_id: CRIO subject ID (from patient creation response)
            session_id: Chatbot session ID
            site_id: CRIO site ID
            study_id: CRIO study ID
            visit_id: CRIO visit ID (e.g., "2624806" for Screening)
            coordinator_email: Coordinator email
            appointment_datetime: Python datetime object
            duration: Duration in minutes (default 60)

        Returns:
            CRIO appointment_id or None if failed
        """

        # Format datetime for CRIO (dd-MMM-yyyy HH:mm format like "15-AUG-2025 09:00")
        dt_crio = self._format_datetime_for_crio(appointment_datetime)

        appointment_data = {
            'siteId': site_id,
            'studyId': study_id,
            'visitId': visit_id,
            'patientId': subject_id,  # IMPORTANT: Use subjectId, not patientId!
            'coordinatorEmail': coordinator_email,
            'appointmentDate': dt_crio,
            'duration': duration
        }

        try:
            endpoint = f"{self.PROXY_URL}/crio/production/appointment?client_id={self.CLIENT_ID}"

            logger.info(f"📅 Booking CRIO appointment")
            logger.info(f"   Subject ID: {subject_id}")
            logger.info(f"   Date/Time: {dt_crio}")
            logger.info(f"   Visit ID: {visit_id}")

            response = self.session.post(
                endpoint,
                json=appointment_data,
                timeout=30
            )

            if response.status_code != 200:
                logger.error(f"❌ CRIO appointment booking failed: {response.status_code}")
                logger.error(f"   Response: {response.text[:500]}")
                return None

            response_data = response.json()
            logger.info(f"✅ CRIO appointment booked successfully")

            # Extract appointment ID from response
            appointment_id = (
                response_data.get('appointmentId') or
                response_data.get('id') or
                response_data.get('calendarAppointmentKey')
            )

            if not appointment_id:
                logger.warning("⚠️  Appointment may have been created but no ID returned")

            # Save appointment to database
            if appointment_id:
                self._save_appointment(
                    appointment_id, subject_id, session_id, site_id,
                    study_id, visit_id, coordinator_email,
                    appointment_datetime, duration
                )

            return appointment_id

        except requests.RequestException as e:
            logger.error(f"❌ Network error booking appointment: {e}")
            return None
        except Exception as e:
            logger.error(f"❌ Unexpected error booking appointment: {e}", exc_info=True)
            return None

    def update_appointment(
        self,
        appointment_id: str,
        site_id: str,
        study_id: str,
        subject_id: str,
        visit_id: str,
        new_datetime: datetime,
        coordinator_email: str = "thastings@delricht.com",
        notes: Optional[str] = None
    ) -> Optional[Dict]:
        """
        Reschedule existing CRIO appointment

        Uses: PUT /crio/production/calendar/update-appointment
        Mirrors: clinical-scheduling-dashboard-v3/src/services/crioApi.ts:759

        Args:
            appointment_id: CRIO calendarAppointmentKey
            site_id: CRIO site ID
            study_id: CRIO study ID
            subject_id: CRIO subject ID (NOT patient ID!)
            visit_id: CRIO visit ID
            new_datetime: New appointment datetime
            coordinator_email: Coordinator email
            notes: Optional notes for rescheduling reason

        Returns:
            {
                'success': True,
                'appointment_id': str,
                'old_date': datetime,
                'new_date': datetime
            }
        """

        # Format datetime for CRIO
        dt_crio = self._format_datetime_for_crio(new_datetime)

        # Get old appointment data for history
        old_appointment = self._get_appointment_data(appointment_id)

        payload = {
            'siteId': site_id,
            'subjectId': subject_id,
            'studyVisitId': visit_id,
            'appointmentId': appointment_id,
            'startDate': dt_crio,
            'endDate': dt_crio,  # Same as start for single appointment
            'calendar': coordinator_email,
            'notes': notes or f'Rescheduled via SMS automation on {datetime.now().strftime("%Y-%m-%d %H:%M")}'
        }

        try:
            endpoint = f"{self.PROXY_URL}/crio/production/calendar/update-appointment"

            logger.info(f"📅 Rescheduling CRIO appointment {appointment_id}")
            logger.info(f"   Old Date: {old_appointment.get('appointment_date') if old_appointment else 'unknown'}")
            logger.info(f"   New Date: {dt_crio}")

            response = self.session.put(
                endpoint,
                json=payload,
                timeout=30
            )

            if response.status_code != 200:
                logger.error(f"❌ CRIO reschedule failed: {response.status_code}")
                logger.error(f"   Response: {response.text[:500]}")
                return {
                    'success': False,
                    'error': f'CRIO API error: {response.status_code}',
                    'details': response.text[:500]
                }

            response_data = response.json()
            logger.info(f"✅ CRIO appointment rescheduled successfully")

            # Update database
            self._update_appointment_in_db(
                appointment_id,
                new_datetime,
                notes,
                old_appointment.get('appointment_date') if old_appointment else None
            )

            return {
                'success': True,
                'appointment_id': appointment_id,
                'old_date': old_appointment.get('appointment_date') if old_appointment else None,
                'new_date': new_datetime,
                'crio_response': response_data
            }

        except requests.RequestException as e:
            logger.error(f"❌ Network error rescheduling appointment: {e}")
            return {
                'success': False,
                'error': f'Network error: {str(e)}'
            }
        except Exception as e:
            logger.error(f"❌ Unexpected error rescheduling appointment: {e}", exc_info=True)
            return {
                'success': False,
                'error': f'Unexpected error: {str(e)}'
            }

    def _get_appointment_data(self, crio_appointment_id: str) -> Optional[Dict]:
        """Get appointment data from database"""
        try:
            query = "SELECT * FROM appointments WHERE crio_appointment_id = %s"
            result = db.execute_query(query, (crio_appointment_id,))
            return result[0] if result else None
        except Exception as e:
            logger.warning(f"⚠️  Could not fetch appointment data: {e}")
            return None

    def _update_appointment_in_db(
        self,
        crio_appointment_id: str,
        new_datetime: datetime,
        notes: Optional[str],
        old_datetime: Optional[datetime] = None
    ):
        """Update appointment in local database"""

        try:
            # Get old appointment data if not provided
            if not old_datetime:
                old_data = self._get_appointment_data(crio_appointment_id)
                old_datetime = old_data['appointment_date'] if old_data else None

            # Update appointment
            update_query = """
                UPDATE appointments
                SET appointment_date = %s,
                    status = 'rescheduled',
                    notes = COALESCE(notes || E'\n', '') || %s,
                    updated_at = CURRENT_TIMESTAMP
                WHERE crio_appointment_id = %s
            """

            db.execute_update(
                update_query,
                (new_datetime, notes or 'Rescheduled via SMS', crio_appointment_id)
            )

            # Save to reschedule history
            if old_datetime:
                history_query = """
                    INSERT INTO appointment_reschedule_history
                    (appointment_id, old_appointment_date, new_appointment_date,
                     old_crio_appointment_id, new_crio_appointment_id,
                     reason_code, reason_text, initiated_by, rescheduled_at)
                    SELECT id, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP
                    FROM appointments
                    WHERE crio_appointment_id = %s
                """

                db.execute_update(
                    history_query,
                    (
                        old_datetime,
                        new_datetime,
                        crio_appointment_id,
                        crio_appointment_id,  # Same ID for reschedule
                        'automated_sms',
                        notes or 'Patient requested via SMS',
                        'system',
                        crio_appointment_id
                    )
                )

            logger.info(f"💾 Updated appointment in database: {crio_appointment_id}")

        except Exception as e:
            logger.error(f"❌ Failed to update appointment in database: {e}", exc_info=True)

    def get_visit_id_for_study(
        self,
        study_id: str,
        site_id: str,
        visit_name: str = "Screening"
    ) -> Optional[str]:
        """
        Get visit ID for a study (e.g., Screening, Recruitment, Baseline)
        This queries CRIO or uses cached mappings

        Args:
            study_id: CRIO study ID
            site_id: CRIO site ID
            visit_name: Visit name (default "Screening")

        Returns:
            Visit ID string or None
        """

        # First try cached mapping from database
        query = """
            SELECT visit_id
            FROM study_visit_mappings
            WHERE study_id = %s AND site_id = %s AND visit_name = %s
        """

        result = db.execute_query(query, (study_id, site_id, visit_name))

        if result:
            visit_id = result[0]['visit_id']
            logger.info(f"✅ Found cached visit ID: {visit_id}")
            return visit_id

        # If not cached, query CRIO availability API to discover visits
        # The availability response includes visit IDs
        logger.info(f"⚠️  No cached visit ID for study {study_id}, visit '{visit_name}'")
        logger.info(f"   Consider calling /api/visit-mappings/discover/{study_id}?site_id={site_id}")

        return None

    def _format_date_for_crio(self, date: datetime) -> str:
        """Format date for CRIO (dd-MMM-yyyy like "15-AUG-1990")"""
        months = ['JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN',
                  'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC']

        day = date.day
        month = months[date.month - 1]
        year = date.year

        return f"{day:02d}-{month}-{year}"

    def _format_datetime_for_crio(self, dt: datetime) -> str:
        """Format datetime for CRIO (dd-MMM-yyyy HH:mm like "15-AUG-2025 09:00")"""
        date_part = self._format_date_for_crio(dt)
        time_part = f"{dt.hour:02d}:{dt.minute:02d}"
        return f"{date_part} {time_part}"

    def _save_patient_mapping(
        self,
        session_id: str,
        contact_info: Dict,
        crio_patient_id: str,
        site_id: str,
        study_id: str
    ):
        """Save session-to-patient mapping in database"""

        # Get contact_info_id
        query = "SELECT id FROM patient_contact_info WHERE session_id = %s"
        result = db.execute_query(query, (session_id,))
        contact_info_id = result[0]['id'] if result else None

        insert_query = """
            INSERT INTO crio_patient_mappings
            (session_id, contact_info_id, crio_patient_id, crio_site_id, crio_study_id, created_at)
            VALUES (%s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (session_id, crio_site_id, crio_study_id) DO UPDATE
            SET crio_patient_id = EXCLUDED.crio_patient_id,
                updated_at = CURRENT_TIMESTAMP
        """

        db.execute_update(
            insert_query,
            (session_id, contact_info_id, crio_patient_id, site_id, study_id)
        )

        logger.info(f"💾 Saved patient mapping: session {session_id} → patient {crio_patient_id}")

    def _save_appointment(
        self,
        crio_appointment_id: str,
        crio_subject_id: str,
        session_id: str,
        site_id: str,
        study_id: str,
        visit_id: str,
        coordinator_email: str,
        appointment_datetime: datetime,
        duration: int
    ):
        """Save appointment to database"""

        insert_query = """
            INSERT INTO appointments
            (crio_appointment_id, crio_patient_id, session_id, site_id, study_id,
             visit_id, coordinator_email, appointment_date, duration_minutes, status,
             created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'scheduled', CURRENT_TIMESTAMP)
        """

        db.execute_update(
            insert_query,
            (crio_appointment_id, crio_subject_id, session_id, site_id, study_id,
             visit_id, coordinator_email, appointment_datetime, duration)
        )

        logger.info(f"💾 Saved appointment: {crio_appointment_id} for {appointment_datetime}")


# Singleton instance
crio_patient_service = CRIOPatientService()
