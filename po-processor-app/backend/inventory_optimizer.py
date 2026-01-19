import pandas as pd
import logging
from typing import List, Dict, Any, Tuple

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class InventoryOptimizer:
    def __init__(self, data_transformer):
        self.transformer = data_transformer

    def optimize_allocations(self, 
                           line_details: pd.DataFrame, 
                           historical_sales: pd.DataFrame, 
                           store_inventory: pd.DataFrame) -> Tuple[pd.DataFrame, List[str]]:
        """
        Run inventory optimization logic.
        
        Updates 'product_uom_qty', 'flagged', and 'flag_reason' in line_details.
        """
        if line_details.empty:
            return line_details, []

        logs = []
        
        # Merge external data
        # Ensure common keys for merging
        # historical_sales must have 'product_id' (str IntRef or Odoo ID? Supabase client returns what?)
        # Supabase client should probably return Internal Reference to match easily, OR we rely on Odoo product ID?
        # User's supabase data usually uses Internal Reference as key? 
        # Let's assume Supabase uses Internal Reference from 'product_id' column for now as verified in implementation plan "product_id TEXT"
        
        # line_details has 'internal_reference'
        
        # Ensure type consistency for merging keys
        line_details['internal_reference'] = line_details['internal_reference'].astype(str)
        # Store ID is safer as int, but might be float if NaN present?
        # We already handled NaNs in data_transformer, so we can cast to int safely if no NaNs.
        # But safest is to use 'Int64' nullable int or just standard int if we are sure.
        # Let's handle safe conversion
        line_details['store_id'] = pd.to_numeric(line_details['store_id'], errors='coerce').fillna(0).astype(int)
        
        # Merge Historical Sales (Avg)
        if not historical_sales.empty:
            # Client now returns: internal_reference, store_id, avg_monthly_sales
            cols_to_rename = {'avg_monthly_sales': 'hist_avg_sales'}
            if 'product_id' in historical_sales.columns:
                 cols_to_rename['product_id'] = 'internal_reference'

            hist_merge = historical_sales.rename(columns=cols_to_rename)

            # Ensure type consistency before merge
            hist_merge['internal_reference'] = hist_merge['internal_reference'].astype(str)
            hist_merge['store_id'] = hist_merge['store_id'].astype(int)

            # Drop existing column to avoid suffixing (_x, _y)
            if 'hist_avg_sales' in line_details.columns:
                line_details = line_details.drop(columns=['hist_avg_sales'])

            logger.info(f"Merging historical sales: {len(hist_merge)} rows")
            line_details = pd.merge(
                line_details,
                hist_merge[['internal_reference', 'store_id', 'hist_avg_sales']],
                on=['internal_reference', 'store_id'],
                how='left'
            )
        else:
            # Ensure column exists if not merging
            if 'hist_avg_sales' not in line_details.columns:
                line_details['hist_avg_sales'] = 0

        # Merge Store Inventory
        if not store_inventory.empty:
            # Client now returns: internal_reference, store_id, store_on_hand
            cols_to_rename = {'quantity': 'store_on_hand'}
            if 'product_id' in store_inventory.columns:
                cols_to_rename['product_id'] = 'internal_reference'

            inv_merge = store_inventory.rename(columns=cols_to_rename)

            # Ensure type consistency before merge
            inv_merge['internal_reference'] = inv_merge['internal_reference'].astype(str)
            inv_merge['store_id'] = inv_merge['store_id'].astype(int)

            # Drop existing column to avoid suffixing
            if 'store_on_hand' in line_details.columns:
                line_details = line_details.drop(columns=['store_on_hand'])

            logger.info(f"Merging store inventory: {len(inv_merge)} rows")
            line_details = pd.merge(
                line_details,
                inv_merge[['internal_reference', 'store_id', 'store_on_hand']],
                on=['internal_reference', 'store_id'],
                how='left'
            )
        else:
             # Ensure column exists if not merging
            if 'store_on_hand' not in line_details.columns:
                line_details['store_on_hand'] = 0
            
        # Fill NaNs
        line_details['hist_avg_sales'] = line_details['hist_avg_sales'].fillna(0)
        line_details['store_on_hand'] = line_details['store_on_hand'].fillna(0) # Treat missing as 0 (or unknown)

        # Optimization Logic per Warehouse
        for warehouse in ['CE', 'CW']:
            wh_lines = line_details[line_details['warehouse'] == warehouse]
            if wh_lines.empty:
                continue
                
            # Group by product to check total demand vs available
            product_groups = wh_lines.groupby('internal_reference')
            
            for ref, group in product_groups:
                total_demand = group['product_uom_qty'].sum()
                
                # Get available qty for this product (all lines for same product share same odoo_available)
                available_qty = group['odoo_available'].iloc[0]
                on_hand_qty = group['odoo_on_hand'].iloc[0]
                
                # Check 1: Zero/Negative Inventory (Flagging)
                if on_hand_qty <= 0:
                    line_details.loc[group.index, 'flagged'] = True
                    line_details.loc[group.index, 'flag_reason'] = "0/- On Hand"
                    continue # Skip allocation if we have none on hand? Or optimize what's available?
                    # User said: "products with zero or negative inventory please automatically delete [Flag]"
                    # If <= 0, we flag.
                
                # Check 2: Shortage based on Available
                if total_demand > available_qty:
                    # Shortage Scenario
                    shortage = total_demand - available_qty

                    if available_qty <= 0:
                         # Available is 0 or negative, but On Hand might be positive (reserved elsewhere)
                         # Flag these too
                        line_details.loc[group.index, 'flagged'] = True
                        extra_msg = f"Available: {available_qty}"
                        current_reason = line_details.loc[group.index[0], 'flag_reason']
                        reason = f"{current_reason}, {extra_msg}" if current_reason else f"Negative Available: {available_qty}"
                        line_details.loc[group.index, 'flag_reason'] = reason
                        continue

                    # User requested: Keep original quantities, just add flag_reason for tracking
                    # Don't modify quantities or flag items - just note the shortage
                    for idx, row in group.iterrows():
                        original = row['product_uom_qty']
                        line_details.loc[idx, 'flag_reason'] = "Shortage"
                        line_details.loc[idx, 'shortage_details'] = f"D: {int(total_demand)}, Ava: {int(available_qty)}"

        return line_details, logs
