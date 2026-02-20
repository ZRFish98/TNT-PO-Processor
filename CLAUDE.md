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
python test_complete_flow.py       # End-to-end workflow test
```

## Architecture

### po-processor-app Structure

The main application follows a multi-stage processing pipeline implemented in Streamlit:

**Backend Components:**
- `backend/pdf_extractor.py` - Extracts PO data from T&T PDF files using pdfplumber and regex
- `backend/data_transformer.py` - Transforms raw PO data into Odoo-ready format with warehouse routing
- `backend/inventory_optimizer.py` - Optimizes order allocations based on inventory and sales data
- `backend/odoo_client.py` - XML-RPC client for Odoo API integration
- `backend/gmail_monitor.py` - Gmail polling thread for auto-ingesting PO PDFs from email
- `backend/bq_lost_lines.py` - Non-blocking BigQuery writer for deleted/lost order lines analytics

**Database:**
- `database/connection.py` - PostgreSQL connection pool, schema init, and migrations for `staged_pos` and `app_settings` tables

**Data Models:**
- `models/schemas.py` - Pydantic models for data validation (PurchaseOrderLine, ProductVariant, SalesOrder, etc.)

**Configuration:**
- `config/settings.yaml` - Application settings including warehouse mappings and store names
- `.env` - API credentials (Odoo, Gmail, PostgreSQL, BigQuery)

**Frontend:**
- `frontend/app.py` - Streamlit multi-page app with navigation (PO Processor, Invoice Verification, Return Processor, Settings)
- `frontend/style.css` - Atiara brand theme (dark mode, Poppins font, pink primary)
- `frontend/views/po_processor.py` - Combined PO workflow page with tab navigation:
  - Dashboard (p2_queue) - PO queue with PDF preview, download tracking, batch ZIP download
  - Transform & Review (p4_inventory) - Odoo data fetch, inventory optimization, line editing
  - Export (p5_export) - Excel export with per-store import selection
- `frontend/views/p1_settings.py` - Settings page (Odoo connection, Gmail OAuth, DB explorer)
- `frontend/views/invoice_verification.py` - Invoice verification workflow (placeholder)
- `frontend/views/return_processor.py` - Return processor workflow (placeholder)

### Windmill Alternative (po-processor-app/f/po_processor/)

An alternative implementation using Windmill workflow automation platform:

**Scripts:**
- `pdf_extractor/main.py` - PDF extraction as Windmill script
- `odoo_process/main.py` - Odoo data fetching and transformation
- `import_to_odoo/main.py` - Import reviewed orders into Odoo

**App:**
- `po_app.app.yaml` - Native Windmill app replacing Streamlit frontend

**Common Modules:**
- `common/odoo_client.py`, `common/data_transformer.py`, `common/schemas.py`, `common/inventory_optimizer.py`

Each script has a `.script.yaml` defining inputs/outputs and a `main.py` with the implementation. Deploy using Windmill CLI (`wmill`).

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
   - Generates SO references starting from user-provided latest Odoo SO number
   - Groups orders by store and creates order summaries

3. **Inventory Optimization** (`InventoryOptimizer.optimize_allocations`)
   - Accepts historical sales and store inventory DataFrames as parameters
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
ODOO_URL=https://atiara-trading-inc.odoo.com
ODOO_DB=atiara-trading-inc
ODOO_USERNAME=official@atiara.ca
DATABASE_URL=postgresql://po_user:password@localhost:5433/po_processor
GMAIL_CLIENT_ID=<client_id>
GMAIL_CLIENT_SECRET=<client_secret>
BQ_SERVICE_ACCOUNT_PATH=/path/to/service-account.json  # local dev
GOOGLE_APPLICATION_CREDENTIALS_JSON=<base64>            # Docker/production
```

**Settings (config/settings.yaml):**
- Odoo connection defaults (URL and database name)
- Warehouse-store mappings (`cw_stores` list)
- Complete store name mappings (`tt_store_names`)

