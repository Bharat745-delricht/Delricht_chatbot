#!/bin/bash

# =============================================================================
# Gemini Chatbot Deployment Script
# Usage: ./deploy.sh [environment]
#   environment: dev | prod | staging (default: dev)
# =============================================================================

set -e  # Exit on any error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
PROJECT_ID="gemini-chatbot-2025"
REGION="us-central1"

# Get environment from argument or default to dev
ENVIRONMENT=${1:-dev}

# Map environment to service name
case "$ENVIRONMENT" in
    dev)
        SERVICE_NAME="gemini-chatbot-dev"
        ;;
    staging)
        SERVICE_NAME="gemini-chatbot-staging"
        ;;
    prod|production)
        SERVICE_NAME="gemini-chatbot"
        ENVIRONMENT="prod"
        ;;
    *)
        echo -e "${RED}Error: Invalid environment '$ENVIRONMENT'${NC}"
        echo "Valid environments: dev, staging, prod"
        exit 1
        ;;
esac

echo -e "${BLUE}=============================================${NC}"
echo -e "${BLUE}  Gemini Chatbot Deployment${NC}"
echo -e "${BLUE}=============================================${NC}"
echo ""
echo -e "Environment:  ${YELLOW}$ENVIRONMENT${NC}"
echo -e "Service:      ${YELLOW}$SERVICE_NAME${NC}"
echo -e "Project:      ${YELLOW}$PROJECT_ID${NC}"
echo -e "Region:       ${YELLOW}$REGION${NC}"
echo ""

# Production safety check
if [[ "$ENVIRONMENT" == "prod" ]]; then
    echo -e "${RED}WARNING: You are deploying to PRODUCTION!${NC}"
    read -p "Are you sure you want to continue? (yes/no): " confirm
    if [[ "$confirm" != "yes" ]]; then
        echo "Deployment cancelled."
        exit 0
    fi
fi

# Check if gcloud is authenticated
echo -e "${BLUE}Checking gcloud authentication...${NC}"
if ! gcloud auth list --filter=status:ACTIVE --format="value(account)" | head -n1 > /dev/null 2>&1; then
    echo -e "${RED}Error: Not authenticated with gcloud. Run 'gcloud auth login'${NC}"
    exit 1
fi

# Set the project
echo -e "${BLUE}Setting project to $PROJECT_ID...${NC}"
gcloud config set project $PROJECT_ID

# Get current timestamp for deployment tag
DEPLOYMENT_TIME=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

# Deploy to Cloud Run with all required env vars and secrets
echo ""
echo -e "${BLUE}Deploying to Cloud Run...${NC}"
echo -e "${YELLOW}This may take 2-5 minutes...${NC}"
echo ""

gcloud run deploy $SERVICE_NAME \
    --source . \
    --region=$REGION \
    --project=$PROJECT_ID \
    --allow-unauthenticated \
    --memory=1Gi \
    --cpu=1 \
    --min-instances=0 \
    --max-instances=10 \
    --timeout=300 \
    --set-env-vars="ENVIRONMENT=$ENVIRONMENT,DB_NAME=gemini_chatbot_database,DB_USER=postgres,DB_HOST=34.56.137.172,DB_PORT=5432,GOOGLE_CLOUD_PROJECT=gemini-chatbot-2025,DEPLOYMENT_TIME=$DEPLOYMENT_TIME,DEPLOYMENT_TAG=$ENVIRONMENT-deploy,SENDGRID_APPOINTMENT_TEMPLATE_ID=d-986f88f6d7af4726a56bb31aec6c3518" \
    --set-secrets="GEMINI_API_KEY=gemini-api-key:latest,DB_PASS=db-password:latest,SENDGRID_API_KEY=sendgrid-api-key:latest,TWILIO_ACCOUNT_SID=TWILIO_ACCOUNT_SID:latest,TWILIO_AUTH_TOKEN=TWILIO_AUTH_TOKEN:latest,TWILIO_PHONE_NUMBER=TWILIO_PHONE_NUMBER:latest"

# Get the service URL
SERVICE_URL=$(gcloud run services describe $SERVICE_NAME \
    --region=$REGION \
    --project=$PROJECT_ID \
    --format="value(status.url)")

echo ""
echo -e "${GREEN}=============================================${NC}"
echo -e "${GREEN}  Deployment Successful!${NC}"
echo -e "${GREEN}=============================================${NC}"
echo ""
echo -e "Service URL: ${YELLOW}$SERVICE_URL${NC}"
echo ""

# Health check
echo -e "${BLUE}Running health check...${NC}"
HEALTH_RESPONSE=$(curl -s -o /dev/null -w "%{http_code}" "$SERVICE_URL/health" || echo "000")

if [[ "$HEALTH_RESPONSE" == "200" ]]; then
    echo -e "${GREEN}Health check passed!${NC}"
else
    echo -e "${YELLOW}Health check returned: $HEALTH_RESPONSE (may take a moment to start)${NC}"
fi

echo ""
echo -e "${GREEN}Deployment complete!${NC}"
echo ""
echo "Useful commands:"
echo "  View logs:    gcloud logging read \"resource.labels.service_name=$SERVICE_NAME\" --limit=50"
echo "  View service: gcloud run services describe $SERVICE_NAME --region=$REGION"
echo ""
