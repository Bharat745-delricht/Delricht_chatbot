#!/usr/bin/env python3
"""
Verify booking in database after automated test
"""

import sys
import json
from core.database import db

def verify_booking(session_id: str):
    """Verify booking data in database"""

    print("=" * 70)
    print(f"BOOKING VERIFICATION FOR: {session_id}")
    print("=" * 70)

    # 1. Chat logs
    chat = db.execute_query("""
        SELECT user_message, bot_response, timestamp
        FROM chat_logs WHERE session_id = %s ORDER BY timestamp
    """, (session_id,))
    print(f"\n📝 Chat Logs: {len(chat)} messages")
    if chat:
        last_msg = chat[-1]
        print(f"   Last message: {last_msg['timestamp']}")
        print(f"   Bot response: {last_msg['bot_response'][:150]}...")

    # 2. Context
    ctx = db.execute_query("""
        SELECT context_data FROM conversation_context WHERE session_id = %s
    """, (session_id,))
    if ctx:
        data = json.loads(ctx[0]['context_data']) if isinstance(ctx[0]['context_data'], str) else ctx[0]['context_data']
        print(f"\n📦 Context:")
        print(f"   State: {data.get('conversation_state')}")
        print(f"   booking_data: {data.get('booking_data')}")
        print(f"   presented_slots: {len(data.get('presented_slots', []))} slots")
        print(f"   selected_slot: {'✓' if data.get('selected_slot') else '✗'}")
        print(f"   booking_site_info: {'✓' if data.get('booking_site_info') else '✗'}")

    # 3. Contact info
    contact = db.execute_query("""
        SELECT first_name, last_name, phone_number, email, date_of_birth
        FROM patient_contact_info WHERE session_id = %s
    """, (session_id,))
    if contact:
        c = contact[0]
        print(f"\n👤 Contact Info:")
        print(f"   ✅ Name: {c['first_name']} {c['last_name']}")
        print(f"   ✅ Phone: {c['phone_number']}")
        print(f"   ✅ Email: {c['email']}")
        print(f"   ✅ DOB: {c['date_of_birth']}")
    else:
        print(f"\n👤 Contact Info: ❌ NOT FOUND")

    # 4. Appointment
    appt = db.execute_query("""
        SELECT appointment_date, status, notes FROM appointments WHERE session_id = %s
    """, (session_id,))
    if appt:
        a = appt[0]
        print(f"\n📅 Appointment:")
        print(f"   ✅ Date: {a['appointment_date']}")
        print(f"   ✅ Status: {a['status']}")
        print(f"   ✅ Notes: {a['notes'][:100]}...")
    else:
        print(f"\n📅 Appointment: ❌ NOT FOUND")

    # 5. Debug flow
    debug = db.execute_query("""
        SELECT step, success, error_message, created_at
        FROM debug_booking_flow WHERE session_id = %s ORDER BY created_at
    """, (session_id,))
    if debug:
        print(f"\n🔍 Debug Flow: {len(debug)} steps")
        for d in debug:
            status = "✅" if d['success'] else "❌"
            print(f"   {status} {d['step']}: {d['error_message'] or 'OK'}")

    # 6. Final verdict
    print("\n" + "=" * 70)
    if contact and appt:
        print("✅ BOOKING VERIFIED SUCCESSFULLY")
    elif chat and "Your booking has been submitted" in chat[-1]['bot_response']:
        print("⚠️  BOOKING MESSAGE SHOWN BUT DATA MISSING")
    else:
        print("❌ BOOKING FAILED")

    print("=" * 70)

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python verify_booking.py <session_id>")
        sys.exit(1)

    verify_booking(sys.argv[1])
