#!/bin/bash

# AI Analytics Service Deployment Script
set -e

# Configuration
PROJECT_ID="gemini-chatbot-2025"
SERVICE_NAME="ai-analytics"
REGION="us-central1"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${BLUE}🧠 Deploying AI Analytics Service${NC}"
echo "=================================================="

# Check if gcloud is authenticated
echo -e "${BLUE}📋 Checking prerequisites...${NC}"
if ! gcloud auth list --filter=status:ACTIVE --format="value(account)" | head -n 1 > /dev/null; then
    echo -e "${RED}❌ Error: No active gcloud authentication found${NC}"
    echo "Please run: gcloud auth login"
    exit 1
fi

# Set project
echo -e "${BLUE}🔧 Setting project to ${PROJECT_ID}...${NC}"
gcloud config set project $PROJECT_ID

# Deploy to Cloud Run
echo -e "${BLUE}🚀 Deploying to Cloud Run...${NC}"
gcloud run deploy $SERVICE_NAME \
    --source . \
    --region=$REGION \
    --project=$PROJECT_ID \
    --platform=managed \
    --allow-unauthenticated \
    --port=8080 \
    --memory=512Mi \
    --cpu=1 \
    --concurrency=100 \
    --max-instances=10

# Get the service URL
SERVICE_URL=$(gcloud run services describe $SERVICE_NAME --platform managed --region $REGION --format 'value(status.url)')

echo -e "${GREEN}✅ Deployment successful!${NC}"
echo "=================================================="
echo -e "${GREEN}🌐 AI Analytics URL: ${SERVICE_URL}${NC}"
echo -e "${BLUE}📊 Features:${NC}"
echo "   • Natural Language BigQuery Interface"
echo "   • Real-time CRIO Data Analysis"  
echo "   • Interactive Query Suggestions"
echo "   • AI-powered Insights Generation"
echo ""
echo -e "${YELLOW}🧪 Test Queries:${NC}"
echo "   • 'How many appointments are there in August 2025?'"
echo "   • 'How many Screens have been completed this week?'"
echo "   • 'Show me Atlanta data'"
echo "   • 'What About just Recruitment?'"
echo ""
echo -e "${BLUE}🔗 API Endpoint:${NC}"
echo "   POST ${SERVICE_URL}/api/bigquery/natural-language"
echo ""

exit 0