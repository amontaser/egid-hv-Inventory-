"""Authentication routes for Hyper-V Inventory"""

from flask import Blueprint, request, redirect, session, url_for, render_template
import os

auth_bp = Blueprint('auth', __name__)

# Simple password authentication - in production, use proper auth system
ADMIN_PASSWORD = os.getenv('ADMIN_PASSWORD', 'admin123')

@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    """Handle user login."""
    if request.method == 'POST':
        password = request.form.get('password')
        if password == ADMIN_PASSWORD:
            session['logged_in'] = True
            return redirect('/')  # Redirect to root instead of vms.dashboard
        else:
            return render_template('login.html', error='Invalid password')
    return render_template('login.html')

@auth_bp.route('/logout')
def logout():
    """Handle user logout."""
    session.clear()
    return redirect(url_for('auth.login'))
