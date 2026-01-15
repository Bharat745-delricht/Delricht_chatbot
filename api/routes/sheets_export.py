"""
Google Sheets Export API - Write availability data to Google Sheets
Handles availability table exports from V3 Dashboard to Google Sheets

Production endpoint: POST /api/sheets/availability/export
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
from datetime import datetime
import logging
import os
import json
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# Configure logging
logger = logging.getLogger(__name__)

# =============================================================================
# Router Setup
# =============================================================================
router = APIRouter(prefix="/api/sheets", tags=["Google Sheets Export"])

# =============================================================================
# Request/Response Models
# =============================================================================
class AvailabilityExportRequest(BaseModel):
    """Request model for availability export"""
    spreadsheetId: Optional[str] = None  # If None, create new sheet
    sheetName: str = "Availability Data"
    headers: List[str]  # ["Site Name", "10/30", "10/31", ...]
    rows: List[List[Any]]  # [["ATL - Gen Med", 15, 23, 18, ...], ...]
    metadata: Dict[str, Any]  # Export metadata (timestamp, date range, etc.)


class CreateSpreadsheetRequest(BaseModel):
    """Request model for creating new spreadsheet"""
    title: str
    ownerEmail: str
    sheetName: str = "Availability Data"


class ExportResponse(BaseModel):
    """Response model for export operations"""
    success: bool
    message: str
    spreadsheetId: Optional[str] = None
    spreadsheetUrl: Optional[str] = None
    rowsWritten: Optional[int] = None
    timestamp: str


# =============================================================================
# Google Sheets Service Setup
# =============================================================================
def get_sheets_service():
    """
    Get authenticated Google Sheets API service
    Uses Service Account credentials from Google Secret Manager
    """
    try:
        # Get credentials from environment or Secret Manager
        credentials_json = os.getenv('GOOGLE_SHEETS_SERVICE_ACCOUNT')

        if not credentials_json:
            logger.error("GOOGLE_SHEETS_SERVICE_ACCOUNT not found in environment")
            raise ValueError("Google Sheets service account credentials not configured")

        # Parse credentials JSON
        credentials_info = json.loads(credentials_json)

        # Create credentials with domain-wide delegation
        credentials = service_account.Credentials.from_service_account_info(
            credentials_info,
            scopes=[
                'https://www.googleapis.com/auth/spreadsheets',
                'https://www.googleapis.com/auth/drive.file'
            ]
        )

        # Delegate to the requesting user for domain-wide delegation
        # This allows the service account to act on behalf of mmorris@delricht.com
        delegated_credentials = credentials.with_subject('mmorris@delricht.com')

        # Build service with delegated credentials
        service = build('sheets', 'v4', credentials=delegated_credentials)
        logger.info("Google Sheets API service initialized successfully")
        return service

    except Exception as e:
        logger.error(f"Failed to initialize Google Sheets service: {e}")
        raise


def get_drive_service():
    """Get authenticated Google Drive API service for permissions"""
    try:
        credentials_json = os.getenv('GOOGLE_SHEETS_SERVICE_ACCOUNT')
        if not credentials_json:
            raise ValueError("Google Sheets service account credentials not configured")

        credentials_info = json.loads(credentials_json)
        credentials = service_account.Credentials.from_service_account_info(
            credentials_info,
            scopes=[
                'https://www.googleapis.com/auth/spreadsheets',
                'https://www.googleapis.com/auth/drive.file'
            ]
        )

        # Delegate to the requesting user for domain-wide delegation
        delegated_credentials = credentials.with_subject('mmorris@delricht.com')

        service = build('drive', 'v3', credentials=delegated_credentials)
        return service

    except Exception as e:
        logger.error(f"Failed to initialize Google Drive service: {e}")
        raise


# =============================================================================
# API Endpoints
# =============================================================================

@router.post("/availability/export", response_model=ExportResponse)
async def export_availability_to_sheets(request: AvailabilityExportRequest):
    """
    Export availability data to Google Sheets

    - If spreadsheetId provided: update existing sheet
    - If no spreadsheetId: create new spreadsheet

    Format: Pivoted table (Site Name | Date1 | Date2 | ...)
    """
    try:
        logger.info(f"Starting availability export: {len(request.rows)} rows, {len(request.headers)} columns")

        # Get authenticated service
        sheets_service = get_sheets_service()

        # If no spreadsheet ID, create new spreadsheet first
        if not request.spreadsheetId:
            logger.info("No spreadsheet ID provided, creating new spreadsheet")
            create_result = await create_spreadsheet(CreateSpreadsheetRequest(
                title=f"DelRicht Availability Export - {datetime.now().strftime('%Y-%m-%d %H:%M')}",
                ownerEmail="mmorris@delricht.com",
                sheetName=request.sheetName
            ))

            if not create_result.success:
                raise HTTPException(status_code=500, detail=create_result.message)

            spreadsheet_id = create_result.spreadsheetId
            logger.info(f"New spreadsheet created: {spreadsheet_id}")
        else:
            spreadsheet_id = request.spreadsheetId

        # Prepare data: headers + rows
        values = [request.headers] + request.rows

        # Write data to sheet
        body = {
            'values': values,
            'majorDimension': 'ROWS'
        }

        # Clear existing data first (to avoid stale data)
        sheets_service.spreadsheets().values().clear(
            spreadsheetId=spreadsheet_id,
            range=f"{request.sheetName}!A1:ZZ10000"
        ).execute()

        # Write new data
        result = sheets_service.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id,
            range=f"{request.sheetName}!A1",
            valueInputOption='RAW',
            body=body
        ).execute()

        rows_written = result.get('updatedRows', 0)
        logger.info(f"Successfully wrote {rows_written} rows to spreadsheet {spreadsheet_id}")

        # Get the sheet ID for the specified sheet name
        spreadsheet = sheets_service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
        sheet_id = None
        for sheet in spreadsheet.get('sheets', []):
            if sheet['properties']['title'] == request.sheetName:
                sheet_id = sheet['properties']['sheetId']
                break

        if sheet_id is None:
            logger.warning(f"Could not find sheet '{request.sheetName}' for formatting")
        else:
            # Format the sheet (bold headers, freeze first row/column)
            format_requests = [
                # Bold header row
                {
                    'repeatCell': {
                        'range': {
                            'sheetId': sheet_id,
                            'startRowIndex': 0,
                            'endRowIndex': 1
                        },
                        'cell': {
                            'userEnteredFormat': {
                                'textFormat': {'bold': True},
                                'backgroundColor': {'red': 0.95, 'green': 0.95, 'blue': 0.95}
                            }
                        },
                        'fields': 'userEnteredFormat(textFormat,backgroundColor)'
                    }
                },
                # Freeze first row and first column
                {
                    'updateSheetProperties': {
                        'properties': {
                            'sheetId': sheet_id,
                            'gridProperties': {
                                'frozenRowCount': 1,
                                'frozenColumnCount': 1
                            }
                        },
                        'fields': 'gridProperties.frozenRowCount,gridProperties.frozenColumnCount'
                    }
                }
            ]

            sheets_service.spreadsheets().batchUpdate(
                spreadsheetId=spreadsheet_id,
                body={'requests': format_requests}
            ).execute()

        spreadsheet_url = f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/edit"

        return ExportResponse(
            success=True,
            message=f"Successfully exported {rows_written} rows to Google Sheets",
            spreadsheetId=spreadsheet_id,
            spreadsheetUrl=spreadsheet_url,
            rowsWritten=rows_written,
            timestamp=datetime.now().isoformat()
        )

    except HttpError as e:
        logger.error(f"Google Sheets API error: {e}")
        raise HTTPException(status_code=500, detail=f"Google Sheets API error: {str(e)}")
    except Exception as e:
        logger.error(f"Export failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Export failed: {str(e)}")


@router.post("/create", response_model=ExportResponse)
async def create_spreadsheet(request: CreateSpreadsheetRequest):
    """
    Create a new Google Spreadsheet and share it with specified owner

    Returns:
        - spreadsheetId: ID of created spreadsheet
        - spreadsheetUrl: Direct URL to open the spreadsheet
    """
    try:
        logger.info(f"Creating new spreadsheet: {request.title}")

        # Get authenticated services
        sheets_service = get_sheets_service()
        drive_service = get_drive_service()

        # Create spreadsheet
        spreadsheet = {
            'properties': {
                'title': request.title
            },
            'sheets': [{
                'properties': {
                    'title': request.sheetName,
                    'gridProperties': {
                        'rowCount': 1000,
                        'columnCount': 50
                    }
                }
            }]
        }

        result = sheets_service.spreadsheets().create(body=spreadsheet).execute()
        spreadsheet_id = result.get('spreadsheetId')

        logger.info(f"Spreadsheet created: {spreadsheet_id}")

        # Share with owner email (grant edit permission)
        permission = {
            'type': 'user',
            'role': 'writer',
            'emailAddress': request.ownerEmail
        }

        try:
            drive_service.permissions().create(
                fileId=spreadsheet_id,
                body=permission,
                sendNotificationEmail=True
            ).execute()
            logger.info(f"Spreadsheet shared with {request.ownerEmail}")
        except HttpError as e:
            logger.warning(f"Failed to share spreadsheet: {e}")
            # Don't fail the entire operation if sharing fails

        spreadsheet_url = f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/edit"

        return ExportResponse(
            success=True,
            message=f"Spreadsheet created and shared with {request.ownerEmail}",
            spreadsheetId=spreadsheet_id,
            spreadsheetUrl=spreadsheet_url,
            timestamp=datetime.now().isoformat()
        )

    except HttpError as e:
        logger.error(f"Google Sheets API error: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to create spreadsheet: {str(e)}")
    except Exception as e:
        logger.error(f"Create spreadsheet failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to create spreadsheet: {str(e)}")


@router.get("/health")
async def sheets_health_check():
    """
    Health check endpoint for Google Sheets integration
    Verifies service account credentials are configured
    """
    try:
        # Check if credentials are available
        credentials_json = os.getenv('GOOGLE_SHEETS_SERVICE_ACCOUNT')

        if not credentials_json:
            return {
                "status": "not_configured",
                "message": "Google Sheets service account credentials not configured",
                "timestamp": datetime.now().isoformat()
            }

        # Try to initialize service
        sheets_service = get_sheets_service()

        return {
            "status": "healthy",
            "message": "Google Sheets API service is operational",
            "timestamp": datetime.now().isoformat()
        }

    except Exception as e:
        logger.error(f"Health check failed: {e}")
        return {
            "status": "error",
            "message": f"Google Sheets service error: {str(e)}",
            "timestamp": datetime.now().isoformat()
        }
