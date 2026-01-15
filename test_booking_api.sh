#!/bin/bash

# API Booking Flow Test with CURL
# Tests the complete booking flow and email notifications

API_URL="https://gemini-chatbot-480267397633.us-central1.run.app"
SESSION_ID="CURL_TEST_$(date +%Y%m%d_%H%M%S)"

echo "============================================================"
echo "API BOOKING FLOW TEST"
echo "============================================================"
echo "API URL: $API_URL"
echo "Session ID: $SESSION_ID"
echo "Test Email: mmorris@delricht.com"
echo ""

# Colors for output
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo "============================================================"
echo "STEP 1: Search for trials in Tulsa"
echo "============================================================"

RESPONSE1=$(curl -s -X POST "$API_URL/api/gemini/chat" \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "'"$SESSION_ID"'",
    "message": "I live in Tulsa and have gout. Are there any trials for me?"
  }')

echo "$RESPONSE1" | jq -r '.response' | head -20
echo ""
echo -e "${GREEN}✓ Step 1 Complete${NC}"
echo ""
sleep 2

echo "============================================================"
echo "STEP 2: Start prescreening"
echo "============================================================"

RESPONSE2=$(curl -s -X POST "$API_URL/api/gemini/chat" \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "'"$SESSION_ID"'",
    "message": "Yes, I want to check my eligibility"
  }')

echo "$RESPONSE2" | jq -r '.response' | head -15
echo ""
echo -e "${GREEN}✓ Step 2 Complete${NC}"
echo ""
sleep 2

echo "============================================================"
echo "STEP 3: Answer prescreening questions"
echo "============================================================"

# Answer first question (typically "Do you have gout?")
echo "Answering Q1: Yes"
RESPONSE3=$(curl -s -X POST "$API_URL/api/gemini/chat" \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "'"$SESSION_ID"'",
    "message": "Yes"
  }')

echo "$RESPONSE3" | jq -r '.response' | head -10
sleep 1

# Answer age question
echo ""
echo "Answering Q2: Age 45"
RESPONSE4=$(curl -s -X POST "$API_URL/api/gemini/chat" \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "'"$SESSION_ID"'",
    "message": "45"
  }')

echo "$RESPONSE4" | jq -r '.response' | head -10
sleep 1

# Answer weight question
echo ""
echo "Answering Q3: Weight 180 pounds"
RESPONSE5=$(curl -s -X POST "$API_URL/api/gemini/chat" \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "'"$SESSION_ID"'",
    "message": "180 pounds"
  }')

echo "$RESPONSE5" | jq -r '.response' | head -10
sleep 1

# Answer height question
echo ""
echo "Answering Q4: Height 5 feet 10 inches"
RESPONSE6=$(curl -s -X POST "$API_URL/api/gemini/chat" \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "'"$SESSION_ID"'",
    "message": "5 feet 10 inches"
  }')

echo "$RESPONSE6" | jq -r '.response' | head -10
echo ""
echo -e "${GREEN}✓ Step 3 Complete${NC}"
echo ""
sleep 2

echo "============================================================"
echo "STEP 4: Book appointment (if offered)"
echo "============================================================"

RESPONSE7=$(curl -s -X POST "$API_URL/api/gemini/chat" \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "'"$SESSION_ID"'",
    "message": "yes I want to book the first slot"
  }')

echo "$RESPONSE7" | jq -r '.response' | head -20

