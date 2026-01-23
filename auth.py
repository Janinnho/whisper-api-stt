"""
Authentication blueprint for Whisper API STT application.
Supports local authentication, HTTP header authentication, and OIDC.
"""
import secrets
import json
from datetime import datetime, timedelta
from functools import wraps
from typing import Optional

import bcrypt
from flask import Blueprint, request, redirect, url_for, render_template, jsonify, g, make_response

from models import User, Session, ApiKey, SessionLocal, generate_uuid
from config import get_setting, set_setting, is_auth_enabled

auth_bp = Blueprint('auth', __name__)

# Session configuration
SESSION_COOKIE_NAME = "whisper_session"
SESSION_DURATION_DAYS = 7


def hash_password(password: str) -> str:
    """Hash a password using bcrypt."""
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')


def verify_password(password: str, password_hash: str) -> bool:
    """Verify a password against its hash."""
    try:
        return bcrypt.checkpw(password.encode('utf-8'), password_hash.encode('utf-8'))
    except Exception:
        return False


def generate_session_token() -> str:
    """Generate a secure session token."""
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """Hash a session token for storage."""
    return bcrypt.hashpw(token.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')


def verify_token(token: str, token_hash: str) -> bool:
    """Verify a session token against its hash."""
    try:
        return bcrypt.checkpw(token.encode('utf-8'), token_hash.encode('utf-8'))
    except Exception:
        return False


def generate_api_key() -> str:
    """Generate a new API key with prefix."""
    key = secrets.token_urlsafe(32)
    return f"wsk_{key}"


def get_api_key_prefix(key: str) -> str:
    """Extract prefix from API key for display."""
    return key[:12] if len(key) >= 12 else key


def create_session(user: User, ip_address: str = None, user_agent: str = None) -> tuple:
    """Create a new session for a user. Returns (session_id, token)."""
    token = generate_session_token()
    session_id = generate_uuid()

    db_session = SessionLocal()
    try:
        session = Session(
            id=session_id,
            user_id=user.id,
            token_hash=hash_token(token),
            created_at=datetime.utcnow(),
            expires_at=datetime.utcnow() + timedelta(days=SESSION_DURATION_DAYS),
            ip_address=ip_address,
            user_agent=user_agent
        )
        db_session.add(session)
        db_session.commit()
        return session_id, token
    finally:
        db_session.close()


def validate_session(session_id: str, token: str) -> Optional[User]:
    """Validate a session and return the user if valid."""
    if not session_id or not token:
        return None

    db_session = SessionLocal()
    try:
        session = db_session.get(Session, session_id)
        if not session:
            return None

        # Check expiration
        if session.expires_at < datetime.utcnow():
            db_session.delete(session)
            db_session.commit()
            return None

        # Verify token
        if not verify_token(token, session.token_hash):
            return None

        # Get user
        user = db_session.query(User).filter(User.id == session.user_id).first()
        if not user or not user.is_active:
            return None

        # Update last seen
        user.last_seen = datetime.utcnow()
        db_session.commit()

        return user
    finally:
        db_session.close()


def delete_session(session_id: str):
    """Delete a session."""
    db_session = SessionLocal()
    try:
        session = db_session.get(Session, session_id)
        if session:
            db_session.delete(session)
            db_session.commit()
    finally:
        db_session.close()


def get_user_by_email(email: str) -> Optional[User]:
    """Get a user by email."""
    db_session = SessionLocal()
    try:
        return db_session.query(User).filter(User.email == email).first()
    finally:
        db_session.close()


def get_user_by_id(user_id: str) -> Optional[User]:
    """Get a user by ID."""
    db_session = SessionLocal()
    try:
        return db_session.get(User, user_id)
    finally:
        db_session.close()


def create_user(email: str, password: str = None, name: str = None, source: str = "local", is_admin: bool = False) -> User:
    """Create a new user."""
    db_session = SessionLocal()
    try:
        user = User(
            id=generate_uuid(),
            email=email,
            name=name or email.split('@')[0],
            password_hash=hash_password(password) if password else None,
            source=source,
            is_admin=is_admin,
            is_active=True,
            first_seen=datetime.utcnow(),
            last_seen=datetime.utcnow(),
            created_at=datetime.utcnow()
        )
        db_session.add(user)
        db_session.commit()
        db_session.refresh(user)
        return user
    finally:
        db_session.close()


def validate_api_key(key: str) -> Optional[ApiKey]:
    """Validate an API key and return the ApiKey record if valid."""
    if not key:
        return None

    # Handle Bearer prefix
    if key.startswith("Bearer "):
        key = key[7:]

    db_session = SessionLocal()
    try:
        # Get potential matches by prefix
        prefix = get_api_key_prefix(key)
        candidates = db_session.query(ApiKey).filter(
            ApiKey.key_prefix == prefix,
            ApiKey.is_enabled == True
        ).all()

        for candidate in candidates:
            if verify_token(key, candidate.key_hash):
                # Check expiration
                if candidate.expires_at and candidate.expires_at < datetime.utcnow():
                    continue

                # Update usage
                candidate.last_used_at = datetime.utcnow()
                candidate.usage_count = (candidate.usage_count or 0) + 1
                db_session.commit()
                return candidate

        return None
    finally:
        db_session.close()


def login_required(f):
    """Decorator to require authentication."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not is_auth_enabled():
            return f(*args, **kwargs)

        if not g.get('user'):
            if request.is_json or request.path.startswith('/api/') or request.path.startswith('/v1/'):
                return jsonify({'error': 'Authentication required'}), 401
            return redirect(url_for('auth.login_page'))

        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    """Decorator to require admin access."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not is_auth_enabled():
            # When auth is disabled, allow access but check if user would be admin
            return f(*args, **kwargs)

        user = g.get('user')
        if not user or not user.is_admin:
            if request.is_json or request.path.startswith('/api/') or request.path.startswith('/v1/'):
                return jsonify({'error': 'Admin access required'}), 403
            return redirect(url_for('index'))

        return f(*args, **kwargs)
    return decorated


def init_auth_middleware(app):
    """Initialize authentication middleware for the Flask app."""

    @app.before_request
    def auth_middleware():
        """Process authentication for each request."""
        g.user = None
        g.user_email = None
        g.is_admin = False

        path = request.path or ""

        # Skip auth for static files
        if path.startswith('/static/'):
            return None

        # Check if auth is enabled
        if not is_auth_enabled():
            # Auth disabled - allow anonymous access
            return None

        # Check for setup wizard redirect
        if not get_setting('setup_completed', False):
            if not path.startswith('/admin/setup') and not path.startswith('/static/'):
                return redirect(url_for('admin.setup_page'))

        # Skip auth for login pages
        if path in ['/login-access', '/auth/login', '/auth/oidc/authorize', '/auth/oidc/callback']:
            return None

        # API endpoint - check API key if required
        if path.startswith('/v1/audio/transcriptions'):
            if get_setting('api_key_required', False):
                auth_header = request.headers.get('Authorization', '')
                api_key = validate_api_key(auth_header)
                if not api_key:
                    return jsonify({'error': 'Invalid API key'}), 401
            return None

        # Try session authentication
        session_id = request.cookies.get(SESSION_COOKIE_NAME + '_id')
        session_token = request.cookies.get(SESSION_COOKIE_NAME + '_token')

        if session_id and session_token:
            user = validate_session(session_id, session_token)
            if user:
                g.user = user
                g.user_email = user.email
                g.is_admin = user.is_admin
                return None

        # Try HTTP header authentication
        if get_setting('auth_http_header_enabled', False):
            header_name = get_setting('auth_http_header_name', 'CF-Access-Authenticated-User-Email')
            header_value = request.headers.get(header_name)
            if header_value:
                user = get_or_create_header_user(header_value)
                if user:
                    g.user = user
                    g.user_email = user.email
                    g.is_admin = user.is_admin
                    return None

        # Not authenticated - redirect based on default method
        default_method = get_setting('auth_default_method', 'local')

        if default_method == 'oidc' and get_setting('auth_oidc_enabled', False):
            return redirect(url_for('auth.oidc_authorize'))
        else:
            return redirect(url_for('auth.login_page'))


def get_or_create_header_user(email: str) -> Optional[User]:
    """Get or create a user from HTTP header authentication."""
    db_session = SessionLocal()
    try:
        user = db_session.query(User).filter(User.email == email).first()
        now = datetime.utcnow()

        if not user:
            # Create new user
            user = User(
                id=generate_uuid(),
                email=email,
                name=email.split('@')[0],
                source='http-header',
                is_admin=False,
                is_active=True,
                first_seen=now,
                last_seen=now,
                created_at=now
            )
            db_session.add(user)
            db_session.flush()

            # If no admin exists, make this one admin
            any_admin = db_session.query(User).filter(User.is_admin == True).first()
            if not any_admin:
                user.is_admin = True

            db_session.commit()
        else:
            user.last_seen = now
            db_session.commit()

        return user
    finally:
        db_session.close()


# Routes
@auth_bp.route('/login-access')
def login_page():
    """Render login page with all available authentication methods."""
    methods = []

    # Local authentication is always available when auth is enabled
    if is_auth_enabled():
        methods.append({'type': 'local', 'name': 'Email & Password'})

    # HTTP header auth
    if get_setting('auth_http_header_enabled', False):
        methods.append({'type': 'http-header', 'name': 'SSO (HTTP Header)'})

    # OIDC
    if get_setting('auth_oidc_enabled', False):
        methods.append({'type': 'oidc', 'name': 'Single Sign-On (OIDC)'})

    return render_template('login.html', methods=methods, error=request.args.get('error'))


@auth_bp.route('/auth/login', methods=['POST'])
def login():
    """Handle local login."""
    data = request.get_json() if request.is_json else request.form

    email = (data.get('email') or '').strip().lower()
    password = data.get('password') or ''

    if not email or not password:
        if request.is_json:
            return jsonify({'error': 'Email and password required'}), 400
        return redirect(url_for('auth.login_page', error='Email and password required'))

    db_session = SessionLocal()
    try:
        user = db_session.query(User).filter(User.email == email).first()

        if not user or not user.password_hash:
            if request.is_json:
                return jsonify({'error': 'Invalid credentials'}), 401
            return redirect(url_for('auth.login_page', error='Invalid credentials'))

        if not verify_password(password, user.password_hash):
            if request.is_json:
                return jsonify({'error': 'Invalid credentials'}), 401
            return redirect(url_for('auth.login_page', error='Invalid credentials'))

        if not user.is_active:
            if request.is_json:
                return jsonify({'error': 'Account is disabled'}), 401
            return redirect(url_for('auth.login_page', error='Account is disabled'))

        # Create session
        session_id, token = create_session(
            user,
            ip_address=request.remote_addr,
            user_agent=request.user_agent.string if request.user_agent else None
        )

        if request.is_json:
            response = jsonify({'success': True, 'user': {'email': user.email, 'name': user.name}})
        else:
            response = make_response(redirect(url_for('index')))

        # Set session cookies
        response.set_cookie(
            SESSION_COOKIE_NAME + '_id',
            session_id,
            httponly=True,
            samesite='Lax',
            max_age=SESSION_DURATION_DAYS * 24 * 3600
        )
        response.set_cookie(
            SESSION_COOKIE_NAME + '_token',
            token,
            httponly=True,
            samesite='Lax',
            max_age=SESSION_DURATION_DAYS * 24 * 3600
        )

        return response
    finally:
        db_session.close()


@auth_bp.route('/auth/logout', methods=['POST', 'GET'])
def logout():
    """Handle logout."""
    session_id = request.cookies.get(SESSION_COOKIE_NAME + '_id')
    if session_id:
        delete_session(session_id)

    if request.is_json:
        response = jsonify({'success': True})
    else:
        response = make_response(redirect(url_for('auth.login_page')))

    response.delete_cookie(SESSION_COOKIE_NAME + '_id')
    response.delete_cookie(SESSION_COOKIE_NAME + '_token')

    return response


@auth_bp.route('/auth/oidc/authorize')
def oidc_authorize():
    """Start OIDC authorization flow."""
    if not get_setting('auth_oidc_enabled', False):
        return redirect(url_for('auth.login_page', error='OIDC not enabled'))

    # This would integrate with authlib for actual OIDC flow
    # For now, redirect to login with error
    return redirect(url_for('auth.login_page', error='OIDC configuration required'))


@auth_bp.route('/auth/oidc/callback')
def oidc_callback():
    """Handle OIDC callback."""
    if not get_setting('auth_oidc_enabled', False):
        return redirect(url_for('auth.login_page', error='OIDC not enabled'))

    # This would handle the OIDC callback with authlib
    # For now, redirect to login with error
    return redirect(url_for('auth.login_page', error='OIDC callback not implemented'))


@auth_bp.route('/api/me')
@login_required
def get_current_user():
    """Get current user info."""
    user = g.user
    if not user:
        return jsonify({'error': 'Not authenticated'}), 401

    preferences = {}
    if user.preferences:
        try:
            preferences = json.loads(user.preferences)
        except Exception:
            pass

    return jsonify({
        'id': user.id,
        'email': user.email,
        'name': user.name,
        'is_admin': user.is_admin,
        'source': user.source,
        'preferences': preferences
    })


@auth_bp.route('/api/me/preferences', methods=['PUT'])
@login_required
def update_preferences():
    """Update current user preferences."""
    user = g.user
    if not user:
        return jsonify({'error': 'Not authenticated'}), 401

    data = request.get_json()
    if not data:
        return jsonify({'error': 'No data provided'}), 400

    db_session = SessionLocal()
    try:
        db_user = db_session.get(User, user.id)
        if not db_user:
            return jsonify({'error': 'User not found'}), 404

        # Get existing preferences
        preferences = {}
        if db_user.preferences:
            try:
                preferences = json.loads(db_user.preferences)
            except Exception:
                pass

        # Update preferences
        if 'theme' in data:
            preferences['theme'] = data['theme']
        if 'language' in data:
            preferences['language'] = data['language']

        db_user.preferences = json.dumps(preferences)
        db_user.updated_at = datetime.utcnow()
        db_session.commit()

        return jsonify({'success': True, 'preferences': preferences})
    finally:
        db_session.close()


@auth_bp.route('/api/me/password', methods=['PUT'])
@login_required
def change_password():
    """Change current user password (local users only)."""
    user = g.user
    if not user:
        return jsonify({'error': 'Not authenticated'}), 401

    if user.source != 'local':
        return jsonify({'error': 'Password change not available for this account type'}), 400

    data = request.get_json()
    current_password = data.get('current_password')
    new_password = data.get('new_password')

    if not current_password or not new_password:
        return jsonify({'error': 'Current and new password required'}), 400

    if len(new_password) < 8:
        return jsonify({'error': 'Password must be at least 8 characters'}), 400

    db_session = SessionLocal()
    try:
        db_user = db_session.get(User, user.id)
        if not db_user:
            return jsonify({'error': 'User not found'}), 404

        if not verify_password(current_password, db_user.password_hash):
            return jsonify({'error': 'Current password is incorrect'}), 401

        db_user.password_hash = hash_password(new_password)
        db_user.updated_at = datetime.utcnow()
        db_session.commit()

        return jsonify({'success': True})
    finally:
        db_session.close()
