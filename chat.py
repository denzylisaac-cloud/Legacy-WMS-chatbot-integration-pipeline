import os
import sys

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

import asyncio
import uuid
from src.warehouse_system.app import run_warehouse_system

async def main():
    session_id = str(uuid.uuid4())
    print("=" * 60)
    print("      WAREHOUSE MANAGEMENT INTERACTIVE CHATBOT CLI      ")
    print("========================================================")
    print("Type 'exit' or 'quit' to end the session.")
    # Fetch sample SKUs from the database to show real examples matching the current dataset
    from src.warehouse_system.rag_pipeline import WarehouseRAG
    rag = WarehouseRAG(db_uri=os.path.abspath("./data/lancedb_store"))
    sample_skus = ["SKU-8821", "SKU-8822"]
    try:
        records = rag.get_all()
        if records:
            sample_skus = [r["sku"] for r in records[:2]]
    except Exception:
        pass

    print("Examples to try:")
    print(f" - 'We are running low on {sample_skus[0]}. Figure out if we need to order more based on our physical rack constraints and calculate the EOQ.'")
    if len(sample_skus) > 1:
        print(f" - 'Check inventory levels for {sample_skus[1]} and see if it fits on the shelf.'")
    print(" - 'give me a list of all the stock on hand'")
    print("========================================================\n")
    
    while True:
        try:
            # Get user query
            query = input("Ask a question: ")
                
            if query.strip().lower() in ['exit', 'quit', 'q']:
                print("\nExiting session. Goodbye!")
                break
                
            if not query.strip():
                continue
                
            print("\nAnalyzing query and executing agent workflow...")
            
            # Execute the tiered agent system
            result = await run_warehouse_system(query, session_id=session_id)
            
            print("\n" + "=" * 50)
            print("AGENT RESPONSE:")
            print("=" * 50)
            print(result["response"])
            print("=" * 50)
            
        except KeyboardInterrupt:
            print("\nExiting session. Goodbye!")
            break
        except Exception as e:
            print(f"\n[Error] Failed to process query: {e}")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
