"""
Export Supabase data to Excel files for inspection
Creates two Excel files:
1. sales_performance_data.xlsx
2. inventory_snapshots_data.xlsx
"""

import sys
import os
from pathlib import Path
import pandas as pd

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.supabase_client import SupabaseClient
from dotenv import load_dotenv

# Load environment
load_dotenv()

# Test data from the PDF
TEST_SKUS = ['535553', '538752', '538753', '532827', '537861', '537868', '519279', '755734']
TEST_STORES = [20, 28, 37]  # Weldrick-020, Waterloo-028, London-037

def main():
    print("=" * 80)
    print("EXPORTING SUPABASE DATA TO EXCEL")
    print("=" * 80)

    # Initialize Supabase
    supabase_url = os.getenv('SUPABASE_URL', 'https://zzxfwmgftwojhmuhkrrp.supabase.co')
    supabase_key = os.getenv('SUPABASE_KEY')

    if not supabase_key:
        print("❌ ERROR: SUPABASE_KEY not found in .env file")
        return

    client = SupabaseClient(supabase_url, supabase_key)

    if not client.connect():
        print("❌ Failed to connect to Supabase")
        return

    print("✅ Connected to Supabase\n")

    # Output directory
    output_dir = Path(__file__).parent.parent.parent

    print(f"Looking for SKUs: {TEST_SKUS}")
    print(f"Looking for Stores: {TEST_STORES}\n")

    # =========================================================================
    # EXPORT 1: Sales Performance Data
    # =========================================================================
    print("-" * 80)
    print("EXPORTING SALES PERFORMANCE DATA")
    print("-" * 80)

    try:
        # Get product and store mappings
        product_map = client._get_product_id_map(TEST_SKUS)
        store_map = client._get_store_id_map(TEST_STORES)

        print(f"Found {len(product_map)} products in database")
        print(f"Found {len(store_map)} stores in database\n")

        if not product_map or not store_map:
            print("⚠️  Missing products or stores - cannot fetch sales data")
            sales_df = pd.DataFrame()
        else:
            internal_product_ids = list(product_map.keys())
            internal_store_ids = list(store_map.keys())

            # Fetch raw sales performance data
            response = client.client.table('sales_performance')\
                .select('*')\
                .in_('store_id', internal_store_ids)\
                .in_('product_id', internal_product_ids)\
                .execute()

            if response.data:
                sales_df = pd.DataFrame(response.data)

                # Map internal IDs back to external values
                sales_df['sku'] = sales_df['product_id'].map(product_map)
                sales_df['store_number'] = sales_df['store_id'].map(store_map)

                # Reorder columns for readability
                cols = ['sku', 'store_number', 'product_id', 'store_id', 'period_start',
                        'period_end', 'total_quantity_sold', 'stocking_unit_size',
                        'order_multiple', 'case_required', 'case_weekly', 'created_at']
                sales_df = sales_df[cols]

                print(f"✅ Fetched {len(sales_df)} sales performance records")

                # Export to Excel
                output_file = output_dir / 'sales_performance_data.xlsx'
                sales_df.to_excel(output_file, index=False, sheet_name='Sales Performance')
                print(f"✅ Exported to: {output_file}")

                # Show summary
                print("\nSummary by SKU and Store:")
                summary = sales_df.groupby(['sku', 'store_number'])['total_quantity_sold'].agg(['count', 'sum', 'mean']).reset_index()
                summary.columns = ['SKU', 'Store', 'Periods', 'Total Sold', 'Avg Per Period']
                print(summary.to_string(index=False))
            else:
                print("⚠️  No sales performance data found")
                sales_df = pd.DataFrame()

    except Exception as e:
        print(f"❌ Error fetching sales performance: {e}")
        sales_df = pd.DataFrame()

    # =========================================================================
    # EXPORT 2: Inventory Snapshots Data
    # =========================================================================
    print("\n" + "-" * 80)
    print("EXPORTING INVENTORY SNAPSHOTS DATA")
    print("-" * 80)

    try:
        if not product_map or not store_map:
            print("⚠️  Missing products or stores - cannot fetch inventory data")
            inventory_df = pd.DataFrame()
        else:
            # Fetch raw inventory snapshots
            response = client.client.table('inventory_snapshots')\
                .select('*')\
                .in_('store_id', internal_store_ids)\
                .in_('product_id', internal_product_ids)\
                .execute()

            if response.data:
                inventory_df = pd.DataFrame(response.data)

                # Map internal IDs back to external values
                inventory_df['sku'] = inventory_df['product_id'].map(product_map)
                inventory_df['store_number'] = inventory_df['store_id'].map(store_map)

                # Reorder columns for readability
                cols = ['sku', 'store_number', 'product_id', 'store_id',
                        'snapshot_date', 'quantity', 'created_at']
                inventory_df = inventory_df[cols]

                # Sort by SKU, Store, Date
                inventory_df = inventory_df.sort_values(['sku', 'store_number', 'snapshot_date'])

                print(f"✅ Fetched {len(inventory_df)} inventory snapshot records")

                # Export to Excel
                output_file = output_dir / 'inventory_snapshots_data.xlsx'
                inventory_df.to_excel(output_file, index=False, sheet_name='Inventory Snapshots')
                print(f"✅ Exported to: {output_file}")

                # Show latest snapshots
                print("\nLatest Snapshot per SKU and Store:")
                latest = inventory_df.sort_values('snapshot_date', ascending=False)\
                    .drop_duplicates(subset=['sku', 'store_number'])[['sku', 'store_number', 'snapshot_date', 'quantity']]
                latest.columns = ['SKU', 'Store', 'Snapshot Date', 'Quantity']
                print(latest.to_string(index=False))
            else:
                print("⚠️  No inventory snapshot data found")
                inventory_df = pd.DataFrame()

    except Exception as e:
        print(f"❌ Error fetching inventory snapshots: {e}")
        inventory_df = pd.DataFrame()

    # =========================================================================
    # EXPORT 3: Combined Analysis (what the app uses)
    # =========================================================================
    print("\n" + "-" * 80)
    print("EXPORTING PROCESSED DATA (AS USED BY APP)")
    print("-" * 80)

    try:
        # Get the processed data using the client methods
        hist_sales = client.get_historical_sales(TEST_STORES, TEST_SKUS)
        store_inv = client.get_store_inventory(TEST_STORES, TEST_SKUS)

        if not hist_sales.empty or not store_inv.empty:
            output_file = output_dir / 'supabase_processed_data.xlsx'

            with pd.ExcelWriter(output_file, engine='openpyxl') as writer:
                if not hist_sales.empty:
                    hist_sales.to_excel(writer, sheet_name='Historical Sales (Processed)', index=False)
                    print(f"✅ Historical Sales: {len(hist_sales)} rows")

                if not store_inv.empty:
                    store_inv.to_excel(writer, sheet_name='Store Inventory (Processed)', index=False)
                    print(f"✅ Store Inventory: {len(store_inv)} rows")

            print(f"✅ Exported combined data to: {output_file}")

            # Show what will be merged with line_details
            print("\n📊 Historical Sales (What gets merged):")
            if not hist_sales.empty:
                print(hist_sales.to_string(index=False))
            else:
                print("   (empty)")

            print("\n🏪 Store Inventory (What gets merged):")
            if not store_inv.empty:
                print(store_inv.to_string(index=False))
            else:
                print("   (empty)")

        else:
            print("⚠️  No processed data to export")

    except Exception as e:
        print(f"❌ Error exporting processed data: {e}")

    print("\n" + "=" * 80)
    print("EXPORT COMPLETE")
    print("=" * 80)
    print(f"\nFiles created in: {output_dir}")
    print("1. sales_performance_data.xlsx - Raw sales performance data from Supabase")
    print("2. inventory_snapshots_data.xlsx - Raw inventory snapshots from Supabase")
    print("3. supabase_processed_data.xlsx - Processed data as used by the app")

if __name__ == "__main__":
    main()
