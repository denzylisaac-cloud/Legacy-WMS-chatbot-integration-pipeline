import os
import sqlite3
from datetime import datetime

DB_FILE = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../data/warehouse_metrics.db"))

def get_connection():
    os.makedirs(os.path.dirname(DB_FILE), exist_ok=True)
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS inventory (
            sku TEXT,
            tenant_id TEXT,
            name TEXT,
            stock INTEGER,
            safety_stock INTEGER,
            daily_demand REAL,
            lead_time INTEGER,
            weight REAL,
            volume REAL,
            location TEXT,
            rack_max_weight REAL,
            rack_max_volume REAL,
            order_cost REAL,
            holding_cost REAL,
            supplier_moq INTEGER,
            last_updated TEXT,
            PRIMARY KEY (sku, tenant_id)
        )
    """)
    conn.commit()
    conn.close()

_db_initialized = False

def upsert_inventory_record(record: dict, tenant_id: str = "default_tenant") -> None:
    global _db_initialized
    if not _db_initialized:
        init_db()
        _db_initialized = True
        
    conn = get_connection()
    cursor = conn.cursor()
    
    timestamp = record.get("last_updated") or datetime.utcnow().isoformat()
    
    cursor.execute("""
        INSERT OR REPLACE INTO inventory (
            sku, tenant_id, name, stock, safety_stock, daily_demand,
            lead_time, weight, volume, location, rack_max_weight,
            rack_max_volume, order_cost, holding_cost, supplier_moq, last_updated
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        str(record.get("sku", "")).strip(),
        tenant_id,
        str(record.get("name", "")).strip(),
        int(record.get("stock", 0)),
        int(record.get("safety_stock", 0)),
        float(record.get("daily_demand", 0.0)),
        int(record.get("lead_time", 0)),
        float(record.get("weight", 0.0)),
        float(record.get("volume", 0.0)),
        str(record.get("location", "Unknown")),
        float(record.get("rack_max_weight", 0.0)),
        float(record.get("rack_max_volume", 0.0)),
        float(record.get("order_cost", 0.0)),
        float(record.get("holding_cost", 0.0)),
        int(record.get("supplier_moq", 0)),
        timestamp
    ))
    conn.commit()
    conn.close()

def upsert_inventory_records(records: list[dict], tenant_id: str = "default_tenant") -> None:
    global _db_initialized
    if not _db_initialized:
        init_db()
        _db_initialized = True
        
    conn = get_connection()
    cursor = conn.cursor()
    
    for record in records:
        timestamp = record.get("last_updated") or datetime.utcnow().isoformat()
        cursor.execute("""
            INSERT OR REPLACE INTO inventory (
                sku, tenant_id, name, stock, safety_stock, daily_demand,
                lead_time, weight, volume, location, rack_max_weight,
                rack_max_volume, order_cost, holding_cost, supplier_moq, last_updated
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            str(record.get("sku", "")).strip(),
            tenant_id,
            str(record.get("name", "")).strip(),
            int(record.get("stock", 0)),
            int(record.get("safety_stock", 0)),
            float(record.get("daily_demand", 0.0)),
            int(record.get("lead_time", 0)),
            float(record.get("weight", 0.0)),
            float(record.get("volume", 0.0)),
            str(record.get("location", "Unknown")),
            float(record.get("rack_max_weight", 0.0)),
            float(record.get("rack_max_volume", 0.0)),
            float(record.get("order_cost", 0.0)),
            float(record.get("holding_cost", 0.0)),
            int(record.get("supplier_moq", 0)),
            timestamp
        ))
    conn.commit()
    conn.close()

def get_inventory_by_sku(sku: str, tenant_id: str = "default_tenant") -> dict:
    init_db()
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM inventory WHERE UPPER(sku) = UPPER(?) AND tenant_id = ?",
        (sku.strip(), tenant_id)
    )
    row = cursor.fetchone()
    conn.close()
    if row:
        return dict(row)
    return None

def get_all_inventory(tenant_id: str = "default_tenant") -> list[dict]:
    init_db()
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM inventory WHERE tenant_id = ?", (tenant_id,))
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]

def clear_inventory(tenant_id: str = "default_tenant") -> None:
    init_db()
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM inventory WHERE tenant_id = ?", (tenant_id,))
    conn.commit()
    conn.close()
