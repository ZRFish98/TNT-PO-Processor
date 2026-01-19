# Authentication Implementation Guide

This document provides instructions for setting up and using the authentication system in the T&T PO Processor application.

## Overview

The application uses **Supabase Auth** for user authentication and role-based access control (RBAC). All users must login before accessing any functionality.

## User Roles

| Role | Access Level | Permissions |
|------|-------------|-------------|
| **Admin** | Full Access | • Access all pages<br>• Configure Odoo/Supabase connections<br>• Manage user roles<br>• View all audit logs |
| **Manager** | Operational | • Upload & extract PDFs<br>• Transform data<br>• Optimize inventory<br>• Export to Excel<br>• Cannot access Configuration |
| **Viewer** | Read-Only | • View data only<br>• Cannot upload, modify, or export<br>• For monitoring/reporting purposes |

## Setup Instructions

### 1. Run Database Migrations

Follow the instructions in [deployment/migrations/README.md](../deployment/migrations/README.md):

```bash
# Go to Supabase dashboard SQL Editor
# Run migrations in order:
# 1. 001_create_profiles.sql
# 2. 002_create_audit_logs.sql
# 3. 003_enable_rls.sql
```

### 2. Create First Admin User

Via Supabase Dashboard:

1. Go to **Authentication** → **Users**
2. Click **Add User** → **Create New User**
3. Enter email: `admin@atiara.ca` (or your email)
4. Set a secure password
5. Click **Create User**

Then set the admin role via SQL Editor:

```sql
UPDATE public.profiles
SET role = 'admin', full_name = 'System Administrator'
WHERE email = 'admin@atiara.ca';
```

### 3. Configure Secrets

#### For Local Development

Copy the example secrets file:

```bash
cd po-processor-app
cp .streamlit/secrets.toml.example .streamlit/secrets.toml
```

Edit `.streamlit/secrets.toml` with your real credentials:

```toml
[supabase]
url = "https://wfgxdqblcoxzspbrwpmh.supabase.co"
anon_key = "your-anon-key-from-supabase"
service_key = "your-service-key-from-supabase"

[odoo]
url = "https://atiara-trading-inc.odoo.com"
db = "atiara-trading-inc"
api_key = "your-odoo-api-key"
default_user = "official@atiara.ca"
```

**Important:** The `secrets.toml` file is git-ignored and should NEVER be committed.

#### For Streamlit Cloud Deployment

1. Go to your app's settings in Streamlit Cloud
2. Navigate to **Secrets** section
3. Paste the same content (with real values)
4. Click **Save**

### 4. Get Supabase Keys

1. Go to Supabase Dashboard: https://supabase.com/dashboard/project/wfgxdqblcoxzspbrwpmh
2. Navigate to **Settings** → **API**
3. Copy:
   - **URL**: Project URL
   - **anon/public key**: For client-side operations (use this in production)
   - **service_role key**: For admin/server-side operations only

## Using the Application

### Login

1. Start the application:
   ```bash
   cd po-processor-app
   streamlit run frontend/app.py
   ```

2. You'll see the login page
3. Enter your email and password
4. Click **Login**

### First-Time Login

If you just created your account:

1. Check your email for confirmation (if email confirmation is enabled)
2. Use the password you set when creating the user
3. After successful login, your profile will be loaded with your assigned role

### Session Management

- Sessions automatically timeout after **30 minutes** of inactivity
- You'll be prompted to login again if your session expires
- Click the **Logout** button in the sidebar to manually logout

### Page Access by Role

#### Admin Users
- ✅ Configuration (Odoo/Supabase setup)
- ✅ Upload & Extract
- ✅ Transform & Review
- ✅ Inventory Optimization
- ✅ Review & Import

#### Manager Users
- ❌ Configuration (Access Denied)
- ✅ Upload & Extract
- ✅ Transform & Review
- ✅ Inventory Optimization
- ✅ Review & Import

#### Viewer Users
- ❌ All pages (Read-only access only - to be implemented)

## Managing Users

### Creating New Users

Via Supabase Dashboard (Admin Only):

1. **Authentication** → **Users** → **Add User**
2. Enter email and password
3. User profile will be auto-created with **viewer** role

### Changing User Roles

Via SQL Editor (Admin Only):

```sql
-- Make someone a manager
UPDATE public.profiles
SET role = 'manager'
WHERE email = 'user@example.com';

-- Make someone an admin
UPDATE public.profiles
SET role = 'admin'
WHERE email = 'user@example.com';

-- Demote to viewer
UPDATE public.profiles
SET role = 'viewer'
WHERE email = 'user@example.com';
```

### User Management Page (Future)

A dedicated UI for user management is planned for Week 3 implementation.

## Audit Logging

All critical operations are automatically logged:

### Logged Events

| Action | When Logged |
|--------|-------------|
| `user_login` | User successfully logs in |
| `pdf_extraction` | PDFs are uploaded and extracted |
| `excel_export` | Excel file is downloaded |
| (More to come) | Data transformations, manual edits |