### Deployment

- **Cloud URL**: `https://internal.atiara.cloud`
- **VPS**: Hostinger VPS ID `1211602` at `72.62.83.239`
- **Docker image**: `ghcr.io/zrfish98/tnt-po-processor:latest`
- **CI/CD**: Push to `master` → GitHub Actions builds and pushes Docker image (`.github/workflows/docker-build.yml`)
- **Compose file (VPS)**: `/docker/po-processor/docker-compose.yml` (copy of `docker-compose.hostinger.yml`)
- **Deploy steps**: `git push origin master` → wait for CI → SSH to VPS → `docker compose pull && docker compose up -d`
- **SSH to VPS**: `sshpass -p '<password>' ssh -o PubkeyAuthentication=no root@72.62.83.239`
- **DB migrations**: Auto-run via `_ensure_schema()` on container restart, or manually via `docker exec po-processor-db psql -U po_user -d po_processor`
- **Traefik**: Reverse proxy with TLS via Let's Encrypt, on `n8n_default` Docker network

---

## External APIs & Connections

### Odoo (XML-RPC)

- **URL**: `https://atiara-trading-inc.odoo.com`
- **Database**: `atiara-trading-inc`
- **User**: `official@atiara.ca`
- **API Key**: stored in `.env` as `ODOO_API_KEY`, also in `Bigquery/service-account.json` sibling dir
- **Auth endpoint**: `/xmlrpc/2/common` → `authenticate(db, user, api_key, {})`
- **Model endpoint**: `/xmlrpc/2/object` → `execute_kw(db, uid, api_key, model, method, args, kwargs)`
- **Version**: Odoo 19.0

Key Odoo models used:
- `sale.order`, `sale.order.line` — Sales orders
- `purchase.order`, `purchase.order.line` — Purchase orders
- `product.product` — Products (has `active` field, use `context={"active_test": False}`)
- `res.partner` — Customers/vendors (has `active` field)
- `x_freight` — Freight shipments (custom model)
- `account.move` — Invoices/credit notes
- `account.move.line` — Invoice/credit note lines
- `x_brand` — Brands (custom model)
- `product.supplierinfo` — Vendor pricelist (has `delay` field for lead time)

### BigQuery

- **Project**: `odoo-471420`
- **Dataset**: `odoo_data`
- **Location**: `northamerica-northeast2`
- **Service Account**: `n8n-odoo-sync@odoo-471420.iam.gserviceaccount.com`
- **Credentials file**: `/Users/henryyu/Desktop/AI/Anti-Gravity/Bigquery/service-account.json`

**Tables** (13 total):
`sale_order`, `sale_order_line`, `purchase_order`, `purchase_order_line`, `product_product`, `res_partner`, `freight_shipment`, `credit_note`, `credit_note_line`, `brand`, `invoice`, `invoice_line`, `lost_order_lines`

**`lost_order_lines`** — Tracks order lines deleted during PO processing (out-of-stock items). Written by `backend/bq_lost_lines.py` via streaming insert. Columns: `id` (UUID), `deleted_at`, `deletion_type` ("delete_flagged"|"save_removed"), `warehouse`, `store_id`, `store_name`, `po_number`, `internal_reference`, `product_id`, `product_name`, `barcode`, `original_qty`, `product_uom_qty`, `price_unit`, `total_price`, `odoo_on_hand`, `odoo_available`, `flagged`, `flag_reason`, `shortage_details`.

**Views**:
- `v_sales` — Denormalized sales view joining sale_order + sale_order_line + product_product + res_partner. Used by all Metabase dashboards. Columns include: `order_date`, `warehouse_name`, `brand`, `category_l1/l2/l3`, `country_of_origin`, `internal_reference`, `barcode_var`, `product_name`, `chinese_name_var`, `partner_name`, `price_subtotal`, `margin`, `cost`, etc.
- `v_price_analysis` — Promotional pricing analysis view (effective_price, regular_price, is_promotional, promo_discount_pct)

