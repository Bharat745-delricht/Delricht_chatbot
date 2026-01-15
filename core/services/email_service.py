"""Email service for sending conversation reports using SendGrid"""
import os
import logging
from typing import Dict, Any, Optional, List
from datetime import datetime
import json

# SendGrid email provider - exactly as per documentation
try:
    from sendgrid import SendGridAPIClient
    from sendgrid.helpers.mail import Mail
    HAS_SENDGRID = True
    logging.info("SendGrid library imported successfully")
except ImportError:
    HAS_SENDGRID = False
    logging.error("SendGrid not installed - email functionality will not work")

from core.database import db
from config import settings

logger = logging.getLogger(__name__)


class EmailService:
    """Service for sending email notifications using SendGrid only"""
    
    def __init__(self):
        # Force SendGrid as the only provider
        self.provider = "sendgrid"
        self.from_email = settings.EMAIL_FROM
        self.from_name = settings.EMAIL_FROM_NAME
        self.scheduler_email = settings.SCHEDULER_EMAIL  # Primary recipient
        self.dashboard_email = settings.DASHBOARD_EMAIL   # CC recipient
        
        # Debug logging
        logger.info(f"Email provider: {self.provider}")
        logger.info(f"HAS_SENDGRID: {HAS_SENDGRID}")
        logger.info(f"EMAIL_FROM: {self.from_email}")
        logger.info(f"SENDGRID_API_KEY present: {bool(settings.SENDGRID_API_KEY)}")
        
        if settings.SENDGRID_API_KEY:
            logger.info(f"SENDGRID_API_KEY length: {len(settings.SENDGRID_API_KEY)}")
            logger.info(f"SENDGRID_API_KEY starts with: {settings.SENDGRID_API_KEY[:10]}...")
        else:
            logger.error("SENDGRID_API_KEY is empty or None")
        
        # Initialize SendGrid client
        if HAS_SENDGRID and settings.SENDGRID_API_KEY:
            try:
                # Strip any whitespace/newlines from API key as defensive measure
                clean_api_key = settings.SENDGRID_API_KEY.strip()
                self.sendgrid = SendGridAPIClient(api_key=clean_api_key)
                logger.info("SendGrid client initialized successfully")
            except Exception as e:
                logger.error(f"Failed to initialize SendGrid client: {str(e)}")
                raise
        else:
            logger.error(f"Cannot initialize SendGrid. HAS_SENDGRID: {HAS_SENDGRID}, API key available: {bool(settings.SENDGRID_API_KEY)}")
            self.sendgrid = None
    
    async def send_conversation_report(self, session_id: str, recipient: str = None):
        """
        Send a detailed conversation report via email

        Args:
            session_id: Session ID
            recipient: Optional override recipient (defaults to scheduler@delricht.com with CC to dashboard)
        """
        try:
            # Get conversation data
            conversation_data = await self._get_conversation_data(session_id)

            if not conversation_data:
                logger.error(f"No conversation data found for session {session_id}")
                return

            # Generate plain text email
            plain_content = self._generate_conversation_html(conversation_data)

            # Send email with full session ID
            subject = f"Clinical Trial Conversation Report - {session_id}"

            # If recipient override provided, use it; otherwise use scheduler@ + CC dashboard@
            if recipient:
                await self._send_email(recipient, subject, plain_content)
                logger.info(f"Sent conversation report for {session_id} to {recipient}")
            else:
                await self._send_email(
                    to_email=self.scheduler_email,
                    subject=subject,
                    content=plain_content,
                    cc_email=self.dashboard_email
                )
                logger.info(f"Sent conversation report for {session_id} to {self.scheduler_email} (CC: {self.dashboard_email})")
            
        except Exception as e:
            logger.error(f"Failed to send conversation report: {str(e)}")
            raise
    
    async def send_daily_summary(self, recipient: str, date: str):
        """Send daily summary of all conversations and prescreenings"""
        try:
            # Get daily statistics
            stats = await self._get_daily_stats(date)
            
            # Generate plain text email
            plain_content = self._generate_daily_summary_html(stats, date)
            
            # Send email
            subject = f"Clinical Trials Dashboard - Daily Summary for {date}"
            await self._send_email(recipient, subject, plain_content)
            
            logger.info(f"Sent daily summary for {date} to {recipient}")
            
        except Exception as e:
            logger.error(f"Failed to send daily summary: {str(e)}")
            raise
    
    async def send_eligibility_notification(
        self,
        session_id: str,
        recipient: str,
        trial_name: str,
        condition: str
    ):
        """Send notification when someone is found eligible"""
        try:
            # Get prescreening details
            ps_data = await self._get_prescreening_data(session_id)

            # Generate plain text email
            plain_content = self._generate_eligibility_notification_html(
                session_id, trial_name, condition, ps_data
            )

            # Send email
            subject = f"New Eligible Candidate - {condition} Trial"
            await self._send_email(recipient, subject, plain_content)

            logger.info(f"Sent eligibility notification for {session_id} to {recipient}")

        except Exception as e:
            logger.error(f"Failed to send eligibility notification: {str(e)}")
            raise

    async def send_visit_rescheduled_notification(
        self,
        patient_name: str,
        patient_email: Optional[str],
        patient_id: str,
        study_name: str,
        visit_type: str,
        old_date: str,
        old_time: str,
        new_date: str,
        new_time: str,
        site_name: str,
        rescheduled_by: str,
        notes: Optional[str] = None
    ):
        """Send notification when a visit is rescheduled"""
        try:
            # Generate plain text email
            plain_content = self._generate_visit_rescheduled_email(
                patient_name=patient_name,
                patient_id=patient_id,
                study_name=study_name,
                visit_type=visit_type,
                old_date=old_date,
                old_time=old_time,
                new_date=new_date,
                new_time=new_time,
                site_name=site_name,
                rescheduled_by=rescheduled_by,
                notes=notes
            )

            # Send to scheduler (primary) and CC dashboard
            subject = f"Visit Rescheduled - {patient_name} ({study_name})"
            await self._send_email(
                to_email=self.scheduler_email,
                subject=subject,
                content=plain_content,
                cc_email=self.dashboard_email
            )

            logger.info(f"Sent reschedule notification for patient {patient_id} to {self.scheduler_email} (CC: {self.dashboard_email})")

            # Optionally send to patient if email provided
            if patient_email:
                patient_content = self._generate_patient_reschedule_confirmation(
                    patient_name=patient_name,
                    study_name=study_name,
                    visit_type=visit_type,
                    new_date=new_date,
                    new_time=new_time,
                    site_name=site_name,
                    notes=notes
                )
                patient_subject = f"Your Visit Has Been Rescheduled - {study_name}"
                await self._send_email(patient_email, patient_subject, patient_content)
                logger.info(f"Sent reschedule confirmation to patient at {patient_email}")

        except Exception as e:
            logger.error(f"Failed to send visit rescheduled notification: {str(e)}")
            raise

    async def send_appointment_confirmation(
        self,
        session_id: str,
        patient_email: str,
        patient_name: str,
        appointment_datetime: datetime,
        site_name: str,
        site_address: Optional[str] = None
    ):
        """Send appointment confirmation using SendGrid Dynamic Template"""
        try:
            # Format appointment date/time
            # Example: "3:20 PM | Friday, January 1, 2026"
            time_str = appointment_datetime.strftime("%I:%M %p").lstrip('0')
            date_str = appointment_datetime.strftime("%A, %B %d, %Y")
            formatted_datetime = f"{time_str} | {date_str}"

            # Get first name for personalization
            first_name = patient_name.split()[0] if patient_name else "there"

            # Format address for display and maps link
            display_address = site_address or "Address available upon confirmation"
            maps_link = f"https://maps.google.com/?q={site_address.replace(' ', '+')}" if site_address else "#"

            # Check if dynamic template is configured
            if settings.SENDGRID_APPOINTMENT_TEMPLATE_ID:
                # Use dynamic template with all required fields
                # Extract day and date separately for template flexibility
                appointment_day = appointment_datetime.strftime("%A")  # "Friday"
                appointment_date = appointment_datetime.strftime("%B %d")  # "January 1"

                template_data = {
                    'patient_first_name': first_name,
                    'appointment_datetime_formatted': formatted_datetime,
                    'appointment_day': appointment_day,
                    'appointment_date': appointment_date,
                    'site_name': site_name,
                    'site_address': display_address,
                    'maps_link': maps_link
                }

                await self._send_template_email(
                    to_email=patient_email,
                    template_id=settings.SENDGRID_APPOINTMENT_TEMPLATE_ID,
                    template_data=template_data,
                    subject=f"Appointment Confirmed - {site_name}"
                )

                logger.info(f"Sent appointment confirmation (dynamic template) to {patient_email} for session {session_id}")

            else:
                # Fallback to embedded HTML if template not configured
                logger.warning("SENDGRID_APPOINTMENT_TEMPLATE_ID not set - using embedded HTML")
                html_content = self._generate_appointment_confirmation_html(
                    patient_first_name=first_name,
                    appointment_datetime_formatted=formatted_datetime,
                    site_name=site_name,
                    site_address=display_address
                )

                subject = f"Appointment Confirmed - {site_name}"
                await self._send_html_email(patient_email, subject, html_content)

                logger.info(f"Sent appointment confirmation (embedded HTML) to {patient_email} for session {session_id}")

        except Exception as e:
            logger.error(f"Failed to send appointment confirmation: {str(e)}")
            raise

    async def send_coordinator_booking_notification(
        self,
        session_id: str,
        patient_name: str,
        patient_email: Optional[str],
        patient_phone: str,
        patient_dob: Optional[str],
        appointment_datetime: datetime,
        site_name: str,
        site_address: Optional[str],
        trial_id: Optional[int] = None,
        trial_name: Optional[str] = None,
        eligibility_status: Optional[str] = None
    ):
        """Send booking notification to coordinator (mmorris@delricht.com)"""
        try:
            # Format appointment date/time for coordinator
            formatted_datetime = appointment_datetime.strftime("%A, %B %d, %Y at %I:%M %p")

            # Get trial information if available
            trial_info = "N/A"
            if trial_id and trial_name:
                trial_info = f"{trial_name} (ID: {trial_id})"
            elif trial_id:
                trial_info = f"Trial ID: {trial_id}"
            elif trial_name:
                trial_info = trial_name

            # Get prescreening details if available
            prescreening_summary = self._get_prescreening_summary(session_id)

            # Generate plain text email for coordinator
            plain_content = self._generate_coordinator_booking_email(
                patient_name=patient_name,
                patient_email=patient_email,
                patient_phone=patient_phone,
                patient_dob=patient_dob,
                appointment_datetime=formatted_datetime,
                site_name=site_name,
                site_address=site_address,
                trial_info=trial_info,
                eligibility_status=eligibility_status,
                prescreening_summary=prescreening_summary,
                session_id=session_id
            )

            # Send to scheduler (primary) and CC dashboard
            subject = f"🆕 New Appointment Booking - {patient_name} at {site_name}"
            await self._send_email(
                to_email=self.scheduler_email,
                subject=subject,
                content=plain_content,
                cc_email=self.dashboard_email
            )

            logger.info(f"Sent coordinator booking notification to {self.scheduler_email} (CC: {self.dashboard_email}) for session {session_id}")

        except Exception as e:
            logger.error(f"Failed to send coordinator booking notification: {str(e)}")
            raise

    def _get_prescreening_summary(self, session_id: str) -> Optional[str]:
        """Get a summary of prescreening Q&A for coordinator"""
        try:
            answers = db.execute_query("""
                SELECT question_text, user_answer
                FROM prescreening_answers
                WHERE session_id = %s
                ORDER BY created_at ASC
                LIMIT 10
            """, (session_id,))

            if not answers:
                return None

            summary = []
            for i, answer in enumerate(answers, 1):
                summary.append(f"  {i}. {answer['question_text']}")
                summary.append(f"     → {answer['user_answer']}")

            return "\n".join(summary)

        except Exception as e:
            logger.error(f"Error getting prescreening summary: {e}")
            return None

    def _generate_coordinator_booking_email(
        self,
        patient_name: str,
        patient_email: Optional[str],
        patient_phone: str,
        patient_dob: Optional[str],
        appointment_datetime: str,
        site_name: str,
        site_address: Optional[str],
        trial_info: str,
        eligibility_status: Optional[str],
        prescreening_summary: Optional[str],
        session_id: str
    ) -> str:
        """Generate plain text email for coordinator booking notification"""

        content = "NEW APPOINTMENT BOOKING\n"
        content += "=" * 50 + "\n\n"

        # Patient Information
        content += "PATIENT INFORMATION\n"
        content += "-" * 20 + "\n"
        content += f"Name: {patient_name}\n"
        content += f"Phone: {patient_phone}\n"
        content += f"Email: {patient_email or 'Not provided'}\n"
        if patient_dob:
            content += f"Date of Birth: {patient_dob}\n"
        if eligibility_status:
            content += f"Eligibility Status: {eligibility_status.upper()}\n"
        content += "\n"

        # Appointment Details
        content += "APPOINTMENT DETAILS\n"
        content += "-" * 20 + "\n"
        content += f"Date & Time: {appointment_datetime}\n"
        content += f"Site: {site_name}\n"
        if site_address:
            content += f"Address: {site_address}\n"
        content += f"Trial: {trial_info}\n"
        content += "\n"

        # Prescreening Summary
        if prescreening_summary:
            content += "PRESCREENING ANSWERS\n"
            content += "-" * 20 + "\n"
            content += prescreening_summary + "\n\n"

        # Action Items
        content += "NEXT STEPS\n"
        content += "-" * 20 + "\n"
        content += "1. Contact patient to confirm appointment\n"
        content += "2. Create patient in CRIO (if not already done)\n"
        content += "3. Schedule visit in CRIO calendar\n"
        content += f"4. Review full conversation: [Dashboard Session ID: {session_id}]\n"
        content += "\n"

        # Footer
        content += "=" * 50 + "\n"
        content += "Clinical Trials Chatbot - Automated Notification\n"
        content += f"Session ID: {session_id}\n"
        content += f"Timestamp: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}\n"

        return content
    
    
    async def _send_email(self, to_email: str, subject: str, content: str, cc_email: Optional[str] = None):
        """
        Send email using SendGrid with plain text content

        Args:
            to_email: Primary recipient
            subject: Email subject
            content: Plain text content
            cc_email: Optional CC recipient
        """

        if not self.sendgrid:
            logger.error(f"SendGrid client not initialized. Cannot send email to {to_email}")
            logger.info(f"Email would be sent to {to_email}")
            logger.info(f"Subject: {subject}")
            return

        try:
            # Use SendGrid with plain text content
            message = Mail(
                from_email=(self.from_email, self.from_name),
                to_emails=to_email,
                subject=subject,
                plain_text_content=content
            )

            # Add CC if provided
            if cc_email:
                message.add_cc(cc_email)
                logger.info(f"CC: {cc_email}")

            response = self.sendgrid.send(message)
            logger.info(f"Email sent successfully via SendGrid!")
            logger.info(f"To: {to_email}" + (f" | CC: {cc_email}" if cc_email else ""))
            logger.info(f"Response status: {response.status_code}")
            logger.info(f"Response body: {response.body}")
            logger.info(f"Response headers: {response.headers}")

        except Exception as e:
            logger.error(f"SendGrid error: {str(e)}")
            if hasattr(e, 'body'):
                logger.error(f"SendGrid error body: {e.body}")
            raise

    async def _send_html_email(self, to_email: str, subject: str, html_content: str):
        """Send email using SendGrid with HTML content"""

        if not self.sendgrid:
            logger.error(f"SendGrid client not initialized. Cannot send email to {to_email}")
            logger.info(f"Email would be sent to {to_email}")
            logger.info(f"Subject: {subject}")
            return

        try:
            # Use SendGrid with HTML content
            message = Mail(
                from_email=self.from_email,
                to_emails=to_email,
                subject=subject,
                html_content=html_content
            )

            response = self.sendgrid.send(message)
            logger.info(f"HTML email sent successfully via SendGrid!")
            logger.info(f"Response status: {response.status_code}")
            logger.info(f"Response body: {response.body}")
            logger.info(f"Response headers: {response.headers}")

        except Exception as e:
            logger.error(f"SendGrid error: {str(e)}")
            if hasattr(e, 'body'):
                logger.error(f"SendGrid error body: {e.body}")
            raise

    async def _send_template_email(self, to_email: str, template_id: str, template_data: Dict[str, Any], subject: str = None):
        """Send email using SendGrid Dynamic Template"""

        if not self.sendgrid:
            logger.error(f"SendGrid client not initialized. Cannot send email to {to_email}")
            logger.info(f"Template email would be sent to {to_email}")
            logger.info(f"Template ID: {template_id}")
            return

        try:
            # Use SendGrid with dynamic template
            message = Mail(from_email=self.from_email, to_emails=to_email)
            message.template_id = template_id
            message.dynamic_template_data = template_data

            # Subject can be set in template or overridden here
            if subject:
                message.subject = subject

            response = self.sendgrid.send(message)
            logger.info(f"Template email sent successfully via SendGrid!")
            logger.info(f"Template ID: {template_id}")
            logger.info(f"Recipient: {to_email}")
            logger.info(f"Response status: {response.status_code}")

        except Exception as e:
            logger.error(f"SendGrid template email error: {str(e)}")
            if hasattr(e, 'body'):
                logger.error(f"SendGrid error body: {e.body}")
            raise
    
    async def _get_conversation_data(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Get all conversation data for a session"""
        
        # Get messages with user_id
        messages = db.execute_query("""
            SELECT timestamp, user_message, bot_response, context_data, user_id
            FROM chat_logs
            WHERE session_id = %s
            ORDER BY timestamp ASC
        """, (session_id,))
        
        # Get prescreening data with eligibility mapping
        prescreening = db.execute_query("""
            SELECT 
                ps.*, 
                ct.trial_name, 
                ct.conditions,
                -- Map eligibility_result to display format
                CASE 
                    WHEN ps.eligibility_result = 'likely_eligible' THEN 'eligible'
                    WHEN ps.eligibility_result = 'potentially_eligible' THEN 'eligible'
                    WHEN ps.eligibility_result = 'likely_ineligible' THEN 'ineligible'
                    WHEN ps.eligibility_result = 'evaluated' THEN 'pending'
                    ELSE ps.eligibility_result
                END as eligible_status
            FROM prescreening_sessions ps
            LEFT JOIN clinical_trials ct ON ps.trial_id = ct.id
            WHERE ps.session_id = %s
            ORDER BY ps.started_at DESC
            LIMIT 1
        """, (session_id,))
        
        # Get prescreening answers
        answers = []
        if prescreening:
            answers = db.execute_query("""
                SELECT 
                    question_id as question_key,
                    question_text,
                    user_answer as answer_text,
                    created_at
                FROM prescreening_answers
                WHERE session_id = %s
                ORDER BY created_at ASC
            """, (session_id,))
        
        # Get contact information if exists
        contact_info = db.execute_query("""
            SELECT first_name, last_name, phone_number, email, 
                   eligibility_status, contact_preference, consent_timestamp
            FROM patient_contact_info
            WHERE session_id = %s
            ORDER BY consent_timestamp DESC
            LIMIT 1
        """, (session_id,))
        
        # Get conversation start time and user_id from first message
        user_id = messages[0]['user_id'] if messages else None
        conversation_start = messages[0]['timestamp'] if messages else None
        
        return {
            "session_id": session_id,
            "user_id": user_id,
            "conversation_start": conversation_start,
            "messages": messages,
            "prescreening": prescreening[0] if prescreening else None,
            "answers": answers,
            "contact_info": contact_info[0] if contact_info else None
        }
    
    async def _get_daily_stats(self, date: str) -> Dict[str, Any]:
        """Get statistics for a specific date"""
        
        stats = db.execute_query("""
            SELECT 
                COUNT(DISTINCT session_id) as total_conversations,
                COUNT(DISTINCT user_id) as unique_users,
                COUNT(*) as total_messages
            FROM chat_logs
            WHERE DATE(timestamp) = %s
        """, (date,))
        
        prescreening_stats = db.execute_query("""
            SELECT 
                COUNT(*) as total_prescreenings,
                COUNT(CASE WHEN status = 'completed' THEN 1 END) as completed,
                COUNT(CASE WHEN eligible = true THEN 1 END) as eligible
            FROM prescreening_sessions
            WHERE DATE(started_at) = %s
        """, (date,))
        
        return {
            "conversations": stats[0] if stats else {},
            "prescreenings": prescreening_stats[0] if prescreening_stats else {}
        }
    
    async def _get_prescreening_data(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Get prescreening details for eligibility notification"""
        
        return db.execute_query("""
            SELECT 
                ps.*,
                json_agg(
                    json_build_object(
                        'question', pa.question_text,
                        'answer', pa.answer_text
                    )
                ) as answers
            FROM prescreening_sessions ps
            LEFT JOIN prescreening_answers pa ON ps.id = pa.prescreening_session_id
            WHERE ps.session_id = %s
            GROUP BY ps.id
        """, (session_id,))
    
    def _generate_conversation_html(self, data: Dict[str, Any]) -> str:
        """Generate plain text email for conversation report"""
        
        # Format conversation start time in Central Time
        conversation_start_ct = self._convert_to_central_time(data.get('conversation_start'))
        
        # Build plain text content
        content = "Clinical Trial Conversation Report\n"
        content += "=" * 38 + "\n\n"
        
        # Participant Details Section
        content += "PARTICIPANT DETAILS\n"
        content += "-" * 20 + "\n"
        
        if data['contact_info']:
            contact = data['contact_info']
            name = f"{contact.get('first_name', '')} {contact.get('last_name', '')}".strip()
            eligibility_display = {
                'eligible': 'Eligible',
                'potentially_eligible': 'Likely Eligible', 
                'ineligible': 'Not Eligible',
                'pending': 'Under Review'
            }.get(contact.get('eligibility_status', 'unknown'), 'Unknown')
            
            content += f"Name: {name}\n"
            content += f"Phone: {contact.get('phone_number', 'Not provided')}\n"
            content += f"Email: {contact.get('email', 'Not provided')}\n"
            content += f"Status: **{eligibility_display}**\n"
        else:
            content += "No participant information available\n"
        
        content += "\n"
        
        # Session Information
        content += "SESSION INFORMATION\n"
        content += "-" * 20 + "\n"
        content += f"Session ID: {data['session_id']}\n"
        content += f"User ID: {data.get('user_id', 'Unknown')}\n"
        content += f"Start Time: {conversation_start_ct}\n\n"
        
        # Prescreening Results
        content += "PRESCREENING RESULTS\n"
        content += "-" * 20 + "\n"
        
        if data['prescreening']:
            ps = data['prescreening']
            trial_name = ps.get('trial_name', 'Unknown Trial')
            condition = ps.get('conditions', 'N/A')
            
            eligibility_display = {
                'eligible': 'Eligible',
                'potentially_eligible': 'Likely Eligible',
                'ineligible': 'Not Eligible', 
                'pending': 'Under Review'
            }.get(ps.get('eligible_status') or ps.get('eligible'), 'Unknown')
            
            content += f"Trial: {trial_name}\n"
            content += f"Condition: {condition}\n"
            content += f"Status: {ps.get('status', 'N/A')}\n"
            content += f"Eligibility: **{eligibility_display}**\n\n"
            
            if data['answers']:
                content += "Prescreening Responses:\n"
                for i, answer in enumerate(data['answers'], 1):
                    content += f"  {i}. {answer['question_text']}\n"
                    content += f"     Answer: {answer['answer_text']}\n\n"
        else:
            content += "No prescreening completed\n\n"
        
        # Conversation Transcript
        content += "CONVERSATION TRANSCRIPT\n"
        content += "-" * 23 + "\n\n"
        
        for msg in data['messages']:
            content += f"USER: {msg['user_message']}\n\n"
            content += f"ASSISTANT: {msg['bot_response']}\n\n"
        
        # Footer
        content += "=" * 38 + "\n"
        content += "Confidential Report\n"
        content += "This email contains confidential patient information.\n"
        content += "Please handle according to HIPAA guidelines.\n"
        
        return content
    
    def _convert_to_central_time(self, timestamp) -> str:
        """Convert timestamp to Central Time format for display"""
        if not timestamp or timestamp == 'N/A':
            return 'N/A'
        
        try:
            from datetime import timezone, timedelta
            import pytz
            
            # Parse the timestamp
            if isinstance(timestamp, str):
                # Handle different timestamp formats
                if timestamp.endswith('Z'):
                    # UTC format with Z
                    dt = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
                else:
                    # Standard ISO format
                    dt = datetime.fromisoformat(timestamp)
            else:
                dt = timestamp
            
            # Ensure datetime is timezone-aware (assume UTC if naive)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            
            # Convert to Central Time
            central_tz = pytz.timezone('US/Central')
            central_dt = dt.astimezone(central_tz)
            
            # Format for display
            return central_dt.strftime("%Y-%m-%d %I:%M:%S %p %Z")
            
        except Exception as e:
            logger.error(f"Error converting timestamp to Central Time: {str(e)}")
            # Fallback to original timestamp string
            return str(timestamp)[:19] if timestamp else 'N/A'
    
    def _generate_daily_summary_html(self, stats: Dict[str, Any], date: str) -> str:
        """Generate plain text for daily summary"""
        
        conv_stats = stats['conversations']
        ps_stats = stats['prescreenings']
        
        content = f"**DAILY SUMMARY - {date}**\n"
        content += "=" * 30 + "\n\n"
        
        content += "CONVERSATIONS\n"
        content += "-" * 13 + "\n"
        content += f"Total conversations: **{conv_stats.get('total_conversations', 0)}**\n"
        content += f"Unique users: {conv_stats.get('unique_users', 0)}\n"
        content += f"Messages exchanged: {conv_stats.get('total_messages', 0)}\n\n"
        
        content += "PRESCREENINGS\n"
        content += "-" * 13 + "\n"
        content += f"Prescreenings started: **{ps_stats.get('total_prescreenings', 0)}**\n"
        content += f"Completed: {ps_stats.get('completed', 0)}\n"
        content += f"Found eligible: {ps_stats.get('eligible', 0)}\n\n"
        
        content += "=" * 30 + "\n"
        content += "Clinical Trials Dashboard\n"
        
        return content
    
    def _generate_eligibility_notification_html(
        self,
        session_id: str,
        trial_name: str,
        condition: str,
        ps_data: Optional[Dict[str, Any]]
    ) -> str:
        """Generate plain text for eligibility notification"""

        content = "**NEW ELIGIBLE CANDIDATE!**\n"
        content += "=" * 26 + "\n\n"

        content += f"**{trial_name}**\n"
        content += "-" * len(trial_name) + "\n\n"

        content += f"Condition: {condition}\n"
        content += f"Session ID: {session_id}\n"
        content += f"Timestamp: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}\n\n"

        content += "A user has completed prescreening and appears to be\n"
        content += "eligible for this trial.\n\n"

        content += "Please review the full conversation report for details.\n\n"

        content += "=" * 26 + "\n"
        content += "Clinical Trials System\n"

        return content

    def _generate_visit_rescheduled_email(
        self,
        patient_name: str,
        patient_id: str,
        study_name: str,
        visit_type: str,
        old_date: str,
        old_time: str,
        new_date: str,
        new_time: str,
        site_name: str,
        rescheduled_by: str,
        notes: Optional[str] = None
    ) -> str:
        """Generate plain text email for visit rescheduling notification (internal)"""

        content = "VISIT RESCHEDULED - INTERNAL NOTIFICATION\n"
        content += "=" * 42 + "\n\n"

        content += "PATIENT INFORMATION\n"
        content += "-" * 20 + "\n"
        content += f"Name: {patient_name}\n"
        content += f"Patient ID: {patient_id}\n"
        content += f"Site: {site_name}\n\n"

        content += "STUDY INFORMATION\n"
        content += "-" * 18 + "\n"
        content += f"Study: {study_name}\n"
        content += f"Visit Type: {visit_type}\n\n"

        content += "SCHEDULE CHANGE\n"
        content += "-" * 15 + "\n"
        content += f"Previous Date: {old_date}\n"
        content += f"Previous Time: {old_time}\n\n"
        content += f"New Date: {new_date}\n"
        content += f"New Time: {new_time}\n\n"

        content += "RESCHEDULING DETAILS\n"
        content += "-" * 20 + "\n"
        content += f"Rescheduled By: {rescheduled_by}\n"
        content += f"Timestamp: {datetime.utcnow().strftime('%Y-%m-%d %I:%M:%S %p UTC')}\n"

        if notes:
            content += f"\nNotes: {notes}\n"

        content += "\n"
        content += "=" * 42 + "\n"
        content += "Clinical Scheduling Dashboard V3\n"
        content += "This is an automated notification. Please verify the\n"
        content += "change has been properly reflected in CRIO.\n"

        return content

    def _generate_patient_reschedule_confirmation(
        self,
        patient_name: str,
        study_name: str,
        visit_type: str,
        new_date: str,
        new_time: str,
        site_name: str,
        notes: Optional[str] = None
    ) -> str:
        """Generate plain text email for patient visit reschedule confirmation"""

        content = f"Dear {patient_name},\n\n"

        content += "Your clinical trial visit has been rescheduled.\n\n"

        content += "UPDATED APPOINTMENT DETAILS\n"
        content += "-" * 27 + "\n"
        content += f"Study: {study_name}\n"
        content += f"Visit Type: {visit_type}\n"
        content += f"Date: {new_date}\n"
        content += f"Time: {new_time}\n"
        content += f"Location: {site_name}\n\n"

        if notes:
            content += f"Additional Information:\n{notes}\n\n"

        content += "IMPORTANT REMINDERS\n"
        content += "-" * 19 + "\n"
        content += "• Please arrive 15 minutes early\n"
        content += "• Bring your photo ID and insurance card\n"
        content += "• Bring any medications you are currently taking\n"
        content += "• Bring your completed visit forms (if applicable)\n\n"

        content += "If you have any questions or need to make changes to this\n"
        content += "appointment, please contact us at:\n\n"
        content += f"Site: {site_name}\n"
        content += "Email: mmorris@delricht.com\n\n"

        content += "Thank you for your participation in this clinical trial.\n\n"

        content += "Best regards,\n"
        content += "DelRicht Research Team\n\n"

        content += "-" * 50 + "\n"
        content += "This is an automated confirmation email.\n"
        content += "Please do not reply to this email.\n"

        return content

    def _generate_appointment_confirmation_html(
        self,
        patient_first_name: str,
        appointment_datetime_formatted: str,
        site_name: str,
        site_address: str
    ) -> str:
        """Generate HTML email for appointment confirmation using branded DelRicht template"""

        # Google Maps link for the address
        maps_link = f"https://maps.google.com/?q={site_address.replace(' ', '+')}"

        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Appointment Confirmation - DelRicht Research</title>

    <!-- Web Fonts -->
    <link href="https://fonts.googleapis.com/css2?family=Poppins:ital,wght@0,400;0,600;1,400&family=Open+Sans:wght@400;700&display=swap" rel="stylesheet">

    <style>
        /* Brand Colors based on DelRicht Guidelines */
        :root {{
            --delricht-blue: #00265E;
            --delricht-black: #000000;
            --delricht-white: #FFFFFF;
            --delricht-green: #00999D;
            --delricht-gold: #D4AF37;
            --delricht-blue-light-10: #1A3C6E;
            --delricht-blue-light-20: #33517E;
            --delricht-blue-light-30: #4D678E;
            --delricht-green-light-10: #1AA3A7;
            --delricht-green-light-20: #33ADB1;
            --delricht-green-light-90: #E6F5F5;
        }}

        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}

        body {{
            font-family: 'Open Sans', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Arial, sans-serif;
            background: linear-gradient(135deg, var(--delricht-blue) 0%, var(--delricht-blue-light-20) 100%);
            display: flex;
            justify-content: center;
            align-items: center;
            min-height: 100vh;
            padding: 20px;
        }}

        .email-container {{
            background-color: var(--delricht-white);
            border-radius: 8px;
            max-width: 600px;
            width: 100%;
            box-shadow: 0 10px 30px rgba(0, 38, 94, 0.15);
            overflow: hidden;
        }}

        .header {{
            background-color: var(--delricht-blue);
            padding: 40px;
            text-align: center;
            position: relative;
        }}

        .header::after {{
            content: "";
            position: absolute;
            bottom: 0;
            left: 0;
            right: 0;
            height: 4px;
            background: linear-gradient(90deg, var(--delricht-green) 0%, var(--delricht-gold) 100%);
        }}

        .logo-container {{
            display: flex;
            justify-content: center;
            align-items: center;
        }}

        .logo-text {{
            font-family: 'Proxima Nova', 'Poppins', sans-serif;
            font-size: 24px;
            font-weight: 700;
            color: var(--delricht-white);
            text-transform: uppercase;
            letter-spacing: 1px;
        }}

        .content {{
            padding: 40px;
            text-align: center;
        }}

        .icon-wrapper {{
            display: inline-block;
            position: relative;
            margin-bottom: 30px;
        }}

        .calendar-icon {{
            width: 60px;
            height: 60px;
            background-color: var(--delricht-green-light-90);
            border-radius: 12px;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 28px;
        }}

        .check-badge {{
            position: absolute;
            bottom: -5px;
            right: -5px;
            background-color: var(--delricht-green);
            color: var(--delricht-white);
            border-radius: 50%;
            width: 24px;
            height: 24px;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 14px;
            font-weight: bold;
            box-shadow: 0 2px 4px rgba(0, 0, 0, 0.1);
        }}

        h1 {{
            font-family: 'Poppins', sans-serif;
            color: var(--delricht-green);
            font-size: 28px;
            font-weight: 600;
            margin: 0 0 20px 0;
            text-transform: capitalize;
        }}

        .confirmation-text {{
            font-family: 'Open Sans', sans-serif;
            color: var(--delricht-blue);
            font-size: 16px;
            line-height: 1.8;
            margin: 0 0 30px 0;
        }}

        .appointment-card {{
            background: linear-gradient(135deg, var(--delricht-green-light-90) 0%, #FFFFFF 100%);
            border: 1px solid #E6F5F5;
            border-radius: 8px;
            padding: 30px;
            margin: 30px 0;
        }}

        .appointment-time {{
            font-family: 'Poppins', sans-serif;
            font-size: 20px;
            font-weight: 600;
            color: var(--delricht-blue);
            margin-bottom: 15px;
        }}

        .location-name {{
            font-family: 'Open Sans', sans-serif;
            color: var(--delricht-blue-light-20);
            font-size: 16px;
            font-weight: 600;
            margin-bottom: 8px;
        }}

        .location-address {{
            color: var(--delricht-blue-light-30);
            text-decoration: none;
            font-size: 15px;
            display: inline-block;
            transition: color 0.3s ease;
        }}

        .location-address:hover {{
            color: var(--delricht-green);
            text-decoration: underline;
        }}

        .cta-button {{
            display: inline-block;
            background-color: var(--delricht-green);
            color: var(--delricht-white);
            padding: 14px 35px;
            border-radius: 6px;
            text-decoration: none;
            font-family: 'Proxima Nova', 'Poppins', sans-serif;
            font-weight: 600;
            font-size: 16px;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            margin: 20px 0;
            transition: all 0.3s ease;
            box-shadow: 0 4px 10px rgba(0, 153, 157, 0.2);
        }}

        .cta-button:hover {{
            background-color: #008A8D;
            transform: translateY(-2px);
            box-shadow: 0 6px 15px rgba(0, 153, 157, 0.3);
        }}

        .footer-section {{
            background-color: #F8FAFB;
            padding: 40px;
            border-top: 1px solid #E6E9EF;
        }}

        .footer-item {{
            margin-bottom: 30px;
            text-align: left;
        }}

        .footer-item:last-child {{
            margin-bottom: 0;
        }}

        .footer-header {{
            display: flex;
            align-items: center;
            gap: 12px;
            margin-bottom: 12px;
        }}

        .footer-icon {{
            width: 36px;
            height: 36px;
            background-color: var(--delricht-green-light-90);
            border-radius: 8px;
            display: flex;
            align-items: center;
            justify-content: center;
            color: var(--delricht-green);
            font-size: 18px;
        }}

        .footer-title {{
            font-family: 'Poppins', sans-serif;
            font-size: 16px;
            font-weight: 600;
            color: var(--delricht-blue);
            text-transform: capitalize;
        }}

        .footer-text {{
            font-family: 'Open Sans', sans-serif;
            color: var(--delricht-blue-light-30);
            font-size: 14px;
            line-height: 1.8;
            margin-left: 48px;
        }}

        .footer-text a {{
            color: var(--delricht-green);
            text-decoration: none;
            font-weight: 600;
            transition: color 0.3s ease;
        }}

        .footer-text a:hover {{
            color: #008A8D;
            text-decoration: underline;
        }}

        /* Mission Statement Footer */
        .mission-footer {{
            background-color: var(--delricht-blue);
            padding: 25px 40px;
            text-align: center;
            border-top: 2px solid var(--delricht-gold);
        }}

        .mission-text {{
            font-family: 'Poppins', sans-serif;
            color: var(--delricht-white);
            font-size: 14px;
            font-style: italic;
            line-height: 1.6;
            margin: 0;
        }}

        @media (max-width: 640px) {{
            .content {{
                padding: 30px 20px;
            }}

            .header {{
                padding: 30px 20px;
            }}

            .footer-section {{
                padding: 30px 20px;
            }}

            .mission-footer {{
                padding: 20px;
            }}

            h1 {{
                font-size: 24px;
            }}

            .appointment-time {{
                font-size: 18px;
            }}

            .logo-text {{
                font-size: 20px;
            }}

            .cta-button {{
                padding: 12px 28px;
                font-size: 14px;
            }}
        }}
    </style>
</head>
<body>
    <div class="email-container">
        <!-- Header with Logo -->
        <div class="header">
            <div class="logo-container">
                <div class="logo-text">DELRICHT RESEARCH</div>
            </div>
        </div>

        <!-- Main Content -->
        <div class="content">
            <div class="icon-wrapper">
                <div class="calendar-icon">📅</div>
                <div class="check-badge">✓</div>
            </div>

            <h1>Mark Your Calendar, {patient_first_name}!</h1>

            <p class="confirmation-text">
                Thank you for scheduling your appointment with DelRicht Research!<br>
                Your participation helps us move medicine forward. This email confirms your upcoming visit.
            </p>

            <div class="appointment-card">
                <div class="appointment-time">{appointment_datetime_formatted}</div>
                <div class="location-name">{site_name}</div>
                <a href="{maps_link}" class="location-address" target="_blank">
                    {site_address}
                </a>
            </div>

            <a href="#" class="cta-button">ADD TO CALENDAR</a>
        </div>

        <!-- Footer Information -->
        <div class="footer-section">
            <div class="footer-item">
                <div class="footer-header">
                    <div class="footer-icon">📅</div>
                    <div class="footer-title">Need to Reschedule?</div>
                </div>
                <div class="footer-text">
                    Please call us at (504) 336-2643 or reply to this email.
                </div>
            </div>

            <div class="footer-item">
                <div class="footer-header">
                    <div class="footer-icon">📞</div>
                    <div class="footer-title">If Questions Arise, Don't Hesitate</div>
                </div>
                <div class="footer-text">
                    Call us at (504) 336-2643 or email <a href="mailto:info@delricht.com">info@delricht.com</a>.<br>
                    Visit <a href="https://delrichtresearch.com" target="_blank">DelRichtResearch.com</a> for more information about clinical research participation.
                </div>
            </div>
        </div>

        <!-- Mission Statement -->
        <div class="mission-footer">
            <p class="mission-text">
                "Moving medicine forward by increasing patient participation in clinical research."
            </p>
        </div>
    </div>
</body>
</html>"""

        return html
    


# Global instance
email_service = EmailService()