"""
Scheduled Reports API Routes
Endpoints for automated report generation triggered by Cloud Scheduler
"""
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from datetime import datetime, timedelta
import pytz
from core.database import db
from core.services.email_service import EmailService

router = APIRouter(prefix="/api/scheduled", tags=["scheduled_reports"])


class ContactReportRequest(BaseModel):
    """Request model for contact report"""
    email: str = "mmorris@delricht.com"
    cc_email: str = "rkallies@delricht.com"
    hours: int = 24


def generate_contact_report(hours=24):
    """
    Generate contact report for the last N hours

    Args:
        hours: Number of hours to look back (default: 24)

    Returns:
        str: Formatted report text
    """
    # Calculate cutoff time
    central = pytz.timezone('America/Chicago')
    now_central = datetime.now(central)
    cutoff_time = now_central - timedelta(hours=hours)

    # Convert to UTC for database query (database stores in UTC)
    cutoff_utc = cutoff_time.astimezone(pytz.UTC).replace(tzinfo=None)

    # Query for contacts collected in the last N hours
    query = """
        SELECT
            pci.session_id,
            COALESCE(ps.condition, 'Unknown') as indication,
            ps.started_at,
            CONCAT(pci.first_name, ' ', pci.last_name) as full_name,
            pci.phone_number
        FROM patient_contact_info pci
        LEFT JOIN prescreening_sessions ps ON pci.session_id = ps.session_id
        WHERE pci.phone_number IS NOT NULL
            AND pci.created_at >= %s
        ORDER BY pci.created_at DESC;
    """

    results = db.execute_query(query, (cutoff_utc,))

    if not results:
        return "No contact collections in the last 24 hours."

    # Format results
    report_lines = []
    report_lines.append(f"Contact Collections Report - {now_central.strftime('%Y-%m-%d')}")
    report_lines.append(f"Period: Last {hours} hours (since {cutoff_time.strftime('%Y-%m-%d %H:%M %Z')})")
    report_lines.append(f"Total Contacts: {len(results)}")
    report_lines.append("\n" + "="*80 + "\n")

    for row in results:
        session_id = row['session_id']
        indication = row['indication']
        started_at = row['started_at']
        full_name = row['full_name']
        phone = row['phone_number']

        # Format time started (convert from UTC to Central)
        if started_at:
            started_utc = pytz.UTC.localize(started_at)
            started_central = started_utc.astimezone(central)
            time_str = started_central.strftime('%Y-%m-%d %H:%M CT')
        else:
            time_str = 'N/A'

        report_lines.append(f"{session_id} - {indication} - {time_str} - {full_name} - {phone}")

    return "\n".join(report_lines)


async def send_contact_report(recipient_email="mmorris@delricht.com", cc_email="rkallies@delricht.com", hours=24):
    """
    Generate and send daily contact report

    Args:
        recipient_email: Email address to send report to
        cc_email: Email address to CC
        hours: Number of hours to look back

    Returns:
        bool: True if successful, False otherwise
    """
    try:
        print(f"Generating contact report for last {hours} hours...")
        report_text = generate_contact_report(hours)

        print(f"Sending report to {recipient_email} (CC: {cc_email})...")

        # Get current date for subject
        central = pytz.timezone('America/Chicago')
        today = datetime.now(central).strftime('%Y-%m-%d')

        # Send email using EmailService's async method with CC
        email_service = EmailService()
        await email_service._send_email(
            to_email=recipient_email,
            subject=f"Daily Contact Collections Report - {today}",
            content=report_text,
            cc_email=cc_email
        )

        print("✓ Report sent successfully!")
        return True

    except Exception as e:
        print(f"Error generating/sending report: {e}")
        import traceback
        traceback.print_exc()
        return False


@router.post("/contact-report")
async def trigger_contact_report(request: Request, body: ContactReportRequest = None):
    """
    Trigger daily contact report generation and email

    This endpoint is designed to be called by Cloud Scheduler.
    It generates a report of all contact collections (with phone numbers)
    from the last 24 hours and emails it to the specified recipient.

    Args:
        body: Optional request body with email, cc_email, and hours parameters

    Returns:
        dict: Status and message
    """
    try:
        # Use default values if body not provided
        email = body.email if body else "mmorris@delricht.com"
        cc_email = body.cc_email if body else "rkallies@delricht.com"
        hours = body.hours if body else 24

        print(f"Triggering contact report for {email} (CC: {cc_email}), last {hours} hours")

        # Generate and send report (await the async function)
        success = await send_contact_report(email, cc_email, hours)

        if success:
            return {
                "status": "success",
                "message": f"Contact report sent to {email} (CC: {cc_email})"
            }
        else:
            raise HTTPException(
                status_code=500,
                detail="Failed to send contact report"
            )

    except Exception as e:
        print(f"Error in contact report endpoint: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(
            status_code=500,
            detail=f"Error generating contact report: {str(e)}"
        )


@router.get("/contact-report/test")
async def test_contact_report():
    """
    Test endpoint to preview contact report without sending email

    Returns:
        dict: Preview of report data
    """
    try:
        report = generate_contact_report(24)

        return {
            "status": "success",
            "preview": report
        }

    except Exception as e:
        print(f"Error in contact report test endpoint: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(
            status_code=500,
            detail=f"Error generating contact report preview: {str(e)}"
        )
