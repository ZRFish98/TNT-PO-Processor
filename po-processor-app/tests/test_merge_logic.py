"""
Test the merge logic between line_details and Supabase data
"""

import sys
import os
from pathlib import Path
import pandas as pd

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.supabase_client import SupabaseClient
from backend.inventory_optimizer import InventoryOptimizer
from backend.data_transformer import DataTransformer
from dotenv import load_dotenv
import yaml

# Load environment
load_dotenv()

# Test data - simulating what line_details would look like
SAMPLE_LINE_DETAILS = pd.DataFrame([
    {
        'internal_reference': '535553',
        'store_id': 20,
        'product_uom_qty': 150,
        'warehouse': 'CE',
        'odoo_on_hand': 1000,
        'odoo_available': 800,
        'flagged': False,
        'flag_reason': ''
    },
    {
        'internal_reference': '538752',
        'store_id': 28,
        'product_uom_qty': 6,
        'warehouse': 'CE',
        'odoo_on_hand': 500,
        'odoo_available': 400,
        'flagged': False,
        'flag_reason': ''
    },
    {
        'internal_reference': '519279',
        'store_id': 20,
        'product_uom_qty': 30,
        'warehouse': 'CE',
        'odoo_on_hand': 200,
        'odoo_available': 150,
        'flagged': False,
        'flag_reason': ''
    }
])

def main():
    print("=" * 80)
    print("TESTING MERGE LOGIC")
    print("=" * 80)

    # Initialize clients
    supabase_url = os.getenv('SUPABASE_URL', 'https://zzxfwmgftwojhmuhkrrp.supabase.co')
    supabase_key = os.getenv('SUPABASE_KEY')

    if not supabase_key:
        print("❌ ERROR: SUPABASE_KEY not found")
        return

    supabase_client = SupabaseClient(supabase_url, supabase_key)

    if not supabase_client.connect():
        print("❌ Failed to connect to Supabase")
        return

    # Load settings
    settings_path = Path(__file__).parent.parent / 'config' / 'settings.yaml'
    with open(settings_path, 'r') as f:
        settings = yaml.safe_load(f)

    # Create test line_details DataFrame
    line_details = SAMPLE_LINE_DETAILS.copy()

    print("\n" + "-" * 80)
    print("INITIAL LINE DETAILS")
    print("-" * 80)
    print(line_details)
    print("\nColumn types:")
    print(line_details.dtypes)

    # Fetch Supabase data
    refs = line_details['internal_reference'].unique().tolist()
    store_ids = line_details['store_id'].unique().tolist()

    print("\n" + "-" * 80)
    print("FETCHING SUPABASE DATA")
    print("-" * 80)
    print(f"SKUs: {refs}")
    print(f"Store IDs: {store_ids}")

    hist_sales = supabase_client.get_historical_sales(store_ids, refs)
    store_inv = supabase_client.get_store_inventory(store_ids, refs)

    print("\n📊 Historical Sales:")
    if not hist_sales.empty:
        print(hist_sales)
        print("\nColumn types:")
        print(hist_sales.dtypes)
    else:
        print("Empty DataFrame")

    print("\n🏪 Store Inventory:")
    if not store_inv.empty:
        print(store_inv)
        print("\nColumn types:")
        print(store_inv.dtypes)
    else:
        print("Empty DataFrame")

    # Run optimizer
    print("\n" + "-" * 80)
    print("RUNNING INVENTORY OPTIMIZER")
    print("-" * 80)

    transformer = DataTransformer(settings)
    optimizer = InventoryOptimizer(transformer)

    optimized_lines, logs = optimizer.optimize_allocations(
        line_details,
        hist_sales,
        store_inv
    )

    print("\n✅ OPTIMIZED LINE DETAILS")
    print("-" * 80)
    print(optimized_lines[['internal_reference', 'store_id', 'hist_avg_sales', 'store_on_hand', 'product_uom_qty', 'flagged']])

    # Verify the merge worked
    print("\n" + "=" * 80)
    print("VERIFICATION")
    print("=" * 80)

    has_data = (optimized_lines['hist_avg_sales'] > 0).any() or (optimized_lines['store_on_hand'] > 0).any()

    if has_data:
        print("✅ SUCCESS: Merge worked! Data populated in hist_avg_sales and/or store_on_hand")
    else:
        print("❌ FAILURE: All zeros in hist_avg_sales and store_on_hand")
        print("\nDebugging info:")
        print("\nline_details types after optimizer processing:")
        print(optimized_lines[['internal_reference', 'store_id']].dtypes)
        print("\nFirst few rows of relevant columns:")
        print(optimized_lines[['internal_reference', 'store_id', 'hist_avg_sales', 'store_on_hand']].head())

if __name__ == "__main__":
    main()
