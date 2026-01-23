# Changelog

All notable changes to this project will be documented in this file.

## [1.3] - 2026-01-23

### Added

- **Admin Console**: New comprehensive admin interface for managing the application
  - Settings management (authentication, API configuration)
  - User management (create, edit, promote/demote admins)
  - API key management (create, enable/disable, delete)
  - Global job view for administrators
  - Dashboard with system statistics

- **Multi-Method Authentication**: Support for multiple authentication methods
  - Local authentication (email/password)
  - HTTP header authentication (e.g., Cloudflare Access, Authelia)
  - OpenID Connect (OIDC) authentication (e.g., Microsoft Entra ID, Okta)
  - Configurable default authentication method

- **Setup Wizard**: First-run setup wizard for initial admin account creation

- **Internationalization (i18n)**: Multi-language support
  - English (default)
  - German
  - Automatic language detection based on browser settings

- **Theme Support**: Light/Dark/Auto theme toggle
  - Persisted user preference
  - System theme detection for auto mode

- **API Key System**: New API key management replacing simple LOCAL_API_KEY
  - Create multiple API keys with descriptive names
  - Enable/disable keys without deletion
  - Usage tracking (last used, usage count)
  - Secure key hashing with bcrypt

- **User Preferences**: Per-user settings
  - Theme preference
  - Language preference

- **Version Display**: Application version shown in footer (v1.3)

- **Favicon**: New microphone icon favicon for the application

### Changed

- **Port Change**: Default port changed from 5000 to 5001
- **Configuration**: Most settings now configurable via Admin Console instead of environment variables
  - Only `OPENAI_API_KEY` remains as required environment variable
  - All other settings stored in database
- **Database Models**: Extended models for Settings, User, Session, ApiKey
- **UI Modernization**:
  - Removed header to maximize content space
  - New navbar with user info and controls
  - Improved responsive design for mobile devices
  - Modern card-based layout

### Deprecated

- `LOCAL_API_KEY` environment variable (use Admin Console API keys instead)
- `CF_ACCESS_ENFORCE` environment variable (use Admin Console authentication settings instead)

### Security

- Password hashing with bcrypt for local authentication
- Session tokens with secure hashing
- API keys stored as hashes, never in plaintext

### Technical

- Modular code structure with blueprints (auth, admin)
- Separated models, config, and version into dedicated files
- Added authlib and bcrypt dependencies for authentication

## [1.2] - Previous Version

- Background jobs with progress tracking
- Jobs and History views
- Database persistence with SQLite
- Cloudflare Access support
- YouTube/Twitch URL ingestion
- OpenAI Cloud API support
