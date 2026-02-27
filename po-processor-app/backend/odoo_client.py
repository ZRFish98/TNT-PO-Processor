import base64
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

    def create_sales_order(self, customer_id: int, warehouse_id: int = None, date_order: str = None, client_order_ref: str = None) -> Tuple[int, str]:
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
            vals = {
                'order_id': order_id,
                'product_id': product_id,
                'product_uom_qty': qty,
                'price_unit': price_unit,
                'x_studio_price_lock': True,
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
        """Find partner ID by name or display_name.

        Odoo child contacts have a short ``name`` (e.g. 'Woodbine Store - 021')
        and a computed ``display_name`` that includes the parent company
        (e.g. 'T&T Supermarket Inc., Woodbine Store - 021').  We try
        ``display_name`` first (settings.yaml uses that format), then fall back
        to ``name``.
        """
        if not self.connected:
            raise ConnectionError("Not connected to Odoo")

        for field in ('display_name', 'name'):
            partners = self.models.execute_kw(
                self.db, self.uid, self.api_key,
                'res.partner', 'search_read',
                [[[field, '=', name]]],
                {'fields': ['id'], 'limit': 1}
            )
            if partners:
                return partners[0]['id']

        return None

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
                ['delivery_status', '!=', 'full'],
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

    def get_open_orders_for_stores(self, partner_names: List[str]) -> Dict[str, Dict[str, Any]]:
        """
        Fetch the latest undelivered, non-tester SO for each partner name.

        Returns {requested_name: {id, name, client_order_ref, ...}} mapping.
        Uses the same filters as get_open_sales_orders (delivery_status != 'delivered').
        Looks up partners individually to handle minor name mismatches.
        """
        if not self.connected:
            raise ConnectionError("Not connected to Odoo")

        result: Dict[str, Dict[str, Any]] = {}

        # Collect all partner IDs, mapping back to the requested name
        partner_id_to_requested: Dict[int, str] = {}
        for name in partner_names:
            pid = self.get_partner_id_by_name(name)
            if pid:
                partner_id_to_requested[pid] = name
            else:
                logger.debug(f"Partner not found in Odoo: {name}")

        if not partner_id_to_requested:
            logger.warning(f"No partners found in Odoo for any of {len(partner_names)} names")
            return {}

        try:
            partner_ids = list(partner_id_to_requested.keys())

            domain = [
                ['partner_id', 'in', partner_ids],
                ['delivery_status', '!=', 'full'],
                ['x_studio_tester', '=', False],
            ]
            fields = [
                'name', 'partner_id', 'state', 'delivery_status',
                'client_order_ref', 'x_studio_delivery_date', 'create_date',
            ]
            orders = self.models.execute_kw(
                self.db, self.uid, self.api_key,
                'sale.order', 'search_read',
                [domain],
                {'fields': fields, 'order': 'create_date desc', 'limit': 500}
            )

            # Group by requested partner name, keep only the latest per partner
            for order in orders:
                pid = order['partner_id'][0] if isinstance(order['partner_id'], (list, tuple)) else order['partner_id']
                requested_name = partner_id_to_requested.get(pid)
                if requested_name and requested_name not in result:
                    result[requested_name] = order

            logger.info(f"Found open SOs for {len(result)} of {len(partner_names)} stores")
            return result

        except Exception as e:
            logger.error(f"Error fetching open orders for stores: {e}")
            return {}

    def get_all_undelivered_orders(self) -> List[Dict[str, Any]]:
        """Fetch all undelivered, non-tester sales orders from Odoo."""
        if not self.connected:
            raise ConnectionError("Not connected to Odoo")

        try:
            domain = [
                ['delivery_status', '!=', 'full'],
                ['x_studio_tester', '=', False],
            ]
            fields = [
                'name', 'date_order', 'partner_id',
                'warehouse_id', 'delivery_status',
            ]
            orders = self.models.execute_kw(
                self.db, self.uid, self.api_key,
                'sale.order', 'search_read',
                [domain],
                {'fields': fields, 'order': 'name desc', 'limit': 500}
            )
            logger.info(f"Fetched {len(orders)} undelivered orders")
            return orders

        except Exception as e:
            logger.error(f"Error fetching undelivered orders: {e}")
            return []

    def update_client_order_ref(self, so_id: int, new_ref: str) -> bool:
        """Update the client_order_ref field on an existing SO."""
        if not self.connected:
            raise ConnectionError("Not connected to Odoo")

        try:
            self.models.execute_kw(
                self.db, self.uid, self.api_key,
                'sale.order', 'write',
                [[so_id], {'client_order_ref': new_ref}]
            )
            logger.info(f"Updated client_order_ref on SO {so_id}")
            return True
        except Exception as e:
            logger.error(f"Error updating client_order_ref on SO {so_id}: {e}")
            return False

    def create_section_line(self, order_id: int, name: str) -> Optional[int]:
        """Create a section header line on a sale.order (display_type='line_section')."""
        if not self.connected:
            raise ConnectionError("Not connected to Odoo")

        try:
            line_id = self.models.execute_kw(
                self.db, self.uid, self.api_key,
                'sale.order.line', 'create',
                [{'order_id': order_id, 'display_type': 'line_section', 'name': name}]
            )
            logger.info(f"Created section line on SO {order_id}: {name}")
            return line_id
        except Exception as e:
            logger.error(f"Error creating section line on SO {order_id}: {e}")
            return None

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

    # ── Credit Note (Return) Methods ──────────────────────────────────────────

    def get_products_by_barcode(self, barcodes: List[str]) -> List[Dict[str, Any]]:
        """Search product.product by a list of barcodes."""
        if not self.connected:
            raise ConnectionError("Not connected to Odoo")

        try:
            products = self.models.execute_kw(
                self.db, self.uid, self.api_key,
                'product.product', 'search_read',
                [[['barcode', 'in', barcodes]]],
                {'fields': ['id', 'name', 'barcode', 'default_code']}
            )
            logger.info(f"Found {len(products)} products by barcode")
            return products
        except Exception as e:
            logger.error(f"Error fetching products by barcode: {e}")
            return []

    def get_credit_notes_by_ref(self, refs: List[str]) -> List[Dict[str, Any]]:
        """Search account.move (credit notes) by ref values to detect duplicates."""
        if not self.connected:
            raise ConnectionError("Not connected to Odoo")

        try:
            domain = [
                ['move_type', '=', 'out_refund'],
                ['ref', 'in', refs],
            ]
            results = self.models.execute_kw(
                self.db, self.uid, self.api_key,
                'account.move', 'search_read',
                [domain],
                {'fields': ['id', 'name', 'ref', 'state', 'partner_id', 'invoice_date']}
            )
            logger.info(f"Found {len(results)} existing credit notes for {len(refs)} ref(s)")
            return results
        except Exception as e:
            logger.error(f"Error checking credit notes by ref: {e}")
            return []

    def create_credit_note(
        self,
        partner_id: int,
        date: Optional[str] = None,
        ref: Optional[str] = None,
        lines: Optional[List[Dict[str, Any]]] = None,
    ) -> Tuple[Optional[int], Optional[str]]:
        """Create an account.move with move_type='out_refund' (credit note).

        Each line dict should contain:
            product_id: int
            quantity: float
            price_unit: float
            name: str (description)

        Returns (move_id, move_name) or (None, None) on failure.
        """
        if not self.connected:
            raise ConnectionError("Not connected to Odoo")

        try:
            vals: Dict[str, Any] = {
                'move_type': 'out_refund',
                'partner_id': partner_id,
            }
            if date:
                vals['invoice_date'] = date
            if ref:
                vals['ref'] = ref

            if lines:
                vals['invoice_line_ids'] = [
                    (0, 0, {
                        'product_id': ln['product_id'],
                        'quantity': ln['quantity'],
                        'price_unit': ln['price_unit'],
                        'name': ln.get('name', ''),
                    })
                    for ln in lines
                ]

            move_id = self.models.execute_kw(
                self.db, self.uid, self.api_key,
                'account.move', 'create',
                [vals]
            )

            move_data = self.models.execute_kw(
                self.db, self.uid, self.api_key,
                'account.move', 'read',
                [[move_id]],
                {'fields': ['name']}
            )
            move_name = move_data[0]['name'] if move_data else str(move_id)

            logger.info(f"Created credit note {move_name} (ID: {move_id})")
            return move_id, move_name

        except Exception as e:
            logger.error(f"Error creating credit note: {e}")
            return None, None

    def attach_pdf_to_record(
        self,
        res_model: str,
        res_id: int,
        filename: str,
        pdf_bytes: bytes,
    ) -> Optional[int]:
        """Create an ir.attachment linked to a record with base64-encoded PDF."""
        if not self.connected:
            raise ConnectionError("Not connected to Odoo")

        try:
            attachment_id = self.models.execute_kw(
                self.db, self.uid, self.api_key,
                'ir.attachment', 'create',
                [{
                    'name': filename,
                    'type': 'binary',
                    'datas': base64.b64encode(pdf_bytes).decode('ascii'),
                    'res_model': res_model,
                    'res_id': res_id,
                    'mimetype': 'application/pdf',
                }]
            )
            logger.info(f"Attached {filename} to {res_model}/{res_id} (attachment ID: {attachment_id})")
            return attachment_id
        except Exception as e:
            logger.error(f"Error attaching PDF to {res_model}/{res_id}: {e}")
            return None

    def post_chatter_message(
        self,
        res_model: str,
        res_id: int,
        body: str,
        attachment_ids: Optional[List[int]] = None,
    ) -> bool:
        """Post a message to the chatter (mail.thread) of a record."""
        if not self.connected:
            raise ConnectionError("Not connected to Odoo")

        try:
            kwargs: Dict[str, Any] = {
                'body': body,
                'message_type': 'comment',
                'subtype_xmlid': 'mail.mt_note',
            }
            if attachment_ids:
                kwargs['attachment_ids'] = attachment_ids

            self.models.execute_kw(
                self.db, self.uid, self.api_key,
                res_model, 'message_post',
                [res_id],
                kwargs,
            )
            logger.info(f"Posted chatter message on {res_model}/{res_id}")
            return True
        except Exception as e:
            logger.error(f"Error posting chatter message on {res_model}/{res_id}: {e}")
            return False

    def get_credit_note_name(self, move_id: int) -> Optional[str]:
        """Read the name (sequence) of a credit note after it has been posted."""
        if not self.connected:
            return None
        try:
            data = self.models.execute_kw(
                self.db, self.uid, self.api_key,
                'account.move', 'read',
                [[move_id]],
                {'fields': ['name']}
            )
            return data[0]['name'] if data and data[0].get('name') else None
        except Exception:
            return None

    def confirm_credit_note(self, move_id: int) -> bool:
        """Confirm (post) a draft credit note via action_post."""
        if not self.connected:
            raise ConnectionError("Not connected to Odoo")

        try:
            self.models.execute_kw(
                self.db, self.uid, self.api_key,
                'account.move', 'action_post',
                [[move_id]]
            )
            logger.info(f"Confirmed credit note {move_id}")
            return True
        except Exception as e:
            logger.error(f"Error confirming credit note {move_id}: {e}")
            return False

    def create_return_transfer(self, move_id: int, action_id: int = 1443) -> bool:
        """Run the [AT] Create Return Transfer server action on a credit note."""
        if not self.connected:
            raise ConnectionError("Not connected to Odoo")

        try:
            self.models.execute_kw(
                self.db, self.uid, self.api_key,
                'ir.actions.server', 'run',
                [[action_id]],
                {'context': {
                    'active_model': 'account.move',
                    'active_id': move_id,
                    'active_ids': [move_id],
                }}
            )
            logger.info(f"Created return transfer for credit note {move_id}")
            return True
        except Exception as e:
            logger.error(f"Error creating return transfer for credit note {move_id}: {e}")
            return False

    def validate_return_transfer(self, move_id: int) -> Tuple[bool, str]:
        """Find and validate the return transfer(s) linked to a credit note.

        Reads x_studio_return_picking_ids from the credit note, sets done
        quantities on stock moves, then calls button_validate on each picking.
        """
        if not self.connected:
            raise ConnectionError("Not connected to Odoo")

        try:
            move_data = self.models.execute_kw(
                self.db, self.uid, self.api_key,
                'account.move', 'read',
                [[move_id]],
                {'fields': ['x_studio_return_picking_ids']}
            )
            if not move_data:
                return False, "Credit note not found"

            picking_ids = move_data[0].get('x_studio_return_picking_ids', [])
            if not picking_ids:
                return False, "No return transfers found on credit note"

            validated = 0
            for picking_id in picking_ids:
                # Read stock moves on this picking
                picking_data = self.models.execute_kw(
                    self.db, self.uid, self.api_key,
                    'stock.picking', 'read',
                    [[picking_id]],
                    {'fields': ['move_ids', 'state']}
                )
                if not picking_data or picking_data[0].get('state') == 'done':
                    validated += 1
                    continue

                # Set done quantity = demand on each stock move
                stock_move_ids = picking_data[0].get('move_ids', [])
                if stock_move_ids:
                    moves = self.models.execute_kw(
                        self.db, self.uid, self.api_key,
                        'stock.move', 'read',
                        [stock_move_ids],
                        {'fields': ['product_uom_qty', 'quantity']}
                    )
                    for mv in moves:
                        demand = mv.get('product_uom_qty', 0)
                        if demand > 0:
                            self.models.execute_kw(
                                self.db, self.uid, self.api_key,
                                'stock.move', 'write',
                                [[mv['id']], {'quantity': demand}]
                            )

                # Validate the picking
                result = self.models.execute_kw(
                    self.db, self.uid, self.api_key,
                    'stock.picking', 'button_validate',
                    [[picking_id]]
                )

                # Handle immediate transfer wizard if returned
                if isinstance(result, dict) and result.get('res_model'):
                    wizard_model = result['res_model']
                    wizard_ctx = result.get('context', {})
                    wizard_id = self.models.execute_kw(
                        self.db, self.uid, self.api_key,
                        wizard_model, 'create',
                        [{}],
                        {'context': wizard_ctx}
                    )
                    self.models.execute_kw(
                        self.db, self.uid, self.api_key,
                        wizard_model, 'process',
                        [[wizard_id]],
                        {'context': wizard_ctx}
                    )

                validated += 1

            logger.info(f"Validated {validated} return transfer(s) for credit note {move_id}")
            return True, f"{validated} transfer(s)"
        except Exception as e:
            logger.error(f"Error validating return transfer for credit note {move_id}: {e}")
            return False, str(e)

    # ── Invoice Verification / Delivery Methods ──────────────────────────────

    def get_order_with_delivery_details(self, so_name: str) -> Optional[Dict[str, Any]]:
        """Fetch a sales order by name with delivery-relevant fields."""
        if not self.connected:
            raise ConnectionError("Not connected to Odoo")

        try:
            orders = self.models.execute_kw(
                self.db, self.uid, self.api_key,
                'sale.order', 'search_read',
                [[['name', '=', so_name]]],
                {'fields': [
                    'name', 'amount_total', 'state', 'delivery_status',
                    'partner_id', 'warehouse_id', 'picking_ids',
                    'x_studio_delivery_date', 'date_order',
                    'payment_term_id', 'invoice_ids',
                ], 'limit': 1}
            )
            if not orders:
                logger.warning(f"Sales order not found: {so_name}")
                return None
            logger.info(f"Fetched delivery details for {so_name}")
            return orders[0]
        except Exception as e:
            logger.error(f"Error fetching delivery details for {so_name}: {e}")
            return None

    def get_delivery_pickings(self, picking_ids: List[int]) -> List[Dict[str, Any]]:
        """Read stock.picking records with delivery step details."""
        if not self.connected:
            raise ConnectionError("Not connected to Odoo")

        if not picking_ids:
            return []

        try:
            pickings = self.models.execute_kw(
                self.db, self.uid, self.api_key,
                'stock.picking', 'read',
                [picking_ids],
                {'fields': [
                    'name', 'state', 'picking_type_id', 'picking_type_code',
                    'scheduled_date', 'date_done', 'move_ids', 'origin',
                ]}
            )
            logger.info(f"Fetched {len(pickings)} delivery picking(s)")
            return pickings
        except Exception as e:
            logger.error(f"Error fetching delivery pickings: {e}")
            return []

    def validate_delivery_picking(self, picking_id: int) -> Tuple[bool, str]:
        """Validate a single delivery picking (set done qty = demand, then confirm).

        Follows the same pattern as validate_return_transfer but for outbound deliveries.
        """
        if not self.connected:
            raise ConnectionError("Not connected to Odoo")

        try:
            picking_data = self.models.execute_kw(
                self.db, self.uid, self.api_key,
                'stock.picking', 'read',
                [[picking_id]],
                {'fields': ['name', 'move_ids', 'state']}
            )
            if not picking_data:
                return False, "Picking not found"

            picking = picking_data[0]
            if picking.get('state') == 'done':
                return True, f"{picking['name']} already done"

            if picking.get('state') == 'cancel':
                return False, f"{picking['name']} is cancelled"

            # Set done quantity = demand on each stock move
            stock_move_ids = picking.get('move_ids', [])
            if stock_move_ids:
                moves = self.models.execute_kw(
                    self.db, self.uid, self.api_key,
                    'stock.move', 'read',
                    [stock_move_ids],
                    {'fields': ['product_uom_qty', 'quantity']}
                )
                for mv in moves:
                    demand = mv.get('product_uom_qty', 0)
                    if demand > 0:
                        self.models.execute_kw(
                            self.db, self.uid, self.api_key,
                            'stock.move', 'write',
                            [[mv['id']], {'quantity': demand}]
                        )

            # Validate the picking
            result = self.models.execute_kw(
                self.db, self.uid, self.api_key,
                'stock.picking', 'button_validate',
                [[picking_id]]
            )

            # Handle immediate transfer wizard if returned
            if isinstance(result, dict) and result.get('res_model'):
                wizard_model = result['res_model']
                wizard_ctx = result.get('context', {})
                wizard_id = self.models.execute_kw(
                    self.db, self.uid, self.api_key,
                    wizard_model, 'create',
                    [{}],
                    {'context': wizard_ctx}
                )
                self.models.execute_kw(
                    self.db, self.uid, self.api_key,
                    wizard_model, 'process',
                    [[wizard_id]],
                    {'context': wizard_ctx}
                )

            logger.info(f"Validated delivery picking {picking['name']}")
            return True, picking['name']
        except Exception as e:
            logger.error(f"Error validating delivery picking {picking_id}: {e}")
            return False, str(e)
