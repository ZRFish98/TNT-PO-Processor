-- Migration: Create audit_logs table for tracking user actions
-- Description: Logs all critical operations for compliance and security
-- Run this migration second in Supabase SQL Editor

-- Create audit_logs table
CREATE TABLE IF NOT EXISTS public.audit_logs (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  user_id UUID REFERENCES auth.users(id) ON DELETE SET NULL,
  action TEXT NOT NULL,
  resource_type TEXT,
  resource_id TEXT,
  metadata JSONB,
  timestamp TIMESTAMPTZ DEFAULT NOW()
);

-- Create indexes for common queries
CREATE INDEX IF NOT EXISTS idx_audit_user ON public.audit_logs(user_id);
CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON public.audit_logs(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_audit_action ON public.audit_logs(action);
CREATE INDEX IF NOT EXISTS idx_audit_resource ON public.audit_logs(resource_type, resource_id);

-- Enable Row Level Security
ALTER TABLE public.audit_logs ENABLE ROW LEVEL SECURITY;

-- Policy: Users can view their own audit logs
CREATE POLICY "Users can view own audit logs"
  ON public.audit_logs
  FOR SELECT
  USING (auth.uid() = user_id);

-- Policy: Admins can view all audit logs
CREATE POLICY "Admins can view all audit logs"
  ON public.audit_logs
  FOR SELECT
  USING (
    EXISTS (
      SELECT 1 FROM public.profiles
      WHERE id = auth.uid() AND role = 'admin'
    )
  );

-- Policy: Authenticated users can insert audit logs
CREATE POLICY "Authenticated users can insert audit logs"
  ON public.audit_logs
  FOR INSERT
  TO authenticated
  WITH CHECK (auth.uid() = user_id);

-- Policy: Only system/admins can delete audit logs (for cleanup)
CREATE POLICY "Admins can delete old audit logs"
  ON public.audit_logs
  FOR DELETE
  USING (
    EXISTS (
      SELECT 1 FROM public.profiles
      WHERE id = auth.uid() AND role = 'admin'
    )
  );

-- Grant necessary permissions
GRANT USAGE ON SCHEMA public TO authenticated;
GRANT SELECT, INSERT ON public.audit_logs TO authenticated;

-- Comments for documentation
COMMENT ON TABLE public.audit_logs IS 'Audit trail of all user actions in the application';
COMMENT ON COLUMN public.audit_logs.user_id IS 'User who performed the action (null if user deleted)';
COMMENT ON COLUMN public.audit_logs.action IS 'Action performed (e.g., pdf_upload, excel_export)';
COMMENT ON COLUMN public.audit_logs.resource_type IS 'Type of resource affected';
COMMENT ON COLUMN public.audit_logs.resource_id IS 'Identifier of the resource';
COMMENT ON COLUMN public.audit_logs.metadata IS 'Additional context as JSON (file counts, order details, etc.)';

-- Create view for recent activity (last 30 days)
CREATE OR REPLACE VIEW public.recent_audit_logs AS
SELECT
  a.id,
  a.user_id,
  p.email,
  p.full_name,
  a.action,
  a.resource_type,
  a.resource_id,
  a.metadata,
  a.timestamp
FROM public.audit_logs a
LEFT JOIN public.profiles p ON a.user_id = p.id
WHERE a.timestamp > NOW() - INTERVAL '30 days'
ORDER BY a.timestamp DESC;

-- Grant view access
GRANT SELECT ON public.recent_audit_logs TO authenticated;

-- Create function to clean up old audit logs (optional - keep 1 year)
CREATE OR REPLACE FUNCTION public.cleanup_old_audit_logs()
RETURNS void AS $$
BEGIN
  DELETE FROM public.audit_logs
  WHERE timestamp < NOW() - INTERVAL '1 year';
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

COMMENT ON FUNCTION public.cleanup_old_audit_logs IS 'Delete audit logs older than 1 year (run periodically)';