**Schema doc**: `/Users/henryyu/Desktop/AI/Anti-Gravity/Bigquery/bigquery_schema.md`

### Metabase (BI/Analytics)

- **URL**: `https://metabase.atiara.cloud`
- **Hosted on**: Hostinger VPS
- **Login**: `official@atiara.ca` / `AtiaraInternal1`
- **Auth**: `POST /api/session` with `{"username": "...", "password": "..."}` → returns `{"id": "<session-token>"}`
- **Session header**: `X-Metabase-Session: <token>`
- **BigQuery database ID**: 2 ("Atiara Bigquery")
- **Version**: v0.58.7

**Key API endpoints**:
- `GET /api/card/<id>` — Get card (question) details
- `PUT /api/card/<id>` — Update card (query, display, visualization_settings)
- `POST /api/card/<id>/query` — Execute card query, returns `{status, data: {rows, cols}}`
- `POST /api/card` — Create new card
- `GET /api/dashboard/<id>` — Get dashboard with dashcards, parameters, parameter_mappings
- `PUT /api/dashboard/<id>` — Update dashboard layout (dashcards array with row/col/size_x/size_y/parameter_mappings)
- `POST /api/dashboard/<id>/cards` — Add card to dashboard
- `DELETE /api/dashboard/<id>/cards` — Remove card from dashboard (body: `{"dashcardId": <id>}`)

**Dashboard template tag pattern** (for native SQL cards on SKU Performance dashboard 6):
- All filter tags use `type: "text"` (NOT `dimension`)
- Tag names: `period`, `warehouse`, `brand`, `country`, `cat1`, `cat2`, `cat3`, `internal_ref`, `barcode`, `product_name`, `chinese_name`
- Period filter: `order_date >= DATE_SUB(CURRENT_DATE(), INTERVAL SAFE_CAST({{period}} AS INT64) DAY)`
- Text filters: `column = {{tag_name}}`
- Name filters: `column LIKE CONCAT('%', {{tag_name}}, '%')`
- Dashboard parameter IDs: `pd`, `wh`, `br`, `co`, `c1`, `c2`, `c3`, `ir`, `bc`, `en`, `cn`
- Parameter mappings use `["variable", ["template-tag", "<tag_name>"]]` targets (NOT `dimension`)

**Dashboards**:
- Dashboard 6: "SKU Performance" — product-level metrics, trends, sankey
- Other dashboards exist in collections under `/api/collection/`

### Windmill (Workflow Automation)

- **Accessed via**: MCP server (`mcp__windmill__*` tools)
- **Workspace**: default

**Key paths**:
- `f/bigquery_sync/sync_table` — Single parameterized script syncing any Odoo model to BigQuery (v10, hash `3ef970c7c8d67c20`)
- `f/bigquery_sync/sync_all` — Flow with 12 parallel branches (one per table)
- `f/bigquery_sync/hourly_sync` — Schedule: hourly, America/Toronto timezone
- `f/bigquery_sync/reconcile_deletions` — Hard-delete reconciliation script
- `f/bigquery_sync/daily_reconciliation` — Schedule: 3 AM ET daily

**Resources**:
- `f/bigquery_sync/bigquery_credentials` — BigQuery service account JSON
- `f/po_processor/odoo_credentials` — Odoo API credentials (url, db, user, api_key)

---

## MCP Servers & Tools

### BigQuery MCP (`mcp__bigquery__*`)

| Tool | Purpose |
|------|---------|
| `list-tables` | List all tables in a dataset |
| `describe-table` | Get table schema (columns, types) |
| `execute-query` | Run SQL queries |

**Caveat**: These tools default to US location, but our dataset is in `northamerica-northeast2`. Use `bq` CLI or Python client as workaround for queries that fail with location errors.

### Windmill MCP (`mcp__windmill__*`)

