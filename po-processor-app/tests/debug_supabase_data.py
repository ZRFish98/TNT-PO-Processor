"""
Debug script to check Supabase data matching for T&T PO 260117110311.PDF

This script helps identify why store_on_hand and hist_avg_sales are showing zeros.
"""

import sys
import os
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.supabase_client import SupabaseClient
from dotenv import load_dotenv
import pandas as pd

# Load environment
load_dotenv()

# Test data from the PDF
TEST_SKUS = ['535553', '538752', '538753', '532827', '537861', '537868', '519279', '755734']
TEST_STORES = [20, 28, 37]  # Weldrick-020, Waterloo-028, London-037

def main():
    # Initialize Supabase
    supabase_url = os.getenv('SUPABASE_URL', 'https://zzxfwmgftwojhmuhkrrp.supabase.co')
    supabase_key = os.getenv('SUPABASE_KEY')

    if not supabase_key:
        print("❌ ERROR: SUPABASE_KEY not found in .env file")
        return

    print("=" * 80)
    print("🔍 SUPABASE DATA DEBUGGING")
    print("=" * 80)

    client = SupabaseClient(supabase_url, supabase_key)

    if not client.connect():
        print("❌ Failed to connect to Supabase")
        return

    print("✅ Connected to Supabase\n")

    # Step 1: Check Products Table
    print("-" * 80)
    print("STEP 1: Checking products table for SKUs from PDF")
    print("-" * 80)
    print(f"Looking for SKUs: {TEST_SKUS}\n")

    try:
        response = client.client.table('products')\
            .select('id, item_id')\
            .in_('item_id', TEST_SKUS)\
            .execute()

        if response.data:
            df_products = pd.DataFrame(response.data)
            print(f"✅ Found {len(df_products)} products:")
            print(df_products.to_string(index=False))
            print()

            found_skus = set(df_products['item_id'].tolist())
            missing_skus = set(TEST_SKUS) - found_skus
            if missing_skus:
                print(f"⚠️  Missing SKUs in products table: {missing_skus}")
        else:
            print("❌ No products found! This is the root cause.")
            print("   The 'products' table doesn't have any matching item_id values.")
            return
    except Exception as e:
        print(f"❌ Error querying products table: {e}")
        return

    # Step 2: Check Stores Table
    print("\n" + "-" * 80)
    print("STEP 2: Checking stores table for store numbers from PDF")
    print("-" * 80)
    print(f"Looking for stores: {TEST_STORES}\n")

    try:
        response = client.client.table('stores')\
            .select('id, store_number')\
            .in_('store_number', TEST_STORES)\
            .execute()

        if response.data:
            df_stores = pd.DataFrame(response.data)
            print(f"✅ Found {len(df_stores)} stores:")
            print(df_stores.to_string(index=False))
            print()

            found_stores = set(df_stores['store_number'].tolist())
            missing_stores = set(TEST_STORES) - found_stores
            if missing_stores:
                print(f"⚠️  Missing stores in stores table: {missing_stores}")
        else:
            print("❌ No stores found! This is another root cause.")
            print("   The 'stores' table doesn't have any matching store_number values.")
            return
    except Exception as e:
        print(f"❌ Error querying stores table: {e}")
        return

    # Step 3: Check Sales Performance
    print("\n" + "-" * 80)
    print("STEP 3: Checking sales_performance table")
    print("-" * 80)

    product_ids = df_products['id'].tolist()
    store_ids = df_stores['id'].tolist()

    print(f"Looking for product_ids: {product_ids}")
    print(f"Looking for store_ids: {store_ids}\n")

    try:
        response = client.client.table('sales_performance')\
            .select('*')\
            .in_('product_id', product_ids)\
            .in_('store_id', store_ids)\
            .execute()

        if response.data:
            df_sales = pd.DataFrame(response.data)
            print(f"✅ Found {len(df_sales)} sales records:")
            print(df_sales.to_string(index=False))
        else:
            print("⚠️  No sales performance data found for these product/store combinations.")
            print("   This means hist_avg_sales will be 0 for all items.")
    except Exception as e:
        print(f"❌ Error querying sales_performance table: {e}")

    # Step 4: Check Inventory Snapshots
    print("\n" + "-" * 80)
    print("STEP 4: Checking inventory_snapshots table")
    print("-" * 80)

    try:
        response = client.client.table('inventory_snapshots')\
            .select('*')\
            .in_('product_id', product_ids)\
            .in_('store_id', store_ids)\
            .execute()

        if response.data:
            df_inventory = pd.DataFrame(response.data)
            print(f"✅ Found {len(df_inventory)} inventory records:")
            print(df_inventory.to_string(index=False))
        else:
            print("⚠️  No inventory snapshot data found for these product/store combinations.")
            print("   This means store_on_hand will be 0 for all items.")
    except Exception as e:
        print(f"❌ Error querying inventory_snapshots table: {e}")

    # Step 5: Test the actual client methods
    print("\n" + "=" * 80)
    print("STEP 5: Testing SupabaseClient methods (as used in the app)")
    print("=" * 80)

    print("\n📊 get_historical_sales():")
    hist_sales = client.get_historical_sales(TEST_STORES, TEST_SKUS)
    if not hist_sales.empty:
        print(hist_sales.to_string(index=False))
    else:
        print("❌ Empty DataFrame returned")

    print("\n🏪 get_store_inventory():")
    store_inv = client.get_store_inventory(TEST_STORES, TEST_SKUS)
    if not store_inv.empty:
        print(store_inv.to_string(index=False))
    else:
        print("❌ Empty DataFrame returned")

    print("\n" + "=" * 80)
    print("SUMMARY")
    print("=" * 80)
    print("\nPossible causes for zeros in store_on_hand and hist_avg_sales:")
    print("1. ❌ SKUs don't exist in 'products' table (item_id column)")
    print("2. ❌ Store numbers don't exist in 'stores' table (store_number column)")
    print("3. ❌ No matching records in 'sales_performance' table")
    print("4. ❌ No matching records in 'inventory_snapshots' table")
    print("5. ⚠️  Column name mismatches (check if item_id vs sku, store_number vs store_id)")
    print("\nCheck the output above to see which case applies.")
    print("=" * 80)

if __name__ == "__main__":
    main()
