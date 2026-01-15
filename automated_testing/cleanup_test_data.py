#!/usr/bin/env python3
"""
Cleanup Test Data Script

Removes all AUTO_TEST_* sessions and related data from the database
"""

import sys
import os
from pathlib import Path

# Add parent directory to path
sys.path.append(str(Path(__file__).parent.parent))

from core.database import db
import argparse


def cleanup_test_data(session_prefix: str = "AUTO_TEST_", dry_run: bool = False):
    """
    Remove all test data from database

    Args:
        session_prefix: Prefix to identify test sessions
        dry_run: If True, only show what would be deleted without deleting
    """

    print(f"\n🧹 Test Data Cleanup")
    print(f"=" * 50)
    print(f"Session prefix: {session_prefix}")
    print(f"Mode: {'DRY RUN (no changes)' if dry_run else 'LIVE (will delete data)'}")
    print()

    try:
        # 1. Find all test sessions
        print("1. Finding test sessions...")
        sessions_query = """
            SELECT session_id, user_id, created_at
            FROM conversation_context
            WHERE session_id LIKE %s
            ORDER BY created_at DESC
        """
        sessions = db.execute_query(sessions_query, (f"{session_prefix}%",))
        print(f"   Found {len(sessions)} test sessions")

        if len(sessions) == 0:
            print("\n✓ No test data to clean up")
            return

        # Show sample
        if len(sessions) > 0:
            print(f"\n   Sample sessions:")
            for session in sessions[:5]:
                print(f"   • {session['session_id']} (created: {session['created_at']})")
            if len(sessions) > 5:
                print(f"   ... and {len(sessions) - 5} more")

        # 2. Count related data
        print("\n2. Counting related data...")

        # Chat logs
        chat_logs_query = """
            SELECT COUNT(*) as count FROM chat_logs WHERE session_id LIKE %s
        """
        chat_logs = db.execute_query(chat_logs_query, (f"{session_prefix}%",))
        chat_log_count = chat_logs[0]['count'] if chat_logs else 0
        print(f"   • Chat logs: {chat_log_count}")

        # Prescreening sessions
        prescreening_query = """
            SELECT COUNT(*) as count FROM prescreening_sessions WHERE session_id LIKE %s
        """
        prescreening = db.execute_query(prescreening_query, (f"{session_prefix}%",))
        prescreening_count = prescreening[0]['count'] if prescreening else 0
        print(f"   • Prescreening sessions: {prescreening_count}")

        # Prescreening answers
        answers_query = """
            SELECT COUNT(*) as count FROM prescreening_answers
            WHERE session_id LIKE %s
        """
        answers = db.execute_query(answers_query, (f"{session_prefix}%",))
        answers_count = answers[0]['count'] if answers else 0
        print(f"   • Prescreening answers: {answers_count}")

        # Contact collection (may not exist in all databases)
        try:
            contact_query = """
                SELECT COUNT(*) as count FROM contact_collection WHERE session_id LIKE %s
            """
            contacts = db.execute_query(contact_query, (f"{session_prefix}%",))
            contact_count = contacts[0]['count'] if contacts else 0
            print(f"   • Contact records: {contact_count}")
        except:
            contact_count = 0
            print(f"   • Contact records: N/A (table doesn't exist)")

        # Total records
        total_records = len(sessions) + chat_log_count + prescreening_count + answers_count + contact_count
        print(f"\n   Total records to delete: {total_records}")

        # 3. Confirm deletion (if not dry run)
        if not dry_run:
            print("\n⚠️  WARNING: This will permanently delete all test data!")
            confirm = input("Type 'DELETE' to confirm: ")

            if confirm != "DELETE":
                print("\n❌ Cleanup cancelled")
                return

            print("\n3. Deleting data...")

            # Delete in order (respect foreign keys)

            # Prescreening answers first
            if answers_count > 0:
                db.execute_update(
                    "DELETE FROM prescreening_answers WHERE session_id LIKE %s",
                    (f"{session_prefix}%",)
                )
                print(f"   ✓ Deleted {answers_count} prescreening answers")

            # Prescreening sessions
            if prescreening_count > 0:
                db.execute_update(
                    "DELETE FROM prescreening_sessions WHERE session_id LIKE %s",
                    (f"{session_prefix}%",)
                )
                print(f"   ✓ Deleted {prescreening_count} prescreening sessions")

            # Contact collection (may not exist)
            if contact_count > 0:
                try:
                    db.execute_update(
                        "DELETE FROM contact_collection WHERE session_id LIKE %s",
                        (f"{session_prefix}%",)
                    )
                    print(f"   ✓ Deleted {contact_count} contact records")
                except:
                    print(f"   ⚠️  Skipped contact_collection (table doesn't exist)")

            # Chat logs
            if chat_log_count > 0:
                db.execute_update(
                    "DELETE FROM chat_logs WHERE session_id LIKE %s",
                    (f"{session_prefix}%",)
                )
                print(f"   ✓ Deleted {chat_log_count} chat logs")

            # Conversation context
            db.execute_update(
                "DELETE FROM conversation_context WHERE session_id LIKE %s",
                (f"{session_prefix}%",)
            )
            print(f"   ✓ Deleted {len(sessions)} conversation contexts")

            print(f"\n✅ Cleanup complete! Deleted {total_records} records")

        else:
            print("\n✓ Dry run complete - no data was deleted")
            print("\nTo actually delete data, run:")
            print(f"  python automated_testing/cleanup_test_data.py --confirm")

    except Exception as e:
        print(f"\n❌ Error during cleanup: {e}")
        import traceback
        traceback.print_exc()


def main():
    parser = argparse.ArgumentParser(description="Clean up automated test data from database")
    parser.add_argument("--session-prefix", default="AUTO_TEST_", help="Session ID prefix to identify test data")
    parser.add_argument("--confirm", action="store_true", help="Actually delete data (default is dry run)")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be deleted without deleting")

    args = parser.parse_args()

    # Default to dry run unless --confirm is specified
    dry_run = not args.confirm or args.dry_run

    cleanup_test_data(session_prefix=args.session_prefix, dry_run=dry_run)


if __name__ == "__main__":
    main()
