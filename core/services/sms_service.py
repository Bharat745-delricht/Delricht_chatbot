"""
SMS Service using Twilio
Handles all SMS sending and logging for patient communication
Mirrors the pattern from email_service.py
"""

from twilio.rest import Client
from twilio.base.exceptions import TwilioRestException
from typing import Optional, Dict
import logging
import os
import re
from datetime import datetime

from core.database import db

logger = logging.getLogger(__name__)


class SMSService:
    """Service for sending SMS messages via Twilio"""

    def __init__(self):
        """Initialize Twilio client with credentials from environment/secrets"""
        self.account_sid = os.getenv('TWILIO_ACCOUNT_SID')
        self.auth_token = os.getenv('TWILIO_AUTH_TOKEN')
        self.from_number = os.getenv('TWILIO_PHONE_NUMBER')

        if not all([self.account_sid, self.auth_token, self.from_number]):
            logger.warning("⚠️  Twilio credentials not configured - SMS sending will fail")
            logger.warning("   Set: TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_PHONE_NUMBER")
            self.client = None
        else:
            try:
                self.client = Client(self.account_sid, self.auth_token)
                logger.info(f"✅ Twilio SMS Service initialized | From: {self.from_number}")
            except Exception as e:
                logger.error(f"❌ Failed to initialize Twilio client: {e}")
                self.client = None

    def _normalize_phone_number(self, phone: str) -> str:
        """
        Normalize phone number to E.164 format (+1XXXXXXXXXX)

        Handles formats:
        - 4045551234 → +14045551234
        - (404) 555-1234 → +14045551234
        - +1 404-555-1234 → +14045551234
        - +14045551234 → +14045551234 (already normalized)
        """
        # Remove all non-digit characters except leading +
        digits_only = re.sub(r'[^\d+]', '', phone)

        # Remove leading + temporarily
        if digits_only.startswith('+'):
            digits_only = digits_only[1:]

        # Remove leading 1 if present (will add back)
        if digits_only.startswith('1') and len(digits_only) == 11:
            digits_only = digits_only[1:]

        # Validate we have 10 digits
        if len(digits_only) != 10:
            raise ValueError(f"Invalid phone number format: {phone} (expected 10 digits)")

        # Return E.164 format
        return f"+1{digits_only}"

    async def send_sms(
        self,
        to_phone: str,
        message: str,
        session_id: Optional[str] = None,
        metadata: Optional[Dict] = None
    ) -> Optional[str]:
        """
        Send SMS and log to database

        Args:
            to_phone: Recipient phone number (any format, will be normalized)
            message: SMS message text (max 1600 chars)
            session_id: Conversation session ID (optional, for linking)
            metadata: Additional data to store (patient_id, appointment_id, etc.)

        Returns:
            Twilio message SID if successful, None if failed
        """

        if not self.client:
            logger.error("❌ Cannot send SMS - Twilio client not initialized")
            return None

        try:
            # Normalize phone number
            normalized_phone = self._normalize_phone_number(to_phone)

            # Truncate message if too long (SMS limit)
            if len(message) > 1600:
                logger.warning(f"⚠️  Message truncated from {len(message)} to 1600 chars")
                message = message[:1597] + "..."

            # Send via Twilio
            logger.info(f"📤 Sending SMS to {normalized_phone}")
            logger.debug(f"   Message: {message[:100]}...")

            twilio_message = self.client.messages.create(
                to=normalized_phone,
                from_=self.from_number,
                body=message
            )

            message_sid = twilio_message.sid
            status = twilio_message.status

            logger.info(f"✅ SMS sent successfully | SID: {message_sid} | Status: {status}")

            # Log to database
            self._log_sms(
                session_id=session_id,
                phone_number=normalized_phone,
                direction='outbound',
                message_text=message,
                twilio_message_sid=message_sid,
                status=status,
                metadata=metadata or {}
            )

            # Update patient_contact_info.last_sms_sent if we have session
            if session_id:
                self._update_last_sms_sent(session_id)

            return message_sid

        except TwilioRestException as e:
            logger.error(f"❌ Twilio API error sending SMS to {to_phone}")
            logger.error(f"   Error code: {e.code} | Message: {e.msg}")

            # Log failure to database
            self._log_sms(
                session_id=session_id,
                phone_number=to_phone,
                direction='outbound',
                message_text=message,
                status='failed',
                error_message=f"Twilio error {e.code}: {e.msg}",
                metadata=metadata or {}
            )

            return None

        except ValueError as e:
            logger.error(f"❌ Invalid phone number: {e}")
            return None

        except Exception as e:
            logger.error(f"❌ Unexpected error sending SMS: {e}", exc_info=True)
            return None

    def _log_sms(
        self,
        phone_number: str,
        direction: str,
        message_text: str,
        session_id: Optional[str] = None,
        twilio_message_sid: Optional[str] = None,
        status: str = 'sent',
        error_message: Optional[str] = None,
        metadata: Optional[Dict] = None
    ):
        """Log SMS message to database"""

        try:
            query = """
                INSERT INTO sms_conversations
                (session_id, phone_number, direction, message_text,
                 twilio_message_sid, status, error_message, metadata, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
            """

            # Convert metadata dict to proper JSON string for JSONB column
            import json
            metadata_json = None
            if metadata:
                metadata_json = json.dumps(metadata)

            db.execute_update(
                query,
                (
                    session_id,
                    phone_number,
                    direction,
                    message_text,
                    twilio_message_sid,
                    status,
                    error_message,
                    metadata_json
                )
            )

            logger.debug(f"💾 SMS logged to database | Direction: {direction} | Phone: {phone_number}")

        except Exception as e:
            logger.error(f"❌ Failed to log SMS to database: {e}", exc_info=True)

    def _update_last_sms_sent(self, session_id: str):
        """Update patient_contact_info.last_sms_sent timestamp"""
        try:
            query = """
                UPDATE patient_contact_info
                SET last_sms_sent = CURRENT_TIMESTAMP
                WHERE session_id = %s
            """
            db.execute_update(query, (session_id,))
        except Exception as e:
            logger.warning(f"⚠️  Could not update last_sms_sent: {e}")

    # =================================================================
    # TEMPLATE METHODS - Pre-built SMS messages for common scenarios
    # =================================================================

    async def send_reschedule_initiation(
        self,
        patient_name: str,
        phone_number: str,
        appointment_date: str,
        study_name: str,
        site_name: str,
        session_id: str
    ) -> Optional[str]:
        """
        Send initial reschedule request SMS (System-initiated)

        Example: "Hi John, your appointment for Study ABC123 at ATL General Medicine
                  is scheduled for Nov 18 at 10:00 AM. Reply YES to confirm, or
                  RESCHEDULE if you need to change."
        """

        message = f"""Hi {patient_name}, this is DelRicht Clinical Research.

Your appointment for {study_name} at {site_name} is scheduled for {appointment_date}.

Reply YES to confirm, or RESCHEDULE if you need to change the time."""

        return await self.send_sms(
            to_phone=phone_number,
            message=message,
            session_id=session_id,
            metadata={
                'message_type': 'reschedule_initiation',
                'study_name': study_name,
                'site_name': site_name
            }
        )

    async def send_availability_request(
        self,
        phone_number: str,
        session_id: str,
        after_date: str
    ) -> Optional[str]:
        """
        Ask patient for their availability preferences

        Example: "When works better for you? (After Nov 20)
                  You can reply with:
                  - Specific dates: 'Nov 22' or 'December 1st'
                  - Preferences: 'Afternoons only', 'Mornings'
                  - Date range: 'Nov 20-25'"
        """

        message = f"""When works better for you? (After {after_date})

You can reply with:
• Specific dates: 'Nov 22' or 'December 1st'
• Preferences: 'Afternoons only', 'Mornings'
• Date range: 'Nov 20-25'"""

        return await self.send_sms(
            to_phone=phone_number,
            message=message,
            session_id=session_id,
            metadata={'message_type': 'availability_request'}
        )

    async def send_slot_options(
        self,
        phone_number: str,
        session_id: str,
        slots: list
    ) -> Optional[str]:
        """
        Present available time slot options (max 2 options)

        Args:
            slots: List of slot dicts with 'formatted_date', 'formatted_time'
                  Example: [{'formatted_date': 'Wednesday Nov 22', 'formatted_time': '2:00 PM'}, ...]
        """

        if not slots or len(slots) == 0:
            raise ValueError("Must provide at least 1 slot option")

        if len(slots) > 2:
            logger.warning(f"⚠️  More than 2 slots provided ({len(slots)}), truncating to 2")
            slots = slots[:2]

        # Build options text
        options_text = ""
        for i, slot in enumerate(slots, 1):
            options_text += f"{i}) {slot['formatted_date']} at {slot['formatted_time']}\n"

        message = f"""Great! Here are {len(slots)} available times:

{options_text.strip()}

Reply 1 or 2 to book."""

        return await self.send_sms(
            to_phone=phone_number,
            message=message,
            session_id=session_id,
            metadata={'message_type': 'slot_options', 'slot_count': len(slots)}
        )

    async def send_confirmation(
        self,
        phone_number: str,
        session_id: str,
        appointment_date: str,
        appointment_time: str,
        site_name: str
    ) -> Optional[str]:
        """
        Send appointment confirmation after successful rescheduling
        """

        message = f"""✓ Your appointment is confirmed!

Date: {appointment_date}
Time: {appointment_time}
Location: {site_name}

You'll receive a confirmation email shortly. See you then!"""

        return await self.send_sms(
            to_phone=phone_number,
            message=message,
            session_id=session_id,
            metadata={'message_type': 'confirmation'}
        )

    async def send_escalation_notice(
        self,
        phone_number: str,
        session_id: str,
        reason: str = "availability"
    ) -> Optional[str]:
        """
        Notify patient that a coordinator will follow up
        """

        if reason == "availability":
            message = """We couldn't find available times matching your preferences.

A coordinator will call you to schedule a time that works. You can also call us at (404) 355-8779."""

        elif reason == "error":
            message = """We encountered an issue rescheduling your appointment.

A coordinator will call you shortly to complete the rescheduling. You can also call us at (404) 355-8779."""

        else:
            message = """A coordinator will follow up with you shortly regarding your appointment.

You can also call us at (404) 355-8779."""

        return await self.send_sms(
            to_phone=phone_number,
            message=message,
            session_id=session_id,
            metadata={'message_type': 'escalation', 'reason': reason}
        )

    async def send_help_response(
        self,
        phone_number: str,
        session_id: str
    ) -> Optional[str]:
        """
        Respond to HELP request
        """

        message = """DelRicht Clinical Research - Appointment Rescheduling

To reschedule, reply with when you're available (e.g., "Afternoons next week").

For immediate assistance, call (404) 355-8779.

Reply STOP to unsubscribe from SMS."""

        return await self.send_sms(
            to_phone=phone_number,
            message=message,
            session_id=session_id,
            metadata={'message_type': 'help_response'}
        )

    # =================================================================
    # UTILITY METHODS
    # =================================================================

    def get_sms_history(self, session_id: str, limit: int = 50) -> list:
        """
        Get SMS conversation history for a session

        Returns list of messages ordered by created_at ASC (oldest first)
        """

        query = """
            SELECT
                id, session_id, phone_number, direction, message_text,
                twilio_message_sid, status, error_message, created_at, metadata
            FROM sms_conversations
            WHERE session_id = %s
            ORDER BY created_at ASC
            LIMIT %s
        """

        return db.execute_query(query, (session_id, limit))

    def get_last_sms_for_phone(self, phone_number: str) -> Optional[Dict]:
        """Get most recent SMS for a phone number"""

        normalized = self._normalize_phone_number(phone_number)

        query = """
            SELECT *
            FROM sms_conversations
            WHERE phone_number = %s
            ORDER BY created_at DESC
            LIMIT 1
        """

        result = db.execute_query(query, (normalized,))
        return result[0] if result else None

    def check_rate_limit(self, session_id: str, max_per_hour: int = 10) -> bool:
        """
        Check if we've exceeded SMS rate limit for this session

        Returns True if OK to send, False if rate limit exceeded
        """

        query = """
            SELECT COUNT(*) as count
            FROM sms_conversations
            WHERE session_id = %s
              AND direction = 'outbound'
              AND created_at > CURRENT_TIMESTAMP - INTERVAL '1 hour'
        """

        result = db.execute_query(query, (session_id,))
        count = result[0]['count'] if result else 0

        if count >= max_per_hour:
            logger.warning(f"⚠️  Rate limit exceeded for session {session_id}: {count}/{max_per_hour} SMS in last hour")
            return False

        return True


# Singleton instance
sms_service = SMSService()
