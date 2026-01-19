import streamlit as st
import pdfplumber
import re
import pandas as pd
from io import BytesIO
from datetime import datetime
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Page configuration
st.set_page_config(
    page_title="T&T PDF Purchase Order Extractor",
    page_icon="📄",
    layout="wide"
)

def validate_po_data(data):
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
                if qty > 0 and price > 0 and qty <= 10000 and price <= 100000:
                    item['# of Order'] = qty
                    item['Price'] = price
                    valid_data.append(item)
                else:
                    logger.warning(f"Invalid quantities/prices: qty={qty}, price={price}")
            except (ValueError, TypeError) as e:
                logger.warning(f"Error converting numeric values: {e}")
                continue
    
    return valid_data

def extract_po_data(pdf_file, filename=""):
    """Extract purchase order data from PDF file with comprehensive error handling"""
    data = []
    current_po = {}
    errors = []
    
    try:
        with pdfplumber.open(pdf_file) as pdf:
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
                            
                            # Extract PO Number - More flexible pattern
                            if po_match := re.search(r'PO\s*No\.?\s*:?\s*(\d+)', line, re.IGNORECASE):
                                current_po['PO No.'] = po_match.group(1)
                                logger.info(f"Found PO No: {current_po['PO No.']}")
                            
                            # Extract Store Name and Store ID - More robust pattern
                            if store_match := re.search(r'Store\s*:?\s*(.*?)\s*-\s*(\d{3})\b', line, re.IGNORECASE):
                                current_po['Store Name'] = store_match.group(1).strip()
                                current_po['Store ID'] = store_match.group(2)
                                logger.info(f"Found Store: {current_po['Store Name']} - {current_po['Store ID']}")
                            
                            # Extract Dates - More flexible patterns
                            if order_date_match := re.search(r'Order\s*Date\s*:?\s*(\d{1,2}/\d{1,2}/\d{4})', line, re.IGNORECASE):
                                current_po['Order Date'] = order_date_match.group(1)
                            
                            if delivery_date_match := re.search(r'Delivery\s*Date.*?:?\s*(\d{1,2}/\d{1,2}/\d{4})', line, re.IGNORECASE):
                                current_po['Delivery Date'] = delivery_date_match.group(1)
                            
                            # Parse item lines - Enhanced pattern matching with Description and Pack extraction
                            if 'PO No.' in current_po and re.match(r'^\d{6}\b', line):
                                parts = line.split()
                                if len(parts) < 3:  # Need at least code and some numbers
                                    continue
                                
                                # Extract English description (words between item code and size/pack)
                                english_desc = ""
                                size = ""
                                pack = ""
                                
                                # Find the size/pack pattern (like "500g/8.00" or "90sx4/16.00")
                                size_pack_pattern = r'(\d+(?:\.\d+)?[a-zA-Z]*\d*[a-zA-Z]*)/(\d+(?:\.\d+)?)'
                                size_pack_matches = re.findall(size_pack_pattern, line)
                                
                                if size_pack_matches:
                                    size, pack = size_pack_matches[0]  # Take the first match
                                    # Find where the size/pack pattern starts
                                    size_pack_full = f"{size}/{pack}"
                                    size_pack_index = line.find(size_pack_full)
                                    
                                    if size_pack_index > 0:
                                        # Extract description part (between item code and size/pack)
                                        desc_part = line[:size_pack_index].strip()
                                        desc_words = desc_part.split()[1:]  # Skip the item code (first word)
                                        english_desc = " ".join(desc_words).strip()
                                
                                # Extract Chinese description from next line
                                chinese_desc = ""
                                if line_num < len(lines):
                                    next_line = lines[line_num].strip() if line_num < len(lines) else ""
                                    # Check if next line contains Chinese characters
                                    if re.search(r'[一-龯]', next_line):
                                        chinese_desc = next_line.strip()
                                
                                # Combine descriptions
                                full_description = english_desc
                                if chinese_desc:
                                    if full_description:
                                        full_description += f" {chinese_desc}"
                                    else:
                                        full_description = chinese_desc
                                
                                # More flexible numeric pattern matching
                                numeric_values = []
                                for part in parts[1:]:  # Skip the first part (Internal Reference)
                                    try:
                                        # Try to convert to float, but skip the size/pack part
                                        if re.match(r'^\d+(?:\.\d+)?$', part) and '/' not in part:
                                            numeric_values.append(float(part))
                                    except ValueError:
                                        continue
                                
                                # Need at least quantity and price
                                if len(numeric_values) >= 2:
                                    # Try different combinations to find qty and price
                                    ordered_qty = None
                                    price = None
                                    
                                    # Common patterns: [..., qty, unit_price, total_price]
                                    if len(numeric_values) >= 3:
                                        ordered_qty = numeric_values[-3]
                                        price = numeric_values[-2]  # Unit price
                                    elif len(numeric_values) >= 2:
                                        ordered_qty = numeric_values[-2]
                                        price = numeric_values[-1]
                                    
                                    if ordered_qty is not None and price is not None:
                                        item_data = {
                                            'PO No.': current_po['PO No.'],
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
                            error_msg = f"Error processing line {line_num} on page {page_num}: {str(e)}"
                            errors.append(error_msg)
                            logger.warning(error_msg)
                            continue
                
                except Exception as e:
                    error_msg = f"Error processing page {page_num}: {str(e)}"
                    errors.append(error_msg)
                    logger.error(error_msg)
                    continue
    
    except Exception as e:
        error_msg = f"Error opening PDF file {filename}: {str(e)}"
        errors.append(error_msg)
        logger.error(error_msg)
        st.error(error_msg)
        return [], errors
    
    # Validate extracted data
    valid_data = validate_po_data(data)
    
    if len(valid_data) < len(data):
        dropped_count = len(data) - len(valid_data)
        warning_msg = f"Dropped {dropped_count} invalid items from {filename}"
        errors.append(warning_msg)
        logger.warning(warning_msg)
    
    return valid_data, errors

# Custom CSS for better styling
st.markdown("""
<style>
    .main-header {
        font-size: 2.5rem;
        font-weight: bold;
        color: #1f77b4;
        text-align: center;
        margin-bottom: 2rem;
    }
    .stats-container {
        background-color: #f0f2f6;
        padding: 1rem;
        border-radius: 10px;
        margin: 1rem 0;
    }
    .success-box {
        background-color: #d4edda;
        border: 1px solid #c3e6cb;
        border-radius: 5px;
        padding: 1rem;
        margin: 1rem 0;
    }
    .warning-box {
        background-color: #fff3cd;
        border: 1px solid #ffeaa7;
        border-radius: 5px;
        padding: 1rem;
        margin: 1rem 0;
    }
    .error-box {
        background-color: #f8d7da;
        border: 1px solid #f5c6cb;
        border-radius: 5px;
        padding: 1rem;
        margin: 1rem 0;
    }
</style>
""", unsafe_allow_html=True)

# Header
st.markdown('<h1 class="main-header">📄 T&T PDF Purchase Order Extractor</h1>', unsafe_allow_html=True)
st.markdown("---")

# Instructions
st.info("📋 Upload PDF files containing T&T purchase orders to extract structured data for processing")

# File uploader with enhanced options
with st.container():
    st.markdown("### 📁 File Upload")
    uploaded_files = st.file_uploader(
        "Choose PDF files", 
        type="pdf", 
        accept_multiple_files=True,
        help="Upload one or more PDF files containing T&T purchase orders"
    )
    
    if uploaded_files:
        st.success(f"✅ {len(uploaded_files)} file(s) uploaded successfully")

if uploaded_files:
    # Processing section
    st.markdown("---")
    st.markdown("### 🔄 Processing Files")
    
    all_data = []
    all_errors = []
    successful_files = 0
    failed_files = 0
    
    # Create progress bar
    progress_bar = st.progress(0)
    status_text = st.empty()
    
    # Process files with progress tracking
    for i, uploaded_file in enumerate(uploaded_files):
        filename = uploaded_file.name
        status_text.text(f"Processing: {filename}")
        
        try:
            # Extract data from PDF
            with st.spinner(f"Extracting data from {filename}..."):
                data, errors = extract_po_data(BytesIO(uploaded_file.getvalue()), filename)
            
            if data:
                all_data.extend(data)
                successful_files += 1
                st.success(f"✅ {filename}: Extracted {len(data)} items")
            else:
                failed_files += 1
                st.error(f"❌ {filename}: No valid data extracted")
            
            if errors:
                all_errors.extend(errors)
                with st.expander(f"⚠️ Issues found in {filename}", expanded=False):
                    for error in errors:
                        st.warning(error)
        
        except Exception as e:
            failed_files += 1
            error_msg = f"❌ {filename}: Critical error - {str(e)}"
            st.error(error_msg)
            all_errors.append(error_msg)
        
        # Update progress
        progress_bar.progress((i + 1) / len(uploaded_files))
    
    # Clear status text
    status_text.empty()
    
    # Show processing summary
    st.markdown("---")
    st.markdown("### 📊 Processing Summary")
    
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Files Processed", len(uploaded_files))
    with col2:
        st.metric("Successful", successful_files)
    with col3:
        st.metric("Failed", failed_files)
    with col4:
        st.metric("Items Extracted", len(all_data))
    
    if all_data:
        # Create DataFrame and apply data quality improvements
        df = pd.DataFrame(all_data)
        
        # Data cleaning and validation
        original_count = len(df)
        
        # Remove duplicates
        df = df.drop_duplicates()
        duplicate_count = original_count - len(df)
        
        # Convert to numeric for proper sorting
        df['Store ID'] = pd.to_numeric(df['Store ID'], errors='coerce')
        df['PO No.'] = pd.to_numeric(df['PO No.'], errors='coerce')
        df['# of Order'] = pd.to_numeric(df['# of Order'], errors='coerce')
        df['Price'] = pd.to_numeric(df['Price'], errors='coerce')
        
        # Remove rows with invalid data
        df = df.dropna(subset=['Store ID', 'PO No.'])
        invalid_count = original_count - duplicate_count - len(df)
        
        # Sort by Store ID and PO No.
        df = df.sort_values(by=['Store ID', 'PO No.'], ascending=[True, True])
        
        # Reorder columns to include new fields
        columns_to_include = ['Store ID', 'Store Name', 'PO No.', 'Order Date', 'Delivery Date', 
                             'Internal Reference', 'Description', 'Size', 'Pack', '# of Order', 'Price']
        available_columns = [col for col in columns_to_include if col in df.columns]
        df = df[available_columns]
        
        # Data quality summary
        if duplicate_count > 0 or invalid_count > 0:
            st.markdown("### 🧹 Data Quality")
            if duplicate_count > 0:
                st.info(f"📋 Removed {duplicate_count} duplicate entries")
            if invalid_count > 0:
                st.warning(f"⚠️ Removed {invalid_count} entries with invalid data")
        
        # Show data preview and statistics
        st.markdown("---")
        st.markdown("### 📋 Extracted Data")
        
        # Summary statistics
        total_value = df['Price'].sum()
        avg_order = df['# of Order'].mean()
        unique_stores = df['Store ID'].nunique()
        unique_pos = df['PO No.'].nunique()
        
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.metric("Total Value", f"${total_value:,.2f}")
        with col2:
            st.metric("Avg Quantity", f"{avg_order:.1f}")
        with col3:
            st.metric("Unique Stores", unique_stores)
        with col4:
            st.metric("Unique POs", unique_pos)
        
        # Show preview
        st.markdown("#### Preview (First 10 rows)")
        st.dataframe(df.head(10), use_container_width=True)
        
        # Show full data in expandable section
        with st.expander(f"📊 View All Data ({len(df)} rows)", expanded=False):
            st.dataframe(df, use_container_width=True)
        
        # Export section
        st.markdown("---")
        st.markdown("### 📥 Download Results")
        
        # Generate filename with timestamp
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"purchase_orders_{timestamp}.xlsx"
        
        # Create Excel file
        excel_buffer = BytesIO()
        with pd.ExcelWriter(excel_buffer, engine='openpyxl') as writer:
            # Main data sheet
            df.to_excel(writer, sheet_name='Purchase Orders', index=False)
            
            # Summary sheet
            summary_data = {
                'Metric': ['Total Files Processed', 'Successful Files', 'Failed Files', 
                          'Total Items Extracted', 'Duplicates Removed', 'Invalid Entries Removed',
                          'Final Valid Items', 'Total Value', 'Unique Stores', 'Unique POs'],
                'Value': [len(uploaded_files), successful_files, failed_files,
                         original_count, duplicate_count, invalid_count,
                         len(df), f"${total_value:,.2f}", unique_stores, unique_pos]
            }
            summary_df = pd.DataFrame(summary_data)
            summary_df.to_excel(writer, sheet_name='Processing Summary', index=False)
            
            # Errors sheet (if any)
            if all_errors:
                errors_df = pd.DataFrame({'Errors': all_errors})
                errors_df.to_excel(writer, sheet_name='Processing Errors', index=False)
        
        excel_buffer.seek(0)
        
        # Download button
        st.download_button(
            label="📥 Download Excel File",
            data=excel_buffer.getvalue(),
            file_name=filename,
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            type="primary",
            use_container_width=True
        )
        
        st.success(f"✅ Ready to download: {len(df)} items in {filename}")
        
        # Processing errors summary
        if all_errors:
            with st.expander(f"⚠️ Processing Issues ({len(all_errors)} total)", expanded=False):
                for error in all_errors[:20]:  # Show first 20 errors
                    st.warning(error)
                if len(all_errors) > 20:
                    st.info(f"... and {len(all_errors) - 20} more issues (see Excel file for complete list)")
    
    else:
        st.error("❌ No valid purchase order data found in any of the uploaded files.")
        st.markdown("### 🔍 Troubleshooting Tips:")
        st.markdown("""
        - Ensure PDFs contain T&T purchase order data
        - Check that PDFs are not corrupted or password-protected
        - Verify that text is extractable (not just images)
        - Look for standard T&T format with PO numbers, store names, and item codes
        """)
        
        # Show all errors for debugging
        if all_errors:
            with st.expander("🔍 Detailed Error Information", expanded=True):
                for error in all_errors:
                    st.error(error)

else:
    # Help section when no files are uploaded
    st.markdown("### 📖 How to Use This Tool")
    st.markdown("""
    1. **Upload PDFs**: Select one or more PDF files containing T&T purchase orders
    2. **Processing**: The tool will automatically extract data from all uploaded files
    3. **Review**: Check the extracted data and any processing issues
    4. **Download**: Get the Excel file ready for use with the main T&T processor
    
    **Expected PDF Format:**
    - T&T purchase order PDFs with standard format
    - Contains PO numbers, store information, dates, and item details
    - Text-based PDFs (not scanned images)
    """)
    
    st.markdown("### 🔗 Integration")
    st.info("💡 The Excel file generated by this tool can be directly used as input for the main T&T Purchase Order Processor!")
