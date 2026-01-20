import pdfplumber
import re
import pandas as pd
import logging
from typing import List, Dict, Any, Tuple
from io import BytesIO

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class PDFExtractor:
    @staticmethod
    def validate_po_data(data: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Validate and clean extracted purchase order data"""
        if not data:
            return []
        
        valid_data = []
        required_fields = ['PO No.', 'Store ID', 'Internal Reference']
        
        for item in data:
            # Check required fields are present and not empty
            if all(item.get(field) and str(item.get(field)).strip() for field in required_fields):
                # Validate numeric fields
                try:
                    qty = float(item.get('# of Order', 0))
                    price = float(item.get('Price', 0))
                    
                    # Sanity checks
                    # Increased max price/qty check to avoid false positives on bulk orders
                    if qty > 0 and price > 0:
                        item['# of Order'] = qty
                        item['Price'] = price
                        valid_data.append(item)
                    else:
                        logger.warning(f"Invalid quantities/prices: qty={qty}, price={price}")
                except (ValueError, TypeError) as e:
                    logger.warning(f"Error converting numeric values: {e}")
                    continue
        
        return valid_data

    @staticmethod
    def extract_from_file(file_content: BytesIO, filename: str) -> Tuple[List[Dict[str, Any]], List[str]]:
        """Extract purchase order data from PDF file object"""
        data = []
        current_po = {}
        errors = []
        
        try:
            with pdfplumber.open(file_content) as pdf:
                total_pages = len(pdf.pages)
                
                for page_num, page in enumerate(pdf.pages, 1):
                    try:
                        text = page.extract_text()
                        if not text:
                            logger.warning(f"No text found on page {page_num} of {filename}")
                            continue
                        
                        lines = text.split('\n')
                        
                        for line_num, line in enumerate(lines, 1):
                            try:
                                line = line.strip()
                                if not line:
                                    continue
                                
                                # Extract PO Number
                                if po_match := re.search(r'PO\s*No\.?\s*:?\s*(\d+)', line, re.IGNORECASE):
                                    current_po['PO No.'] = po_match.group(1)
                                
                                # Extract Store Name and Store ID
                                if store_match := re.search(r'Store\s*:?\s*(.*?)\s*-\s*(\d{3})\b', line, re.IGNORECASE):
                                    current_po['Store Name'] = store_match.group(1).strip()
                                    current_po['Store ID'] = store_match.group(2)
                                
                                # Extract Dates
                                if order_date_match := re.search(r'Order\s*Date\s*:?\s*(\d{1,2}/\d{1,2}/\d{4})', line, re.IGNORECASE):
                                    current_po['Order Date'] = order_date_match.group(1)
                                
                                if delivery_date_match := re.search(r'Delivery\s*Date.*?:?\s*(\d{1,2}/\d{1,2}/\d{4})', line, re.IGNORECASE):
                                    current_po['Delivery Date'] = delivery_date_match.group(1)
                                
                                # Parse item lines - Pattern: Starts with 6 digits (Internal Reference)
                                if 'PO No.' in current_po and re.match(r'^\d{6}\b', line):
                                    parts = line.split()
                                    if len(parts) < 3:
                                        continue
                                    
                                    # Extract English description (words between item code and size/pack)
                                    english_desc = ""
                                    size = ""
                                    pack = ""
                                    
                                    # Find the size/pack pattern (like "500g/8.00" or "90sx4/16.00")
                                    size_pack_pattern = r'(\d+(?:\.\d+)?[a-zA-Z]*\d*[a-zA-Z]*)/(\d+(?:\.\d+)?)'
                                    size_pack_matches = re.findall(size_pack_pattern, line)
                                    
                                    if size_pack_matches:
                                        size, pack = size_pack_matches[0]
                                        size_pack_full = f"{size}/{pack}"
                                        size_pack_index = line.find(size_pack_full)
                                        
                                        if size_pack_index > 0:
                                            desc_part = line[:size_pack_index].strip()
                                            desc_words = desc_part.split()[1:]  # Skip item code
                                            english_desc = " ".join(desc_words).strip()
                                    
                                    # Extract Chinese description from next line
                                    chinese_desc = ""
                                    if line_num < len(lines):
                                        next_line = lines[line_num].strip()
                                        if re.search(r'[一-龯]', next_line):
                                            chinese_desc = next_line.strip()
                                    
                                    # Combine descriptions
                                    full_description = english_desc
                                    if chinese_desc:
                                        full_description = f"{english_desc} {chinese_desc}" if english_desc else chinese_desc
                                    
                                    # Parse numerics
                                    numeric_values = []
                                    for part in parts[1:]:
                                        try:
                                            # Skip if it's a 6-digit number (likely an item code/vendor item number)
                                            if re.match(r'^\d{6}$', part):
                                                continue
                                            # Match numeric values (integers or decimals), but not parts with '/'
                                            if re.match(r'^\d+(?:\.\d+)?$', part) and '/' not in part:
                                                numeric_values.append(float(part))
                                        except ValueError:
                                            continue
                                    
                                    # Logic to find qty and price
                                    ordered_qty = None
                                    price = None
                                    
                                    if len(numeric_values) >= 2:
                                        if len(numeric_values) >= 3:
                                            ordered_qty = numeric_values[-3]
                                            price = numeric_values[-2] # Unit price often second to last? Or total price? 
                                            # Original code: price = numeric_values[-2]  # Unit price logic assumption from original
                                        elif len(numeric_values) >= 2:
                                            ordered_qty = numeric_values[-2]
                                            price = numeric_values[-1]
                                        
                                        if ordered_qty is not None and price is not None:
                                            item_data = {
                                                'PO No.': current_po.get('PO No.', ''),
                                                'Store ID': current_po.get('Store ID', ''),
                                                'Store Name': current_po.get('Store Name', ''),
                                                'Order Date': current_po.get('Order Date', ''),
                                                'Delivery Date': current_po.get('Delivery Date', ''),
                                                'Internal Reference': parts[0],
                                                'Description': full_description,
                                                'Size': size,
                                                'Pack': pack,
                                                '# of Order': ordered_qty,
                                                'Price': price
                                            }
                                            data.append(item_data)
                            
                            except Exception as e:
                                errors.append(f"Error processing line {line_num} on page {page_num}: {str(e)}")
                                continue
                    
                    except Exception as e:
                        errors.append(f"Error processing page {page_num}: {str(e)}")
                        continue
        
        except Exception as e:
            errors.append(f"Error opening PDF: {str(e)}")
            return [], errors

        valid_data = PDFExtractor.validate_po_data(data)
        
        if len(valid_data) < len(data):
            errors.append(f"Dropped {len(data) - len(valid_data)} invalid items from {filename}")
            
        return valid_data, errors

    @staticmethod
    def process_multiple_pdfs(files: List[Tuple[BytesIO, str]]) -> Tuple[pd.DataFrame, List[str]]:
        """Process a list of (file_content, filename) tuples"""
        all_data = []
        all_errors = []
        
        for file_content, filename in files:
            data, errors = PDFExtractor.extract_from_file(file_content, filename)
            all_data.extend(data)
            all_errors.extend(errors)
            
        if not all_data:
            return pd.DataFrame(), all_errors
            
        df = pd.DataFrame(all_data)
        
        # Clean up types
        if not df.empty:
            df['Store ID'] = pd.to_numeric(df['Store ID'], errors='coerce')
            df['PO No.'] = pd.to_numeric(df['PO No.'], errors='coerce')
            
            # Sort
            df = df.sort_values(by=['Store ID', 'PO No.'])
            
        return df, all_errors
