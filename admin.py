"""
Admin blueprint for Whisper API STT application.
Handles admin console, settings, user management, and API key management.
"""
import json
import secrets
from datetime import datetime
from typing import Optional

from flask import Blueprint, request, redirect, url_for, render_template, jsonify, g

from models import User, ApiKey, TranscriptionJob, SessionLocal, generate_uuid
from config import (
    get_setting, set_setting, SettingsManager, is_auth_enabled, is_setup_completed,
    get_cloud_provider_config, is_cloud_provider_configured, get_cloud_provider_label
)
from auth import (
    admin_required, login_required, hash_password, verify_password,
    generate_api_key, get_api_key_prefix, hash_token, create_user
)
from version import VERSION

admin_bp = Blueprint('admin', __name__, url_prefix='/admin')


# Setup wizard routes (accessible without auth when setup not completed)
@admin_bp.route('/setup')
def setup_page():
    """Render setup wizard page."""
    if is_setup_completed():
        return redirect(url_for('index'))
    return render_template('setup.html', version=VERSION)


@admin_bp.route('/setup', methods=['POST'])
def setup_complete():
    """Complete initial setup."""
    if is_setup_completed():
        return jsonify({'error': 'Setup already completed'}), 400

    data = request.get_json() if request.is_json else request.form

    email = (data.get('email') or '').strip().lower()
    password = data.get('password') or ''
    name = (data.get('name') or '').strip()

    if not email:
        return jsonify({'error': 'Email is required'}), 400

    if not password or len(password) < 8:
        return jsonify({'error': 'Password must be at least 8 characters'}), 400

    db_session = SessionLocal()
    try:
        # Check if user already exists
        existing = db_session.query(User).filter(User.email == email).first()
        if existing:
            return jsonify({'error': 'User with this email already exists'}), 400

        # Create admin user
        user = User(
            id=generate_uuid(),
            email=email,
            name=name or email.split('@')[0],
            password_hash=hash_password(password),
            source='local',
            is_admin=True,
            is_active=True,
            first_seen=datetime.utcnow(),
            last_seen=datetime.utcnow(),
            created_at=datetime.utcnow()
        )
        db_session.add(user)
        db_session.commit()

        # Mark setup as completed and enable auth
        set_setting('setup_completed', True, email)
        set_setting('auth_enabled', True, email)

        if request.is_json:
            return jsonify({'success': True, 'redirect': url_for('auth.login_page')})
        return redirect(url_for('auth.login_page'))
    except Exception as e:
        db_session.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        db_session.close()


# Admin dashboard
@admin_bp.route('/')
@admin_required
def dashboard():
    """Render admin dashboard."""
    return render_template('admin.html', version=VERSION, user=g.user)


# Settings management
@admin_bp.route('/settings')
@admin_required
def get_settings():
    """Get all settings."""
    settings = SettingsManager.get_all_raw()
    return jsonify(settings)


@admin_bp.route('/settings', methods=['PUT'])
@admin_required
def update_settings():
    """Update settings."""
    data = request.get_json()
    if not data:
        return jsonify({'error': 'No data provided'}), 400

    user_email = g.user.email if g.user else None

    updated = []
    for key, value in data.items():
        if set_setting(key, value, user_email):
            updated.append(key)

    return jsonify({'success': True, 'updated': updated})


# API Key management
@admin_bp.route('/api-keys')
@admin_required
def list_api_keys():
    """List all API keys."""
    db_session = SessionLocal()
    try:
        keys = db_session.query(ApiKey).order_by(ApiKey.created_at.desc()).all()
        result = []
        for k in keys:
            result.append({
                'id': k.id,
                'name': k.name,
                'key_prefix': k.key_prefix,
                'is_enabled': k.is_enabled,
                'created_at': k.created_at.isoformat() + 'Z' if k.created_at else None,
                'created_by': k.created_by,
                'last_used_at': k.last_used_at.isoformat() + 'Z' if k.last_used_at else None,
                'expires_at': k.expires_at.isoformat() + 'Z' if k.expires_at else None,
                'usage_count': k.usage_count or 0
            })
        return jsonify(result)
    finally:
        db_session.close()


@admin_bp.route('/api-keys', methods=['POST'])
@admin_required
def create_api_key():
    """Create a new API key."""
    data = request.get_json()
    name = (data.get('name') or '').strip()

    if not name:
        return jsonify({'error': 'Name is required'}), 400

    # Generate the actual key
    raw_key = generate_api_key()
    key_prefix = get_api_key_prefix(raw_key)

    db_session = SessionLocal()
    try:
        api_key = ApiKey(
            id=generate_uuid(),
            name=name,
            key_hash=hash_token(raw_key),
            key_prefix=key_prefix,
            is_enabled=True,
            created_at=datetime.utcnow(),
            created_by=g.user.email if g.user else None,
            usage_count=0
        )
        db_session.add(api_key)
        db_session.commit()

        # Return the full key only once - it cannot be retrieved later
        return jsonify({
            'success': True,
            'id': api_key.id,
            'name': api_key.name,
            'key': raw_key,  # Only shown once!
            'key_prefix': key_prefix
        })
    finally:
        db_session.close()


