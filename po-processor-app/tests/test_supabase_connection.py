import os
import sys
from dotenv import load_dotenv
import pandas as pd

# Add parent dir to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from backend.supabase_client import SupabaseClient

import yaml

def load_settings():
    try:
        with open('config/settings.yaml', 'r') as f:
            return yaml.safe_load(f)
    except FileNotFoundError:
        return {}

def test_connection():
    load_dotenv()
    settings = load_settings()
    
    url = os.getenv("SUPABASE_URL") or settings.get('supabase', {}).get('url')
    key = os.getenv("SUPABASE_KEY")
    
    if not url or not key:
        print(f"❌ Missing Supabase Credentials. URL: {url}, Key: {'[HIDDEN]' if key else 'None'}")
        return

    print(f"Connecting to {url}...")
    client = SupabaseClient(url, key)
    if client.connect():
        print("✅ Connected to Supabase!")
        
        # Test 1: Fetch Stores Map
        # Pick a store number we know exists (e.g., 22)
        print("\nTesting Store Map...")
        store_map = client._get_store_id_map([22])
        print(f"Store Map Result: {store_map}")
        
        # Test 2: Fetch Sales Performance
        # Use known SKU if possible, or just test empty
        print("\nTesting Historical Sales...")
        # We'll use a dummy SKU if we don't know one, 
        # or list products first?
        # Let's try to query products table to get a valid Item ID
        try:
            prod_resp = client.client.table('products').select('item_id').limit(1).execute()
            if prod_resp.data:
                test_sku = prod_resp.data[0]['item_id']
                print(f"Found Test SKU: {test_sku}")
                
                sales = client.get_historical_sales([22], [test_sku])
                print(f"Sales Data:\n{sales}")
                
                inv = client.get_store_inventory([22], [test_sku])
                print(f"Inventory Data:\n{inv}")
            else:
                print("⚠️ No products found in DB to test with.")
        except Exception as e:
            print(f"⚠️ Error querying products for test: {e}")

    else:
        print("❌ Failed to connect.")

if __name__ == "__main__":
    test_connection()
