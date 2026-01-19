# Database Migrations

This directory contains SQL migrations for setting up authentication and security in the PO Processor application.

## Migration Order

Run these migrations in order in the Supabase SQL Editor:

1. **001_create_profiles.sql** - Creates user profiles table with RBAC
2. **002_create_audit_logs.sql** - Creates audit logging table
3. **003_enable_rls.sql** - Enables Row Level Security on data tables

## How to Run Migrations

### Via Supabase Dashboard

1. Go to your Supabase project: https://supabase.com/dashboard/project/wfgxdqblcoxzspbrwpmh
2. Navigate to **SQL Editor** in the left sidebar
3. Click **New Query**
4. Copy and paste the contents of `001_create_profiles.sql`
5. Click **Run** or press `Cmd+Enter`
6. Repeat for `002_create_audit_logs.sql` and `003_enable_rls.sql`

### Via Supabase CLI (Alternative)

```bash
# Install Supabase CLI if not already installed
npm install -g supabase

# Login to Supabase
supabase login

# Link to your project
supabase link --project-ref wfgxdqblcoxzspbrwpmh

# Run migrations
supabase db push

# Or apply individual migrations
psql postgresql://postgres:[PASSWORD]@db.wfgxdqblcoxzspbrwpmh.supabase.co:5432/postgres < 001_create_profiles.sql
```

## Post-Migration Setup

After running all migrations:

### 1. Create First Admin User

Go to **Authentication > Users** in Supabase dashboard:

1. Click **Add User** → **Create New User**
2. Enter email: `admin@atiara.ca`
3. Enter a secure password
4. Click **Create User**

### 2. Set Admin Role

Go to **Table Editor** → **profiles**:

1. Find the newly created user (might be empty - wait for trigger)
2. If profile doesn't exist, manually insert:
   ```sql
   INSERT INTO public.profiles (id, email, role, full_name)
   SELECT id, email, 'admin', 'System Administrator'
   FROM auth.users
   WHERE email = 'admin@atiara.ca';
   ```
3. Or update existing profile:
   ```sql
   UPDATE public.profiles
   SET role = 'admin', full_name = 'System Administrator'
   WHERE email = 'admin@atiara.ca';
   ```

### 3. Create Test Users (Optional)

For testing different roles:

```sql
-- Manager user
INSERT INTO auth.users (email, encrypted_password, email_confirmed_at)
VALUES ('manager@atiara.ca', crypt('test_password', gen_salt('bf')), NOW());

-- Update profile role
UPDATE public.profiles SET role = 'manager' WHERE email = 'manager@atiara.ca';

-- Viewer user
INSERT INTO auth.users (email, encrypted_password, email_confirmed_at)
VALUES ('viewer@atiara.ca', crypt('test_password', gen_salt('bf')), NOW());

-- Profile will auto-create with 'viewer' role
```

### 4. Verify RLS is Working

Test with different users:

```sql
-- As admin user (should see all)
SELECT * FROM public.profiles;

-- As regular user (should see only own profile)
SELECT * FROM public.profiles;

-- Test sales data access
SELECT * FROM public.sales_performance LIMIT 1;
```

## Rollback Instructions

If you need to undo migrations (USE WITH CAUTION):

```sql
-- Rollback 003_enable_rls.sql
DROP POLICY IF EXISTS "Authenticated users can read sales data" ON public.sales_performance;
DROP POLICY IF EXISTS "Admins can modify sales data" ON public.sales_performance;
DROP POLICY IF EXISTS "Authenticated users can read inventory" ON public.inventory_snapshots;
DROP POLICY IF EXISTS "Admins can modify inventory data" ON public.inventory_snapshots;
DROP POLICY IF EXISTS "Authenticated users can read products" ON public.products;
DROP POLICY IF EXISTS "Admins can modify products" ON public.products;

ALTER TABLE public.sales_performance DISABLE ROW LEVEL SECURITY;
ALTER TABLE public.inventory_snapshots DISABLE ROW LEVEL SECURITY;
ALTER TABLE public.products DISABLE ROW LEVEL SECURITY;

-- Rollback 002_create_audit_logs.sql
DROP VIEW IF EXISTS public.recent_audit_logs;
DROP FUNCTION IF EXISTS public.cleanup_old_audit_logs();
DROP TABLE IF EXISTS public.audit_logs CASCADE;

-- Rollback 001_create_profiles.sql
DROP TRIGGER IF EXISTS on_auth_user_created ON auth.users;
DROP TRIGGER IF EXISTS set_updated_at ON public.profiles;
DROP FUNCTION IF EXISTS public.handle_new_user();
DROP FUNCTION IF EXISTS public.handle_updated_at();
DROP TABLE IF EXISTS public.profiles CASCADE;
```

## Troubleshooting

### Trigger Not Firing

If new users don't get profiles automatically:

```sql
-- Check if trigger exists
SELECT * FROM information_schema.triggers WHERE trigger_name = 'on_auth_user_created';

-- Manually create profile for existing user
INSERT INTO public.profiles (id, email, role)
SELECT id, email, 'viewer'
FROM auth.users
WHERE email = 'user@example.com'
ON CONFLICT (id) DO NOTHING;
```

### RLS Blocking Queries

If you can't access data after enabling RLS:

1. Make sure you're authenticated (not using anon key)
2. Check your user has a profile with proper role
3. Verify policies are created:
   ```sql
   SELECT * FROM pg_policies WHERE tablename IN ('profiles', 'audit_logs', 'sales_performance');
   ```

### Permission Errors

If you get permission denied errors:

```sql
-- Grant necessary permissions
GRANT USAGE ON SCHEMA public TO authenticated;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO authenticated;
GRANT INSERT ON public.audit_logs TO authenticated;
```

## Maintenance

### Clean Up Old Audit Logs

Run periodically (e.g., monthly):

```sql
SELECT public.cleanup_old_audit_logs();
```

Or set up a Supabase Database Webhook or pg_cron job.

### Monitor Audit Logs

View recent activity:

```sql
SELECT * FROM public.recent_audit_logs LIMIT 50;
```

Check specific user activity:

```sql
SELECT * FROM public.audit_logs
WHERE user_id = '<user-uuid>'
ORDER BY timestamp DESC;
```
