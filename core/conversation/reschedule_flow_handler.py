"""
Automated SMS Rescheduling Flow Handler
Orchestrates the conversation flow for patient-initiated appointment rescheduling
"""

from typing import Optional, Dict, List
from datetime import datetime, timedelta
import logging
import re
import json

from core.database import db
from core.services.sms_service import sms_service
from core.services.crio_availability_service import CRIOAvailabilityService
from core.services.crio_patient_service import crio_patient_service
from core.services.email_service import email_service

logger = logging.getLogger(__name__)


class RescheduleFlowHandler:
    """
    Handles automated SMS rescheduling conversation flow

    State Machine:
    - RESCHEDULING_INITIATED → Initial SMS sent, waiting for response
    - RESCHEDULING_AWAITING_CONFIRMATION → Waiting for YES/RESCHEDULE
    - RESCHEDULING_AWAITING_AVAILABILITY → Asking patient when they're available
    - RESCHEDULING_AWAITING_SELECTION → Presented options, waiting for 1 or 2
    - RESCHEDULING_CONFIRMING → Booking appointment in CRIO
    - RESCHEDULING_COMPLETED → Success!
    - RESCHEDULING_FAILED → Escalate to coordinator
    """

    def __init__(self):
        try:
            self.availability_service = CRIOAvailabilityService()
        except Exception as e:
            logger.warning(f"⚠️  Could not initialize CRIOAvailabilityService: {e}")
            self.availability_service = None

    async def process_message(
        self,
        session_id: str,
        phone_number: str,
        message: str,
        current_state: str
    ) -> Dict:
        """
        Main entry point for reschedule flow

        Routes message to appropriate handler based on current state

        Returns:
            {
                'status': 'success' | 'failed' | 'escalated',
                'next_state': str,
                'action_taken': str
            }
        """

        logger.info(f"🔄 [RESCHEDULE-FLOW] Session: {session_id} | State: {current_state}")
        logger.info(f"   Message: {message}")

        try:
            # Normalize state to uppercase for comparison (database might use lowercase)
            state_upper = current_state.upper() if current_state else ''

            if state_upper == 'RESCHEDULING_AWAITING_CONFIRMATION':
                return await self.handle_confirmation_response(session_id, phone_number, message)

            elif state_upper == 'RESCHEDULING_AWAITING_AVAILABILITY':
                return await self.handle_availability_response(session_id, phone_number, message)

            elif state_upper == 'RESCHEDULING_AWAITING_SELECTION':
                return await self.handle_slot_selection(session_id, phone_number, message)

            else:
                logger.warning(f"⚠️  Unexpected state for reschedule flow: {current_state}")
                return {'status': 'failed', 'error': 'Invalid state'}

        except Exception as e:
            logger.error(f"❌ Error in reschedule flow: {e}", exc_info=True)
            await self._escalate(session_id, phone_number, f"System error: {str(e)}")
            return {'status': 'escalated', 'reason': 'error'}

    # ===================================================================
    # STATE HANDLERS
    # ===================================================================

    async def handle_confirmation_response(
        self,
        session_id: str,
        phone_number: str,
        message: str
    ) -> Dict:
        """
        Patient replied to initial SMS: "YES" or "RESCHEDULE"
        """

        message_lower = message.lower().strip()

        # Check for reschedule intent
        reschedule_keywords = ['reschedule', 'change', 'move', 'different']
        if any(kw in message_lower for kw in reschedule_keywords):
            logger.info(f"   ✅ Patient wants to reschedule")

            # Load reschedule request data
            request_data = self._load_reschedule_request(session_id)

            if not request_data:
                logger.error(f"   ❌ No reschedule request found for session {session_id}")
                await self._escalate(session_id, phone_number, "Missing reschedule request data")
                return {'status': 'escalated', 'reason': 'missing_data'}

            # Ask for availability
            after_date_str = request_data['reschedule_after_date'].strftime('%B %d')
            await sms_service.send_availability_request(phone_number, session_id, after_date_str)

            # Update state
            self._update_state(session_id, 'RESCHEDULING_AWAITING_AVAILABILITY')
            self._update_reschedule_status(session_id, 'patient_responded')

            return {'status': 'success', 'next_state': 'RESCHEDULING_AWAITING_AVAILABILITY'}

        # Check for confirmation (YES)
        confirm_keywords = ['yes', 'confirm', 'okay', 'ok', 'correct', 'good']
        if any(kw in message_lower for kw in confirm_keywords):
            logger.info(f"   ✅ Patient confirmed existing appointment")

            # Mark as completed
            response = "Great! Your appointment is confirmed. See you then!"
            await sms_service.send_sms(phone_number, response, session_id)

            self._update_state(session_id, 'RESCHEDULING_COMPLETED')
            self._update_reschedule_status(session_id, 'completed', notes='Patient confirmed existing appointment')

            return {'status': 'success', 'action_taken': 'confirmed_existing'}

        # Unclear response - ask again
        logger.info(f"   ⚠️  Unclear response: {message}")
        response = "Please reply YES to confirm your appointment, or RESCHEDULE to change it."
        await sms_service.send_sms(phone_number, response, session_id)

        return {'status': 'clarification_needed'}

    async def handle_availability_response(
        self,
        session_id: str,
        phone_number: str,
        message: str
    ) -> Dict:
        """
        Patient provided availability preferences
        Extract dates/times and find matching slots
        """

        logger.info(f"   🔍 Extracting availability from: {message}")

        # Extract availability data from natural language
        availability_data = self._extract_availability(message)

        # Load reschedule request data
        request_data = self._load_reschedule_request(session_id)

        if not request_data:
            await self._escalate(session_id, phone_number, "Missing reschedule request data")
            return {'status': 'escalated', 'reason': 'missing_data'}

        # Find matching slots
        slots = await self._find_matching_slots(
            site_id=request_data['site_id'],
            study_id=request_data['study_id'],
            after_date=request_data['reschedule_after_date'],
            availability_data=availability_data,
            max_results=2
        )

        if not slots or len(slots) == 0:
            logger.warning(f"   ⚠️  No available slots found")
            await sms_service.send_escalation_notice(phone_number, session_id, reason='availability')
            await self._escalate(session_id, phone_number, "No available slots matching patient preferences")
            self._update_state(session_id, 'RESCHEDULING_FAILED')
            return {'status': 'escalated', 'reason': 'no_availability'}

        # Present options
        await sms_service.send_slot_options(phone_number, session_id, slots)

        # Save slot options to metadata for selection
        self._save_slot_options(session_id, slots)

        # Update state
        self._update_state(session_id, 'RESCHEDULING_AWAITING_SELECTION')

        return {'status': 'success', 'next_state': 'RESCHEDULING_AWAITING_SELECTION', 'slots_found': len(slots)}

    async def handle_slot_selection(
        self,
        session_id: str,
        phone_number: str,
        message: str
    ) -> Dict:
        """
        Patient selected a time slot (1 or 2)
        Book appointment in CRIO and confirm
        """

        # Parse selection (1, 2, "first", "second", etc.)
        selection_index = self._parse_slot_selection(message)

        if selection_index is None:
            logger.warning(f"   ⚠️  Invalid selection: {message}")
            response = "Please reply with 1 or 2 to select a time."
            await sms_service.send_sms(phone_number, response, session_id)
            return {'status': 'invalid_selection'}

        # Load saved slot options
        slots = self._load_slot_options(session_id)

        if not slots or selection_index >= len(slots):
            logger.error(f"   ❌ No slots saved or invalid index: {selection_index}")
            await self._escalate(session_id, phone_number, "Slot selection error")
            return {'status': 'escalated', 'reason': 'slot_error'}

        selected_slot = slots[selection_index]
        logger.info(f"   ✅ Patient selected slot {selection_index + 1}: {selected_slot['formatted_datetime']}")

        # Send "working on it" message
        response = f"Perfect! I'm rescheduling your appointment to {selected_slot['formatted_datetime']}. One moment..."
        await sms_service.send_sms(phone_number, response, session_id)

        self._update_state(session_id, 'RESCHEDULING_CONFIRMING')

        # Book appointment in CRIO
        booking_result = await self._reschedule_crio_appointment(session_id, selected_slot)

        if not booking_result or not booking_result.get('success'):
            logger.error(f"   ❌ Failed to reschedule in CRIO: {booking_result.get('error') if booking_result else 'unknown'}")
            await sms_service.send_escalation_notice(phone_number, session_id, reason='error')
            await self._escalate(session_id, phone_number, f"CRIO booking failed: {booking_result.get('error') if booking_result else 'unknown'}")
            self._update_state(session_id, 'RESCHEDULING_FAILED')
            return {'status': 'escalated', 'reason': 'booking_failed'}

        # Success! Send confirmation
        request_data = self._load_reschedule_request(session_id)
        site_name = self._get_site_name(request_data['site_id'])

        await sms_service.send_confirmation(
            phone_number=phone_number,
            session_id=session_id,
            appointment_date=selected_slot['formatted_date'],
            appointment_time=selected_slot['formatted_time'],
            site_name=site_name
        )

        # Send email confirmation (dual-channel)
        await self._send_email_confirmation(session_id, selected_slot, request_data)

        # Update database
        self._update_state(session_id, 'RESCHEDULING_COMPLETED')
        self._update_reschedule_status(
            session_id,
            'completed',
            new_appointment_id=booking_result.get('appointment_id'),
            new_appointment_date=selected_slot['datetime']
        )

        logger.info(f"   ✅ Rescheduling completed successfully!")

        return {'status': 'success', 'appointment_id': booking_result.get('appointment_id')}

    # ===================================================================
    # AVAILABILITY EXTRACTION & SLOT FINDING
    # ===================================================================

    def _extract_availability(self, message: str) -> Dict:
        """
        Extract availability preferences from natural language

        Looks for:
        - Time of day: morning, afternoon, evening
        - Specific dates: "Nov 22", "December 1st"
        - Day preferences: "not Fridays", "weekdays only"
        - Date ranges: "Nov 20-25", "next week"
        """

        message_lower = message.lower()
        availability = {
            'time_of_day': None,
            'specific_dates': [],
            'excluded_days': [],
            'date_range': None
        }

        # Time of day
        if any(word in message_lower for word in ['morning', 'am', 'before noon']):
            availability['time_of_day'] = 'morning'
        elif any(word in message_lower for word in ['afternoon', 'pm', 'after lunch', '1pm', '2pm', '3pm', '4pm']):
            availability['time_of_day'] = 'afternoon'
        elif any(word in message_lower for word in ['evening', 'night', 'after work', '5pm', '6pm']):
            availability['time_of_day'] = 'evening'

        # Excluded days
        days_of_week = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']
        for day in days_of_week:
            if f'not {day}' in message_lower or f'no {day}' in message_lower:
                availability['excluded_days'].append(day.capitalize())

        # Date ranges (simple heuristics)
        if 'next week' in message_lower:
            availability['date_range'] = 'next_week'
        elif 'this week' in message_lower:
            availability['date_range'] = 'this_week'

        logger.info(f"   📅 Extracted availability: {availability}")

        return availability

    async def _find_matching_slots(
        self,
        site_id: str,
        study_id: str,
        after_date: datetime,
        availability_data: Dict,
        max_results: int = 2
    ) -> List[Dict]:
        """
        Query CRIO availability and filter by patient preferences

        Returns list of formatted slot dictionaries
        """

        if not self.availability_service:
            logger.error("   ❌ CRIO availability service not initialized")
            return []

        try:
            # Get coordinator email for this site
            coordinator_query = db.execute_query(
                "SELECT coordinator_email FROM site_coordinators WHERE site_id = %s",
                (site_id,)
            )
            coordinator_email = coordinator_query[0]['coordinator_email'] if coordinator_query else "thastings@delricht.com"

            logger.info(f"   🔍 Searching availability for next 30 days")
            logger.info(f"   Site: {site_id}, Study: {study_id}, Coordinator: {coordinator_email}")

            # Query CRIO availability (FIXED: use correct method signature)
            slots = self.availability_service.get_next_available_slots(
                site_id=site_id,
                study_id=study_id,
                coordinator_email=coordinator_email,
                num_slots=20,  # Get more than needed, will filter
                days_ahead=30
            )

            if not slots:
                logger.warning(f"   ⚠️  No slots returned from CRIO")
                return []

            logger.info(f"   Found {len(slots)} raw slots from CRIO")

            # Filter by time of day (use datetime_obj for datetime operations)
            time_of_day = availability_data.get('time_of_day')
            if time_of_day == 'morning':
                slots = [s for s in slots if s['datetime_obj'].hour < 12]
            elif time_of_day == 'afternoon':
                slots = [s for s in slots if 12 <= s['datetime_obj'].hour < 17]
            elif time_of_day == 'evening':
                slots = [s for s in slots if s['datetime_obj'].hour >= 17]

            # Filter by excluded days
            excluded_days = availability_data.get('excluded_days', [])
            if excluded_days:
                excluded_day_names = [d.lower() for d in excluded_days]
                slots = [s for s in slots if s['datetime_obj'].strftime('%A').lower() not in excluded_day_names]

            logger.info(f"   After filtering: {len(slots)} slots")

            # Format slots for SMS (use datetime_obj for strftime)
            formatted_slots = []
            for slot in slots[:max_results]:
                formatted_slots.append({
                    'datetime': slot['datetime_obj'],
                    'formatted_date': slot['datetime_obj'].strftime('%A %b %d'),  # "Wednesday Nov 22"
                    'formatted_time': slot['datetime_obj'].strftime('%-I:%M %p'),  # "2:00 PM"
                    'formatted_datetime': slot['datetime_obj'].strftime('%A %b %d at %-I:%M %p'),  # "Wednesday Nov 22 at 2:00 PM"
                    'capacity': slot.get('capacity_remaining', 1)
                })

            return formatted_slots

        except Exception as e:
            logger.error(f"   ❌ Error finding slots: {e}", exc_info=True)
            return []

    # ===================================================================
    # CRIO APPOINTMENT BOOKING
    # ===================================================================

    async def _reschedule_crio_appointment(
        self,
        session_id: str,
        selected_slot: Dict
    ) -> Optional[Dict]:
        """
        Call CRIO to reschedule the appointment

        Returns:
            {
                'success': True,
                'appointment_id': str,
                'old_date': datetime,
                'new_date': datetime
            }
        """

        try:
            # Load reschedule request data
            request_data = self._load_reschedule_request(session_id)

            if not request_data:
                logger.error(f"   ❌ No reschedule request data found")
                return {'success': False, 'error': 'Missing request data'}

            # Get CRIO IDs from direct column and metadata
            logger.info(f"   📋 Loading CRIO IDs for appointment rescheduling")
            appointment_id = request_data.get('current_appointment_id')
            site_id = request_data['site_id']
            study_id = request_data['study_id']
            logger.info(f"   📋 Direct columns: appt={appointment_id}, site={site_id}, study={study_id}")

            # Get subject_id and visit_id from metadata JSONB (preferred) or fallback to database lookup
            metadata = request_data.get('metadata', {})
            logger.info(f"   📋 Metadata retrieved: {json.dumps(metadata, indent=2) if metadata else 'EMPTY'}")

            subject_id = metadata.get('subject_id') if metadata else None
            visit_id = metadata.get('visit_id') if metadata else None

            if subject_id and visit_id:
                logger.info(f"   ✅ IDs found in metadata: subject={subject_id}, visit={visit_id}")
            else:
                logger.warning(f"   ⚠️  IDs not in metadata, attempting database lookup...")

            # Fallback: Try database lookup if not in metadata
            if not subject_id:
                subject_id = self._get_subject_id_for_session(session_id, site_id, study_id)
                if subject_id:
                    logger.info(f"   ✅ Subject ID found via database lookup: {subject_id}")
                else:
                    logger.error(f"   ❌ Subject ID not found in metadata OR database")

            if not visit_id:
                visit_id = self._get_visit_id(site_id, study_id)
                if visit_id:
                    logger.info(f"   ✅ Visit ID found via database lookup: {visit_id}")
                else:
                    logger.error(f"   ❌ Visit ID not found in metadata OR database")

            if not all([appointment_id, subject_id, visit_id]):
                logger.error(f"   ❌ MISSING REQUIRED IDs FOR CRIO API CALL:")
                logger.error(f"      - appointment_id: {appointment_id or 'MISSING'}")
                logger.error(f"      - subject_id: {subject_id or 'MISSING'}")
                logger.error(f"      - visit_id: {visit_id or 'MISSING'}")
                logger.error(f"   💡 TIP: Ensure subject_id and visit_id are provided in the form or stored in metadata")
                return {'success': False, 'error': 'Missing CRIO IDs'}

            # Call CRIO update_appointment
            logger.info(f"   📞 Calling CRIO API to reschedule appointment")
            logger.info(f"      - Appointment ID: {appointment_id}")
            logger.info(f"      - New datetime: {selected_slot['datetime']}")
            logger.info(f"      - Site/Study: {site_id}/{study_id}")
            logger.info(f"      - Subject/Visit: {subject_id}/{visit_id}")

            result = crio_patient_service.update_appointment(
                appointment_id=appointment_id,
                site_id=site_id,
                study_id=study_id,
                subject_id=subject_id,
                visit_id=visit_id,
                new_datetime=selected_slot['datetime'],
                coordinator_email="thastings@delricht.com",
                notes=f"Rescheduled via SMS by patient on {datetime.now().strftime('%Y-%m-%d %H:%M')}"
            )

            if result and result.get('success'):
                logger.info(f"   ✅ CRIO appointment rescheduled successfully!")
                logger.info(f"      - Old date: {result.get('old_date')}")
                logger.info(f"      - New date: {result.get('new_date')}")
            else:
                logger.error(f"   ❌ CRIO rescheduling FAILED: {result.get('error', 'Unknown error')}")

            return result

        except Exception as e:
            logger.error(f"   ❌ EXCEPTION during CRIO appointment rescheduling: {e}", exc_info=True)
            logger.error(f"      Session: {session_id}")
            logger.error(f"      Selected slot: {selected_slot}")
            return {'success': False, 'error': str(e)}

    # ===================================================================
    # DATABASE HELPERS
    # ===================================================================

    def _load_reschedule_request(self, session_id: str) -> Optional[Dict]:
        """Load reschedule request data from database"""

        query = """
            SELECT *
            FROM reschedule_requests
            WHERE session_id = %s
            ORDER BY created_at DESC
            LIMIT 1
        """

        result = db.execute_query(query, (session_id,))
        return result[0] if result else None

    def _update_state(self, session_id: str, new_state: str):
        """Update conversation state"""

        query = """
            UPDATE conversation_context
            SET current_state = %s,
                updated_at = CURRENT_TIMESTAMP
            WHERE session_id = %s
        """

        db.execute_update(query, (new_state, session_id))
        logger.info(f"   📝 Updated state: {new_state}")

    def _update_reschedule_status(
        self,
        session_id: str,
        status: str,
        new_appointment_id: Optional[str] = None,
        new_appointment_date: Optional[datetime] = None,
        notes: Optional[str] = None
    ):
        """Update reschedule_requests status"""

        query = """
            UPDATE reschedule_requests
            SET status = %s,
                new_appointment_id = COALESCE(%s, new_appointment_id),
                new_appointment_date = COALESCE(%s, new_appointment_date),
                failure_reason = COALESCE(%s, failure_reason),
                updated_at = CURRENT_TIMESTAMP
            WHERE session_id = %s
        """

        db.execute_update(query, (status, new_appointment_id, new_appointment_date, notes, session_id))

    def _save_slot_options(self, session_id: str, slots: List[Dict]):
        """Save slot options to metadata for later selection"""

        # Convert datetime objects to ISO strings for JSON
        slots_serializable = []
        for slot in slots:
            slot_copy = slot.copy()
            slot_copy['datetime'] = slot['datetime'].isoformat()
            slots_serializable.append(slot_copy)

        query = """
            UPDATE reschedule_requests
            SET metadata = jsonb_set(
                COALESCE(metadata, '{}'::jsonb),
                '{slot_options}',
                %s::jsonb
            )
            WHERE session_id = %s
        """

        db.execute_update(query, (json.dumps(slots_serializable), session_id))

    def _load_slot_options(self, session_id: str) -> Optional[List[Dict]]:
        """Load previously saved slot options"""

        query = """
            SELECT metadata->>'slot_options' as slot_options
            FROM reschedule_requests
            WHERE session_id = %s
        """

        result = db.execute_query(query, (session_id,))

        if not result or not result[0]['slot_options']:
            return None

        try:
            slots = json.loads(result[0]['slot_options'])

            # Convert ISO strings back to datetime
            for slot in slots:
                slot['datetime'] = datetime.fromisoformat(slot['datetime'])

            return slots

        except Exception as e:
            logger.error(f"   ❌ Error loading slot options: {e}")
            return None

    def _parse_slot_selection(self, message: str) -> Optional[int]:
        """
        Parse patient's slot selection

        Accepts: "1", "2", "first", "second", "option 1", etc.
        Returns: 0 for first option, 1 for second option, None if invalid
        """

        message_lower = message.lower().strip()

        # Direct numbers
        if message_lower == '1' or 'first' in message_lower or 'option 1' in message_lower:
            return 0
        elif message_lower == '2' or 'second' in message_lower or 'option 2' in message_lower:
            return 1

        # Try to extract any digit
        digit_match = re.search(r'\d', message)
        if digit_match:
            digit = int(digit_match.group())
            if digit in [1, 2]:
                return digit - 1

        return None

    def _get_subject_id_for_session(self, session_id: str, site_id: str, study_id: str) -> Optional[str]:
        """Get CRIO subject ID from patient mappings"""

        query = """
            SELECT crio_patient_id as subject_id
            FROM crio_patient_mappings
            WHERE session_id = %s
              AND crio_site_id = %s
              AND crio_study_id = %s
            ORDER BY created_at DESC
            LIMIT 1
        """

        result = db.execute_query(query, (session_id, site_id, study_id))
        return result[0]['subject_id'] if result else None

    def _get_visit_id(self, site_id: str, study_id: str, visit_name: str = "Screening") -> Optional[str]:
        """Get visit ID from cached mappings"""
        return crio_patient_service.get_visit_id_for_study(study_id, site_id, visit_name)

    def _get_site_name(self, site_id: str) -> str:
        """Get site name from database"""

        query = "SELECT site_name FROM site_coordinators WHERE site_id = %s"
        result = db.execute_query(query, (site_id,))

        if result:
            return result[0]['site_name']

        return f"Site {site_id}"

    # ===================================================================
    # ESCALATION & NOTIFICATIONS
    # ===================================================================

    async def _escalate(self, session_id: str, phone_number: str, reason: str):
        """
        Escalate to coordinator when automation can't complete

        Sends email to mmorris@delricht.com with:
        - Patient info
        - Reschedule request details
        - SMS conversation history
        - Escalation reason
        """

        logger.warning(f"   ⚠️  ESCALATING: {reason}")

        # Mark as escalated in database
        query = """
            UPDATE reschedule_requests
            SET escalated_to_coordinator = TRUE,
                escalation_reason = %s,
                status = 'escalated',
                updated_at = CURRENT_TIMESTAMP
            WHERE session_id = %s
        """

        db.execute_update(query, (reason, session_id))

        # Get request data and SMS history
        request_data = self._load_reschedule_request(session_id)
        sms_history = sms_service.get_sms_history(session_id)

        # Build email content
        email_body = f"""
        <h2>SMS Rescheduling Escalation</h2>

        <p><strong>Reason:</strong> {reason}</p>

        <h3>Patient Information</h3>
        <ul>
            <li><strong>Name:</strong> {request_data.get('patient_name', 'Unknown')}</li>
            <li><strong>Phone:</strong> {phone_number}</li>
            <li><strong>Site:</strong> {request_data.get('site_id', 'Unknown')}</li>
            <li><strong>Study:</strong> {request_data.get('study_id', 'Unknown')}</li>
            <li><strong>Current Appointment:</strong> {request_data.get('current_appointment_date', 'Unknown')}</li>
            <li><strong>Reschedule After:</strong> {request_data.get('reschedule_after_date', 'Unknown')}</li>
        </ul>

        <h3>SMS Conversation History</h3>
        <table border="1" cellpadding="5">
            <tr>
                <th>Time</th>
                <th>Direction</th>
                <th>Message</th>
            </tr>
        """

        for sms in sms_history:
            email_body += f"""
            <tr>
                <td>{sms['created_at']}</td>
                <td>{sms['direction'].upper()}</td>
                <td>{sms['message_text']}</td>
            </tr>
            """

        email_body += """
        </table>

        <p><strong>Action Required:</strong> Please call the patient to complete rescheduling manually.</p>
        """

        # Send email (async)
        try:
            # Use email_service if available
            # For now, just log (email service integration can be added)
            logger.info(f"   📧 TODO: Send escalation email to mmorris@delricht.com")
            logger.info(f"   Email content: {email_body[:200]}...")

        except Exception as e:
            logger.error(f"   ❌ Failed to send escalation email: {e}")

    async def _send_email_confirmation(
        self,
        session_id: str,
        selected_slot: Dict,
        request_data: Dict
    ):
        """Send email confirmation after successful rescheduling"""

        try:
            # Get patient contact info
            query = "SELECT * FROM patient_contact_info WHERE session_id = %s"
            contact_result = db.execute_query(query, (session_id,))

            if not contact_result:
                logger.warning(f"   ⚠️  No contact info found for email confirmation")
                return

            contact = contact_result[0]

            # Send email (integrate with existing email_service)
            logger.info(f"   📧 TODO: Send email confirmation to {contact.get('email', 'unknown')}")
            logger.info(f"   Appointment: {selected_slot['formatted_datetime']}")

        except Exception as e:
            logger.error(f"   ❌ Failed to send email confirmation: {e}")


# Singleton instance
reschedule_flow_handler = RescheduleFlowHandler()
