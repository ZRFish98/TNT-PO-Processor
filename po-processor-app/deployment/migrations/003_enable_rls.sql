-- Migration: Enable Row Level Security on data tables
-- Description: Protect sales_performance and inventory_snapshots from unauthorized access
-- Run this migration third in Supabase SQL Editor

-- Enable RLS on sales_performance table
ALTER TABLE IF EXISTS public.sales_performance ENABLE ROW LEVEL SECURITY;

-- Policy: Authenticated users can read sales data
CREATE POLICY "Authenticated users can read sales data"
  ON public.sales_performance
  FOR SELECT
  TO authenticated
  USING (true);

-- Policy: Only admins can insert/update/delete sales data
CREATE POLICY "Admins can modify sales data"
  ON public.sales_performance
  FOR ALL
  USING (
    EXISTS (
      SELECT 1 FROM public.profiles
      WHERE id = auth.uid() AND role = 'admin'
    )
  );

-- Enable RLS on inventory_snapshots table
ALTER TABLE IF EXISTS public.inventory_snapshots ENABLE ROW LEVEL SECURITY;

-- Policy: Authenticated users can read inventory
CREATE POLICY "Authenticated users can read inventory"
  ON public.inventory_snapshots
  FOR SELECT
  TO authenticated
  USING (true);

-- Policy: Only admins can modify inventory data
CREATE POLICY "Admins can modify inventory data"
  ON public.inventory_snapshots
  FOR ALL
  USING (
    EXISTS (
      SELECT 1 FROM public.profiles
      WHERE id = auth.uid() AND role = 'admin'
    )
  );

-- Enable RLS on products table (if exists)
ALTER TABLE IF EXISTS public.products ENABLE ROW LEVEL SECURITY;

-- Policy: Authenticated users can read products
CREATE POLICY "Authenticated users can read products"
  ON public.products
  FOR SELECT
  TO authenticated
  USING (true);

-- Policy: Only admins can modify products
CREATE POLICY "Admins can modify products"
  ON public.products
  FOR ALL
  USING (
    EXISTS (
      SELECT 1 FROM public.profiles
      WHERE id = auth.uid() AND role = 'admin'
    )
  );

-- Grant necessary permissions
GRANT USAGE ON SCHEMA public TO authenticated, anon;
GRANT SELECT ON public.sales_performance TO authenticated;
GRANT SELECT ON public.inventory_snapshots TO authenticated;
GRANT SELECT ON public.products TO authenticated;

-- Comments
COMMENT ON POLICY "Authenticated users can read sales data" ON public.sales_performance
  IS 'All authenticated users can read sales performance data';
COMMENT ON POLICY "Authenticated users can read inventory" ON public.inventory_snapshots
  IS 'All authenticated users can read inventory snapshot data';

-- Verify RLS is enabled (this will show which tables have RLS)
DO $$
DECLARE
  tbl record;
BEGIN
  RAISE NOTICE 'Tables with RLS enabled:';
  FOR tbl IN
    SELECT schemaname, tablename, rowsecurity
    FROM pg_tables
    WHERE schemaname = 'public' AND rowsecurity = true
  LOOP
    RAISE NOTICE '  - %.%', tbl.schemaname, tbl.tablename;
  END LOOP;
END $$;
