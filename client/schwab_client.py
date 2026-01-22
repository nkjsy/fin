"""
Auto-Refresh Schwab Client

Wrapper around Schwab Client that proactively refreshes the access token
before the 30-minute expiration to ensure uninterrupted operation.
"""

import time
from schwab.client import Client
from schwab.auth import easy_client

from client import config
from logger import get_logger


logger = get_logger("CLIENT")


class AutoRefreshSchwabClient:
    """
    Wrapper around Schwab Client that proactively refreshes the access token.
    
    The Schwab access token expires every 30 minutes. While schwab-py handles
    automatic refresh, it can sometimes fail. This wrapper proactively recreates
    the client before expiration to ensure uninterrupted operation.
    
    Usage:
        wrapper = AutoRefreshSchwabClient()
        client = wrapper.client  # Use this for API calls
        
        # In your main loop, periodically call:
        wrapper.ensure_fresh()
    """
    
    # Refresh 5 minutes before expiration (25 minutes)
    ACCESS_TOKEN_REFRESH_SECONDS = 25 * 60
    
    # 5.5 days for refresh token (proactive weekly refresh)
    REFRESH_TOKEN_MAX_AGE_SECONDS = 5.5 * 24 * 60 * 60
    
    def __init__(self):
        """Initialize with a fresh client."""
        self._client = None
        self._client_created_at = None
        self._create_client()
    
    @property
    def client(self) -> Client:
        """Get the current client, refreshing if needed."""
        self.ensure_fresh()
        return self._client
    
    def _create_client(self):
        """Create a new authenticated Schwab client."""
        logger.info("Authenticating with Schwab...")
        self._client = easy_client(
            api_key=config.SCHWAB_API_KEY,
            app_secret=config.SCHWAB_APP_SECRET,
            callback_url=config.SCHWAB_CALLBACK_URL,
            token_path=config.SCHWAB_TOKEN_PATH,
            max_token_age=self.REFRESH_TOKEN_MAX_AGE_SECONDS
        )
        self._client_created_at = time.time()
        logger.info("Authentication successful")
    
    def ensure_fresh(self):
        """
        Ensure the client has a fresh access token.
        
        Call this periodically (e.g., every polling cycle) to proactively
        refresh the client before the 30-minute access token expires.
        """
        if self._client is None or self._client_created_at is None:
            self._create_client()
            return
        
        elapsed = time.time() - self._client_created_at
        if elapsed >= self.ACCESS_TOKEN_REFRESH_SECONDS:
            logger.info(f"Access token age: {elapsed/60:.1f}min, refreshing...")
            self._create_client()
    
    def force_refresh(self):
        """Force an immediate client refresh."""
        logger.info("Forcing client refresh...")
        self._create_client()
