"""
Authentication and Authorization Module
Handles user authentication, session management, and role-based access control
"""

import streamlit as st
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, Tuple, List
import logging

logger = logging.getLogger(__name__)

# Session timeout: 30 minutes
SESSION_TIMEOUT_MINUTES = 30


class AuthManager:
    """Manages authentication and authorization for the application"""

    def __init__(self, supabase_client):
        """
        Initialize AuthManager with Supabase client

        Args:
            supabase_client: Instance of SupabaseClient
        """
        self.supabase = supabase_client.client if supabase_client else None

    def check_authentication(self) -> bool:
        """
        Check if user is authenticated and session is valid

        Returns:
            bool: True if authenticated, False otherwise
        """
        if 'authenticated' not in st.session_state:
            st.session_state['authenticated'] = False
            return False

        if not st.session_state['authenticated']:
            return False

        # Check session timeout
        if self._is_session_expired():
            self.logout()
            st.warning("Session expired. Please login again.")
            return False

        # Update last activity
        st.session_state['last_activity'] = datetime.now()
        return True

    def _is_session_expired(self) -> bool:
        """Check if session has expired due to inactivity"""
        if 'last_activity' not in st.session_state:
            return False

        last_activity = st.session_state['last_activity']
        timeout = timedelta(minutes=SESSION_TIMEOUT_MINUTES)

        return datetime.now() - last_activity > timeout

    def login(self, email: str, password: str) -> Tuple[bool, Optional[str]]:
        """
        Authenticate user with email and password

        Args:
            email: User email
            password: User password

        Returns:
            tuple: (success: bool, error_message: Optional[str])
        """
        if not self.supabase:
            return False, "Supabase client not initialized"

        try:
            # Authenticate with Supabase
            response = self.supabase.auth.sign_in_with_password({
                "email": email,
                "password": password
            })

            if not response.user:
                return False, "Authentication failed"

            # Set session state
            st.session_state['authenticated'] = True
            st.session_state['user_id'] = response.user.id
            st.session_state['user_email'] = response.user.email
            st.session_state['last_activity'] = datetime.now()

            # Load user profile
            success = self._load_user_profile()
            if not success:
                self.logout()
                return False, "Failed to load user profile. Please contact administrator."

            # Log login event
            self._log_audit_event('user_login', 'authentication', metadata={
                'email': email,
                'login_time': datetime.now().isoformat()
            })

            logger.info(f"User logged in: {email}")
            return True, None

        except Exception as e:
            logger.error(f"Login error: {e}")
            return False, f"Login failed: {str(e)}"

    def _load_user_profile(self) -> bool:
        """
        Load user profile from database

        Returns:
            bool: True if successful, False otherwise
        """
        try:
            user_id = st.session_state['user_id']

            response = self.supabase.table('profiles')\
                .select('*')\
                .eq('id', user_id)\
                .single()\
                .execute()

            if not response.data:
                logger.error(f"No profile found for user {user_id}")
                return False

            profile = response.data
            st.session_state['user_profile'] = profile
            st.session_state['user_role'] = profile['role']
            st.session_state['user_full_name'] = profile.get('full_name', '')

            logger.info(f"Loaded profile for {profile['email']} with role {profile['role']}")
            return True

        except Exception as e:
            logger.error(f"Error loading profile: {e}")
            return False

    def logout(self):
        """Logout current user and clear session"""
        try:
            if 'user_email' in st.session_state:
                email = st.session_state['user_email']
                logger.info(f"User logged out: {email}")

            # Sign out from Supabase
            if self.supabase:
                self.supabase.auth.sign_out()

        except Exception as e:
            logger.error(f"Logout error: {e}")
        finally:
            # Clear all auth-related session state
            auth_keys = [
                'authenticated', 'user_id', 'user_email', 'user_profile',
                'user_role', 'user_full_name', 'last_activity'
            ]
            for key in auth_keys:
                if key in st.session_state:
                    del st.session_state[key]

    def check_role(self, required_roles: List[str]) -> bool:
        """
        Check if user has required role

        Args:
            required_roles: List of acceptable roles

        Returns:
            bool: True if user has required role
        """
        if 'user_role' not in st.session_state:
            return False

        return st.session_state['user_role'] in required_roles

    def require_role(self, required_roles: List[str], page_name: str = "this page"):
        """
        Require specific role to access page, show error and stop if unauthorized

        Args:
            required_roles: List of acceptable roles
            page_name: Name of page for error message
        """
        if not self.check_role(required_roles):
            current_role = st.session_state.get('user_role', 'unknown')
            st.error(f"⛔ Access Denied - {page_name} requires one of these roles: {', '.join(required_roles)}")
            st.info(f"Your current role: **{current_role}**")
            st.stop()

    def _log_audit_event(self, action: str, resource_type: str,
                        resource_id: str = None, metadata: dict = None):
        """
        Log audit event to database

        Args:
            action: Action performed
            resource_type: Type of resource
            resource_id: Resource identifier
            metadata: Additional metadata
        """
        try:
            if not self.supabase:
                return

            user_id = st.session_state.get('user_id')
            if not user_id:
                return

            self.supabase.table('audit_logs').insert({
                'user_id': user_id,
                'action': action,
                'resource_type': resource_type,
                'resource_id': resource_id,
                'metadata': metadata
            }).execute()

        except Exception as e:
            logger.error(f"Error logging audit event: {e}")

    def log_audit_event(self, action: str, resource_type: str,
                       resource_id: str = None, metadata: dict = None):
        """
        Public method to log audit events from application code

        Args:
            action: Action performed (e.g., 'pdf_upload', 'excel_export')
            resource_type: Type of resource (e.g., 'purchase_order', 'sales_order')
            resource_id: Resource identifier
            metadata: Additional context as dict
        """
        self._log_audit_event(action, resource_type, resource_id, metadata)


def show_login_page(auth_manager: AuthManager):
    """
    Display login page UI

    Args:
        auth_manager: AuthManager instance
    """
    st.title("🔐 T&T PO Processor Login")
    st.markdown("---")

    # Center the login form
    col1, col2, col3 = st.columns([1, 2, 1])

    with col2:
        st.subheader("Please sign in to continue")

        email = st.text_input("Email", key="login_email", placeholder="user@example.com")
        password = st.text_input("Password", type="password", key="login_password")

        if st.button("Login", type="primary", use_container_width=True):
            if not email or not password:
                st.error("Please enter both email and password")
            else:
                with st.spinner("Authenticating..."):
                    success, error = auth_manager.login(email, password)

                    if success:
                        st.success("Login successful!")
                        st.rerun()
                    else:
                        st.error(error or "Login failed")

        st.markdown("---")
        st.caption("Contact your administrator if you need access or have forgotten your password.")


def show_user_info_sidebar(auth_manager: AuthManager):
    """
    Display user info and logout button in sidebar

    Args:
        auth_manager: AuthManager instance
    """
    with st.sidebar:
        st.markdown("---")

        # User info
        user_name = st.session_state.get('user_full_name', '')
        user_email = st.session_state.get('user_email', '')
        user_role = st.session_state.get('user_role', 'viewer')

        # Role badge color
        role_colors = {
            'admin': '🔴',
            'manager': '🟡',
            'viewer': '🟢'
        }
        role_badge = role_colors.get(user_role, '⚪')

        st.markdown(f"**{role_badge} Logged in as:**")
        if user_name:
            st.markdown(f"**{user_name}**")
        st.markdown(f"`{user_email}`")
        st.caption(f"Role: **{user_role.title()}**")

        if st.button("🚪 Logout", use_container_width=True):
            auth_manager.logout()
            st.rerun()