| Tool | Purpose |
|------|---------|
| `listScripts` | List all scripts |
| `getScriptByPath` | Get script source code |
| `createScript` | Create new script (fails if path exists — delete first) |
| `deleteScriptByPath` / `deleteScriptByHash` | Delete script |
| `runScriptByPath` | Run a script with arguments |
| `runScriptPreviewAndWaitResult` | Run inline code and wait for result (times out on large syncs) |
| `listFlows` / `getFlowByPath` | List/get flows |
| `createFlow` / `updateFlow` / `deleteFlowByPath` | Manage flows |
| `listSchedules` / `getSchedule` | List/get schedules |
| `createSchedule` / `updateSchedule` / `deleteSchedule` | Manage schedules |
| `listResource` / `getResource` | List/get resources |
| `createResource` / `updateResource` / `deleteResource` | Manage resources |
| `listVariable` / `getVariable` | List/get variables |
| `createVariable` / `updateVariable` / `deleteVariable` | Manage variables |
| `listJobs` / `listQueue` / `listWorkers` | Monitor execution |
| `queryDocumentation` | Search Windmill docs |
| `runFlowByPath` | Run a flow |
| `listResourceType` | List available resource types |
| Custom flow/script tools (`s-f_*`, `f-f_*`) | Pre-built domain flows (PO processing, Shopify sync, Excel export, PDF extract) |

### Hostinger API MCP (`mcp__hostinger-api__*`)

Manages Hostinger infrastructure (VPS, DNS, domains, hosting, billing).

**VPS tools** (`VPS_*`): Create/manage VMs, firewalls, snapshots, SSH keys, PTR records, projects, post-install scripts, metrics, backups, Monarx security
**DNS tools** (`DNS_*`): Get/update/reset/validate DNS records, snapshots
**Domain tools** (`domains_*`): Check availability, purchase, manage WHOIS, forwarding, privacy, nameservers, domain lock
**Hosting tools** (`hosting_*`): Create websites, deploy JS/static/WordPress, list orders/websites, generate subdomains
**Billing tools** (`billing_*`): Subscriptions, payment methods, catalog, auto-renewal, service orders
**Reach tools** (`reach_*`): Contact management, segments, profiles

### Pencil MCP (`mcp__pencil__*`)

Design editor for `.pen` files (encrypted format — must use Pencil tools, not Read/Grep).

| Tool | Purpose |
|------|---------|
| `get_editor_state` | Get current editor context |
| `open_document` | Open/create .pen files |
| `get_guidelines` | Design guidelines (code, table, tailwind, landing-page) |
| `get_style_guide_tags` / `get_style_guide` | Style guide discovery |
| `batch_get` | Search/read nodes in .pen files |
| `batch_design` | Insert/copy/update/replace/move/delete/image operations |
| `snapshot_layout` | Check computed layout rectangles |
| `get_screenshot` | Visual validation screenshot |
| `get_variables` / `set_variables` | Manage design variables/themes |
| `find_empty_space_on_canvas` | Find empty canvas space |
| `search_all_unique_properties` | Search node properties |
| `replace_all_matching_properties` | Bulk property replacement |

---

## Key File Locations

| File | Location |
|------|----------|
| BQ service account | `/Users/henryyu/Desktop/AI/Anti-Gravity/Bigquery/service-account.json` |
| BQ schema doc | `/Users/henryyu/Desktop/AI/Anti-Gravity/Bigquery/bigquery_schema.md` |
| BQ full sync script | `/Users/henryyu/Desktop/AI/Anti-Gravity/Bigquery/full_sync.py` |
| BQ reconciliation | `/Users/henryyu/Desktop/AI/Anti-Gravity/Bigquery/run_reconciliation.py` |
| Windmill sync script | Deployed at `f/bigquery_sync/sync_table` (source via `getScriptByPath`) |
| Odoo credentials | `.env` in po-processor-app, also in Windmill resource `f/po_processor/odoo_credentials` |
| Sankey HTML (standalone) | `/Users/henryyu/Desktop/AI/Anti-Gravity/product_sales_sankey.html` |