@admin_bp.route('/api-keys/<key_id>', methods=['PUT'])
@admin_required
def update_api_key(key_id):
    """Update an API key (enable/disable)."""
    data = request.get_json()

    db_session = SessionLocal()
    try:
        api_key = db_session.get(ApiKey, key_id)
        if not api_key:
            return jsonify({'error': 'API key not found'}), 404

        if 'is_enabled' in data:
            api_key.is_enabled = bool(data['is_enabled'])

        if 'name' in data:
            api_key.name = data['name']

        db_session.commit()
        return jsonify({'success': True})
    finally:
        db_session.close()


@admin_bp.route('/api-keys/<key_id>', methods=['DELETE'])
@admin_required
def delete_api_key(key_id):
    """Delete an API key."""
    db_session = SessionLocal()
    try:
        api_key = db_session.get(ApiKey, key_id)
        if not api_key:
            return jsonify({'error': 'API key not found'}), 404

        db_session.delete(api_key)
        db_session.commit()
        return jsonify({'success': True})
    finally:
        db_session.close()


# User management
@admin_bp.route('/users')
@admin_required
def list_users():
    """List all users."""
    db_session = SessionLocal()
    try:
        users = db_session.query(User).order_by(User.created_at.asc()).all()
        result = []
        for u in users:
            result.append({
                'id': u.id,
                'email': u.email,
                'name': u.name,
                'source': u.source,
                'is_admin': u.is_admin,
                'is_active': u.is_active,
                'first_seen': u.first_seen.isoformat() + 'Z' if u.first_seen else None,
                'last_seen': u.last_seen.isoformat() + 'Z' if u.last_seen else None,
                'created_at': u.created_at.isoformat() + 'Z' if u.created_at else None
            })
        return jsonify(result)
    finally:
        db_session.close()


@admin_bp.route('/users', methods=['POST'])
@admin_required
def create_user_admin():
    """Create a new local user."""
    data = request.get_json()

    email = (data.get('email') or '').strip().lower()
    password = data.get('password') or ''
    name = (data.get('name') or '').strip()
    is_admin = bool(data.get('is_admin', False))

    if not email:
        return jsonify({'error': 'Email is required'}), 400

    if not password or len(password) < 8:
        return jsonify({'error': 'Password must be at least 8 characters'}), 400

    db_session = SessionLocal()
    try:
        existing = db_session.query(User).filter(User.email == email).first()
        if existing:
            return jsonify({'error': 'User with this email already exists'}), 400

        user = User(
            id=generate_uuid(),
            email=email,
            name=name or email.split('@')[0],
            password_hash=hash_password(password),
            source='local',
            is_admin=is_admin,
            is_active=True,
            first_seen=datetime.utcnow(),
            last_seen=datetime.utcnow(),
            created_at=datetime.utcnow()
        )
        db_session.add(user)
        db_session.commit()

        return jsonify({
            'success': True,
            'id': user.id,
            'email': user.email,
            'name': user.name
        })
    finally:
        db_session.close()


@admin_bp.route('/users/<user_id>', methods=['PUT'])
@admin_required
def update_user(user_id):
    """Update a user."""
    data = request.get_json()

    db_session = SessionLocal()
    try:
        user = db_session.get(User, user_id)
        if not user:
            return jsonify({'error': 'User not found'}), 404

        if 'name' in data:
            user.name = data['name']

        if 'is_active' in data:
            user.is_active = bool(data['is_active'])

        if 'password' in data and data['password']:
            if len(data['password']) < 8:
                return jsonify({'error': 'Password must be at least 8 characters'}), 400
            user.password_hash = hash_password(data['password'])

        user.updated_at = datetime.utcnow()
        db_session.commit()
        return jsonify({'success': True})
    finally:
        db_session.close()


@admin_bp.route('/users/<user_id>', methods=['DELETE'])
@admin_required
def delete_user(user_id):
    """Delete a user."""
    db_session = SessionLocal()
    try:
        user = db_session.get(User, user_id)
        if not user:
            return jsonify({'error': 'User not found'}), 404

        # Prevent deleting yourself
        if g.user and user.id == g.user.id:
            return jsonify({'error': 'Cannot delete yourself'}), 400

        # Prevent deleting the last admin
        if user.is_admin:
            admin_count = db_session.query(User).filter(User.is_admin == True).count()
            if admin_count <= 1:
                return jsonify({'error': 'Cannot delete the last admin'}), 400

        db_session.delete(user)
        db_session.commit()
        return jsonify({'success': True})
    finally:
        db_session.close()


