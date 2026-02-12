import re
import xmlrpc.client
import ssl
import logging
from typing import List, Dict, Any, Optional, Tuple
import os
from functools import lru_cache
import time

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class OdooClient:
    def __init__(self, url: str, db: str, username: str, api_key: str):
        self.url = url
        self.db = db
        self.username = username
        self.api_key = api_key
        self.common = None
        self.models = None
        self.uid = None
        self.connected = False

    def connect(self) -> bool:
        """Connect to Odoo XML-RPC interface"""
        try:
            # Create a custom context to ignore SSL verification if needed (not recommended for production but helpful for some setups)
            # context = ssl._create_unverified_context()
            
            self.common = xmlrpc.client.ServerProxy(f'{self.url}/xmlrpc/2/common')
            self.uid = self.common.authenticate(self.db, self.username, self.api_key, {})
            
            if self.uid:
                self.models = xmlrpc.client.ServerProxy(f'{self.url}/xmlrpc/2/object')
                self.connected = True
                logger.info(f"Successfully connected to Odoo: {self.url} (User ID: {self.uid})")
                return True
            else:
                logger.error("Authentication failed")
                return False
                
        except Exception as e:
            logger.error(f"Connection error: {e}")
            return False

    def get_products(self, internal_references: List[str] = None) -> List[Dict[str, Any]]:
        """
        Fetch products from Odoo.
        If internal_references is provided, fetches only those specific products.
        Otherwise, fetches ALL products that have an internal reference set.
        """
        if not self.connected:
            raise ConnectionError("Not connected to Odoo")

        try:
            # Domain filter: Products must have an internal reference
            domain = [['default_code', '!=', False]]
            
            if internal_references:
                # Optimized: chunk the query if too many references
                domain.append(['default_code', 'in', internal_references])

            # Fields to fetch
            fields = [
                'name',
                'barcode',
                'default_code',
                'x_studio_tt_om_int',       # Units Per Order
                'x_studio_tt_price',        # T&T Price
                'x_studio_canada_east_on_hand',
                'x_studio_ce_available',
                'x_studio_canada_west_on_hand',
                'x_studio_cw_available',
                'image_1920'                # Product image
            ]

            # Execute search_read
            logger.info("Fetching products from Odoo...")
            products = self.models.execute_kw(
                self.db, self.uid, self.api_key,
                'product.product', 'search_read',
                [domain],
                {'fields': fields}
            )
            
            logger.info(f"Fetched {len(products)} products")
            return products

        except Exception as e:
            logger.error(f"Error fetching products: {e}")
            return []

    def create_sales_order(self, customer_id: int, warehouse_id: int = None, date_order: str = None, client_order_ref: str = None) -> int:
        """Create a Sales Order header"""
        if not self.connected:
            raise ConnectionError("Not connected to Odoo")

        try:
            vals = {
                'partner_id': customer_id,
            }
            
            if warehouse_id:
                vals['warehouse_id'] = warehouse_id
            
            if date_order:
                vals['date_order'] = date_order
                
            if client_order_ref:
                vals['client_order_ref'] = client_order_ref

            so_id = self.models.execute_kw(
                self.db, self.uid, self.api_key,
                'sale.order', 'create',
                [vals]
            )
            
            # Get the name (SO Reference) of the created order
            so_name = self.models.execute_kw(
                self.db, self.uid, self.api_key,
                'sale.order', 'read',
                [[so_id]],
                {'fields': ['name']}
            )[0]['name']
            
            logger.info(f"Created Sales Order {so_name} (ID: {so_id})")
            return so_id, so_name

        except Exception as e:
            logger.error(f"Error creating Sales Order: {e}")
            raise e

    def create_sales_order_line(self, order_id: int, product_id: int, qty: float, price_unit: float) -> int:
        """Create a Sales Order Line"""
        if not self.connected:
            raise ConnectionError("Not connected to Odoo")

        try:
            # First, simple create
            vals = {
                'order_id': order_id,
                'product_id': product_id,
                'product_uom_qty': qty,
                'price_unit': price_unit
            }

            line_id = self.models.execute_kw(
                self.db, self.uid, self.api_key,
                'sale.order.line', 'create',
                [vals]
            )
            
            return line_id

        except Exception as e:
            logger.error(f"Error creating SO Line for SO {order_id}, Product {product_id}: {e}")
            raise e

    def get_partner_id_by_name(self, name: str) -> Optional[int]:
        """Find partner ID by name"""
        if not self.connected:
            raise ConnectionError("Not connected to Odoo")

        domain = [['name', '=', name]]
        partners = self.models.execute_kw(
            self.db, self.uid, self.api_key,
            'res.partner', 'search_read',
            [domain],
            {'fields': ['id'], 'limit': 1}
        )

        return partners[0]['id'] if partners else None

    def get_latest_so_number(self) -> Optional[int]:
        """
        Fetch the numeric part of the highest-numbered sale.order name.
        Assumes SO names follow the pattern OATS00NNNN (e.g. OATS003270 → 3270).
        Returns None if no matching orders are found or the pattern doesn't match.
        """
        if not self.connected:
            raise ConnectionError("Not connected to Odoo")

        try:
            orders = self.models.execute_kw(
                self.db, self.uid, self.api_key,
                'sale.order', 'search_read',
                [[['name', 'like', 'OATS00']]],
                {'fields': ['name'], 'order': 'name desc', 'limit': 1}
            )

            if not orders:
                logger.warning("No OATS00* sales orders found in Odoo")
                return None

            latest_name = orders[0]['name']
            match = re.search(r'OATS00(\d+)', latest_name)
            if match:
                number = int(match.group(1))
                logger.info(f"Latest SO: {latest_name} → number {number}")
                return number

            logger.warning(f"SO name '{latest_name}' did not match expected pattern OATS00NNNN")
            return None

        except Exception as e:
            logger.error(f"Error fetching latest SO number: {e}")
            return None

    def get_open_sales_orders(self, partner_name: str) -> List[Dict[str, Any]]:
        """
        Fetch open (non-delivered, non-tester) sale.order records for a specific store.

        Filters:
        - partner_id.name matches partner_name
        - delivery_status != 'delivered'
        - x_studio_tester = False
        """
        if not self.connected:
            raise ConnectionError("Not connected to Odoo")

        try:
            partner_id = self.get_partner_id_by_name(partner_name)
            if not partner_id:
                logger.warning(f"Partner not found in Odoo: {partner_name}")
                return []

            domain = [
                ['partner_id', '=', partner_id],
                ['delivery_status', '!=', 'delivered'],
                ['x_studio_tester', '=', False],
            ]

            fields = [
                'name',
                'partner_id',
                'warehouse_id',
                'create_date',
                'state',
                'delivery_status',
                'client_order_ref',
                'x_studio_delivery_date',
                'x_studio_tester',
            ]

            orders = self.models.execute_kw(
                self.db, self.uid, self.api_key,
                'sale.order', 'search_read',
                [domain],
                {'fields': fields, 'order': 'create_date desc', 'limit': 100}
            )

            logger.info(f"Found {len(orders)} open SOs for partner: {partner_name}")
            return orders

        except Exception as e:
            logger.error(f"Error fetching open SOs for {partner_name}: {e}")
            return []

    def append_lines_to_order(
        self, so_id: int, lines: List[Dict[str, Any]]
    ) -> Tuple[List[int], List[Dict[str, Any]]]:
        """
        Append sale.order.line records to an existing sale.order.

        Each dict in lines must contain:
            product_id: int
            product_uom_qty: float
            price_unit: float

        Returns (created_line_ids, failed_lines).
        Note: XML-RPC has no transaction support — partial writes are possible.
        """
        if not self.connected:
            raise ConnectionError("Not connected to Odoo")

        created_ids = []
        failed_lines = []

        for i, line in enumerate(lines):
            try:
                line_id = self.create_sales_order_line(
                    order_id=so_id,
                    product_id=line['product_id'],
                    qty=line['product_uom_qty'],
                    price_unit=line['price_unit'],
                )
                created_ids.append(line_id)
            except Exception as e:
                logger.error(f"Failed to append line {i} (product {line.get('product_id')}) to SO {so_id}: {e}")
                failed_lines.append({'index': i, 'line': line, 'error': str(e)})

        if failed_lines:
            logger.warning(
                f"append_lines_to_order: {len(failed_lines)} of {len(lines)} lines failed for SO {so_id}"
            )

        return created_ids, failed_lines
