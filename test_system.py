import os
# Load local configuration from ignored .env file
def load_env():
    env_path = os.path.abspath(os.path.join(os.path.dirname(__file__), ".env"))
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ[k.strip()] = v.strip()

load_env()

import json
import asyncio
from src.warehouse_system.rag_pipeline import WarehouseRAG
from src.warehouse_system.app import run_warehouse_system

async def main():
    # 1. Ingest mock data from all formats into LanceDB
    mock_data_dir = os.path.abspath("./src/warehouse_system/mock_data")
    db_path = os.path.abspath("./data/lancedb_store")
    
    print(f"Initializing Warehouse RAG database at {db_path}...")
    rag = WarehouseRAG(db_uri=db_path)
    
    if not rag.is_populated():
        print(f"Database empty. Ingesting mock data files from {mock_data_dir}...")
        rag.ingest_directory(mock_data_dir)
    else:
        print("Database already populated. Skipping ingestion to conserve API rate limits.")
    
    # 2. Define the test query
    query = "how many skus do I have?"
    print(f"\nSending user query to Main UI Agent:\n\"{query}\"\n")
    
    # 3. Execute the tiered agent system
    result = await run_warehouse_system(query)
    
    # 4. Display the results
    print("\n" + "="*40)
    print("FINAL AGENT RESPONSE")
    print("="*40)
    print(result["response"])
    print("="*40 + "\n")
    
    print("AGENT CALL TRACE:")
    for idx, step in enumerate(result["trace"], 1):
        print(f" {idx}. [{step['caller']} -> {step['callee']}] - Action: {step['action']}")
        if step['data']:
            print(f"    Data: {json.dumps(step['data'])}")
            
    # 5. Save the execution trace
    os.makedirs("./logs", exist_ok=True)
    trace_path = "./logs/trace_output.json"
    with open(trace_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)
    print(f"\n[Success] Full trace output saved to: {os.path.abspath(trace_path)}")

if __name__ == "__main__":
    asyncio.run(main())