@admin_bp.route('/users/<user_id>/promote', methods=['POST'])
@admin_required
def toggle_admin(user_id):
    """Toggle admin status for a user."""
    db_session = SessionLocal()
    try:
        user = db_session.get(User, user_id)
        if not user:
            return jsonify({'error': 'User not found'}), 404

        # Prevent demoting yourself
        if g.user and user.id == g.user.id:
            return jsonify({'error': 'Cannot change your own admin status'}), 400

        # Prevent demoting the last admin
        if user.is_admin:
            admin_count = db_session.query(User).filter(User.is_admin == True).count()
            if admin_count <= 1:
                return jsonify({'error': 'Cannot demote the last admin'}), 400

        user.is_admin = not user.is_admin
        user.updated_at = datetime.utcnow()
        db_session.commit()

        return jsonify({'success': True, 'is_admin': user.is_admin})
    finally:
        db_session.close()


# Job management (global view for admins)
@admin_bp.route('/jobs')
@admin_required
def list_all_jobs():
    """List all jobs (admin global view)."""
    status_filter = request.args.get('status')
    limit = int(request.args.get('limit', '100'))

    db_session = SessionLocal()
    try:
        q = db_session.query(TranscriptionJob)

        if status_filter:
            q = q.filter(TranscriptionJob.status == status_filter)

        q = q.order_by(TranscriptionJob.started_at.desc().nullslast())
        jobs = q.limit(limit).all()

        result = []
        for j in jobs:
            result.append({
                'id': j.id,
                'user_id': j.user_id,
                'user_email': j.user_email or 'API',
                'source_type': j.source_type,
                'source_url': j.source_url,
                'original_filename': j.original_filename,
                'method': j.method,
                'model': j.cloud_model if j.method == 'cloud' else j.local_model_size,
                'status': j.status,
                'percent': j.percent,
                'error': j.error,
                'started_at': j.started_at.isoformat() + 'Z' if j.started_at else None,
                'finished_at': j.finished_at.isoformat() + 'Z' if j.finished_at else None,
                'duration_seconds': j.duration_seconds,
                'text_len': len(j.result_text or '')
            })
        return jsonify(result)
    finally:
        db_session.close()


@admin_bp.route('/jobs/<job_id>', methods=['DELETE'])
@admin_required
def delete_job(job_id):
    """Delete a job."""
    db_session = SessionLocal()
    try:
        job = db_session.get(TranscriptionJob, job_id)
        if not job:
            return jsonify({'error': 'Job not found'}), 404

        db_session.delete(job)
        db_session.commit()
        return jsonify({'success': True})
    finally:
        db_session.close()


@admin_bp.route('/jobs/<job_id>/cancel', methods=['POST'])
@admin_required
def cancel_job_admin(job_id):
    """Cancel a job (admin version - imported from main app)."""
    # This will be handled by the main app's job cancellation logic
    # We just need to mark it in the database
    db_session = SessionLocal()
    try:
        job = db_session.get(TranscriptionJob, job_id)
        if not job:
            return jsonify({'error': 'Job not found'}), 404

        if job.status not in ('running', 'queued'):
            return jsonify({'error': 'Job is not running'}), 400

        job.status = 'cancelled'
        job.error = 'Cancelled by admin'
        job.finished_at = datetime.utcnow()
        db_session.commit()

        return jsonify({'success': True})
    finally:
        db_session.close()


# Stats endpoint
@admin_bp.route('/stats')
@admin_required
def get_stats():
    """Get system statistics."""
    db_session = SessionLocal()
    try:
        cloud_cfg = get_cloud_provider_config()
        cloud_provider = cloud_cfg.get("provider") or "openai"
        cloud_provider_label = get_cloud_provider_label(cloud_provider)
        user_count = db_session.query(User).count()
        admin_count = db_session.query(User).filter(User.is_admin == True).count()
        active_user_count = db_session.query(User).filter(User.is_active == True).count()

        job_count = db_session.query(TranscriptionJob).count()
        completed_jobs = db_session.query(TranscriptionJob).filter(TranscriptionJob.status == 'completed').count()
        error_jobs = db_session.query(TranscriptionJob).filter(TranscriptionJob.status == 'error').count()
        running_jobs = db_session.query(TranscriptionJob).filter(TranscriptionJob.status == 'running').count()

        api_key_count = db_session.query(ApiKey).count()
        active_api_keys = db_session.query(ApiKey).filter(ApiKey.is_enabled == True).count()

        return jsonify({
            'users': {
                'total': user_count,
                'admins': admin_count,
                'active': active_user_count
            },
            'jobs': {
                'total': job_count,
                'completed': completed_jobs,
                'error': error_jobs,
                'running': running_jobs
            },
            'api_keys': {
                'total': api_key_count,
                'active': active_api_keys
            },
            'system': {
                'cloud_provider': cloud_provider,
                'cloud_provider_label': cloud_provider_label,
                'cloud_provider_configured': is_cloud_provider_configured(),
                'cloud_api_enabled': get_setting('feature_cloud_api_enabled', True),
                'url_input_enabled': get_setting('feature_url_input_enabled', True),
                'auth_enabled': is_auth_enabled()
            },
            'version': VERSION
        })
    finally:
        db_session.close()
