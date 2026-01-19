from supabase import create_client, Client
import os
import pandas as pd
import logging
from typing import Dict, Any, List

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class SupabaseClient:
    def __init__(self, url: str, key: str):
        self.url = url
        self.key = key
        self.client: Client = None

    def connect(self) -> bool:
        """Initialize Supabase client"""
        try:
            self.client = create_client(self.url, self.key)
            logger.info("Supabase client initialized")
            return True
        except Exception as e:
            logger.error(f"Supabase connection error: {e}")
            return False


            
    def _get_product_id_map(self, skus: List[str]) -> Dict[int, str]:
        """
        Helper: Map Internal ID (int) -> SKU (str)
        Query 'products' table where item_id IN skus
        """
        if not skus: return {}
        try:
            # item_id is the SKU in Supabase products table
            response = self.client.table('products')\
                .select('id, item_id')\
                .in_('item_id', skus)\
                .execute()
            
            if not response.data:
                return {}
                
            # Return map: internal_id (int) -> item_id (str)
            return {row['id']: str(row['item_id']) for row in response.data}
        except Exception as e:
            logger.error(f"Error fetching product map: {e}")
            return {}

    def _get_store_id_map(self, store_numbers: List[int]) -> Dict[int, int]:
        """
        Helper: Map Internal ID (int) -> Store Number (int)
        Query 'stores' table where store_number IN store_numbers
        """
        if not store_numbers: return {}
        try:
            # store_number is the External ID in Supabase stores table
            response = self.client.table('stores')\
                .select('id, store_number')\
                .in_('store_number', store_numbers)\
                .execute()
            
            if not response.data:
                return {}
            
            # Return map: internal_id (int) -> store_number (int)
            return {row['id']: int(row['store_number']) for row in response.data}
        except Exception as e:
            logger.error(f"Error fetching store map: {e}")
            return {}

    def get_historical_sales(self, store_numbers: List[int], skus: List[str]) -> pd.DataFrame:
        """
        Fetch historical sales data
        Inputs are External Store Numbers and SKUs.
        """
        if not self.client:
            raise ConnectionError("Supabase client not initialized")

        try:
            # 1. Get ID Mappings
            product_map = self._get_product_id_map(skus)
            store_map = self._get_store_id_map(store_numbers)
            
            if not product_map or not store_map:
                logger.warning("No matching products or stores found in Supabase.")
                return pd.DataFrame()
            
            internal_product_ids = list(product_map.keys())
            internal_store_ids = list(store_map.keys())

            # 2. Fetch Sales Performance using Internal IDs
            response = self.client.table('sales_performance')\
                .select('*')\
                .in_('store_id', internal_store_ids)\
                .in_('product_id', internal_product_ids)\
                .execute()
                
            data = response.data
            if not data:
                return pd.DataFrame()
                
            df = pd.DataFrame(data)
            
            # 3. Map Internal IDs back to External Keys for the App
            df['sku'] = df['product_id'].map(product_map)
            df['store_number'] = df['store_id'].map(store_map)
            
            # 4. Calculate Average
            # Group by SKU (mapped) and store_number (mapped)
            avg_sales = df.groupby(['sku', 'store_number'])['total_quantity_sold'].mean().reset_index()
            avg_sales.rename(columns={
                'total_quantity_sold': 'avg_monthly_sales',
                'sku': 'internal_reference', # Match App Schema
                'store_number': 'store_id'   # Match App Schema (it calls store_number 'store_id')
            }, inplace=True)

            # Round to nearest integer
            avg_sales['avg_monthly_sales'] = avg_sales['avg_monthly_sales'].round().astype(int)

            # Ensure type consistency for merging
            avg_sales['internal_reference'] = avg_sales['internal_reference'].astype(str)
            avg_sales['store_id'] = avg_sales['store_id'].astype(int)

            return avg_sales

        except Exception as e:
            logger.error(f"Error fetching historical sales: {e}")
            return pd.DataFrame()

    def get_store_inventory(self, store_numbers: List[int], skus: List[str]) -> pd.DataFrame:
        """
        Fetch latest store inventory snapshots
        Inputs are External Store Numbers and SKUs.
        """
        if not self.client:
            raise ConnectionError("Supabase client not initialized")

        try:
            # 1. Get ID Mappings
            product_map = self._get_product_id_map(skus)
            store_map = self._get_store_id_map(store_numbers)
            
            if not product_map or not store_map:
                return pd.DataFrame()
            
            internal_product_ids = list(product_map.keys())
            internal_store_ids = list(store_map.keys())

            # 2. Fetch Snapshots
            response = self.client.table('inventory_snapshots')\
                .select('*')\
                .in_('store_id', internal_store_ids)\
                .in_('product_id', internal_product_ids)\
                .execute()
                
            data = response.data
            if not data:
                return pd.DataFrame()
                
            df = pd.DataFrame(data)
            
            # 3. Map back to External Keys
            df['sku'] = df['product_id'].map(product_map)
            df['store_number'] = df['store_id'].map(store_map)
            
            # 4. Get Latest
            df['snapshot_date'] = pd.to_datetime(df['snapshot_date'])
            latest_inventory = df.sort_values('snapshot_date', ascending=False)\
                .drop_duplicates(subset=['sku', 'store_number'])
            
            result = latest_inventory[['sku', 'store_number', 'quantity']].copy()
            result.rename(columns={
                'sku': 'internal_reference',
                'store_number': 'store_id',
                'quantity': 'store_on_hand'
            }, inplace=True)

            # Ensure type consistency for merging
            result['internal_reference'] = result['internal_reference'].astype(str)
            result['store_id'] = result['store_id'].astype(int)

            return result

        except Exception as e:
            logger.error(f"Error fetching store inventory: {e}")
            return pd.DataFrame()
