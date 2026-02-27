"""Invoice/tester-sheet PDF splitting, Gemini OCR extraction, and order grouping."""

import io
import json
import logging
from collections import defaultdict
from typing import Any, Optional

from pypdf import PdfReader, PdfWriter

logger = logging.getLogger(__name__)


class InvoiceExtractor:
    """Splits multi-page invoice/tester PDFs and uses Gemini to extract metadata.

    Identifies each page's sales order by the Ref# in the footer (e.g. OATS003781),
    detects the document type (invoice / tnt_invoice / tester_sheet), and extracts
    the total amount from the last page of invoices.
    """

    _PROMPT = (
        "You are analyzing a scanned page from a signed delivery document. "
        "This page is from one of these document types:\n"
        "1. Regular Invoice — Atiara's own invoice, header says 'Invoice' or 'Order'\n"
        "2. T&T Invoice — T&T Supermarket's specified invoice format\n"
        "3. Tester Sheet — A product tester/sampling sheet from T&T\n\n"
        "Extract ALL of the following from this page:\n\n"
        "1. REFERENCE NUMBER (ref_number): Look at the FOOTER area for a reference "
        "number matching the pattern 'OATS' followed by digits (e.g. OATS003781). "
        "It may appear as 'Ref#', 'Reference', or just the OATS number.\n\n"
        "2. CUSTOMER NAME (customer_name): The customer/store name on the document. "
        "For T&T documents this is typically the store name like "
        "'Promenade Store - 009' or 'T&T Supermarket - Store 009'. "
        "Look in the 'Ship To', 'Deliver To', 'Customer', or address area.\n\n"
        "3. PO REFERENCE (po_ref): The purchase order number from the customer. "
        "This is NOT the OATS reference. It is the customer's own PO number, "
        "typically a numeric string like '620510938'. Look for labels like "
        "'PO#', 'PO No.', 'Purchase Order', 'Customer Reference', or 'Your Ref'.\n\n"
        "4. DOCUMENT TYPE (doc_type): One of 'invoice', 'tnt_invoice', 'tester_sheet'.\n\n"
        "5. LAST PAGE (is_last_page): Whether this is the last page of the document "
        "(has grand total, signature, or final 'Total' line).\n\n"
        "6. TOTAL AMOUNT (total_amount): ALWAYS look for the grand total on EVERY page, "
        "not just the last page. Due to formatting issues the total sometimes appears "
        "on an earlier page. Search for 'Total', 'Grand Total', 'Amount Due', 'TOTAL', "
        "or any prominent dollar amount that represents the invoice grand total "
        "(not subtotals or line item amounts). "
        "Return as a number without currency symbols. "
        "For tester sheets, set to null.\n\n"
        "7. PAGE CONTENT SUMMARY (page_content_summary): 2-3 words describing the page.\n\n"
        "Return valid JSON with this exact format:\n"
        '{"ref_number": "OATS003781", "customer_name": "Promenade Store - 009", '
        '"po_ref": "620510938", "doc_type": "invoice", '
        '"is_last_page": false, "total_amount": 778.57, '
        '"page_content_summary": "product listing"}\n\n'
        "Set total_amount to null ONLY if there is no grand total visible on this page. "
        "If you see a total, always extract it even if this is not the last page."
    )

    def __init__(self, api_key: str, model: str = "gemini-2.5-flash") -> None:
        self.api_key = api_key
        self.model_name = model
        self._client: Any = None

    def _get_client(self) -> Any:
        """Lazy-init google-genai client."""
        if self._client is None:
            from google import genai

            self._client = genai.Client(api_key=self.api_key)
        return self._client

    # ── PDF splitting (reuses same logic as ReturnExtractor) ─────────────────

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

    # ── PDF compression ──────────────────────────────────────────────────────

    @staticmethod
    def compress_page_pdf(page_pdf_bytes: bytes, jpeg_quality: int = 50) -> bytes:
        """Re-encode embedded images in a single-page PDF as JPEG.

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

            return page_pdf_bytes
        except Exception as e:
            logger.warning("PDF compression failed, keeping original: %s", e)
            return page_pdf_bytes

    # ── Gemini OCR ───────────────────────────────────────────────────────────

    def extract_page(self, page_pdf_bytes: bytes, page_number: int) -> dict:
        """Send a single-page PDF to Gemini and return structured metadata."""
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

        if not response.text:
            raise ValueError(f"Empty Gemini response for page {page_number}")

        raw = json.loads(response.text)

        # Normalize and validate response
        total_raw = raw.get("total_amount")
        return {
            "page_number": page_number,
            "ref_number": raw.get("ref_number"),
            "customer_name": raw.get("customer_name"),
            "po_ref": raw.get("po_ref"),
            "doc_type": raw.get("doc_type", "invoice"),
            "is_last_page": bool(raw.get("is_last_page", False)),
            "total_amount": float(total_raw) if total_raw is not None else None,
            "page_content_summary": raw.get("page_content_summary", ""),
        }

    # ── Merge pages back into a single PDF ───────────────────────────────────

    @staticmethod
    def merge_pages(page_pdfs: list[bytes]) -> bytes:
        """Merge multiple single-page PDFs into one document."""
        writer = PdfWriter()
        for page_pdf in page_pdfs:
            reader = PdfReader(io.BytesIO(page_pdf))
            for page in reader.pages:
                writer.add_page(page)
        buf = io.BytesIO()
        writer.write(buf)
        return buf.getvalue()

    # ── Group pages by order ─────────────────────────────────────────────────

    @staticmethod
    def group_pages_by_order(
        page_data: list[dict],
    ) -> dict[str, dict]:
        """Group extracted page metadata by ref_number.

        Returns a dict keyed by ref_number, each containing:
            - ref_number: str
            - customer_name: str | None
            - po_ref: str | None
            - doc_type: str (most common doc_type across pages)
            - page_indices: list[int] (0-based indices into original page list)
            - total_amount: float | None (from the last page of invoices)
            - page_count: int
        """
        groups: dict[str, list[dict]] = defaultdict(list)

        for i, page in enumerate(page_data):
            ref = page.get("ref_number")
            if not ref:
                ref = f"Unknown_{i + 1}"
            groups[ref].append({**page, "_index": i})

        result: dict[str, dict] = {}
        for ref, pages in groups.items():
            # Determine doc_type by majority vote
            doc_types = [p.get("doc_type", "invoice") for p in pages]
            doc_type = max(set(doc_types), key=doc_types.count)

            # Find total from last page (or any page that has it)
            total_amount: Optional[float] = None
            for p in reversed(pages):
                if p.get("total_amount") is not None:
                    total_amount = p["total_amount"]
                    break

            # Take first non-null customer_name and po_ref across pages
            customer_name: Optional[str] = None
            po_ref: Optional[str] = None
            for p in pages:
                if not customer_name and p.get("customer_name"):
                    customer_name = p["customer_name"]
                if not po_ref and p.get("po_ref"):
                    po_ref = p["po_ref"]

            result[ref] = {
                "ref_number": ref,
                "customer_name": customer_name,
                "po_ref": po_ref,
                "doc_type": doc_type,
                "page_indices": [p["_index"] for p in pages],
                "total_amount": total_amount,
                "page_count": len(pages),
            }

        return result