### Viewing Audit Logs

Via SQL Editor:

```sql
-- View recent activity (last 30 days)
SELECT * FROM public.recent_audit_logs
ORDER BY timestamp DESC
LIMIT 50;

-- View specific user's activity
SELECT *
FROM public.audit_logs a
LEFT JOIN public.profiles p ON a.user_id = p.id
WHERE p.email = 'user@example.com'
ORDER BY timestamp DESC;

-- View all Excel exports
SELECT *
FROM public.recent_audit_logs
WHERE action = 'excel_export'
ORDER BY timestamp DESC;
```

### Audit Log Cleanup

Old logs (> 1 year) can be cleaned up:

```sql
SELECT public.cleanup_old_audit_logs();
```

Consider running this monthly or setting up a pg_cron job.

## Security Features

### Row Level Security (RLS)

All sensitive tables have RLS enabled:

- ✅ `profiles` - Users can only see own profile; admins see all
- ✅ `audit_logs` - Users see own logs; admins see all
- ✅ `sales_performance` - Authenticated users can read
- ✅ `inventory_snapshots` - Authenticated users can read
- ✅ `products` - Authenticated users can read

### API Key Security

- **Anon Key**: Public key, safe for client-side use with RLS
- **Service Key**: Admin-only, server-side operations only
- Never expose service keys in client code or logs

### Session Security

- 30-minute inactivity timeout
- Automatic token refresh via Supabase
- Logout clears all session data

### Password Security

- Passwords hashed by Supabase (bcrypt)
- Never stored in plaintext
- Password reset available (to be configured)

## Troubleshooting

### Can't Login

**Error:** "Authentication failed"
- Check email/password are correct
- Verify user exists in Supabase Auth dashboard
- Ensure Supabase URL and anon_key are correct in secrets

**Error:** "Failed to load user profile"
- Check that profile exists in `profiles` table
- Verify the auto-create trigger is working:
  ```sql
  SELECT * FROM information_schema.triggers
  WHERE trigger_name = 'on_auth_user_created';
  ```

### Access Denied Errors

**Error:** "Access Denied - Configuration page requires admin role"
- Check your role: `SELECT role FROM profiles WHERE email = 'your@email.com'`
- Ask admin to update your role if needed

### Session Expired Issues

- Sessions expire after 30 minutes of inactivity
- Just login again - this is expected behavior
- To extend timeout, modify `SESSION_TIMEOUT_MINUTES` in `backend/auth.py`

### RLS Blocking Data Access

If you can't see data after enabling RLS:

1. Make sure you're authenticated (not using anon access without login)
2. Verify policies exist:
   ```sql
   SELECT * FROM pg_policies
   WHERE tablename IN ('sales_performance', 'inventory_snapshots');
   ```
3. Check you have proper role in profiles table

## Development Tips

### Testing Different Roles

Create test users for each role:

```sql
-- Create test users (in Supabase Auth Dashboard first)
-- Then set roles:

UPDATE public.profiles SET role = 'admin'
WHERE email = 'test-admin@atiara.ca';

UPDATE public.profiles SET role = 'manager'
WHERE email = 'test-manager@atiara.ca';

UPDATE public.profiles SET role = 'viewer'
WHERE email = 'test-viewer@atiara.ca';
```

Test each role's access in different browser sessions or incognito windows.

### Adding Audit Logging to New Features

When adding new functionality, log critical operations:

```python
# In your feature code
from backend.auth import AuthManager

# Log an action
auth_manager.log_audit_event(
    action='custom_action',
    resource_type='resource_name',
    resource_id='optional_id',
    metadata={
        'custom_field': 'value',
        'count': 123
    }
)
```

### Adding New Roles

To add a new role (e.g., "analyst"):

1. Update the CHECK constraint:
   ```sql
   ALTER TABLE public.profiles
   DROP CONSTRAINT profiles_role_check;

   ALTER TABLE public.profiles
   ADD CONSTRAINT profiles_role_check
   CHECK (role IN ('admin', 'manager', 'viewer', 'analyst'));
   ```

2. Update auth-roadmap.md with new role permissions

3. Add role checks in `frontend/app.py`:
   ```python
   if page == "Analytics":
       auth_manager.require_role(['admin', 'analyst'], "Analytics page")
   ```

## Next Steps

See [auth-roadmap.md](auth-roadmap.md) for planned enhancements:

- Week 3: User management UI
- Week 3: Password reset flow
- Week 3: OAuth integration (if needed)
- Future: Audit log viewer UI
- Future: Advanced RLS policies (store-level access)

## Support

For issues or questions:

1. Check this documentation
2. Review [auth-roadmap.md](auth-roadmap.md) for planned features
3. Check [deployment/migrations/README.md](../deployment/migrations/README.md) for migration issues
4. Contact system administrator
