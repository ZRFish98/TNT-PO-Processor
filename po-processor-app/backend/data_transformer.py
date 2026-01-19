import pandas as pd
import logging
from typing import List, Dict, Tuple, Any
from models.schemas import ProductVariant
import yaml

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class DataTransformer:
    def __init__(self, settings: Dict[str, Any]):
        self.settings = settings
        self.cw_stores = set(settings.get('warehouse_mapping', {}).get('cw_stores', []))

    def get_warehouse_for_store(self, store_id: int) -> str:
        """Determines if store is CE or CW"""
        try:
            store_id = int(store_id)
        except:
            return 'CE' # Default safety
            
        if store_id in self.cw_stores:
            return 'CW'
        return 'CE'

    def transform_data(self, 
                     po_df: pd.DataFrame, 
                     product_variants: List[Dict[str, Any]],
                     starting_so_ref: int = 391) -> Tuple[pd.DataFrame, pd.DataFrame, List[str]]:
        """
        Transform extracted PO data into Odoo-ready format.
        
        Args:
            po_df: DataFrame containing extracted PDF data
            product_variants: List of product dictionaries from Odoo API
        
        Returns:
            order_summaries: DataFrame (headers)
            order_line_details: DataFrame (lines)
            errors: List of error strings
        """
        errors = []
        
        if po_df.empty:
            return pd.DataFrame(), pd.DataFrame(), ["No PO data to transform"]

        # Convert products to DataFrame for easier lookups
        products_df = pd.DataFrame(product_variants)
        if products_df.empty:
            return pd.DataFrame(), pd.DataFrame(), ["No product data fetched from Odoo"]
            
        # Ensure 'default_code' is string
        products_df['default_code'] = products_df['default_code'].astype(str)
        po_df['Internal Reference'] = po_df['Internal Reference'].astype(str)
        
        # Check for multi-product references
        ref_counts = products_df['default_code'].value_counts()
        multi_refs = ref_counts[ref_counts > 1].index.tolist()
        
        if multi_refs:
            logger.info(f"Found {len(multi_refs)} internal references with multiple products")

        expanded_lines = []
        
        # Process each PO line
        for _, row in po_df.iterrows():
            internal_ref = row['Internal Reference']
            
            # Find matching product(s)
            matched_products = products_df[products_df['default_code'] == internal_ref]
            
            if matched_products.empty:
                # User requirement: Remove line if product not found
                errors.append(f"Product not found in Odoo: {internal_ref} (Store: {row['Store ID']}, PO: {row['PO No.']})")
                continue

            # Handle Logic
            total_ordered_qty = row['# of Order']
            po_price = row['Price'] # Total price for this line in PO?
            
            # Check if multi-product
            num_products = len(matched_products)
            
            # Logic from original Odoo-T-T-Test:
            # If multiple products share same IntRef, split the order
            
            units_per_product_share = 0
            
            # This logic assumes 'Units Per Order' is present in Odoo data (x_studio_tt_om_int)
            # If missing, deafult to 1?
            
            for idx, (_, product) in enumerate(matched_products.iterrows()):
                units_per_order = product.get('x_studio_tt_om_int') or 1.0
                
                # Split logic
                if num_products > 1:
                    # Distribute total units equally? 
                    # Original code: total_units = row['# of Order'] * products.iloc[0]['Units Per Order']
                    # units_per_product = total_units / len(products)
                    # This implies the PO QTY is "cases", and we convert to "units" then split units?
                    
                    # Re-evaluating original logic:
                    # total_units = row['# of Order'] * matched_products.iloc[0]['x_studio_tt_om_int']
                    # This assumes all variants have same Units Per Order, or uses first one's.
                    # Let's follow implementation plan: calculate correct quantities.
                    
                    # Safe bet: assume PO Qty is 'Cases'. Odoo Qty is 'Units'.
                    # Total Units = PO Qty * Units Per Case
                    
                    base_units_per_case = matched_products.iloc[0].get('x_studio_tt_om_int', 1.0)
                    total_units_bundle = total_ordered_qty * base_units_per_case
                    
                    # Split units
                    units_this_product = int(total_units_bundle / num_products)
                    if idx == 0:
                        units_this_product += int(total_units_bundle % num_products)
                        
                    # Calculate unit price
                    # Original code: unit_price = row['Price'] / product['Units Per Order']
                    # Wait, if row['Price'] is Total Price for line...
                    # Or is it Unit Price per Case?
                    # PDF extractor extract says: price = numeric_values[-2] or [-1].
                    # Usually PO has Qty, Unit Price, Extension.
                    # App.py 153: price = numeric_values[-2] # Unit price
                    # So row['Price'] is Unit Price per Case.
                    
                    # Odoo needs Unit Price per Unit.
                    # unit_price = row['Price'] / base_units_per_case
                    
                    unit_price_per_item = po_price / base_units_per_case
                    
                else:
                    # Single product
                    units_per_order = product.get('x_studio_tt_om_int') or 1.0
                    total_units_bundle = total_ordered_qty * units_per_order
                    units_this_product = total_units_bundle
                    unit_price_per_item = po_price / units_per_order
                
                # Inventory data (Odoo)
                wh = self.get_warehouse_for_store(row['Store ID'])
                if wh == 'CE':
                    on_hand = product.get('x_studio_canada_east_on_hand', 0)
                    available = product.get('x_studio_ce_available', 0)
                else:
                    on_hand = product.get('x_studio_canada_west_on_hand', 0)
                    available = product.get('x_studio_cw_available', 0)

                line_data = {
                    'store_id': row['Store ID'],
                    'store_name': row['Store Name'],
                    'order_date': row.get('Order Date', ''),
                    'delivery_date': row.get('Delivery Date', ''),
                    'po_number': str(row['PO No.']).replace('.0', '') if pd.notna(row['PO No.']) else '',
                    'internal_reference': internal_ref,
                    'product_id': product['id'],
                    'product_name': product['name'],
                    'barcode': product.get('barcode'),
                    'product_image': product.get('image_1920'),  # Product image
                    'warehouse': wh,
                    'units_per_order': units_per_order,
                    'original_qty': total_ordered_qty,
                    'po_unit_price': po_price,

                    # Calculated for Odoo
                    'product_uom_qty': units_this_product,
                    'price_unit': unit_price_per_item,
                    'total_price': units_this_product * unit_price_per_item,

                    # Inventory Snapshot
                    'odoo_on_hand': on_hand,
                    'odoo_available': available,

                    # Placeholder for Supabase data (filled by Optimizer later)
                    'store_on_hand': 0,
                    'hist_avg_sales': 0,

                    'flagged': False,
                    'flag_reason': None,
                    'shortage_details': None
                }
                
                expanded_lines.append(line_data)

        order_line_details = pd.DataFrame(expanded_lines)
        
        # Create Summaries (Headers) and Assign SO Refs
        # Group by Store ID
        summaries = []
        
        # We need to map Store ID -> SO Ref to add back to lines
        store_so_map = {}
        
        if not order_line_details.empty:
            # Filter out invalid Store IDs (NaN, 0, etc)
            order_line_details = order_line_details.dropna(subset=['store_id'])
            
            # Sort by Store ID for consistent numbering
            unique_stores = sorted(order_line_details['store_id'].unique().tolist())
            
            for index, store_id in enumerate(unique_stores):
                # Generate SO Reference
                current_ref_num = starting_so_ref + index
                so_ref = f"OATS00{current_ref_num}"
                store_so_map[store_id] = so_ref
                
                group = order_line_details[order_line_details['store_id'] == store_id]
                wh = self.get_warehouse_for_store(store_id)
                
                # Store Mapping from settings
                tt_names = self.settings.get('tt_store_names', {})
                official_name = tt_names.get(int(store_id), f"Store {store_id}")
                
                # Group PO Numbers
                po_list = sorted(group['po_number'].unique().astype(str).tolist())
                po_numbers_str = ", ".join(po_list)

                # Get Order Date and Delivery Date from first line in group
                order_date = group['order_date'].iloc[0] if not group['order_date'].empty else ''
                delivery_date = group['delivery_date'].iloc[0] if not group['delivery_date'].empty else ''

                summaries.append({
                    'store_id': store_id,
                    'store_name': group['store_name'].iloc[0],
                    'official_name': official_name,
                    'warehouse': wh,
                    'po_count': len(po_list),
                    'po_numbers': po_numbers_str, # User requested PO Number here
                    'so_reference': so_ref,       # Generated SO Ref
                    'order_date': order_date,
                    'delivery_date': delivery_date,
                    'total_lines': len(group),
                    'total_value': group['total_price'].sum()
                })
                
            # Add SO Reference back to Line Details
            order_line_details['so_reference'] = order_line_details['store_id'].map(store_so_map)

        order_summaries = pd.DataFrame(summaries)
        
        return order_summaries, order_line_details, errors

