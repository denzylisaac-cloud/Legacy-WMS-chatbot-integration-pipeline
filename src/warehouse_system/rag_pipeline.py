import os
import re
import numpy as np
import lancedb
from .data_parser import parse_file

# Dimension of vector embedding
VECTOR_DIMENSION = 128

def text_to_vector(text, dimension=VECTOR_DIMENSION):
    """
    A robust, self-contained hashing vectorizer that represents text
    as a normalized vector of word occurrences.
    """
    words = re.findall(r'\w+', text.lower())
    vec = np.zeros(dimension, dtype=np.float32)
    for word in words:
        # Use a stable hash to avoid differences between python sessions
        # fnv-1a or similar simple hash
        h = 2166136261
        for char in word:
            h = h ^ ord(char)
            h = (h * 16777619) & 0xffffffff
        idx = h % dimension
        vec[idx] += 1.0
    
    norm = np.linalg.norm(vec)
    if norm > 0:
        vec = vec / norm
    return vec.tolist()

def generate_search_document(record):
    """Concatenate record fields into a single search document text."""
    parts = [
        f"sku: {record['sku']}",
        f"name: {record['name']}",
        f"location: {record['location']}",
        f"stock: {record['stock']}",
        f"safety stock: {record['safety_stock']}",
        f"daily demand: {record['daily_demand']}",
        f"lead time: {record['lead_time']} days",
        f"weight: {record['weight']} kg",
        f"volume: {record['volume']} m3",
        f"moq: {record['supplier_moq']}",
        f"order cost: {record['order_cost']}",
        f"holding cost: {record['holding_cost']}"
    ]
    return " ".join(parts)

import urllib.request
import json

def get_gemini_embedding(text, dimension=VECTOR_DIMENSION):
    """
    Call Gemini Embedding 2 (text-embedding-004) to generate embeddings.
    Requests a projected output dimension of 128.
    Falls back to text_to_vector (hashing) if the API fails or is rate-limited.
    """
    api_key = os.environ.get("GEMINI_EMBEDDING_API_KEY") or os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return text_to_vector(text, dimension)
        
    url = f"https://generativelanguage.googleapis.com/v1beta/models/text-embedding-004:embedContent?key={api_key}"
    headers = {"Content-Type": "application/json"}
    payload = {
        "model": "models/text-embedding-004",
        "content": {
            "parts": [{"text": text}]
        },
        "outputDimensionality": dimension
    }
    
    try:
        req = urllib.request.Request(
            url, 
            data=json.dumps(payload).encode("utf-8"), 
            headers=headers, 
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=5) as response:
            res_data = json.loads(response.read().decode("utf-8"))
            return res_data["embedding"]["values"]
    except Exception as e:
        # Fallback to local hashing to ensure rate limits do not break ingestion/search
        return text_to_vector(text, dimension)

class WarehouseRAG:
    def __init__(self, db_uri="./data/lancedb_store"):
        os.makedirs(os.path.dirname(db_uri), exist_ok=True)
        self.db = lancedb.connect(db_uri)
        self.table_name = "catalog"
        self.table = None

    def ingest_records(self, records, tenant_id="default_tenant"):
        """Ingest a list of standardized dict records into both SQLite and LanceDB (with tenant scope)."""
        if not records:
            return
            
        from .db_store import upsert_inventory_records
        
        # Deduplicate by SKU locally for this batch
        unique_records = {}
        for r in records:
            if not r.get("sku"):
                continue
            unique_records[r["sku"]] = r
            
        # SQLite Upsert (Batch)
        upsert_inventory_records(list(unique_records.values()), tenant_id=tenant_id)
            
        # LanceDB Upsert/Ingest
        try:
            self.table = self.db.open_table(self.table_name)
            current_data = self.table.search().to_list()
        except Exception:
            current_data = []
            
        # Merge new records into current_data, keyed on (sku, tenant_id)
        merged = {}
        for r in current_data:
            key = (r.get("sku", "").strip().upper(), r.get("tenant_id", "default_tenant"))
            merged[key] = r
            
        for r in unique_records.values():
            key = (r["sku"].strip().upper(), tenant_id)
            doc = generate_search_document(r)
            vector = get_gemini_embedding(doc)
            
            inserted_record = dict(r)
            inserted_record["vector"] = vector
            inserted_record["document"] = doc
            inserted_record["tenant_id"] = tenant_id
            
            merged[key] = inserted_record
            
        data_to_insert = list(merged.values())
        self.table = self.db.create_table(self.table_name, data=data_to_insert, mode="overwrite")
        print(f"Dual-ingested {len(unique_records)} records for tenant '{tenant_id}' (Total database size: {len(data_to_insert)} items)")

    def ingest_directory(self, directory_path, tenant_id="default_tenant"):
        """Parse all supported files in directory and store in LanceDB and SQLite."""
        all_records = []
        for filename in os.listdir(directory_path):
            file_path = os.path.join(directory_path, filename)
            if os.path.isfile(file_path):
                try:
                    records = parse_file(file_path)
                    all_records.extend(records)
                    print(f"Parsed {len(records)} records from {filename}")
                except Exception as e:
                    print(f"Skipped {filename} due to: {e}")
        
        if not all_records:
            print("No records found to ingest.")
            return
            
        self.ingest_records(all_records, tenant_id=tenant_id)

    def is_populated(self, tenant_id="default_tenant"):
        """Check if the LanceDB catalog table exists and contains records for this tenant."""
        try:
            table = self.db.open_table(self.table_name)
            rows = table.search().where(f"tenant_id = '{tenant_id}'").to_list()
            return len(rows) > 0
        except Exception:
            return False

    def query(self, text, limit=3, tenant_id="default_tenant"):
        """Vector search LanceDB for matching records within a tenant."""
        if self.table is None:
            try:
                self.table = self.db.open_table(self.table_name)
            except Exception:
                print("Table not found. Please run ingestion first.")
                return []
                
        query_vec = get_gemini_embedding(text)
        # Search the table and filter by tenant_id
        results = self.table.search(query_vec).where(f"tenant_id = '{tenant_id}'").limit(limit).to_list()
        return results

    def get_by_sku(self, sku, tenant_id="default_tenant"):
        """Direct lookup of a record by its SKU (exact case-insensitive match) within a tenant."""
        if self.table is None:
            try:
                self.table = self.db.open_table(self.table_name)
            except Exception:
                return None
        
        # LanceDB supports SQL filtering
        results = self.table.search().where(f"sku = '{sku.strip()}' AND tenant_id = '{tenant_id}'").to_list()
        if results:
            return results[0]
            
        # Fallback to standard search
        results = self.table.search().where(f"tenant_id = '{tenant_id}'").to_list()
        for r in results:
            if r["sku"].strip().lower() == sku.strip().lower():
                return r
        return None

    def get_all(self, tenant_id="default_tenant"):
        """Retrieve all catalog records from LanceDB within a tenant."""
        if self.table is None:
            try:
                self.table = self.db.open_table(self.table_name)
            except Exception:
                return []
        return self.table.search().where(f"tenant_id = '{tenant_id}'").to_list()
