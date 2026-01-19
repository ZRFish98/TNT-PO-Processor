"""
Test complete flow from PDF extraction to optimization with real data
"""

import sys
import os
from pathlib import Path
import pandas as pd

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.pdf_extractor import PDFExtractor
from backend.data_transformer import DataTransformer
from backend.inventory_optimizer import InventoryOptimizer
from backend.supabase_client import SupabaseClient
from backend.odoo_client import OdooClient
from dotenv import load_dotenv
import yaml

load_dotenv()

def main():
    print("=" * 80)
    print("COMPLETE FLOW TEST: PDF → Transform → Optimize")
    print("=" * 80)

    # Load settings
    settings_path = Path(__file__).parent.parent / 'config' / 'settings.yaml'
    with open(settings_path, 'r') as f:
        settings = yaml.safe_load(f)

    # Initialize clients
    print("\n1. Initializing clients...")

    odoo_client = OdooClient(
        url=os.getenv('ODOO_URL', 'https://one-team-worldwide-ltd.odoo.com'),
        db=os.getenv('ODOO_DB', 'one-team-worldwide-ltd'),
        username=os.getenv('ODOO_USERNAME'),
        api_key=os.getenv('ODOO_API_KEY')
    )

    if not odoo_client.connect():
        print("❌ Failed to connect to Odoo")
        return

    supabase_client = SupabaseClient(
        url=os.getenv('SUPABASE_URL', 'https://zzxfwmgftwojhmuhkrrp.supabase.co'),
        key=os.getenv('SUPABASE_KEY')
    )

    if not supabase_client.connect():
        print("❌ Failed to connect to Supabase")
        return

    print("✅ Connected to Odoo and Supabase")

    # Extract PDF
    print("\n2. Extracting PDF...")
    pdf_path = Path(__file__).parent.parent.parent / 'T&T PO 260117110311.PDF'

    if not pdf_path.exists():
        print(f"❌ PDF not found at {pdf_path}")
        return

    extractor = PDFExtractor()
    po_data = extractor.process_multiple_pdfs([str(pdf_path)])

    if po_data.empty:
        print("❌ No data extracted from PDF")
        return

    print(f"✅ Extracted {len(po_data)} lines from PDF")
    print(f"   Stores: {po_data['Store ID'].unique().tolist()}")
    print(f"   SKUs: {po_data['Internal Reference'].unique().tolist()}")

    # Transform
    print("\n3. Fetching Odoo products and transforming...")
    refs = po_data['Internal Reference'].unique().tolist()
    products = odoo_client.get_products(internal_references=refs)

    if not products:
        print("❌ No products fetched from Odoo")
        return

    print(f"✅ Fetched {len(products)} products from Odoo")

    transformer = DataTransformer(settings)
    summaries, line_details, errors = transformer.transform_data(po_data, products)

    if line_details.empty:
        print("❌ No line details generated")
        return

    print(f"✅ Transformed into {len(line_details)} line details")

    print("\n   BEFORE OPTIMIZATION:")
    print(f"   Columns: {line_details.columns.tolist()}")
    print(f"   Sample data:")
    print(line_details[['internal_reference', 'store_id', 'store_on_hand', 'hist_avg_sales']].head())

    # Optimize
    print("\n4. Running inventory optimization...")

    refs_for_supabase = line_details['internal_reference'].unique().tolist()
    stores_for_supabase = line_details['store_id'].unique().tolist()

    print(f"   Fetching Supabase data for:")
    print(f"   - Stores: {stores_for_supabase}")
    print(f"   - SKUs: {refs_for_supabase}")

    hist_sales = supabase_client.get_historical_sales(stores_for_supabase, refs_for_supabase)
    store_inv = supabase_client.get_store_inventory(stores_for_supabase, refs_for_supabase)

    print(f"\n   Supabase results:")
    print(f"   - Historical sales: {len(hist_sales)} rows")
    if not hist_sales.empty:
        print(f"     {hist_sales.head()}")

    print(f"   - Store inventory: {len(store_inv)} rows")
    if not store_inv.empty:
        print(f"     {store_inv.head()}")

    optimizer = InventoryOptimizer(transformer)
    optimized_lines, logs = optimizer.optimize_allocations(line_details, hist_sales, store_inv)

    print(f"\n✅ Optimization complete")

    print("\n   AFTER OPTIMIZATION:")
    print(f"   Sample data:")
    result = optimized_lines[['internal_reference', 'store_id', 'store_on_hand', 'hist_avg_sales', 'product_uom_qty', 'flagged']].head(15)
    print(result.to_string())

    # Verification
    print("\n" + "=" * 80)
    print("VERIFICATION")
    print("=" * 80)

    non_zero_sales = (optimized_lines['hist_avg_sales'] > 0).sum()
    non_zero_inv = (optimized_lines['store_on_hand'] > 0).sum()

    print(f"Lines with non-zero hist_avg_sales: {non_zero_sales} / {len(optimized_lines)}")
    print(f"Lines with non-zero store_on_hand: {non_zero_inv} / {len(optimized_lines)}")

    if non_zero_sales > 0 or non_zero_inv > 0:
        print("\n✅ SUCCESS: Data was merged correctly!")
    else:
        print("\n❌ FAILURE: All values are still zero")
        print("\nDebugging info:")
        print(f"Line details types: {optimized_lines[['internal_reference', 'store_id']].dtypes}")
        print(f"Hist sales types: {hist_sales.dtypes if not hist_sales.empty else 'Empty'}")
        print(f"Store inv types: {store_inv.dtypes if not store_inv.empty else 'Empty'}")

if __name__ == "__main__":
    main()
