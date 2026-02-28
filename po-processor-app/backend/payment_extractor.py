"""Payment statement PDF parser for T&T cheque remittances.

Parses text-based payment statement PDFs from T&T Supermarket, extracting
cheque metadata and line items. Each reference is decoded to identify
the store, document type (invoice/credit/return), and Odoo search reference.
"""

import io
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import pdfplumber


@dataclass(frozen=True)
class PaymentLineItem:
    """A single line from the payment statement."""

    reference_number: str
    invoice_date: str
    gross_amount: float
    discount_taken: float
    net_amount: float
    is_credit: bool
    store_code: str
    store_id: str
    store_name: str
    doc_type: str  # "invoice" | "credit_note" | "return"
    odoo_search_ref: str


@dataclass
class PaymentStatement:
    """Parsed payment statement."""

    cheque_number: str
    cheque_date: str
    total_amount: float
    payer: str
    payee: str
    lines: List[PaymentLineItem] = field(default_factory=list)
    page_count: int = 0


# Regex for data lines in the payment statement.
# Matches:  REF_NUMBER  MM/DD/YY  GROSS_AMOUNT [-]  .DISCOUNT  NET_AMOUNT [-]
_LINE_RE = re.compile(
    r"^(\S+)"  # reference number (no spaces)
    r"\s+(\d{2}/\d{2}/\d{2})"  # invoice date MM/DD/YY
    r"\s+([\d,]+\.\d{2})\s*(-?)"  # gross amount + optional credit "-"
    r"\s+\.(\d{2})"  # discount (.00 format)
    r"\s+([\d,]+\.\d{2})\s*(-?)"  # net amount + optional credit "-"
)

# Cheque number: 6 consecutive digits appearing near "533131" style
_CHEQUE_NUM_RE = re.compile(r"\b(\d{6})\b")

# Date: "Feb 01, 26" or similar
_DATE_RE = re.compile(
    r"((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2},\s*\d{2,4})"
)

# Total amount on last page
_TOTAL_RE = re.compile(r"TOTAL\s+AMOUNT:\s*([\d,]+\.\d{2})")

# Reference prefix pattern: 2-letter store code + "G5" + optional digit(s) + "-"
_REF_PREFIX_RE = re.compile(r"^([A-Z]{2})G5\d*-(.+)$")


