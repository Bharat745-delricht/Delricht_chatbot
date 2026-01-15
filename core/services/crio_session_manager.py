"""
CRIO Session Manager - Backend Authentication for Chatbot Scheduling
Handles automatic authentication and token lifecycle management
Completely separate from V3 Dashboard token management
"""

import requests
import logging
from datetime import datetime, timedelta
from typing import Optional, Dict
import threading
import os

logger = logging.getLogger(__name__)


class CRIOSessionManager:
    """
    Manages CRIO session authentication for backend chatbot services
    Automatically refreshes tokens before expiry
    Thread-safe singleton instance

    This is SEPARATE from the V3 Dashboard authentication system.
    V3 Dashboard uses user-provided tokens stored in localStorage.
    This service uses backend credentials for automated scheduling.
    """

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if not hasattr(self, 'initialized'):
            self.proxy_url = "https://scheduling-dashboard-proxy-480267397633.us-central1.run.app"
            self.session_id: Optional[str] = None
            self.csrf_token: Optional[str] = None
            self.authenticated_at: Optional[datetime] = None
            self.token_expiry_hours = 7  # Refresh before 8-hour CRIO limit
            self.auth_failures = 0
            self.max_auth_failures = 3
            self.initialized = True

            logger.info("🔐 CRIO Session Manager initialized (Backend Auth System)")

    def _authenticate(self) -> bool:
        """
        Authenticate with CRIO and store tokens
        Uses credentials from Secret Manager/environment variables
        """
        try:
            # Get credentials from environment (injected by Cloud Run from Secret Manager)
            username = os.getenv('CRIO_USERNAME', '').strip()
            password = os.getenv('CRIO_PASSWORD', '').strip()

            if not username or not password:
                logger.error("❌ CRIO credentials not configured in environment variables")
                logger.error("   Required: CRIO_USERNAME and CRIO_PASSWORD")
                logger.error("   These should be injected from Secret Manager by Cloud Run")
                return False

            logger.info(f"🔑 Authenticating with CRIO as {username}...")

            # Call proxy authentication endpoint
            response = requests.post(
                f"{self.proxy_url}/crio/auth/login",
                json={
                    "username": username,
                    "password": password,
                    "environment": "production"
                },
                timeout=30
            )

            if response.status_code != 200:
                logger.error(f"❌ CRIO authentication failed: {response.status_code}")
                logger.error(f"   Response: {response.text[:200]}")
                self.auth_failures += 1
                return False

            auth_data = response.json()

            if not auth_data.get('success'):
                error_msg = auth_data.get('error', 'Unknown error')
                logger.error(f"❌ CRIO authentication failed: {error_msg}")
                self.auth_failures += 1
                return False

            # Store tokens
            self.session_id = auth_data['session_id']
            self.csrf_token = auth_data['csrf_token']
            self.authenticated_at = datetime.now()
            self.auth_failures = 0  # Reset failure count on success

            logger.info(f"✅ CRIO authentication successful")
            logger.info(f"   Session ID: {self.session_id[:20]}...")
            logger.info(f"   CSRF Token: {self.csrf_token[:20]}...")
            logger.info(f"   Valid until: {self.get_token_expiry().isoformat()}")

            return True

        except requests.RequestException as e:
            logger.error(f"❌ Network error during CRIO authentication: {e}")
            self.auth_failures += 1
            return False
        except Exception as e:
            logger.error(f"❌ Unexpected error during CRIO authentication: {e}")
            self.auth_failures += 1
            return False

    def get_valid_tokens(self) -> Optional[Dict[str, str]]:
        """
        Get valid authentication tokens, refreshing if necessary

        Returns:
            Dict with session_id and csrf_token, or None if authentication fails
        """

        # Check if we've exceeded max auth failures
        if self.auth_failures >= self.max_auth_failures:
            logger.error(f"❌ Max authentication failures reached ({self.max_auth_failures})")
            logger.error("   Check CRIO credentials and service availability")
            return None

        # Check if we have tokens
        if not self.session_id or not self.csrf_token:
            logger.info("🔄 No tokens available, authenticating...")
            if not self._authenticate():
                return None

        # Check if tokens are expired
        elif self.is_token_expired():
            logger.info("🔄 Tokens expired, re-authenticating...")
            if not self._authenticate():
                return None

        return {
            'session_id': self.session_id,
            'csrf_token': self.csrf_token
        }

    def is_token_expired(self) -> bool:
        """Check if current tokens are expired"""
        if not self.authenticated_at:
            return True

        age = datetime.now() - self.authenticated_at
        max_age = timedelta(hours=self.token_expiry_hours)

        return age > max_age

    def get_token_expiry(self) -> Optional[datetime]:
        """Get the expiry time of current tokens"""
        if not self.authenticated_at:
            return None

        return self.authenticated_at + timedelta(hours=8)  # CRIO tokens valid for 8 hours

    def get_token_age_minutes(self) -> Optional[int]:
        """Get age of tokens in minutes"""
        if not self.authenticated_at:
            return None

        age = datetime.now() - self.authenticated_at
        return int(age.total_seconds() / 60)

    def force_refresh(self) -> bool:
        """Force token refresh (useful for testing or after errors)"""
        logger.info("🔄 Forcing token refresh...")
        return self._authenticate()

    def reset_failures(self):
        """Reset authentication failure counter (for manual recovery)"""
        self.auth_failures = 0
        logger.info("🔄 Authentication failure counter reset")

    def get_status(self) -> Dict:
        """Get current authentication status for monitoring"""
        is_authenticated = bool(self.session_id and self.csrf_token)
        is_expired = self.is_token_expired()
        token_age = self.get_token_age_minutes()

        status = {
            'service': 'CRIO Backend Session Manager',
            'is_authenticated': is_authenticated,
            'is_expired': is_expired,
            'status': 'healthy' if (is_authenticated and not is_expired) else 'unhealthy',
            'token_age_minutes': token_age,
            'authenticated_at': self.authenticated_at.isoformat() if self.authenticated_at else None,
            'expires_at': self.get_token_expiry().isoformat() if self.get_token_expiry() else None,
            'auth_failures': self.auth_failures,
            'max_failures': self.max_auth_failures,
            'time_until_expiry': self._format_time_until_expiry(),
            'credentials_configured': bool(os.getenv('CRIO_USERNAME') and os.getenv('CRIO_PASSWORD'))
        }

        # Add warning messages
        warnings = []
        if not status['credentials_configured']:
            warnings.append('CRIO credentials not configured in environment')
        if is_expired:
            warnings.append('Tokens expired - will refresh on next use')
        if self.auth_failures > 0:
            warnings.append(f'Recent authentication failures: {self.auth_failures}')
        if self.auth_failures >= self.max_auth_failures:
            warnings.append('Max failures reached - manual intervention required')

        status['warnings'] = warnings

        return status

    def _format_time_until_expiry(self) -> Optional[str]:
        """Format time remaining until token expiry"""
        if not self.authenticated_at:
            return None

        expiry = self.get_token_expiry()
        if not expiry:
            return None

        remaining = expiry - datetime.now()

        if remaining.total_seconds() < 0:
            return "expired"

        hours = int(remaining.total_seconds() // 3600)
        minutes = int((remaining.total_seconds() % 3600) // 60)

        if hours > 0:
            return f"{hours}h {minutes}m"
        else:
            return f"{minutes}m"


# Singleton instance - will be initialized on first import
crio_session_manager = CRIOSessionManager()
