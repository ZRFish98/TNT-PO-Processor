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
    client_order_ref: str # PO Number
