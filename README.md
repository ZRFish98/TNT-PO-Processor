# T&T Supermarket Purchase Order Processing System

A comprehensive purchase order processing application suite for T&T Supermarket, featuring PDF extraction, data transformation, inventory optimization, and Odoo ERP integration.

## 📋 Project Overview

This repository contains three separate applications for processing T&T purchase orders:

1. **po-processor-app** - Main production application (Streamlit-based)
2. **TNT_PO_Extract** - Standalone PDF extraction tool
3. **Odoo-T-T-Test** - Excel-based PO processor (legacy/alternative version)

The primary application is `po-processor-app`, which provides a complete workflow from PDF extraction through Odoo import.

## 🚀 Features

### Main Application (po-processor-app)

- **PDF Extraction**: Parse T&T purchase order PDFs and extract line items
- **Product Matching**: Automatically match products with Odoo database
- **Inventory Optimization**: Allocate products based on warehouse inventory and historical sales data
- **Multi-Warehouse Support**: Handle Canada East (CE) and Canada West (CW) warehouses
- **Visual Product Review**: Display product images for faster identification
- **Interactive Editing**: Review and adjust quantities, prices, and flag items
- **Advanced Filtering**: Filter by SKU, flagged status, and flag reasons
- **Shortage Tracking**: Identify products with demand exceeding available inventory
- **Excel Export**: Generate Odoo-ready import files

## 🛠️ Installation

### Prerequisites

- Python 3.8+
- pip package manager
- Odoo API access
- Supabase account (for historical data)

### Setup

1. Clone the repository:
```bash
git clone <your-repo-url>
cd T-TPO
```

2. Install dependencies for the main application:
```bash
cd po-processor-app
pip install -r requirements.txt
```

3. Configure environment variables:
```bash
cp .env.example .env
# Edit .env and add your API keys
```

Required environment variables:
- `ODOO_API_KEY` - Your Odoo API key
- `SUPABASE_URL` - Your Supabase project URL
- `SUPABASE_KEY` - Your Supabase anon/service key

4. Update settings (if needed):
```bash
# Edit config/settings.yaml to customize:
# - Warehouse-store mappings
# - Store names
# - Starting SO reference numbers
```

## 📖 Usage

### Running the Main Application

```bash
cd po-processor-app
streamlit run frontend/app.py
```

The application will open in your browser at `http://localhost:8501`

### Workflow

1. **Configuration**: Connect to Odoo and Supabase
2. **Upload & Extract**: Upload PDF files and extract purchase order data
3. **Transform & Review**: Match products with Odoo and transform data
4. **Inventory Optimization**: Run allocation logic and review orders
5. **Review & Import**: Export to Excel for Odoo import

### Running Alternative Applications

```bash
# PDF extraction tool
cd TNT_PO_Extract
streamlit run app.py

# Excel-based processor
cd Odoo-T-T-Test
streamlit run streamlit_app_cloud.py
```

## 📁 Project Structure

```
T&TPO/
├── po-processor-app/          # Main application
│   ├── backend/               # Processing logic
│   │   ├── pdf_extractor.py   # PDF parsing
│   │   ├── data_transformer.py # Data transformation
│   │   ├── inventory_optimizer.py # Allocation logic
│   │   ├── odoo_client.py     # Odoo API client
│   │   └── supabase_client.py # Supabase client
│   ├── frontend/              # Streamlit UI
│   │   └── app.py
│   ├── models/                # Data schemas
│   │   └── schemas.py
│   ├── config/                # Configuration
│   │   └── settings.yaml
│   ├── tests/                 # Test scripts
│   └── requirements.txt
├── TNT_PO_Extract/           # Standalone PDF extractor
├── Odoo-T-T-Test/           # Legacy Excel processor
├── CLAUDE.md                # Development guide
└── README.md
```

## 🔧 Configuration

### Warehouse Mappings

Edit `config/settings.yaml` to configure which stores belong to which warehouse:

```yaml
warehouse_mapping:
  cw_stores: [1, 3, 4, 5, 6, 7, 8, 10, 13, 14, 17, 19, 23, 24, 25, 26, 29, 30, 31, 33, 36]
```

All other stores default to Canada East (CE).

### Store Names

Update the `tt_store_names` section in `settings.yaml` to customize store display names.

## 📊 Data Flow

1. **PDF Extraction** → Extract PO number, store ID, products, quantities, prices
2. **Product Matching** → Fetch matching products from Odoo by internal reference
3. **Unit Conversion** → Convert "cases" to "units" using Odoo's Units Per Order
4. **Warehouse Routing** → Route orders to CE or CW based on store ID
5. **Optimization** → Merge historical sales and inventory data, handle shortages
6. **Export** → Generate Excel file with Sales Orders and Sales Order Lines sheets

## 🔐 Security

- Never commit `.env` files or API keys to the repository
- The `.gitignore` file is configured to exclude sensitive files
- Use `.env.example` as a template for required environment variables

## 🧪 Testing

```bash
cd po-processor-app/tests
python verify_fix_column_merge.py  # Verify column merging
python inspect_excel.py            # Inspect Excel structure
```

## 📝 Key Features Details

### Inventory Optimization

- Merges Supabase historical sales and inventory data
- Flags items with zero/negative inventory
- Tracks shortages with demand vs. available quantities
- Calculates priority scores based on sales velocity
- Provides allocation rationale in flag_reason field

### Excel Export Format

**Sales Orders Sheet:**
- Order Reference
- Customer
- Order Date
- Delivery Date
- Client Order Ref (PO Numbers)

**Sales Order Lines Sheet:**
- Order Reference
- Product (Internal Reference)
- Description
- Quantity (in units)
- Unit Price
- Lock Unit Price (TRUE)

## 🤝 Contributing

For development guidelines, see [CLAUDE.md](CLAUDE.md).

## 📄 License

[Add your license information here]

## 🐛 Known Issues

- Multi-product internal references split quantities equally across variants
- PDF parsing requires specific T&T format
- Warehouse inventory must be properly configured in Odoo

## 📞 Support

[Add your contact information or support channels here]
