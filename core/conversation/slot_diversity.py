"""
Slot Diversity Algorithm
Selects diverse appointment slots across different half-days to give patients better options
"""

from datetime import datetime
from typing import List, Dict, Any
import logging

logger = logging.getLogger(__name__)


def select_diverse_slots(all_slots: List[Dict[str, Any]], num_slots: int = 3) -> List[Dict[str, Any]]:
    """
    Select diverse appointment slots spanning different half-days

    Strategy:
    1. Group slots by half-day (AM/PM for each date)
    2. Select slots from different half-days to maximize variety
    3. Prefer earlier dates but ensure time-of-day diversity

    Example:
    - Input: [8:00 AM, 8:30 AM, 9:00 AM, 2:00 PM, 2:30 PM, next day 9:00 AM, ...]
    - Output: [8:00 AM (12/31), 2:00 PM (12/31), 9:00 AM (1/1)]

    Args:
        all_slots: List of available slots (must be sorted chronologically)
        num_slots: Number of diverse slots to select (default: 3)

    Returns:
        List of diverse slots
    """
    if not all_slots:
        return []

    if len(all_slots) <= num_slots:
        return all_slots

    # Group slots by half-day
    half_day_groups = {}

    for slot in all_slots:
        dt = datetime.fromisoformat(slot['datetime'])
        date_key = dt.strftime('%Y-%m-%d')
        half_day = 'AM' if dt.hour < 12 else 'PM'
        key = f"{date_key}_{half_day}"

        if key not in half_day_groups:
            half_day_groups[key] = []
        half_day_groups[key].append(slot)

    logger.info(f"📊 Slot diversity: Found {len(half_day_groups)} half-day groups from {len(all_slots)} total slots")

    # Select diverse slots
    selected = []
    used_half_days = set()

    # Strategy: Pick first slot from each unique half-day until we have enough
    for slot in all_slots:
        if len(selected) >= num_slots:
            break

        dt = datetime.fromisoformat(slot['datetime'])
        date_key = dt.strftime('%Y-%m-%d')
        half_day = 'AM' if dt.hour < 12 else 'PM'
        key = f"{date_key}_{half_day}"

        # Only take first slot from each half-day block
        if key not in used_half_days:
            selected.append(slot)
            used_half_days.add(key)
            logger.info(f"   ✓ Selected: {slot['display']} ({half_day})")

    # If we still need more slots (e.g., only 2 half-days available but need 3 slots)
    # Fill with next available from already-used half-days
    if len(selected) < num_slots:
        for slot in all_slots:
            if len(selected) >= num_slots:
                break
            if slot not in selected:
                selected.append(slot)
                logger.info(f"   ✓ Filled: {slot['display']} (additional)")

    logger.info(f"📊 Diversity result: Selected {len(selected)} slots spanning {len(used_half_days)} half-days")

    return selected


def select_slots_by_time_preference(
    all_slots: List[Dict[str, Any]],
    preference: str,
    num_slots: int = 3
) -> List[Dict[str, Any]]:
    """
    Filter slots by time-of-day preference

    Args:
        all_slots: All available slots
        preference: 'morning', 'afternoon', 'evening', or 'any'
        num_slots: Number of slots to return

    Returns:
        Filtered list of slots matching preference
    """
    if preference == 'any' or not preference:
        return all_slots[:num_slots]

    filtered = []

    for slot in all_slots:
        dt = datetime.fromisoformat(slot['datetime'])
        hour = dt.hour

        matches = False
        if preference == 'morning' and hour < 12:
            matches = True
        elif preference == 'afternoon' and 12 <= hour < 17:
            matches = True
        elif preference == 'evening' and hour >= 17:
            matches = True

        if matches:
            filtered.append(slot)
            if len(filtered) >= num_slots:
                break

    logger.info(f"📊 Time preference filter: {len(filtered)} {preference} slots from {len(all_slots)} total")

    return filtered


def format_slot_diversity_summary(slots: List[Dict[str, Any]]) -> str:
    """
    Generate a summary showing the diversity of selected slots
    Useful for logging and debugging

    Args:
        slots: List of slots to summarize

    Returns:
        Human-readable summary string
    """
    if not slots:
        return "No slots available"

    summary_parts = []
    for slot in slots:
        dt = datetime.fromisoformat(slot['datetime'])
        date = dt.strftime('%m/%d')
        time = dt.strftime('%I:%M %p')
        half_day = 'AM' if dt.hour < 12 else 'PM'
        summary_parts.append(f"{date} {time} ({half_day})")

    return " | ".join(summary_parts)
