"""
Configuration management for Whisper API STT application.
Settings are stored in the database and cached in memory.
"""
import json
import os
from datetime import datetime
from typing import Any, Optional

from models import Setting, SessionLocal, init_db

# Default settings to initialize on first run
DEFAULT_SETTINGS = {
    # Authentication settings
    "auth_enabled": {"value": "false", "type": "boolean", "category": "auth"},
    "auth_default_method": {"value": "local", "type": "string", "category": "auth"},
    "auth_http_header_name": {"value": "CF-Access-Authenticated-User-Email", "type": "string", "category": "auth"},
    "auth_http_header_enabled": {"value": "false", "type": "boolean", "category": "auth"},
    "auth_oidc_enabled": {"value": "false", "type": "boolean", "category": "auth"},
    "auth_oidc_client_id": {"value": "", "type": "string", "category": "auth"},
    "auth_oidc_client_secret": {"value": "", "type": "string", "category": "auth"},
    "auth_oidc_discovery_url": {"value": "", "type": "string", "category": "auth"},
    "auth_oidc_redirect_uri": {"value": "", "type": "string", "category": "auth"},

    # API settings
    "api_key_required": {"value": "false", "type": "boolean", "category": "api"},
    "api_default_model": {"value": "base", "type": "string", "category": "api"},
    "max_cloud_file_mb": {"value": "20", "type": "integer", "category": "api"},
    "chunk_duration_seconds": {"value": "600", "type": "integer", "category": "api"},
    "reencode_bitrate": {"value": "64k", "type": "string", "category": "api"},
    "job_timeout_seconds": {"value": "43200", "type": "integer", "category": "api"},

    # System settings
    "setup_completed": {"value": "false", "type": "boolean", "category": "system"},
}


class SettingsManager:
    """Manages application settings stored in the database."""

    _cache: dict = {}
    _initialized: bool = False

    @classmethod
    def initialize(cls):
        """Initialize settings with defaults if not already present."""
        if cls._initialized:
            return

        session = SessionLocal()
        try:
            for key, config in DEFAULT_SETTINGS.items():
                existing = session.get(Setting, key)
                if not existing:
                    setting = Setting(
                        key=key,
                        value=config["value"],
                        value_type=config["type"],
                        category=config["category"],
                        updated_at=datetime.utcnow()
                    )
                    session.add(setting)
            session.commit()
            cls._refresh_cache(session)
            cls._initialized = True
        finally:
            session.close()

    @classmethod
    def _refresh_cache(cls, session=None):
        """Refresh the in-memory cache from database."""
        close_session = False
        if session is None:
            session = SessionLocal()
            close_session = True

        try:
            settings = session.query(Setting).all()
            cls._cache = {s.key: s for s in settings}
        finally:
            if close_session:
                session.close()

    @classmethod
    def _convert_value(cls, setting: Setting) -> Any:
        """Convert setting value to appropriate Python type."""
        if setting is None or setting.value is None:
            return None

        value_type = setting.value_type or "string"
        value = setting.value

        if value_type == "boolean":
            return value.lower() in ("true", "1", "yes", "on")
        elif value_type == "integer":
            try:
                return int(value)
            except (ValueError, TypeError):
                return 0
        elif value_type == "json":
            try:
                return json.loads(value)
            except (json.JSONDecodeError, TypeError):
                return None
        else:
            return value

    @classmethod
    def get(cls, key: str, default: Any = None) -> Any:
        """Get a setting value by key."""
        if not cls._initialized:
            cls.initialize()

        setting = cls._cache.get(key)
        if setting is None:
            return default

        return cls._convert_value(setting)

    @classmethod
    def get_raw(cls, key: str) -> Optional[Setting]:
        """Get the raw Setting object by key."""
        if not cls._initialized:
            cls.initialize()
        return cls._cache.get(key)

    @classmethod
    def set(cls, key: str, value: Any, updated_by: str = None) -> bool:
        """Set a setting value."""
        if not cls._initialized:
            cls.initialize()

        session = SessionLocal()
        try:
            setting = session.get(Setting, key)

            # Convert value to string for storage
            if isinstance(value, bool):
                str_value = "true" if value else "false"
                value_type = "boolean"
            elif isinstance(value, int):
                str_value = str(value)
                value_type = "integer"
            elif isinstance(value, (dict, list)):
                str_value = json.dumps(value)
                value_type = "json"
            else:
                str_value = str(value) if value is not None else ""
                value_type = "string"

            if setting:
                setting.value = str_value
                setting.value_type = value_type
                setting.updated_at = datetime.utcnow()
                setting.updated_by = updated_by
            else:
                # Create new setting
                setting = Setting(
                    key=key,
                    value=str_value,
                    value_type=value_type,
                    updated_at=datetime.utcnow(),
                    updated_by=updated_by
                )
                session.add(setting)

            session.commit()
            cls._cache[key] = setting
            return True
        except Exception:
            session.rollback()
            return False
        finally:
            session.close()

    @classmethod
    def get_all(cls, category: str = None) -> dict:
        """Get all settings, optionally filtered by category."""
        if not cls._initialized:
            cls.initialize()

        result = {}
        for key, setting in cls._cache.items():
            if category is None or setting.category == category:
                result[key] = cls._convert_value(setting)
        return result

    @classmethod
    def get_all_raw(cls, category: str = None) -> list:
        """Get all settings as raw Setting objects."""
        if not cls._initialized:
            cls.initialize()

        result = []
        for key, setting in cls._cache.items():
            if category is None or setting.category == category:
                result.append({
                    "key": setting.key,
                    "value": cls._convert_value(setting),
                    "value_type": setting.value_type,
                    "category": setting.category,
                    "updated_at": setting.updated_at.isoformat() + "Z" if setting.updated_at else None,
                    "updated_by": setting.updated_by
                })
        return result


# Convenience functions
def get_setting(key: str, default: Any = None) -> Any:
    """Get a setting value."""
    return SettingsManager.get(key, default)


def set_setting(key: str, value: Any, updated_by: str = None) -> bool:
    """Set a setting value."""
    return SettingsManager.set(key, value, updated_by)


def is_auth_enabled() -> bool:
    """Check if authentication is enabled."""
    return SettingsManager.get("auth_enabled", False)


def is_setup_completed() -> bool:
    """Check if initial setup has been completed."""
    return SettingsManager.get("setup_completed", False)


def get_auth_config() -> dict:
    """Get all authentication-related settings."""
    return SettingsManager.get_all("auth")


def get_api_config() -> dict:
    """Get all API-related settings."""
    return SettingsManager.get_all("api")
