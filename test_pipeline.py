import os
import shutil
import sqlite3
import json
import asyncio
from src.warehouse_system.rag_pipeline import WarehouseRAG
from src.warehouse_system.adapters import IngestionManager
from src.warehouse_system.app import run_warehouse_system
from src.warehouse_system.db_store import get_all_inventory

async def run_verification():
    print("====================================================")
    print("      AI SYSTEM VERIFICATION AND TEST RUNNER        ")
    print("====================================================\n")
    
    # 1. Clean previous data folders for a clean start
    for folder in ["./data/sftp_drop", "./data/sftp_processed", "./data/edi_inbox", "./data/edi_processed", "./data/dlq", "./data/wms_replica.db"]:
        path = os.path.abspath(folder)
        if os.path.exists(path):
            if os.path.isdir(path):
                shutil.rmtree(path)
            else:
                os.remove(path)
                
    # Reinitialize paths
    RAG_DB_PATH = os.path.abspath("./data/lancedb_store")
    if os.path.exists(RAG_DB_PATH):
        shutil.rmtree(RAG_DB_PATH)
        
    rag = WarehouseRAG(db_uri=RAG_DB_PATH)
    config_path = os.path.abspath("./src/warehouse_system/wms_config.yaml")
    manager = IngestionManager(config_path)

    # 2. Setup SFTP Drop File (Valid CSV for default_tenant)
    sftp_dir = os.path.abspath("./data/sftp_drop")
    os.makedirs(sftp_dir, exist_ok=True)
    csv_content = """sku,name,stock,location,safety_stock,daily_demand,weight,volume,lead_time,supplier_moq,order_cost,holding_cost,rack_max_weight,rack_max_volume
SKU-5010,Heavy Duty Copper Wires,150,Shelf-A1,30,5.5,1.2,0.015,5,10,35.0,8.5,800.0,15.0
SKU-5020,Premium Fiberglass Pipes,75,Shelf-A2,15,3.2,0.8,0.035,7,20,40.0,12.0,500.0,12.0
"""
    with open(os.path.join(sftp_dir, "wms_export_sftp.csv"), "w", encoding="utf-8") as f:
        f.write(csv_content)

    # 3. Setup SFTP Invalid Drop File (Missing SKU and stock validation test -> DLQ routing)
    invalid_csv_content = """sku,name,stock,location
,Broken Item Column,40,Shelf-Z9
SKU-8888,Missing Stock Column,,Shelf-Z9
"""
    with open(os.path.join(sftp_dir, "wms_export_invalid.csv"), "w", encoding="utf-8") as f:
        f.write(invalid_csv_content)

    # 4. Setup Database Poll Replica DB (Valid rows for client_a)
    replica_db = os.path.abspath("./data/wms_replica.db")
    conn = sqlite3.connect(replica_db)
    cursor = conn.cursor()
    # Populate replica table
    cursor.execute("""
        INSERT INTO wms_inventory VALUES
        ('SKU-6010', 'Industrial Hydraulic Fluid', 400, 100, 15.0, 10, 5.0, 0.05, 'Zone B', 'Shelf-B5', 12.0, 25.0, 50),
        ('SKU-6020', 'Rotary Pneumatic Valves', 25, 50, 6.5, 4, 0.45, 0.005, 'Zone B', 'Shelf-B6', 5.0, 8.5, 100)
    """)
    conn.commit()
    conn.close()

    # 5. Run Ingestion Manager Polling
    print("1. Running Ingestion Adapter Polling...")
    ingested_count = manager.run_ingest(rag)
    print(f"-> Ingested {ingested_count} records total across WMS sources.")

    # 6. Verify Dual-Writes & Tenant Isolation
    print("\n2. Verifying Dual-Write SQLite & LanceDB Stores...")
    # SQLite Verification
    default_sqlite_records = get_all_inventory(tenant_id="default_tenant")
    client_a_sqlite_records = get_all_inventory(tenant_id="client_a")
    
    print(f"-> default_tenant SQLite records found: {len(default_sqlite_records)}")
    for r in default_sqlite_records:
        print(f"   * {r['sku']}: {r['name']} (Stock: {r['stock']}, Location: {r['location']})")
        
    print(f"-> client_a SQLite records found: {len(client_a_sqlite_records)}")
    for r in client_a_sqlite_records:
        print(f"   * {r['sku']}: {r['name']} (Stock: {r['stock']}, Location: {r['location']})")

    # LanceDB Verification
    default_lancedb_records = rag.get_all(tenant_id="default_tenant")
    client_a_lancedb_records = rag.get_all(tenant_id="client_a")
    print(f"-> default_tenant LanceDB records found: {len(default_lancedb_records)}")
    print(f"-> client_a LanceDB records found: {len(client_a_lancedb_records)}")

    # Assert tenant isolation check
    default_skus = {r["sku"] for r in default_sqlite_records}
    client_a_skus = {r["sku"] for r in client_a_sqlite_records}
    assert default_skus.isdisjoint(client_a_skus), "Tenant isolation failure! Scopes leaked between default_tenant and client_a."
    print("-> [SUCCESS] Data isolation assert check passed: No client leakages detected.")

    # 7. Check DLQ Routing
    print("\n3. Verifying Dead-Letter Queue (DLQ) Routing for invalid records...")
    dlq_dir = os.path.abspath("./data/dlq/default_tenant")
    if os.path.exists(dlq_dir):
        dlq_files = os.listdir(dlq_dir)
        print(f"-> DLQ files generated: {dlq_files}")
        if "wms_export_invalid.csv" in dlq_files and "wms_export_invalid.csv.err" in dlq_files:
            print("-> [SUCCESS] Invalid file was successfully routed to the DLQ with .err log details.")
            with open(os.path.join(dlq_dir, "wms_export_invalid.csv.err"), "r", encoding="utf-8") as f:
                print(f"   [DLQ Error Report Preview]:\n   {f.read().strip().replace(chr(10), '   ')}")
        else:
            print("-> [FAILURE] Invalid file was not routed correctly to the DLQ.")
    else:
        print("-> [FAILURE] DLQ directory not found.")

    # 8. Local reasoning engine query test (Rate limit fallback test with Stockpyl wrapping)
    print("\n4. Running Local Reasoning Queries...")
    
    # Test 1: Basic inventory lookup
    res_lookup = await run_warehouse_system("Find the current stock and location of SKU-6010", tenant_id="client_a")
    print(f"-> Query: 'Find the current stock and location of SKU-6010'")
    print(f"  Agent Response:\n{res_lookup['response'].strip()}")
    
    # Test 2: Stockpyl Newsvendor model optimal order quantity calculation
    res_math_nv = await run_warehouse_system("Calculate newsvendor optimal order quantity for SKU-6010", tenant_id="client_a")
    print(f"\n-> Query: 'Calculate newsvendor optimal order quantity for SKU-6010'")
    print(f"  Agent Response:\n{res_math_nv['response'].strip()}")

    # Test 3: Stockpyl Safety Stock (s, S) policy calculation
    res_math_ss = await run_warehouse_system("Run safety stock (s, S) policy calculation for SKU-6010", tenant_id="client_a")
    print(f"\n-> Query: 'Run safety stock (s, S) policy calculation for SKU-6010'")
    print(f"  Agent Response:\n{res_math_ss['response'].strip()}")
    
    # Test 4: Out-of-scope query guardrail rejection
    res_out = await run_warehouse_system("What is the recipe for chocolate chip cookies?", tenant_id="client_a")
    print(f"\n-> Query: 'What is the recipe for chocolate chip cookies?'")
    print(f"  Agent Response:\n{res_out['response'].strip()}")

if __name__ == "__main__":
    asyncio.run(run_verification())
