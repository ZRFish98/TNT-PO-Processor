from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Literal
from datetime import datetime, date

class PurchaseOrderLine(BaseModel):
    """Raw extracted PO line from PDF"""
    po_number: str = Field(alias="PO No.")
    store_id: int = Field(alias="Store ID")
    store_name: str = Field(alias="Store Name")
    order_date: str = Field(alias="Order Date")
    delivery_date: str = Field(alias="Delivery Date")
    internal_reference: str = Field(alias="Internal Reference")
    description: str = Field(alias="Description")
    size: Optional[str] = Field(None, alias="Size")
    pack: Optional[str] = Field(None, alias="Pack")
    ordered_qty: float = Field(alias="# of Order")
    price: float = Field(alias="Price")

    class Config:
        populate_by_name = True

class ProductVariant(BaseModel):
    """Odoo product variant data"""
    id: int
    name: str
    barcode: Optional[str] = None
    default_code: Optional[str] = None  # Internal Reference
    units_per_order: Optional[float] = Field(None, alias="x_studio_tt_om_int")
    tt_price: Optional[float] = Field(None, alias="x_studio_tt_price")
    
    # Inventory fields
    ce_on_hand: Optional[float] = Field(0, alias="x_studio_canada_east_on_hand")
    ce_available: Optional[float] = Field(0, alias="x_studio_ce_available")
    cw_on_hand: Optional[float] = Field(0, alias="x_studio_canada_west_on_hand")
    cw_available: Optional[float] = Field(0, alias="x_studio_cw_available")

    class Config:
        populate_by_name = True

class SalesOrderLine(BaseModel):
    """Processed SO line ready for Odoo import"""
    product_id: int
    product_uom_qty: float
    price_unit: float
    name: str # Description
    
    # Metadata for UI (not sent to Odoo in this structure, but used for processing)
    internal_reference: str
    store_id: int
    warehouse: Literal["CE", "CW"]
    flagged: bool = False
    flag_reason: Optional[str] = None
    
    # Inventory snapshot for UI
    available_qty: float = 0
    on_hand_qty: float = 0
    store_inventory_qty: Optional[float] = None
    avg_monthly_sales: Optional[float] = None

class SalesOrder(BaseModel):
    """Odoo Sales Order Header"""
    partner_id: int  # Customer/Store ID
    date_order: datetime
    lines: List[SalesOrderLine] = []
    warehouse: Literal["CE", "CW"]
    store_id: int
    client_order_ref: str  # PO Number


class ReturnLineItem(BaseModel):
    """A single line item from a T&T return form."""
    item_id: Optional[str] = None          # Internal reference from return form
    upc: Optional[str] = None              # T&T UPC (12-digit format)
    description_en: Optional[str] = None
    description_cn: Optional[str] = None
    size_pack: Optional[str] = None
    item_class: Optional[str] = None
    quantity: float = 0                    # Qty on return form (PDF)
    cost: float = 0
    amount: float = 0
    reason: Optional[str] = None

    # Populated after Odoo matching
    odoo_product_id: Optional[int] = None
    odoo_product_name: Optional[str] = None
    odoo_barcode: Optional[str] = None     # EAN-13 from Odoo
    matched: bool = False


class ReturnFormPage(BaseModel):
    """High-level summary of one return form page."""
    page_number: int
    store_name: Optional[str] = None
    doc_no: Optional[str] = None           # e.g. "EF0216367539"
    print_date: Optional[str] = None
    line_items: List[ReturnLineItem] = []
    total_amount: float = 0
    extraction_confidence: Optional[float] = None  # 0-1 from Gemini


class StagedPO(BaseModel):
    """A PO record stored in the PostgreSQL staging queue."""
    id: Optional[int] = None
    source: str = "manual"          # 'gmail' | 'manual'
    gmail_message_id: Optional[str] = None
    gmail_subject: Optional[str] = None
    gmail_from: Optional[str] = None
    gmail_received_at: Optional[str] = None
    original_filename: str
    po_number: Optional[str] = None
    store_id: Optional[int] = None
    store_name: Optional[str] = None
    order_date: Optional[str] = None
    delivery_date: Optional[str] = None
    region: Optional[str] = None    # 'east' | 'west'
    extracted_data: List[Dict] = []
    status: str = "unprocessed"     # unprocessed | processing | processed | error
    error_message: Optional[str] = None
    created_at: Optional[datetime] = None
    processed_at: Optional[datetime] = None
