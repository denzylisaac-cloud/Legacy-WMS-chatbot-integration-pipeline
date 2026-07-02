import os
import asyncio
from src.warehouse_system.app import run_warehouse_system

async def run_math_tests():
    # Enforce loading from .env
    print("====================================================")
    # List of diverse logistics math tasks to run
    test_queries = [
        {
            "tenant": "default_tenant",
            "query": "Find the reorder point (ROP) for SKU-5020 and check if we need to place an order immediately."
        },
        {
            "tenant": "default_tenant",
            "query": "Calculate the Economic Order Quantity (EOQ) for SKU-5010."
        },
        {
            "tenant": "default_tenant",
            "query": "Audit rack constraints: if we order 600 units of SKU-5010, will it exceed the weight and volume capacities of its designated shelf?"
        },
        {
            "tenant": "default_tenant",
            "query": "Calculate the Newsvendor optimal order quantity for SKU-5010 based on its demand variance."
        },
        {
            "tenant": "default_tenant",
            "query": "Determine the optimal (s, S) safety stock policy levels for SKU-5020."
        }
    ]

    print(f"Executing {len(test_queries)} logistics math queries via tiered agent chain...")
    print("Please wait as API calls are paced by 15s to respect rate limits...\n")

    for idx, q in enumerate(test_queries, 1):
        print("----------------------------------------------------")
        print(f"TEST QUERY #{idx} ({q['tenant']}):")
        print(f"Query: \"{q['query']}\"")
        print("----------------------------------------------------")
        
        try:
            result = await run_warehouse_system(q["query"], tenant_id=q["tenant"])
            
            # Print response
            print(f"Agent Response:\n{result['response'].strip()}")
            print("\n--- Trace Audit ---")
            
            # Check if Nvidia API responded (if it triggered fallback, 'NVIDIA_API_KEY' error or similar fallback print would show)
            fallback_triggered = False
            for step in result.get("trace", []):
                if step.get("callee") == "run_local_agent_reasoning":
                    fallback_triggered = True
                    break
            
            if fallback_triggered:
                print("Status: [LOCAL FALLBACK] Executed locally due to rate limit/API issue.")
            else:
                print("Status: [NVIDIA API SUCCESS] Completed via Llama-3-Nemotron remote NIM endpoints.")
                
            # Print execution steps
            print("Steps:")
            for step in result.get("trace", []):
                print(f"  * {step['caller']} -> {step['callee']} ({step['action']})")
                
        except Exception as e:
            print(f"Test Execution Error: {e}")
        
        print()
        if idx < len(test_queries):
            # Sleep between queries to prevent RPM limits
            print("Pacing delay: Sleeping 15s to protect API quota...")
            await asyncio.sleep(15)

if __name__ == "__main__":
    asyncio.run(run_math_tests())
