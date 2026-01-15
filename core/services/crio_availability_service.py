"""
CRIO Availability Service - Python Backend Implementation
Ports the React TypeScript availability parser to Python for backend use
Handles Tyler calendar event parsing and capacity calculation
"""

from typing import List, Dict, Optional
from datetime import datetime, timedelta, time
import requests
import logging
import re
from core.database import db

logger = logging.getLogger(__name__)


class CRIOAvailabilityService:
    """
    Backend service for checking CRIO calendar availability
    Implements the same algorithm as the TypeScript AvailabilityParser
    """

    PROXY_URL = "https://scheduling-dashboard-proxy-480267397633.us-central1.run.app"
    TYLER_USER_ID = "5540"  # Tyler Hastings' CRIO user ID
    TYLER_USER_KEY = "5540"  # For API queries

    # Visit types that count against prescreen capacity
    PRESCREEN_VISIT_TYPES = ['Recruitment', 'Screening']

    def __init__(self):
        self.session = requests.Session()

    def _get_shared_session_tokens(self) -> Optional[Dict[str, str]]:
        """
        Get valid CRIO session tokens from shared database
        Returns None if no valid session exists

        These tokens are populated by V3 Dashboard after user login
        """
        try:
            result = db.execute_query("""
                SELECT session_id, csrf_token, expires_at,
                       EXTRACT(EPOCH FROM (expires_at - NOW())) / 3600 as hours_remaining
                FROM crio_shared_session
                WHERE is_active = TRUE
                  AND expires_at > NOW()
                ORDER BY authenticated_at DESC
                LIMIT 1
            """)

            if result and len(result) > 0:
                session = result[0]

                # Update usage tracking
                db.execute_update("""
                    UPDATE crio_shared_session
                    SET last_used_at = NOW(),
                        used_by_chatbot_count = used_by_chatbot_count + 1
                    WHERE is_active = TRUE
                      AND expires_at > NOW()
                """)

                logger.info(f"✅ Using shared CRIO session (expires in {session['hours_remaining']:.1f} hours)")

                return {
                    'session_id': session['session_id'],
                    'csrf_token': session['csrf_token']
                }
            else:
                return None

        except Exception as e:
            logger.error(f"❌ Failed to get shared session tokens: {e}")
            return None

    def get_next_available_slots(
        self,
        site_id: str,
        study_id: str,
        coordinator_email: str,
        num_slots: int = 3,
        days_ahead: int = 14
    ) -> List[Dict]:
        """
        Find next available appointment slots across multiple days

        Args:
            site_id: CRIO site ID (e.g., "2327")
            study_id: CRIO study ID
            coordinator_email: Coordinator email for the site
            num_slots: Number of available slots to return
            days_ahead: How many days to search into the future

        Returns:
            List of available slots:
            [{
                'datetime': '2025-08-15T09:00:00',
                'date': '2025-08-15',
                'time': '9:00 AM',
                'display': 'Friday, August 15 at 9:00 AM',
                'capacity_remaining': 2,
                'capacity_total': 3,
                'site_id': '2327',
                'site_name': 'ATL - General Medicine'
            }]
        """

        current_date = datetime.now().date()
        end_date = current_date + timedelta(days=days_ahead)

        logger.info(f"🔍 Searching for {num_slots} available slots at site {site_id}")
        logger.info(f"   Searching from {current_date} to {end_date}")

        # CRITICAL FIX: Fetch all events for the entire date range at once
        # CRIO API quirk: single-day queries return 0 events, but multi-day queries work
        events = self._fetch_calendar_events(site_id, current_date, end_date)

        if not events:
            logger.warning(f"No events returned from CRIO for site {site_id}")
            return []

        logger.info(f"✅ Fetched {len(events)} total events from CRIO")

        # Separate Tyler admin events from patient visits
        tyler_events = [
            e for e in events
            if str(e.get('userId')) == self.TYLER_USER_ID and e.get('isAppointment')
        ]

        patient_visits = [
            e for e in events
            if e.get('visit') in self.PRESCREEN_VISIT_TYPES
        ]

        logger.info(f"   Tyler admin events: {len(tyler_events)}")
        logger.info(f"   Patient visits: {len(patient_visits)}")

        if not tyler_events:
            logger.warning("No Tyler admin events found - no availability to show")
            return []

        # Parse all Tyler events to extract slots
        all_slots = []
        for tyler_event in tyler_events:
            event_slots = self._parse_tyler_event_to_slots(tyler_event, patient_visits)
            all_slots.extend(event_slots)

        # Get today's date for filtering
        today = datetime.now().date()

        # Filter to only available slots (capacity > 0), exclude weekends, and exclude today (next-day only)
        available_slots = [
            s for s in all_slots
            if s['capacity_remaining'] > 0 and
            datetime.fromisoformat(s['datetime']).weekday() < 5 and
            datetime.fromisoformat(s['datetime']).date() > today  # Next-day only
        ]

        # Sort by datetime
        available_slots.sort(key=lambda s: s['datetime'])

        # Return top N slots
        result = available_slots[:num_slots]
        logger.info(f"✅ Returning {len(result)} available slots (next-day only, excluding today)")
        return result

    def _get_availability_for_date(
        self,
        site_id: str,
        study_id: str,
        date: datetime.date,
        coordinator_email: str
    ) -> List[Dict]:
        """Get all available slots for a specific date"""

        try:
            # Step 1: Fetch all calendar events for the date
            events = self._fetch_calendar_events(site_id, date)

            if not events:
                logger.debug(f"No events found for {date}")
                return []

            # Step 2: Separate Tyler admin events from patient visits
            tyler_events = [
                e for e in events
                if str(e.get('userId')) == self.TYLER_USER_ID and e.get('isAppointment')
            ]

            patient_visits = [
                e for e in events
                if e.get('visit') in self.PRESCREEN_VISIT_TYPES
            ]

            logger.debug(f"Found {len(tyler_events)} Tyler events, {len(patient_visits)} patient visits")

            if not tyler_events:
                return []

            # Step 3: Parse Tyler events to extract capacity and generate slots
            slots = []
            for tyler_event in tyler_events:
                event_slots = self._parse_tyler_event_to_slots(
                    tyler_event, patient_visits
                )
                slots.extend(event_slots)

            # Step 4: Filter only available slots (capacity > 0)
            available = [s for s in slots if s['capacity_remaining'] > 0]

            return available

        except Exception as e:
            logger.error(f"❌ Failed to get availability for {date}: {e}", exc_info=True)
            return []

    def _fetch_calendar_events(
        self,
        site_id: str,
        start_date: datetime.date,
        end_date: datetime.date
    ) -> List[Dict]:
        """
        Call CRIO internal schedule API to fetch calendar events for a date range
        Uses shared session from database (populated by V3 Dashboard login)

        CRITICAL: CRIO API only returns events for MULTI-DAY queries.
        Single-day queries (start=end) return 0 events due to API quirk.
        """

        # Get valid session tokens from shared database
        tokens = self._get_shared_session_tokens()
        if not tokens:
            logger.warning("⚠️  No valid CRIO session available in database")
            logger.info("   Log into V3 Dashboard to activate shared session")
            return []

        # Format dates for CRIO API (YYYY-MM-DD)
        start_str = start_date.strftime('%Y-%m-%d')
        end_str = end_date.strftime('%Y-%m-%d')

        endpoint = f"{self.PROXY_URL}/crio/production/internal/schedule"

        # Build URL with multiple filter-user parameters to match V3 Dashboard behavior
        # Cannot use params dict because we need duplicate keys
        url = (
            f"{endpoint}?"
            f"site_key={site_id}&"
            f"start={start_str}&"
            f"end={end_str}&"
            f"csrf_token={tokens['csrf_token']}&"
            f"session_id={tokens['session_id']}&"
            f"filter-user-{self.TYLER_USER_ID}={self.TYLER_USER_ID}"
        )

        try:
            response = self.session.get(url, timeout=15)

            if response.status_code == 401:
                logger.warning("⚠️ Got 401 Unauthorized - shared session tokens may be expired")
                logger.info("   User needs to log into V3 Dashboard to refresh session")
                return []

            response.raise_for_status()
            data = response.json()

            # CRIO internal API returns events in nested structure
            if data.get('success') and 'data' in data:
                return data['data']
            else:
                logger.warning(f"Unexpected response format: {data}")
                return []

        except requests.RequestException as e:
            logger.error(f"❌ CRIO API request failed: {e}")
            return []

    def _parse_tyler_event_to_slots(
        self,
        tyler_event: Dict,
        patient_visits: List[Dict]
    ) -> List[Dict]:
        """
        Parse Tyler event title to extract capacity and generate time slots
        Implements the core availability parsing algorithm from TypeScript
        """

        event_name = tyler_event.get('name', '') or tyler_event.get('title', '')
        start_time_str = tyler_event.get('start')
        end_time_str = tyler_event.get('end')

        if not event_name or not start_time_str or not end_time_str:
            return []

        # Parse capacity from event title
        capacity_info = self._extract_capacity_from_title(event_name)
        if not capacity_info:
            logger.debug(f"Could not parse capacity from: {event_name}")
            return []

        # Generate 30-minute time blocks
        slots = self._generate_time_blocks(
            start_time_str, end_time_str, capacity_info, event_name
        )

        # Calculate remaining capacity (subtract overlapping patient visits)
        for slot in slots:
            overlaps = self._count_overlapping_visits(
                slot['datetime_obj'], patient_visits
            )
            slot['capacity_remaining'] = max(0, slot['capacity_total'] - overlaps)

        return slots

    def _extract_capacity_from_title(self, title: str) -> Optional[Dict]:
        """
        Extract capacity from Tyler event title using regex patterns
        Ported from V3 Dashboard AvailabilityParser.tsx to match exact patterns

        Patterns handled (from V3 Dashboard):
        - "4 PS per hour (1 on hour, 2 on half hour)" → alternating capacity
        - "2 PS/Hour" → hourly capacity
        - "7 Recruitment / Hr" → hourly capacity (NEW from V3)
        - "4 General Recruitment / Hr" → hourly capacity (NEW from V3)
        - "4 PS per half hour" → half-hourly capacity
        - "89Bio 1/30" → study-specific capacity
        - "Viking 301 2/hr" → study with hourly capacity
        - "3 1283 Recruitments/Hr" → study number with recruitments (NEW from V3)
        """

        title_lower = title.lower()

        # Pattern 1: Alternating (1 on hour, 2 on half hour)
        match = re.search(r'(\d+)\s+on\s+hour.*?(\d+)\s+on\s+half', title_lower)
        if match:
            return {
                'type': 'alternating',
                'on_hour': int(match.group(1)),
                'on_half': int(match.group(2))
            }

        # Pattern 2: PS/Hour or PS per hour
        match = re.search(r'(\d+)\s*ps\s*[/]?\s*(?:per\s+)?hour', title_lower)
        if match:
            return {
                'type': 'hourly',
                'capacity': int(match.group(1))
            }

        # Pattern 3: Recruitment / Hr (FROM V3 DASHBOARD - Line 255, 540)
        # Matches: "7 Recruitment / Hr", "4 General Recruitment / Hr", "2 Recruitment (No Viking) / Hr"
        match = re.search(r'(\d+)\s*(?:general\s+)?recruitment(?:\s*\([^)]*\))?\s*/\s*hr', title_lower)
        if match:
            return {
                'type': 'hourly',
                'capacity': int(match.group(1))
            }

        # Pattern 4: Study Number Recruitments (FROM V3 - Line 431)
        # Matches: "3 1283 Recruitments/Hr", "2 1283 Recruitment/Hour"
        match = re.search(r'(\d+)\s+\d{3,5}\s+recruitments?\s*/\s*h(?:ou)?r', title_lower)
        if match:
            return {
                'type': 'hourly',
                'capacity': int(match.group(1))
            }

        # Pattern 5: PS per half hour
        match = re.search(r'(\d+)\s*ps\s*per\s*half', title_lower)
        if match:
            return {
                'type': 'half_hourly',
                'capacity': int(match.group(1))
            }

        # Pattern 6: Study-specific (e.g., "89Bio 1/30")
        match = re.search(r'(\d+)/30', title_lower)
        if match:
            return {
                'type': 'half_hourly',
                'capacity': int(match.group(1))
            }

        # Pattern 7: Study with hourly capacity (e.g., "Viking 301 2/hr")
        match = re.search(r'(\d+)/hr', title_lower)
        if match:
            return {
                'type': 'hourly',
                'capacity': int(match.group(1))
            }

        # Pattern 8: Just a number followed by PS (assume half-hourly)
        match = re.search(r'(\d+)\s*ps', title_lower)
        if match:
            return {
                'type': 'half_hourly',
                'capacity': int(match.group(1))
            }

        return None

    def _generate_time_blocks(
        self,
        start: str,
        end: str,
        capacity_info: Dict,
        event_name: str
    ) -> List[Dict]:
        """Generate 30-minute time blocks with capacity"""

        # Parse datetime strings (support both ISO and CRIO format)
        try:
            # Try ISO format first
            start_dt = datetime.fromisoformat(start.replace('Z', '+00:00'))
            end_dt = datetime.fromisoformat(end.replace('Z', '+00:00'))
        except ValueError:
            # Try CRIO format: "12/22/2025 08:00 AM"
            try:
                start_dt = datetime.strptime(start, '%m/%d/%Y %I:%M %p')
                end_dt = datetime.strptime(end, '%m/%d/%Y %I:%M %p')
            except ValueError as e:
                logger.error(f"Failed to parse datetime: {e}")
                return []

        # Remove timezone info for consistency
        start_dt = start_dt.replace(tzinfo=None)
        end_dt = end_dt.replace(tzinfo=None)

        slots = []
        current = start_dt

        while current < end_dt:
            # Calculate capacity for this slot based on type
            if capacity_info['type'] == 'alternating':
                # Check if on the hour (:00) or half hour (:30)
                if current.minute == 0:
                    capacity = capacity_info['on_hour']
                elif current.minute == 30:
                    capacity = capacity_info['on_half']
                else:
                    capacity = 0

            elif capacity_info['type'] == 'hourly':
                # Only on the hour (:00)
                capacity = capacity_info['capacity'] if current.minute == 0 else 0

            else:  # half_hourly
                # Every 30-minute block
                capacity = capacity_info['capacity']

            # Only add slots with capacity
            if capacity > 0:
                slots.append({
                    'datetime_obj': current,
                    'datetime': current.isoformat(),
                    'date': current.strftime('%Y-%m-%d'),
                    'time': current.strftime('%-I:%M %p'),  # "9:00 AM"
                    'display': current.strftime('%A, %B %-d at %-I:%M %p'),  # "Friday, August 15 at 9:00 AM"
                    'capacity_total': capacity,
                    'capacity_remaining': capacity,  # Will be updated
                    'event_name': event_name
                })

            # Move to next 30-minute block
            current += timedelta(minutes=30)

        return slots

    def _count_overlapping_visits(
        self,
        slot_datetime: datetime,
        patient_visits: List[Dict]
    ) -> int:
        """Count how many patient visits overlap with this time slot"""

        slot_end = slot_datetime + timedelta(minutes=30)
        count = 0

        for visit in patient_visits:
            try:
                visit_start_str = visit.get('start')
                visit_end_str = visit.get('end')

                if not visit_start_str or not visit_end_str:
                    continue

                visit_start = datetime.fromisoformat(visit_start_str.replace('Z', '+00:00')).replace(tzinfo=None)
                visit_end = datetime.fromisoformat(visit_end_str.replace('Z', '+00:00')).replace(tzinfo=None)

                # Check for overlap: visit starts before slot ends AND visit ends after slot starts
                if visit_start < slot_end and visit_end > slot_datetime:
                    count += 1

            except (ValueError, AttributeError) as e:
                logger.warning(f"Failed to parse visit datetime: {e}")
                continue

        return count


# Singleton instance
crio_availability_service = CRIOAvailabilityService()
