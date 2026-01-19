# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This repository contains three separate T&T Supermarket purchase order processing applications:

1. **po-processor-app** - Main production application (Streamlit-based)
2. **TNT_PO_Extract** - Standalone PDF extraction tool
3. **Odoo-T-T-Test** - Excel-based PO processor (legacy/alternative version)

The primary application is `po-processor-app`, which provides a complete workflow for processing T&T purchase orders from PDF extraction through Odoo import.

## Development Commands

### Running the Main Application
```bash
cd po-processor-app
streamlit run frontend/app.py
```

### Running Alternative Applications
```bash
# PDF extraction tool
cd TNT_PO_Extract
streamlit run app.py

# Excel-based processor
cd Odoo-T-T-Test
streamlit run streamlit_app_cloud.py
```

### Installing Dependencies
```bash
# Main application
cd po-processor-app
pip install -r requirements.txt

# Other applications have their own requirements.txt
```

### Running Tests
```bash
cd po-processor-app/tests
python verify_fix_column_merge.py  # Verify column merging logic
python inspect_excel.py            # Inspect Excel file structure
```

## Architecture

### po-processor-app Structure

The main application follows a multi-stage processing pipeline implemented in Streamlit:

**Backend Components:**
- `backend/pdf_extractor.py` - Extracts PO data from T&T PDF files using pdfplumber and regex
- `backend/data_transformer.py` - Transforms raw PO data into Odoo-ready format with warehouse routing
- `backend/inventory_optimizer.py` - Optimizes order allocations based on inventory and sales data
- `backend/odoo_client.py` - XML-RPC client for Odoo API integration
- `backend/supabase_client.py` - Client for historical sales and inventory data

**Data Models:**
- `models/schemas.py` - Pydantic models for data validation (PurchaseOrderLine, ProductVariant, SalesOrder, etc.)

**Configuration:**
- `config/settings.yaml` - Application settings including warehouse mappings and store names
- `.env` - API credentials (Odoo API key, Supabase key)

**Frontend:**
- `frontend/app.py` - Streamlit UI with 5-page workflow:
  1. Configuration - Connect to Odoo/Supabase
  2. Upload & Extract - Upload PDFs and extract line items
  3. Transform & Review - Fetch Odoo product data and transform to sales orders
  4. Inventory Optimization - Run allocation logic and review/edit orders
  5. Review & Import - Export to Excel for Odoo import

### Processing Pipeline Flow

1. **PDF Extraction** (`PDFExtractor.process_multiple_pdfs`)
   - Parses T&T PDF purchase orders using regex patterns
   - Extracts: PO number, store ID, internal reference, quantity, price
   - Handles multi-line descriptions (English + Chinese)
   - Validates extracted data

2. **Data Transformation** (`DataTransformer.transform_data`)
   - Fetches matching products from Odoo by internal reference
   - Handles multi-product scenarios (same internal reference → multiple variants)
   - Converts quantities from "cases" to "units" using `x_studio_tt_om_int` (Units Per Order)
   - Calculates unit prices from case prices
   - Routes orders to CE (Canada East) or CW (Canada West) warehouses based on store ID
   - Generates SO references starting from configurable number (default: OATS00391)
   - Groups orders by store and creates order summaries

3. **Inventory Optimization** (`InventoryOptimizer.optimize_allocations`)
   - Merges historical sales data and store inventory from Supabase
   - Flags items with zero/negative inventory
   - Handles shortage scenarios with proportional allocation
   - Calculates priority scores based on sales velocity and days of supply
   - Provides allocation rationale in `flag_reason` field

4. **Excel Export**
   - Generates two-sheet Excel workbook:
     - "Sales Orders" sheet: order headers (SO reference, customer, date, PO numbers)
     - "Sales Order Lines" sheet: line items (SO reference, product, quantity, price)
   - Excludes flagged items from export

### Key Data Mappings

**Odoo Custom Fields:**
- `x_studio_tt_om_int` - Units Per Order (conversion from cases to units)
- `x_studio_tt_price` - T&T Price
- `x_studio_canada_east_on_hand` / `x_studio_ce_available` - CE warehouse inventory
- `x_studio_canada_west_on_hand` / `x_studio_cw_available` - CW warehouse inventory

**Warehouse Assignment:**
- CW stores are defined in `config/settings.yaml` (stores 1,3,4,5,6,7,8,10,13,14,17,19,23,24,25,26,29,30,31,33,36)
- All other stores default to CE

**Store Names:**
- Store IDs (001-040) map to official names in `config/settings.yaml`
- Used for creating Odoo customer records

### Supabase Schema

The application expects these tables in Supabase:

**products**
- `id` (int) - Internal Supabase ID
- `item_id` (text) - Internal reference (matches Odoo default_code)

**sales_performance**
- `store_id` (int)
- `product_id` (int) - Foreign key to products.id
- `total_quantity_sold` (numeric)
- Used to calculate average monthly sales

**inventory_snapshots**
- `store_id` (int)
- `product_id` (int) - Foreign key to products.id
- `quantity` (numeric)
- `snapshot_date` (date)
- Latest snapshot used for current store inventory

### Common Pitfalls

**Multi-Product Internal References:**
When a single internal reference maps to multiple Odoo product variants, the transformer splits the ordered quantity equally across all variants. The first variant gets any remainder from integer division. This is intentional behavior to handle product bundles.

**Quantity Conversions:**
The PDF contains "case" quantities, but Odoo requires "unit" quantities. Always multiply by `x_studio_tt_om_int` (Units Per Order). The `DataTransformer` handles this automatically.

**Price Calculations:**
The PDF price is per case. Unit price = PDF price ÷ Units Per Order. This is handled in `data_transformer.py:131-138`.

**Session State Management:**
The Streamlit app heavily relies on `st.session_state` for workflow progression. When modifying data, ensure you update session state AND call `st.rerun()` to trigger UI refresh.

**Warehouse Routing:**
Store-to-warehouse mapping is critical. Always check `config/settings.yaml` for the `cw_stores` list. Incorrect routing will cause inventory allocation failures.

### Testing Strategy

When making changes to extraction or transformation logic:

1. Use sample PDFs from the root directory (e.g., `T&T PO 2601161223.PDF`)
2. Test with the provided Excel file (`Odoo Import Ready (22).xlsx`) as expected output reference
3. Verify column merging doesn't occur (common issue - see `tests/verify_fix_column_merge.py`)
4. Check that flagged items are properly excluded from final export

### Configuration Files

**Environment Variables (.env):**
```
ODOO_API_KEY=<api_key>
SUPABASE_KEY=<service_key>
```

**Settings (config/settings.yaml):**
- Odoo connection defaults
- Supabase project configuration
- Warehouse-store mappings
- Starting SO reference number
- Complete store name mappings
