
import pandas as pd
import sys
import os

# Add parent dir to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from backend.inventory_optimizer import InventoryOptimizer

# Mock DataTransformer (not used in this specific function logic really, just for init)
class MockTransformer:
    pass

def test_optimization_merge():
    # 1. Setup Data
    line_details = pd.DataFrame({
        'store_id': [1, 1],
        'internal_reference': ['REF001', 'REF002'],
        'warehouse': ['CE', 'CE'],
        'product_uom_qty': [10, 20],
        'odoo_available': [100, 100],
        'odoo_on_hand': [100, 100],
        # Pre-existing columns initialized by frontend
        'store_on_hand': [0, 0], 
        'hist_avg_sales': [0, 0]
    })
    
    historical_sales = pd.DataFrame({
        'product_id': ['REF001'],
        'store_id': [1],
        'avg_monthly_sales': [50]
    })
    
    store_inventory = pd.DataFrame({
        'product_id': ['REF002'],
        'store_id': [1],
        'quantity': [5]
    })
    
    # 2. Run Optimization
    optimizer = InventoryOptimizer(MockTransformer())
    result_df, logs = optimizer.optimize_allocations(line_details, historical_sales, store_inventory)
    
    # 3. Assertions
    print("Columns:", result_df.columns.tolist())
    
    # Check for suffixing
    if 'store_on_hand_x' in result_df.columns or 'store_on_hand_y' in result_df.columns:
        print("FAILED: Suffix columns found!")
        sys.exit(1)
        
    if 'hist_avg_sales_x' in result_df.columns:
        print("FAILED: Suffix columns found for sales!")
        sys.exit(1)
        
    # Check for correct merging
    # REF001 should have hist_sales=50, store_inv=0 (default fillna)
    row1 = result_df[result_df['internal_reference'] == 'REF001'].iloc[0]
    if row1['hist_avg_sales'] != 50.0:
        print(f"FAILED: Expected hist_avg_sales 50, got {row1['hist_avg_sales']}")
        sys.exit(1)
        
    # REF002 should have store_on_hand=5
    row2 = result_df[result_df['internal_reference'] == 'REF002'].iloc[0]
    if row2['store_on_hand'] != 5.0:
        print(f"FAILED: Expected store_on_hand 5, got {row2['store_on_hand']}")
        sys.exit(1)
        
    print("SUCCESS: Merge logic works correctly.")

if __name__ == "__main__":
    test_optimization_merge()