class PaymentExtractor:
    """Extracts payment statement data from T&T cheque remittance PDFs."""

    @staticmethod
    def extract(
        pdf_bytes: bytes,
        store_names: Dict[int, str],
        store_codes: Dict[str, int],
    ) -> PaymentStatement:
        """Extract a payment statement from PDF bytes.

        Args:
            pdf_bytes: Raw PDF file content.
            store_names: Mapping of store ID (int) to full store name string.
            store_codes: Mapping of 2-letter code to store ID (int).

        Returns:
            PaymentStatement with all parsed lines.
        """
        lines: List[PaymentLineItem] = []
        cheque_number = ""
        cheque_date = ""
        total_amount = 0.0
        page_count = 0

        with pdfplumber.open(PaymentExtractor._bytes_to_stream(pdf_bytes)) as pdf:
            page_count = len(pdf.pages)

            for page_idx, page in enumerate(pdf.pages):
                text = page.extract_text() or ""
                page_lines = text.split("\n")

                # Extract header from page 1
                if page_idx == 0:
                    cheque_number, cheque_date = PaymentExtractor._parse_header(
                        page_lines
                    )

                # Extract total from any page (usually last)
                for raw_line in page_lines:
                    total_match = _TOTAL_RE.search(raw_line)
                    if total_match:
                        total_amount = PaymentExtractor._parse_amount(
                            total_match.group(1)
                        )

                # Parse data lines
                in_data_section = False
                for raw_line in page_lines:
                    stripped = raw_line.strip()

                    # Start of data section after header row
                    if "REFERENCE NUMBER" in stripped and "INVOICE DATE" in stripped:
                        in_data_section = True
                        continue

                    # End markers
                    if "***VOID***" in stripped or "TOTAL AMOUNT:" in stripped:
                        in_data_section = False
                        continue

                    if not in_data_section:
                        continue

                    # Skip empty lines and non-data lines
                    if not stripped or stripped.startswith("Page "):
                        continue

                    parsed = PaymentExtractor._parse_line(
                        stripped, store_names, store_codes
                    )
                    if parsed:
                        lines.append(parsed)

        return PaymentStatement(
            cheque_number=cheque_number,
            cheque_date=cheque_date,
            total_amount=total_amount,
            payer="T & T SUPERMARKET INC.",
            payee="ATIARA TRADING INC.",
            lines=lines,
            page_count=page_count,
        )

    @staticmethod
    def _bytes_to_stream(pdf_bytes: bytes) -> io.BytesIO:
        """Convert bytes to a file-like object for pdfplumber."""
        return io.BytesIO(pdf_bytes)

    @staticmethod
    def _parse_header(page_lines: List[str]) -> Tuple[str, str]:
        """Extract cheque number and date from page 1 lines."""
        cheque_number = ""
        cheque_date = ""

        for line in page_lines:
            # Look for 6-digit cheque number (appears near top)
            if not cheque_number:
                num_match = _CHEQUE_NUM_RE.search(line)
                if num_match and "Page" not in line:
                    cheque_number = num_match.group(1)

            # Look for date like "Feb 01, 26"
            if not cheque_date:
                date_match = _DATE_RE.search(line)
                if date_match:
                    cheque_date = date_match.group(1)

            if cheque_number and cheque_date:
                break

        return cheque_number, cheque_date

    @staticmethod
    def _parse_line(
        line: str,
        store_names: Dict[int, str],
        store_codes: Dict[str, int],
    ) -> Optional[PaymentLineItem]:
        """Parse a single payment statement data line."""
        match = _LINE_RE.match(line)
        if not match:
            return None

        reference_number = match.group(1)
        invoice_date = match.group(2)
        gross_amount = PaymentExtractor._parse_amount(match.group(3))
        gross_credit = match.group(4) == "-"
        discount_taken = float(f"0.{match.group(5)}")
        net_amount = PaymentExtractor._parse_amount(match.group(6))
        net_credit = match.group(7) == "-"

        is_credit = gross_credit or net_credit

        store_code, store_id, store_name, doc_type, odoo_search_ref = (
            PaymentExtractor._decode_reference(
                reference_number, store_names, store_codes
            )
        )

        return PaymentLineItem(
            reference_number=reference_number,
            invoice_date=invoice_date,
            gross_amount=gross_amount,
            discount_taken=discount_taken,
            net_amount=net_amount,
            is_credit=is_credit,
            store_code=store_code,
            store_id=store_id,
            store_name=store_name,
            doc_type=doc_type,
            odoo_search_ref=odoo_search_ref,
        )

    @staticmethod
    def _decode_reference(
        raw_ref: str,
        store_names: Dict[int, str],
        store_codes: Dict[str, int],
    ) -> Tuple[str, str, str, str, str]:
        """Decode a T&T payment reference number.

        Returns:
            (store_code, store_id, store_name, doc_type, odoo_search_ref)
        """
        ref_match = _REF_PREFIX_RE.match(raw_ref)
        if not ref_match:
            # Fallback: try splitting on first dash
            parts = raw_ref.split("-", 1)
            if len(parts) == 2:
                store_code = parts[0][:2] if len(parts[0]) >= 2 else "??"
                doc_ref = parts[1]
            else:
                return ("??", "???", "Unknown", "invoice", raw_ref)
        else:
            store_code = ref_match.group(1)
            doc_ref = ref_match.group(2)

        # Look up store
        store_id_int = store_codes.get(store_code)
        store_id = f"{store_id_int:03d}" if store_id_int else "???"

        # Extract short store name from full name
        full_name = store_names.get(store_id_int, "") if store_id_int else ""
        if full_name:
            # "T&T Supermarket Inc., Calgary  - 007" -> "Calgary"
            comma_pos = full_name.find(",")
            dash_pos = full_name.rfind(" - ")
            if comma_pos >= 0 and dash_pos > comma_pos:
                store_name = full_name[comma_pos + 1 : dash_pos].strip()
            else:
                store_name = full_name
        else:
            store_name = f"Store {store_id}"

        # Normalize known typos: OATSO -> OATS0
        if doc_ref.startswith("OATSO"):
            doc_ref = "OATS0" + doc_ref[5:]

        # Classify document type
        if doc_ref.upper().startswith("OATS"):
            doc_type = "invoice"
        elif doc_ref.upper().startswith("ASO") or doc_ref.upper().startswith("SO"):
            doc_type = "credit_note"
        elif doc_ref.isdigit():
            doc_type = "return"
        else:
            doc_type = "invoice"  # fallback

        return (store_code, store_id, store_name, doc_type, doc_ref)

    @staticmethod
    def _parse_amount(amount_str: str) -> float:
        """Parse an amount string like '1,619.73' to float."""
        return float(amount_str.replace(",", ""))
