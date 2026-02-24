"""Return form PDF splitting, Gemini OCR extraction, and barcode mapping."""

import io
import json
import logging
from typing import Any, Callable, Optional

from pypdf import PdfReader, PdfWriter

logger = logging.getLogger(__name__)


def _tnt_upc_to_ean13(tnt_upc: str) -> Optional[str]:
    """Convert a T&T 13-digit UPC (leading 0 + 12 digits) to an EAN-13 barcode.

    T&T format: "0" + first 12 digits of EAN-13 (drops the check digit).
    Reverse: strip leading "0" -> 12 digits -> compute check digit -> EAN-13.
    """
    if not tnt_upc or len(tnt_upc) < 12:
        return None

    # Strip leading zero if present to get the 12-digit base
    if tnt_upc.startswith("0"):
        base_12 = tnt_upc[1:13]
    else:
        base_12 = tnt_upc[:12]

    if len(base_12) != 12 or not base_12.isdigit():
        return None

    total = sum(int(d) * (1 if i % 2 == 0 else 3) for i, d in enumerate(base_12))
    check = (10 - (total % 10)) % 10
    return base_12 + str(check)


class ReturnExtractor:
    """Splits multi-page return PDFs and uses Gemini for OCR.

    Uses the google-genai SDK and sends PDF bytes directly to Gemini
    (no PNG conversion needed — much faster for scanned documents).
    """

    _PROMPT = (
        "Extract structured data from this scanned T&T Supermarket return form. "
        "The form contains a store name, document number (Doc No.), print date, "
        "and a table of returned items with: Item ID, UPC, Description (English), "
        "Chinese Description, Size/Pack, Class, Qty, Cost, Amount, Reason. "
        "Preserve Chinese text exactly as printed. Use null for illegible or missing fields. "
        'Return valid JSON: {"store_name": str, "doc_no": str, "print_date": str, '
        '"line_items": [{"item_id": str, "upc": str, "description_en": str, '
        '"description_cn": str, "size_pack": str, "item_class": str, '
        '"quantity": number, "cost": number, "amount": number, "reason": str}]}'
    )

    def __init__(self, api_key: str, model: str = "gemini-2.5-flash") -> None:
        self.api_key = api_key
        self.model_name = model
        self._client = None

    def _get_client(self) -> Any:
        """Lazy-init google-genai client."""
        if self._client is None:
            from google import genai

            self._client = genai.Client(api_key=self.api_key)
        return self._client

    # ── PDF splitting ─────────────────────────────────────────────────────────

    @staticmethod
    def split_pdf(pdf_bytes: bytes) -> list[bytes]:
        """Split a multi-page PDF into a list of single-page PDFs."""
        reader = PdfReader(io.BytesIO(pdf_bytes))
        pages: list[bytes] = []
        for page in reader.pages:
            writer = PdfWriter()
            writer.add_page(page)
            buf = io.BytesIO()
            writer.write(buf)
            pages.append(buf.getvalue())
        logger.info("Split PDF into %d page(s)", len(pages))
        return pages

    # ── PDF compression (re-encode scanned images as JPEG) ──────────────────

    @staticmethod
    def compress_page_pdf(page_pdf_bytes: bytes, jpeg_quality: int = 50) -> bytes:
        """Re-encode embedded images in a single-page PDF as JPEG.

        Scanned return forms store pages as uncompressed PNG images inside the
        PDF.  Re-encoding at JPEG quality 50 typically achieves ~96% size
        reduction while keeping text perfectly legible for both human review
        and Gemini OCR.

        Returns the original bytes unchanged if compression fails or produces
        a larger result.
        """
        from PIL import Image

        try:
            reader = PdfReader(io.BytesIO(page_pdf_bytes))
            writer = PdfWriter()
            writer.add_page(reader.pages[0])

            for page in writer.pages:
                for img_obj in page.images:
                    pil_img = Image.open(io.BytesIO(img_obj.data))
                    if pil_img.mode == "RGBA":
                        pil_img = pil_img.convert("RGB")
                    img_obj.replace(pil_img, quality=jpeg_quality)

            buf = io.BytesIO()
            writer.write(buf)
            compressed = buf.getvalue()

            if len(compressed) < len(page_pdf_bytes):
                logger.info(
                    "Compressed page PDF: %dKB -> %dKB (%.0f%% smaller)",
                    len(page_pdf_bytes) // 1024,
                    len(compressed) // 1024,
                    100 * (1 - len(compressed) / len(page_pdf_bytes)),
                )
                return compressed

            logger.info("Compression did not reduce size; keeping original")
            return page_pdf_bytes
        except Exception as e:
            logger.warning("PDF compression failed, keeping original: %s", e)
            return page_pdf_bytes

    # ── PDF-to-image (for thumbnails only) ────────────────────────────────────

    @staticmethod
    def pdf_page_to_image(page_pdf_bytes: bytes, dpi: int = 72) -> bytes:
        """Convert a single-page PDF to a PNG image (thumbnails only).

        Uses low DPI (72) since this is only for UI thumbnails, not OCR.
        """
        from pdf2image import convert_from_bytes

        images = convert_from_bytes(page_pdf_bytes, dpi=dpi, fmt="png")
        buf = io.BytesIO()
        images[0].save(buf, format="PNG")
        return buf.getvalue()

    # ── Gemini OCR (sends PDF directly — no image conversion) ─────────────────

    def extract_page(self, page_pdf_bytes: bytes, page_number: int) -> dict:
        """Send a single-page PDF to Gemini and return structured JSON."""
        from google.genai import types

        client = self._get_client()
        pdf_part = types.Part.from_bytes(data=page_pdf_bytes, mime_type="application/pdf")

        response = client.models.generate_content(
            model=self.model_name,
            contents=[self._PROMPT, pdf_part],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
            ),
        )

        data = json.loads(response.text)
        data["page_number"] = page_number
        return data

    # ── Odoo product matching ─────────────────────────────────────────────────

    @staticmethod
    def match_products_to_odoo(
        extracted_pages: list[dict],
        odoo_client: Any,
    ) -> tuple[list[dict], list[dict]]:
        """Match extracted items to Odoo products by item_id (default_code) and UPC (barcode).

        Returns:
            (matched_pages, unmatched_items)
        """
        # Collect all item_ids and UPCs for batch lookup
        all_item_ids: list[str] = []
        all_ean13s: list[str] = []
        upc_to_ean13_map: dict[str, str] = {}

        for page in extracted_pages:
            for item in page.get("line_items", []):
                iid = item.get("item_id")
                if iid:
                    all_item_ids.append(iid)
                upc = item.get("upc")
                if upc:
                    ean13 = _tnt_upc_to_ean13(upc)
                    if ean13:
                        all_ean13s.append(ean13)
                        upc_to_ean13_map[upc] = ean13

        # Fetch products by internal reference (one code may map to multiple variants)
        products_by_code: dict[str, list[dict]] = {}
        if all_item_ids:
            products = odoo_client.get_products(internal_references=list(set(all_item_ids)))
            for p in products:
                code = p.get("default_code")
                if code:
                    products_by_code.setdefault(code, []).append(p)

        # Fetch products by barcode (for UPC fallback)
        products_by_barcode: dict[str, dict] = {}
        if all_ean13s:
            barcode_products = odoo_client.get_products_by_barcode(list(set(all_ean13s)))
            for p in barcode_products:
                bc = p.get("barcode")
                if bc:
                    products_by_barcode[bc] = p

        # Match each line item (may expand one item into multiple rows)
        unmatched: list[dict] = []
        for page in extracted_pages:
            new_line_items: list[dict] = []
            for item in page.get("line_items", []):
                matched = False
                iid = item.get("item_id")

                # Try matching by item_id -> default_code
                if iid and iid in products_by_code:
                    variants = products_by_code[iid]
                    if len(variants) == 1:
                        # Single product — keep original qty
                        p = variants[0]
                        item["odoo_product_id"] = p["id"]
                        item["odoo_product_name"] = p["name"]
                        item["odoo_barcode"] = p.get("barcode")
                        item["matched"] = True
                        new_line_items.append(item)
                    else:
                        # Multiple variants — expand into one row per variant, qty=0
                        for p in variants:
                            expanded = dict(item)  # shallow copy
                            expanded["odoo_product_id"] = p["id"]
                            expanded["odoo_product_name"] = p["name"]
                            expanded["odoo_barcode"] = p.get("barcode")
                            expanded["matched"] = True
                            expanded["quantity"] = 0
                            expanded["actual_received"] = 0
                            expanded["amount"] = 0
                            new_line_items.append(expanded)
                    matched = True

                # Fallback: try UPC -> EAN-13 barcode
                if not matched:
                    upc = item.get("upc")
                    ean13 = upc_to_ean13_map.get(upc) if upc else None
                    if ean13 and ean13 in products_by_barcode:
                        p = products_by_barcode[ean13]
                        item["odoo_product_id"] = p["id"]
                        item["odoo_product_name"] = p["name"]
                        item["odoo_barcode"] = p.get("barcode")
                        item["matched"] = True
                        new_line_items.append(item)
                        matched = True

                if not matched:
                    item["matched"] = False
                    new_line_items.append(item)
                    unmatched.append({
                        "page": page.get("page_number"),
                        "item_id": iid,
                        "upc": item.get("upc"),
                        "description": item.get("description_en"),
                    })

            page["line_items"] = new_line_items

        return extracted_pages, unmatched
