import streamlit as st
import pandas as pd
import yaml
import os
import sys
from datetime import datetime
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Add parent directory to path to import backend modules
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from backend.odoo_client import OdooClient
from backend.supabase_client import SupabaseClient
from backend.pdf_extractor import PDFExtractor
from backend.data_transformer import DataTransformer
from backend.inventory_optimizer import InventoryOptimizer
from backend.inventory_optimizer import InventoryOptimizer

# Page Config
st.set_page_config(
    page_title="T&T PO Processor",
    page_icon="🛒",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Load Settings
@st.cache_resource
def load_settings():
    try:
        with open('config/settings.yaml', 'r') as f:
            return yaml.safe_load(f)
    except FileNotFoundError:
        return {}

settings = load_settings()



# Initialize Session State
if 'current_page' not in st.session_state:
    st.session_state['current_page'] = "Configuration"
if 'odoo_client' not in st.session_state:
    st.session_state['odoo_client'] = None
if 'supabase_client' not in st.session_state:
    st.session_state['supabase_client'] = None
if 'extracted_po_data' not in st.session_state:
    st.session_state['extracted_po_data'] = pd.DataFrame()
if 'po_errors' not in st.session_state:
    st.session_state['po_errors'] = []
if 'order_summaries' not in st.session_state:
    st.session_state['order_summaries'] = pd.DataFrame()
if 'line_details' not in st.session_state:
    st.session_state['line_details'] = pd.DataFrame()
if 'transform_errors' not in st.session_state:
    st.session_state['transform_errors'] = []



# Sidebar Navigation
st.sidebar.title("Navigation")
page = st.sidebar.radio("Go to",
    ["Configuration",
     "Upload & Extract", 
     "Transform & Review", 
     "Inventory Optimization", 
     "Review & Import"],
    index=["Configuration", "Upload & Extract", "Transform & Review", "Inventory Optimization", "Review & Import"].index(st.session_state['current_page'])
)

def next_page(page_name):
    st.session_state['current_page'] = page_name
    st.rerun()

# --- Page 1: Configuration ---
if page == "Configuration":


    st.title("⚙️ Configuration")

    col1, col2 = st.columns(2)
    
    with col1:
        st.subheader("Odoo Connection")
        # Initialize session state for config if not present
        if 'config_odoo_url' not in st.session_state:
            st.session_state['config_odoo_url'] = settings.get('odoo', {}).get('default_url', '')
        if 'config_odoo_db' not in st.session_state:
            st.session_state['config_odoo_db'] = settings.get('odoo', {}).get('default_db', '')
        if 'config_odoo_user' not in st.session_state:
            st.session_state['config_odoo_user'] = settings.get('odoo', {}).get('default_user', 'official@atiara.ca')
        # API Key is sensitive, maybe don't persist in session state explicitly or use env?
        # Streamlit handles key persistence automatically if key is provided.
        
        odoo_url = st.text_input("Odoo URL", key='config_odoo_url')
        odoo_db = st.text_input("Database", key='config_odoo_db')
        odoo_user = st.text_input("Username", key='config_odoo_user')
        odoo_key = st.text_input("API Key", type="password", value=os.getenv('ODOO_API_KEY', ''), key='config_odoo_key')
        
        if st.button("Connect Odoo"):
            client = OdooClient(odoo_url, odoo_db, odoo_user, odoo_key)
            if client.connect():
                st.session_state['odoo_client'] = client
                st.success("✅ Connected to Odoo")
            else:
                st.error("❌ Connection Failed")
                
    with col2:
        st.subheader("Supabase Connection")
        if 'config_sb_url' not in st.session_state:
            st.session_state['config_sb_url'] = settings.get('supabase', {}).get('url', '')
            
        sb_url = st.text_input("Supabase URL", key='config_sb_url')
        sb_key = st.text_input("Supabase Key", type="password", value=os.getenv('SUPABASE_KEY', ''), key='config_sb_key')
        
        if st.button("Connect Supabase"):
            client = SupabaseClient(sb_url, sb_key)
            if client.connect():
                st.session_state['supabase_client'] = client
                st.success("✅ Connected to Supabase")
            else:
                st.error("❌ Connection Failed")
    
    st.divider()
    st.subheader("Processing Settings")
    
    if 'config_start_so' not in st.session_state:
        st.session_state['config_start_so'] = settings.get('processing', {}).get('starting_so_ref', 391)
        
    st.number_input("Starting SO Reference", min_value=1, step=1, key='config_start_so')

# --- Page 2: Upload & Extract ---
elif page == "Upload & Extract":


    st.title("📂 Upload & Extract PDFs")

    # Check if data already exists
    if not st.session_state['extracted_po_data'].empty:
        st.info(f"✅ Data currently loaded: {len(st.session_state['extracted_po_data'])} rows.")
        if st.button("🗑️ Clear & Restart"):
            st.session_state['extracted_po_data'] = pd.DataFrame()
            st.session_state['po_errors'] = []
            st.session_state['line_details'] = pd.DataFrame() # Clear downstream too
            st.session_state['order_summaries'] = pd.DataFrame()
            st.rerun()
        
        if st.button("Next: Transform", type="primary"):
             next_page("Transform & Review")
             
    else:
        uploaded_files = st.file_uploader("Upload T&T Purchase Orders (PDF)", type=['pdf'], accept_multiple_files=True)
        
        if uploaded_files:
            if st.button("Extract Data"):
                with st.spinner("Extracting data from PDFs..."):
                    file_tuples = [(f, f.name) for f in uploaded_files]
                    extracted_df, errors = PDFExtractor.process_multiple_pdfs(file_tuples)

                    st.session_state['extracted_po_data'] = extracted_df
                    st.session_state['po_errors'] = errors



                    if not extracted_df.empty:
                        st.success(f"✅ Extracted {len(extracted_df)} items")
                        st.dataframe(extracted_df)
                    else:
                        st.warning("No data extracted")
                    
                    if errors:
                        with st.expander("Errors & Warnings"):
                            for e in errors:
                                st.write(e)
                                
                if not st.session_state['extracted_po_data'].empty:
                     if st.button("Next: Transform", type="primary"):
                         next_page("Transform & Review")

# --- Page 3: Transform & Review ---
elif page == "Transform & Review":


    st.title("🔄 Transform & Review")

    if st.session_state['extracted_po_data'].empty:
        st.warning("No extracted data found. Please go back to Upload.")
    else:
        if st.session_state['line_details'].empty:
            if st.button("Fetch Attributes & Transform"):
                if not st.session_state['odoo_client']:
                    st.error("Please connect to Odoo first in Configuration")
                else:
                    with st.spinner("Fetching product data from Odoo and transforming..."):
                        # Get Unique Internal Refs
                        refs = st.session_state['extracted_po_data']['Internal Reference'].unique().astype(str).tolist()
                        
                        # Fetch Product Data
                        products = st.session_state['odoo_client'].get_products(internal_references=refs)
                        
                        # Transform
                        transformer = DataTransformer(settings)
                        start_ref = st.session_state.get('config_start_so', 391)
                        summary, details, logs = transformer.transform_data(
                            st.session_state['extracted_po_data'], 
                            products,
                            starting_so_ref=start_ref
                        )
                        
                        st.session_state['order_summaries'] = summary
                        st.session_state['line_details'] = details
                        st.session_state['transform_errors'] = logs
                        st.rerun()

        else:
            # Display Data
            st.subheader("Order Summaries")
            st.dataframe(st.session_state['order_summaries'])
            
            st.subheader("Line Details (Before Optimization)")
            st.dataframe(st.session_state['line_details'])
            
            if st.session_state['transform_errors']:
                with st.expander("Transformation Logs"):
                    for log in st.session_state['transform_errors']:
                        st.write(log)
            
            if st.button("Next: Optimize Inventory"):
                next_page("Inventory Optimization")

# --- Page 4: Inventory Optimization ---
elif page == "Inventory Optimization":


    st.title("📦 Inventory Optimization")

    if st.session_state['line_details'].empty:
         st.warning("No line details available.")
    else:
        # Ensure columns exist (migration for old session state)
        # This must be done BEFORE any filtering or copying occurs
        for col in ['store_on_hand', 'hist_avg_sales']:
            if col not in st.session_state['line_details'].columns:
                st.session_state['line_details'][col] = 0
                st.session_state['line_details'][col] = st.session_state['line_details'][col].astype(float) # Ensure type

        # Ensure product_image and shortage_details columns exist
        if 'product_image' not in st.session_state['line_details'].columns:
            st.session_state['line_details']['product_image'] = None
        if 'shortage_details' not in st.session_state['line_details'].columns:
            st.session_state['line_details']['shortage_details'] = None
                
        # Optimization Trigger
        col1, col2 = st.columns([1, 4])
        with col1:
             if st.button("Run Optimization Engine"):
                if not st.session_state['supabase_client']:
                    st.warning("Supabase not connected. Skipping historical data.")
                    # Still run with 0s
                    hist_sales = pd.DataFrame()
                    store_inv = pd.DataFrame()
                else:
                    # Fetch Supabase Data
                    # Ensure we pass the correct keys
                    refs = st.session_state['line_details']['internal_reference'].unique().astype(str).tolist()
                    store_ids = st.session_state['line_details']['store_id'].unique().tolist()
                    # Ensure store_ids are ints
                    store_ids = [int(s) for s in store_ids if pd.notna(s)]
                    
                    with st.spinner("Fetching History from Supabase..."):
                        hist_sales = st.session_state['supabase_client'].get_historical_sales(store_ids, refs)
                        store_inv = st.session_state['supabase_client'].get_store_inventory(store_ids, refs)

                    # --- DEBUG VIEW (User Request) ---
                    with st.expander("🕵️‍♂️ Verify Supabase Data Extraction", expanded=True):
                        st.info("Please check if the data below looks correct. If tables are empty, no matching data was found.")

                        d_col1, d_col2 = st.columns(2)
                        with d_col1:
                            st.markdown("### 📉 Historical Sales (from Supabase)")
                            if not hist_sales.empty:
                                st.dataframe(hist_sales)
                                st.write(f"Rows: {len(hist_sales)}")
                                st.write(f"Data types: {hist_sales.dtypes.to_dict()}")
                            else:
                                st.warning("No Historical Sales Data Found")

                        with d_col2:
                            st.markdown("### 🏪 Store Inventory (from Supabase)")
                            if not store_inv.empty:
                                st.dataframe(store_inv)
                                st.write(f"Rows: {len(store_inv)}")
                                st.write(f"Data types: {store_inv.dtypes.to_dict()}")
                            else:
                                st.warning("No Store Inventory Data Found")

                        # Show line_details merge keys for comparison
                        st.markdown("### 🔑 Line Details Merge Keys (Before Merge)")
                        sample_keys = st.session_state['line_details'][['internal_reference', 'store_id']].head(10)
                        st.dataframe(sample_keys)
                        st.write(f"Data types: {sample_keys.dtypes.to_dict()}")
                    # ---------------------------------

                transformer = DataTransformer(settings)
                optimizer = InventoryOptimizer(transformer)
                
                optimized_lines, logs = optimizer.optimize_allocations(
                    st.session_state['line_details'],
                    hist_sales,
                    store_inv
                )

                # Debug: Check if merge worked
                with st.expander("🔬 Post-Merge Debug Info", expanded=True):
                    st.write("### After Optimization Merge Results")
                    sample = optimized_lines[['internal_reference', 'store_id', 'hist_avg_sales', 'store_on_hand']].head(10)
                    st.dataframe(sample)
                    st.write(f"Non-zero hist_avg_sales count: {(optimized_lines['hist_avg_sales'] > 0).sum()}")
                    st.write(f"Non-zero store_on_hand count: {(optimized_lines['store_on_hand'] > 0).sum()}")

                st.session_state['line_details'] = optimized_lines
                st.success("Optimization Complete")
                st.rerun()

        # Product Allocation Summary Table
        st.subheader("📊 Product Allocation Summary")
        st.markdown("**Products requiring allocation decisions** (Total Demand > Available Inventory, Available > 0)")

        # Create summary by product and warehouse
        if not st.session_state['line_details'].empty:
            summary_data = []

            for warehouse in ['CE', 'CW']:
                wh_data = st.session_state['line_details'][st.session_state['line_details']['warehouse'] == warehouse].copy()

                if wh_data.empty:
                    continue

                # Group by internal_reference to get totals
                # Note: odoo_available is already warehouse-specific (set in data_transformer based on warehouse)
                product_groups = wh_data.groupby('internal_reference').agg({
                    'product_uom_qty': 'sum',  # Total demand across all stores
                    'odoo_available': 'first',  # Available (warehouse-specific, same for all rows of same product in this warehouse)
                    'odoo_on_hand': 'first',    # On hand (warehouse-specific, same for all rows of same product in this warehouse)
                    'product_name': 'first',
                    'product_image': 'first'
                }).reset_index()

                product_groups['warehouse'] = warehouse

                # Filter conditions:
                # 1. Total demand > Available (needs allocation)
                # 2. Available > 0 (has some inventory to allocate)
                product_groups['needs_allocation'] = (
                    (product_groups['product_uom_qty'] > product_groups['odoo_available']) &
                    (product_groups['odoo_available'] > 0)
                )

                summary_data.append(product_groups)

            if summary_data:
                summary_df = pd.concat(summary_data, ignore_index=True)

                # Filter to show only products that need allocation
                allocation_needed = summary_df[summary_df['needs_allocation'] == True].copy()

                if not allocation_needed.empty:
                    # Calculate shortage
                    allocation_needed['shortage'] = allocation_needed['product_uom_qty'] - allocation_needed['odoo_available']

                    # Convert images to data URIs
                    allocation_needed['product_image'] = allocation_needed['product_image'].apply(
                        lambda x: f"data:image/png;base64,{x}" if pd.notna(x) and x and not str(x).startswith('data:') else x
                    )

                    # Prepare display columns
                    display_summary = allocation_needed[[
                        'warehouse', 'product_image', 'internal_reference', 'product_name',
                        'product_uom_qty', 'odoo_available', 'shortage'
                    ]].copy()

                    # Rename for clarity
                    display_summary.columns = [
                        'Warehouse', 'Image', 'SKU', 'Product Name',
                        'Total Demand', 'Available', 'Shortage'
                    ]

                    st.dataframe(
                        display_summary,
                        column_config={
                            "Image": st.column_config.ImageColumn("Image", width="small"),
                            "Total Demand": st.column_config.NumberColumn("Total Demand", format="%d"),
                            "Available": st.column_config.NumberColumn("Available", format="%d"),
                            "Shortage": st.column_config.NumberColumn("Shortage", format="%d"),
                        },
                        hide_index=True,
                        use_container_width=True
                    )

                    st.warning(f"⚠️ {len(allocation_needed)} products need allocation decisions across warehouses")
                else:
                    st.success("✅ All products have sufficient inventory!")
            else:
                st.info("No summary data available")

        st.divider()

        # Display and Edit
        st.subheader("Review & Edit Allocations")

        tab_ce, tab_cw = st.tabs(["Canada East (CE)", "Canada West (CW)"])
        
        # Interactive Editor
        # We need to update session state with edits
        
        def render_warehouse_tab(warehouse_code):
            df = st.session_state['line_details'][st.session_state['line_details']['warehouse'] == warehouse_code].copy()
            if df.empty:
                st.info(f"No orders for {warehouse_code}")
                return

            # Columns to show in order
            # Order: store_id, store_name, image, price, qty, Flagged?, odoo_available, odoo_on_hand,
            #        store_on_hand, hist_avg_sales, flag_reason, shortage_details, internal_reference, po_number, product_name
            editable_cols = ['price_unit', 'product_uom_qty', 'flagged']
            display_cols = [
                'store_id', 'store_name', 'product_image',
                'odoo_available', 'odoo_on_hand', 'store_on_hand', 'hist_avg_sales',
                'flag_reason', 'shortage_details', 'internal_reference', 'po_number', 'product_name'
            ]

            # Combine in the requested order
            full_cols = [
                'store_id', 'store_name', 'product_image', 'price_unit', 'product_uom_qty', 'flagged',
                'odoo_available', 'odoo_on_hand', 'store_on_hand', 'hist_avg_sales',
                'flag_reason', 'shortage_details', 'internal_reference', 'po_number', 'product_name'
            ]

            # Highlight flagged rows
            st.write(f"**{warehouse_code} Orders**")

            # Filters
            st.markdown("### 🔍 Filters")
            filter_col1, filter_col2, filter_col3, filter_col4 = st.columns([2, 1, 1, 1])

            with filter_col1:
                # Get unique internal references for this warehouse
                available_refs = sorted(df['internal_reference'].unique().tolist())
                selected_refs = st.multiselect(
                    "Filter by Internal Reference (SKU)",
                    options=available_refs,
                    default=[],
                    key=f"filter_ref_{warehouse_code}"
                )

            with filter_col2:
                flag_filter = st.selectbox(
                    "Flagged Status",
                    options=["All", "Flagged Only", "Not Flagged"],
                    key=f"filter_flag_{warehouse_code}"
                )

            with filter_col3:
                # Get unique flag reasons, excluding None/NaN
                flag_reasons = df['flag_reason'].dropna().unique().tolist()
                flag_reasons = ["All"] + sorted([str(r) for r in flag_reasons if r])
                reason_filter = st.selectbox(
                    "Flag Reason",
                    options=flag_reasons,
                    key=f"filter_reason_{warehouse_code}"
                )

            with filter_col4:
                st.write("")  # Spacing
                st.write("")  # Spacing
                if st.button("Clear Filters", key=f"clear_filters_{warehouse_code}"):
                    st.rerun()

            # Apply filters
            filtered_df = df.copy()

            if selected_refs:
                filtered_df = filtered_df[filtered_df['internal_reference'].isin(selected_refs)]

            if flag_filter == "Flagged Only":
                filtered_df = filtered_df[filtered_df['flagged'] == True]
            elif flag_filter == "Not Flagged":
                filtered_df = filtered_df[filtered_df['flagged'] == False]

            if reason_filter != "All":
                filtered_df = filtered_df[filtered_df['flag_reason'] == reason_filter]

            # Show filter results
            if len(filtered_df) < len(df):
                st.info(f"Showing {len(filtered_df)} of {len(df)} items (filtered)")

            # Create display dataframe with converted images
            display_df = filtered_df[full_cols].copy()

            # Convert base64 images to data URIs for Streamlit display only
            if 'product_image' in display_df.columns:
                display_df['product_image'] = display_df['product_image'].apply(
                    lambda x: f"data:image/png;base64,{x}" if pd.notna(x) and x and not str(x).startswith('data:') else x
                )

            st.divider()

            edited_df = st.data_editor(
                display_df,
                key=f"editor_{warehouse_code}",
                num_rows="dynamic",
                height=800,  # Increased table height
                column_config={
                    "product_image": st.column_config.ImageColumn("Image", help="Product image from Odoo", width="small"),
                    "product_uom_qty": st.column_config.NumberColumn("Qty", min_value=0, step=1),
                    "price_unit": st.column_config.NumberColumn("Price", min_value=0.01, format="$%.2f"),
                    "flagged": st.column_config.CheckboxColumn("Flagged?"),
                },
                disabled=display_cols,
                hide_index=True,
                use_container_width=True
            )

            # Action buttons
            col1, col2 = st.columns([1, 3])

            with col1:
                # Delete flagged items
                flagged_count = edited_df['flagged'].sum()
                if flagged_count > 0:
                    if st.button(f"🗑️ Delete {flagged_count} Flagged Items", key=f"delete_flagged_{warehouse_code}"):
                        # Update session state with edited values first
                        for col in editable_cols:
                            st.session_state['line_details'].loc[filtered_df.index, col] = edited_df[col].values

                        # Remove flagged rows from main DF
                        indices_to_drop = edited_df[edited_df['flagged']].index
                        st.session_state['line_details'] = st.session_state['line_details'].drop(indices_to_drop)
                        st.rerun()

            with col2:
                # Save changes button
                if st.button(f"💾 Save Changes to {warehouse_code}", key=f"save_{warehouse_code}"):
                    # Update session state with edited values
                    for col in editable_cols:
                        st.session_state['line_details'].loc[filtered_df.index, col] = edited_df[col].values
                    st.success(f"✅ Changes saved for {warehouse_code}")
                    st.rerun()
                
        with tab_ce:
            render_warehouse_tab('CE')
            
        with tab_cw:
            render_warehouse_tab('CW')
            
        if st.button("Next: Final Review", type="primary"):
            next_page("Review & Import")

# --- Page 5: Import ---
elif page == "Review & Import":


    st.title("🚀 Review & Import to Odoo")

    st.subheader("Final Summary")
    
    # Calculate summary stats
    total_orders = st.session_state['line_details']['store_id'].nunique()
    total_lines = len(st.session_state['line_details'])
    total_value = (st.session_state['line_details']['product_uom_qty'] * st.session_state['line_details']['price_unit']).sum()
    
    col_stat1, col_stat2, col_stat3 = st.columns(3)
    col_stat1.metric("Total Orders", total_orders)
    col_stat2.metric("Total Lines", total_lines)
    col_stat3.metric("Total Value", f"${total_value:,.2f}")
    
    st.dataframe(st.session_state['line_details'][['store_name', 'product_name', 'product_uom_qty', 'price_unit', 'total_price', 'warehouse', 'flagged']])

    st.divider()
    
    # Initialization for Import Status (keeping legacy keys safe or removing)
    
    import io
    from datetime import datetime

    def to_excel(summaries, lines):
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            # Sheet 1: Sales Orders (Headers)
            # Columns: Order Reference, Customer, Order Date, Delivery Date, Client Order Ref
            if not summaries.empty:
                headers_df = pd.DataFrame({
                    'Order Reference': summaries['so_reference'],
                    'Customer': summaries['official_name'],
                    'Order Date': summaries['order_date'],
                    'Delivery Date': summaries['delivery_date'],
                    'Client Order Ref': summaries['po_numbers']
                })
                headers_df.to_excel(writer, sheet_name='Sales Orders', index=False)
            
            # Sheet 2: Sales Order Lines (Details)
            # Columns: Order Reference, Product, Quantity, Unit Price, Lock Unit Price
            if not lines.empty:
                # Filter flagged items out? User didn't explicitly say. But usually we only import clean rows.
                # User: "Review and Import".
                # Let's import logic: "valid_lines = ... [~flagged]"
                valid_lines = lines[~lines['flagged']].copy()

                lines_df = pd.DataFrame({
                    'Order Reference': valid_lines['so_reference'],
                    'Product': valid_lines['internal_reference'],
                    'Description': valid_lines['product_name'],
                    'Quantity': valid_lines['product_uom_qty'],
                    'Unit Price': valid_lines['price_unit'],
                    'Lock Unit Price': True  # Set to TRUE for all lines
                })
                lines_df.to_excel(writer, sheet_name='Sales Order Lines', index=False)
                
        return output.getvalue()

    st.subheader("Export for Odoo")
    
    col1, col2 = st.columns([2, 1])
    with col1:
        st.info("Download the Excel file below to import into Odoo.")
        
        if not st.session_state['order_summaries'].empty:
            excel_data = to_excel(st.session_state['order_summaries'], st.session_state['line_details'])

            if st.download_button(
                label="📥 Download Excel Import File",
                data=excel_data,
                file_name=f"odoo_import_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            ):
                pass
        else:
            st.warning("No data to export.")
            
    # Legacy Import Logs Display (Optional, can remove)
    # st.write("Direct API Import Disabled.")

