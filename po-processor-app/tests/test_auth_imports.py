#!/usr/bin/env python3
"""
Test script to verify authentication module is properly set up
Run this to check if all imports work before starting the app
"""

import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

def test_imports():
    """Test that all required modules can be imported"""
    print("Testing imports...")

    try:
        from backend.auth import AuthManager, show_login_page, show_user_info_sidebar
        print("✓ Auth module imports successful")
    except Exception as e:
        print(f"✗ Auth module import failed: {e}")
        return False

    try:
        from backend.odoo_client import OdooClient
        print("✓ OdooClient import successful")
    except Exception as e:
        print(f"✗ OdooClient import failed: {e}")
        return False

    try:
        from backend.supabase_client import SupabaseClient
        print("✓ SupabaseClient import successful")
    except Exception as e:
        print(f"✗ SupabaseClient import failed: {e}")
        return False

    try:
        from backend.pdf_extractor import PDFExtractor
        print("✓ PDFExtractor import successful")
    except Exception as e:
        print(f"✗ PDFExtractor import failed: {e}")
        return False

    try:
        from backend.data_transformer import DataTransformer
        print("✓ DataTransformer import successful")
    except Exception as e:
        print(f"✗ DataTransformer import failed: {e}")
        return False

    try:
        from backend.inventory_optimizer import InventoryOptimizer
        print("✓ InventoryOptimizer import successful")
    except Exception as e:
        print(f"✗ InventoryOptimizer import failed: {e}")
        return False

    print("\n✅ All imports successful!")
    return True

def test_auth_manager_init():
    """Test that AuthManager can be initialized"""
    print("\nTesting AuthManager initialization...")

    try:
        from backend.auth import AuthManager

        # Test with None (no Supabase client)
        auth = AuthManager(None)
        print("✓ AuthManager initialized with None")

        # Test that it has expected methods
        assert hasattr(auth, 'check_authentication')
        assert hasattr(auth, 'login')
        assert hasattr(auth, 'logout')
        assert hasattr(auth, 'check_role')
        assert hasattr(auth, 'require_role')
        assert hasattr(auth, 'log_audit_event')
        print("✓ AuthManager has all required methods")

        print("\n✅ AuthManager initialization successful!")
        return True

    except Exception as e:
        print(f"✗ AuthManager initialization failed: {e}")
        import traceback
        traceback.print_exc()
        return False

def check_files():
    """Check that required files exist"""
    print("\nChecking required files...")

    files_to_check = [
        'backend/auth.py',
        'frontend/app.py',
        '.streamlit/secrets.toml.example',
        'deployment/migrations/001_create_profiles.sql',
        'deployment/migrations/002_create_audit_logs.sql',
        'deployment/migrations/003_enable_rls.sql',
        'docs/AUTHENTICATION.md',
        'docs/auth-roadmap.md'
    ]

    all_exist = True
    for file in files_to_check:
        if os.path.exists(file):
            print(f"✓ {file}")
        else:
            print(f"✗ {file} NOT FOUND")
            all_exist = False

    if all_exist:
        print("\n✅ All required files exist!")
    else:
        print("\n⚠️  Some files are missing!")

    return all_exist

if __name__ == '__main__':
    print("=" * 60)
    print("Authentication Setup Test")
    print("=" * 60)

    success = True

    # Test imports
    if not test_imports():
        success = False

    # Test AuthManager
    if not test_auth_manager_init():
        success = False

    # Check files
    if not check_files():
        success = False

    print("\n" + "=" * 60)
    if success:
        print("✅ ALL TESTS PASSED!")
        print("\nNext steps:")
        print("1. Run database migrations in Supabase")
        print("2. Create admin user")
        print("3. Configure .streamlit/secrets.toml")
        print("4. Run: streamlit run frontend/app.py")
    else:
        print("❌ SOME TESTS FAILED!")
        print("Please fix the errors above before proceeding.")
    print("=" * 60)

    sys.exit(0 if success else 1)
