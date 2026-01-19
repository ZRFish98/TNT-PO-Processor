
import pandas as pd

try:
    xl = pd.ExcelFile("/Users/henryyu/Desktop/AI/Anti-Gravity/T&TPO/Odoo Import Ready (22).xlsx")
    print("Sheet Names:", xl.sheet_names)
    
    for sheet in xl.sheet_names:
        print(f"\n--- Sheet: {sheet} ---")
        df = pd.read_excel(xl, sheet_name=sheet)
        print("Columns:", df.columns.tolist())
        print(df.head(2).to_string())
except Exception as e:
    print(f"Error reading excel: {e}")
