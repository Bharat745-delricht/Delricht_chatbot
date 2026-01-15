"""Configuration settings for the clinical trials chatbot"""
import os
from typing import Optional
from pydantic_settings import BaseSettings


def detect_environment() -> str:
    """
    Detect current environment from Cloud Run service name or env var.
    Returns: 'dev', 'staging', or 'prod'
    """
    # First check explicit ENVIRONMENT variable
    explicit_env = os.getenv("ENVIRONMENT", "").lower()
    if explicit_env in ("dev", "staging", "prod", "production", "development"):
        if explicit_env == "production":
            return "prod"
        if explicit_env == "development":
            return "dev"
        return explicit_env

    # Detect from Cloud Run service name
    service_name = os.getenv("K_SERVICE", "")
    if "dev" in service_name.lower():
        return "dev"
    elif "staging" in service_name.lower():
        return "staging"
    elif service_name:  # Has a service name but not dev/staging = production
        return "prod"

    # Local development
    return "dev"


class Settings(BaseSettings):
    """Application settings using Pydantic for validation and environment variable loading"""

    # Database configuration
    DB_NAME: str = "gemini_chatbot_database"
    DB_USER: str = "gemini_chatbot_user"
    DB_PASS: str
    DB_HOST: Optional[str] = None
    DB_PORT: int = 5432
    INSTANCE_CONNECTION_NAME: str = "gemini-chatbot-2025:us-central1:gemini-chatbot-db"

    # Gemini AI configuration
    GEMINI_API_KEY: str
    GOOGLE_CLOUD_PROJECT: str = "gemini-chatbot-2025"

    # Embedding configuration
    EMBEDDING_MODEL: str = "text-embedding-004"

    # Email configuration
    EMAIL_PROVIDER: str = "sendgrid"
    MAILCHIMP_API_KEY: Optional[str] = None
    SENDGRID_API_KEY: Optional[str] = None
    SENDGRID_APPOINTMENT_TEMPLATE_ID: Optional[str] = None
    EMAIL_FROM: str = "info@delricht.com"
    EMAIL_FROM_NAME: str = "DelRicht Research"
    SCHEDULER_EMAIL: str = "scheduler@delricht.com"  # Primary recipient for notifications
    DASHBOARD_EMAIL: str = "mmorris@delricht.com"    # CC recipient for notifications

    # SMS configuration (Twilio)
    TWILIO_ACCOUNT_SID: Optional[str] = None
    TWILIO_AUTH_TOKEN: Optional[str] = None
    TWILIO_PHONE_NUMBER: Optional[str] = None

    # CRIO Backend Authentication (for chatbot scheduling)
    CRIO_USERNAME: str = os.getenv('CRIO_USERNAME', '')
    CRIO_PASSWORD: str = os.getenv('CRIO_PASSWORD', '')

    # Application settings
    MAX_CHUNK_SIZE: int = 1000
    CHUNK_OVERLAP: int = 200
    ENVIRONMENT: str = detect_environment()
    SECRET_KEY: str = "your-secret-key-change-in-production"
    DEBUG: bool = False
    LOG_LEVEL: str = "INFO"

    # Cloud Run detection
    IS_CLOUD_RUN: bool = bool(os.getenv("K_SERVICE"))

    # Environment helpers
    @property
    def is_production(self) -> bool:
        """Check if running in production environment"""
        return self.ENVIRONMENT == "prod"

    @property
    def is_development(self) -> bool:
        """Check if running in development environment"""
        return self.ENVIRONMENT == "dev"

    @property
    def is_staging(self) -> bool:
        """Check if running in staging environment"""
        return self.ENVIRONMENT == "staging"

    @property
    def service_url(self) -> str:
        """Get the appropriate service URL for current environment"""
        urls = {
            "dev": "https://gemini-chatbot-dev-480267397633.us-central1.run.app",
            "staging": "https://gemini-chatbot-staging-480267397633.us-central1.run.app",
            "prod": "https://gemini-chatbot-480267397633.us-central1.run.app"
        }
        return urls.get(self.ENVIRONMENT, urls["dev"])
    
    class Config:
        # Load from .env file
        env_file = ".env"
        case_sensitive = False


# Global settings instance
settings = Settings()