# Check if booking details are needed
if echo "$RESPONSE7" | grep -q "name\|email\|phone"; then
  echo ""
  echo "Booking details requested..."
  sleep 2

  echo ""
  echo "============================================================"
  echo "STEP 5: Provide booking details"
  echo "============================================================"

  # Provide name
  echo "Providing name: Marshall Morris"
  RESPONSE8=$(curl -s -X POST "$API_URL/api/gemini/chat" \
    -H "Content-Type: application/json" \
    -d '{
      "session_id": "'"$SESSION_ID"'",
      "message": "Marshall Morris"
    }')

  echo "$RESPONSE8" | jq -r '.response' | head -10
  sleep 1

  # Provide email
  echo ""
  echo "Providing email: mmorris@delricht.com"
  RESPONSE9=$(curl -s -X POST "$API_URL/api/gemini/chat" \
    -H "Content-Type: application/json" \
    -d '{
      "session_id": "'"$SESSION_ID"'",
      "message": "mmorris@delricht.com"
    }')

  echo "$RESPONSE9" | jq -r '.response' | head -10
  sleep 1

  # Provide phone
  echo ""
  echo "Providing phone: 504-336-2643"
  RESPONSE10=$(curl -s -X POST "$API_URL/api/gemini/chat" \
    -H "Content-Type: application/json" \
    -d '{
      "session_id": "'"$SESSION_ID"'",
      "message": "504-336-2643"
    }')

  echo "$RESPONSE10" | jq -r '.response' | head -10
  sleep 1

  # Provide DOB
  echo ""
  echo "Providing DOB: 01/15/1980"
  RESPONSE11=$(curl -s -X POST "$API_URL/api/gemini/chat" \
    -H "Content-Type: application/json" \
    -d '{
      "session_id": "'"$SESSION_ID"'",
      "message": "01/15/1980"
    }')

  echo "$RESPONSE11" | jq -r '.response'
  echo ""
  echo -e "${GREEN}✓ Booking details provided${NC}"
fi

echo ""
echo "============================================================"
echo "CHECKING BOOKING STATUS"
echo "============================================================"

# Check database for the booking
echo "Querying database for session: $SESSION_ID"
echo ""

DB_CHECK=$(DB_PASS="Delricht2017!" python3 -c "
from core.database import db
import json

# Check conversation context
context = db.execute_query('SELECT * FROM conversation_context WHERE session_id = %s', ('$SESSION_ID',))
print('Conversation context:', 'FOUND' if context else 'NOT FOUND')

# Check appointments
appts = db.execute_query('SELECT * FROM appointments WHERE session_id = %s', ('$SESSION_ID',))
if appts:
    print('Appointment created: YES')
    print('  ID:', appts[0]['id'])
    print('  Status:', appts[0]['status'])
    print('  Site:', appts[0]['site_id'])
    print('  Date:', appts[0]['appointment_date'])
else:
    print('Appointment created: NO')

# Check contact info
contacts = db.execute_query('SELECT * FROM patient_contact_info WHERE session_id = %s', ('$SESSION_ID',))
if contacts:
    print('Contact info saved: YES')
    print('  Name:', contacts[0]['first_name'], contacts[0]['last_name'])
    print('  Email:', contacts[0]['email'])
    print('  Phone:', contacts[0]['phone_number'])
else:
    print('Contact info saved: NO')
" 2>&1)

echo "$DB_CHECK"
echo ""

echo "============================================================"
echo "CHECKING CLOUD RUN LOGS FOR EMAIL ACTIVITY"
echo "============================================================"
echo "Searching for email-related log entries..."
echo ""

# Get recent logs related to emails
gcloud logging read "resource.labels.service_name=gemini-chatbot" \
  --limit=20 \
  --format="table(timestamp, severity, textPayload)" \
  --filter="textPayload:\"email\" AND timestamp>=\"$(date -u -v-5M '+%Y-%m-%dT%H:%M:%S')\"" \
  2>/dev/null || echo "Unable to fetch logs (gcloud auth may be needed)"

echo ""
echo "============================================================"
echo "TEST SUMMARY"
echo "============================================================"
echo "Session ID: $SESSION_ID"
echo ""
echo "Next steps:"
echo "  1. Check mmorris@delricht.com inbox for:"
echo "     - Patient confirmation email (HTML)"
echo "     - Coordinator notification email (plain text)"
echo ""
echo "  2. Check spam/junk folder"
echo ""
echo "  3. Review full logs:"
echo "     gcloud logging read \"resource.labels.service_name=gemini-chatbot\" \\"
echo "       --limit=50 \\"
echo "       --filter='textPayload:\"$SESSION_ID\"'"
echo ""
echo "  4. Check database:"
echo "     SELECT * FROM appointments WHERE session_id = '$SESSION_ID';"
echo ""
echo "============================================================"
