import os
import json
import asyncio
import re
import math
import contextvars
from google.antigravity import Agent, LocalAgentConfig
from .rag_pipeline import WarehouseRAG
from .db_store import get_inventory_by_sku, get_all_inventory

# Programmatic helper to load project-level environment variables
def load_dot_env():
    env_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../.env"))
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    if "=" in line:
                        k, v = line.split("=", 1)
                        # Set if not already set in active shell
                        k_clean = k.strip()
                        if k_clean not in os.environ:
                            os.environ[k_clean] = v.strip()

load_dot_env()

# Context variable to hold active tenant ID for request-scoped lookups
tenant_context = contextvars.ContextVar("tenant_id", default="default_tenant")

# Initialize RAG vector database
RAG_DB_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../data/lancedb_store"))
rag = WarehouseRAG(db_uri=RAG_DB_PATH)

# Global trace log to capture agent communication steps
trace_logs = []

# In-memory session store for multi-turn conversation context
SESSION_STORE = {}

def log_trace(caller, callee, action, data=None):
    trace_logs.append({
        "caller": caller,
        "callee": callee,
        "action": action,
        "data": data or {}
    })

# --- DETERMINISTIC TOOL-AGENTS (Child Agents as local tools) ---

def query_stock_units_agent(sku: str) -> str:
    """
    Stock & Replenishment Agent: Retrieves current inventory units, safety stock counts,
    and average daily demand thresholds from the SQLite structured store.
    """
    log_trace("Logistics Math Agent", "Stock & Replenishment Agent", "subagent_query", {"sku": sku})
    tenant_id = tenant_context.get()
    r = get_inventory_by_sku(sku, tenant_id=tenant_id)
    if not r:
        res_str = f"Error: SKU {sku} not found."
    else:
        res_str = f"Stock: {r['stock']}, Safety Stock: {r['safety_stock']}, Daily Demand: {r['daily_demand']}"
        
    log_trace("Stock & Replenishment Agent", "Logistics Math Agent", "subagent_response", {"result": res_str})
    return res_str

def query_physical_properties_agent(sku: str) -> str:
    """
    Dimensions & Physicals Agent: Retrieves unit weights, volume/dimensions,
    and coordinates from the SQLite structured store.
    """
    log_trace("Logistics Math Agent", "Dimensions & Physicals Agent", "subagent_query", {"sku": sku})
    tenant_id = tenant_context.get()
    r = get_inventory_by_sku(sku, tenant_id=tenant_id)
    if not r:
        res_str = f"Error: SKU {sku} not found."
    else:
        res_str = f"Weight: {r['weight']} kg, Volume: {r['volume']} m3, Location: {r['location']}"
        
    log_trace("Dimensions & Physicals Agent", "Logistics Math Agent", "subagent_response", {"result": res_str})
    return res_str

def query_location_replenishment_agent(sku: str) -> str:
    """
    Location/Replenishment Agent: Retrieves shelf capacity limits, lead times,
    cost parameters, and supplier MOQ from the SQLite structured store.
    """
    log_trace("Logistics Math Agent", "Location/Replenishment Agent", "subagent_query", {"sku": sku})
    tenant_id = tenant_context.get()
    r = get_inventory_by_sku(sku, tenant_id=tenant_id)
    if not r:
        res_str = f"Error: SKU {sku} not found."
    else:
        res_str = (
            f"Rack Max Weight Capacity: {r['rack_max_weight']} kg, "
            f"Rack Max Volume Capacity: {r['rack_max_volume']} m3, "
            f"Lead Time: {r['lead_time']} days, Supplier MOQ: {r['supplier_moq']} units, "
            f"Order Cost: ${r['order_cost']}, Holding Cost: ${r['holding_cost']}/unit/year"
        )
        
    log_trace("Location/Replenishment Agent", "Logistics Math Agent", "subagent_response", {"result": res_str})
    return res_str

# --- LOGISTICS MATH TOOLS ---

def calculate_eoq(annual_demand: float, order_cost: float, holding_cost: float) -> str:
    """Calculate the Economic Order Quantity (EOQ) using stockpyl."""
    try:
        annual_demand = float(annual_demand)
        order_cost = float(order_cost)
        holding_cost = float(holding_cost)
    except Exception as e:
        return f"Error: Arguments must be numeric. Details: {e}"
        
    if holding_cost <= 0:
        return "Error: Holding cost must be greater than zero."
        
    from stockpyl.ss import economic_order_quantity
    try:
        # stockpyl: economic_order_quantity(fixed_cost, holding_cost, demand_rate)
        eoq_val, cost = economic_order_quantity(
            fixed_cost=order_cost,
            holding_cost=holding_cost,
            demand_rate=annual_demand
        )
        result = int(round(eoq_val))
        log_trace("Logistics Math Agent", "calculate_eoq", "calculated_formula", {
            "annual_demand": annual_demand, "order_cost": order_cost, "holding_cost": holding_cost, "result": result
        })
        return f"Calculated EOQ: {result} units"
    except Exception as e:
        return f"Error calculating EOQ: {e}"

def calculate_rop(daily_demand: float, lead_time: int, safety_stock: int) -> str:
    """Calculate the Reorder Point (ROP)."""
    try:
        daily_demand = float(daily_demand)
        lead_time = int(lead_time)
        safety_stock = int(safety_stock)
    except Exception as e:
        return f"Error: Arguments must be numeric. Details: {e}"
        
    rop = (daily_demand * lead_time) + safety_stock
    result = int(round(rop))
    log_trace("Logistics Math Agent", "calculate_rop", "calculated_formula", {
        "daily_demand": daily_demand, "lead_time": lead_time, "safety_stock": safety_stock, "result": result
    })
    return f"Calculated Reorder Point (ROP): {result} units"

def check_rack_constraints(sku: str, quantity: int, current_stock: int, unit_weight: float, unit_volume: float, max_weight: float, max_volume: float) -> str:
    """Check if the given order quantity + current stock fits physically on the shelf (weight & volume)."""
    try:
        quantity = int(quantity)
        current_stock = int(current_stock)
        unit_weight = float(unit_weight)
        unit_volume = float(unit_volume)
        max_weight = float(max_weight)
        max_volume = float(max_volume)
    except Exception as e:
        return f"Error: Arguments must be numeric. Details: {e}"
        
    total_units = current_stock + quantity
    total_weight = total_units * unit_weight
    total_volume = total_units * unit_volume
    
    weight_ok = total_weight <= max_weight
    volume_ok = total_volume <= max_volume
    
    log_trace("Logistics Math Agent", "check_rack_constraints", "constraint_check", {
        "sku": sku, "quantity": quantity, "current_stock": current_stock,
        "total_weight": total_weight, "max_weight": max_weight,
        "total_volume": total_volume, "max_volume": max_volume,
        "fits": weight_ok and volume_ok
    })
    
    msg = f"Rack constraint check for SKU {sku} with new order of {quantity} units (Current Stock: {current_stock}):\n"
    msg += f"- Total units if ordered: {total_units}\n"
    msg += f"- Total Weight: {total_weight:.2f} kg (Max Shelf Limit: {max_weight:.2f} kg) -> {'PASS' if weight_ok else 'FAIL'}\n"
    msg += f"- Total Volume: {total_volume:.2f} m3 (Max Shelf Limit: {max_volume:.2f} m3) -> {'PASS' if volume_ok else 'FAIL'}\n"
    
    if weight_ok and volume_ok:
        msg += "Result: SUCCESS. The items fit on the designated rack shelf."
    else:
        msg += "Result: FAILED. The order exceeds shelf physical constraints. We need to allocate additional shelf space."
    return msg

def calculate_newsvendor_quantity(demand_mean: float, demand_sd: float, unit_holding_cost: float, stockout_cost: float, lead_time: int = 0) -> str:
    """
    Calculate the single-period optimal order quantity (Newsvendor Model) under normal demand uncertainty.
    Returns the optimal base stock level and expected cost.
    """
    from stockpyl.newsvendor import newsvendor_normal
    try:
        demand_mean = float(demand_mean)
        demand_sd = float(demand_sd)
        unit_holding_cost = float(unit_holding_cost)
        stockout_cost = float(stockout_cost)
        lead_time = int(lead_time)
    except Exception as e:
        return f"Error: Arguments must be numeric. Details: {e}"
        
    try:
        base_stock_level, expected_cost = newsvendor_normal(
            holding_cost=unit_holding_cost,
            stockout_cost=stockout_cost,
            demand_mean=demand_mean,
            demand_sd=demand_sd,
            lead_time=lead_time
        )
        opt_q = int(round(base_stock_level))
        cost = round(expected_cost, 2)
        
        msg = f"Calculated Newsvendor Base Stock Level: {opt_q} units\n"
        msg += f"- Expected Period Cost: ${cost}\n"
        msg += f"- Underage/Stockout Cost: ${stockout_cost}/unit\n"
        msg += f"- Overage/Holding Cost: ${unit_holding_cost}/unit\n"
        msg += f"- Target Service Level (Critical Ratio): {stockout_cost / (unit_holding_cost + stockout_cost):.2%}"
        
        log_trace("Logistics Math Agent", "calculate_newsvendor_quantity", "calculated_formula", {
            "demand_mean": demand_mean, "demand_sd": demand_sd, "holding_cost": unit_holding_cost,
            "stockout_cost": stockout_cost, "lead_time": lead_time, "result": opt_q
        })
        return msg
    except Exception as e:
        return f"Error executing Newsvendor calculation: {e}"

def calculate_ss_policy(demand_mean: float, demand_sd: float, holding_cost: float, stockout_cost: float, fixed_cost: float) -> str:
    """
    Calculate heuristic s and S (Reorder Point & Order-Up-To Level) for an (s, S) policy under normal demand distribution using Ehrhardt Power Approximation.
    """
    from stockpyl.ss import s_s_power_approximation
    try:
        demand_mean = float(demand_mean)
        demand_sd = float(demand_sd)
        holding_cost = float(holding_cost)
        stockout_cost = float(stockout_cost)
        fixed_cost = float(fixed_cost)
    except Exception as e:
        return f"Error: Arguments must be numeric. Details: {e}"
        
    try:
        reorder_point, order_up_to = s_s_power_approximation(
            holding_cost=holding_cost,
            stockout_cost=stockout_cost,
            fixed_cost=fixed_cost,
            demand_mean=demand_mean,
            demand_sd=demand_sd
        )
        s_val = int(round(reorder_point))
        S_val = int(round(order_up_to))
        
        msg = f"Calculated (s, S) Inventory Policy (Ehrhardt Power Approximation):\n"
        msg += f"- Reorder Point (s): {s_val} units\n"
        msg += f"- Order-Up-To Level (S): {S_val} units\n"
        msg += f"- Target Order Size (S - s): {S_val - s_val} units\n"
        msg += f"- Fixed Order Setup Cost: ${fixed_cost}\n"
        msg += f"- Holding Cost: ${holding_cost}/unit/period\n"
        msg += f"- Stockout Penalty: ${stockout_cost}/unit/period"
        
        log_trace("Logistics Math Agent", "calculate_ss_policy", "calculated_formula", {
            "demand_mean": demand_mean, "demand_sd": demand_sd, "holding_cost": holding_cost,
            "stockout_cost": stockout_cost, "fixed_cost": fixed_cost, "result_s": s_val, "result_S": S_val
        })
        return msg
    except Exception as e:
        return f"Error executing (s, S) policy calculation: {e}"

NVIDIA_TOOLS_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "query_stock_units_agent",
            "description": "Stock & Replenishment Agent: Retrieves current inventory stock levels, safety stock counts, and average daily demand for a SKU.",
            "parameters": {
                "type": "object",
                "properties": {
                    "sku": {"type": "string", "description": "The SKU code (e.g. SKU-8821)"}
                },
                "required": ["sku"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "query_physical_properties_agent",
            "description": "Dimensions & Physicals Agent: Retrieves unit weight, volume, and storage location for a SKU.",
            "parameters": {
                "type": "object",
                "properties": {
                    "sku": {"type": "string", "description": "The SKU code (e.g. SKU-8821)"}
                },
                "required": ["sku"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "query_location_replenishment_agent",
            "description": "Location/Replenishment Agent: Retrieves rack capacities, lead times, MOQ, order cost, and holding cost for a SKU.",
            "parameters": {
                "type": "object",
                "properties": {
                    "sku": {"type": "string", "description": "The SKU code (e.g. SKU-8821)"}
                },
                "required": ["sku"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "calculate_eoq",
            "description": "Calculates the Economic Order Quantity (EOQ) using annual demand, order cost, and holding cost.",
            "parameters": {
                "type": "object",
                "properties": {
                    "annual_demand": {"type": "number", "description": "Annual demand (daily demand * 365)"},
                    "order_cost": {"type": "number", "description": "Cost per order ($)"},
                    "holding_cost": {"type": "number", "description": "Holding cost per unit per year ($)"}
                },
                "required": ["annual_demand", "order_cost", "holding_cost"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "calculate_rop",
            "description": "Calculates the Reorder Point (ROP) using daily demand, lead time, and safety stock.",
            "parameters": {
                "type": "object",
                "properties": {
                    "daily_demand": {"type": "number", "description": "Average daily demand units"},
                    "lead_time": {"type": "integer", "description": "Supplier lead time in days"},
                    "safety_stock": {"type": "integer", "description": "Safety stock level"}
                },
                "required": ["daily_demand", "lead_time", "safety_stock"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "check_rack_constraints",
            "description": "Checks if the order quantity fits on the shelf space (weight & volume limits).",
            "parameters": {
                "type": "object",
                "properties": {
                    "sku": {"type": "string", "description": "SKU code"},
                    "quantity": {"type": "integer", "description": "Proposed order quantity"},
                    "current_stock": {"type": "integer", "description": "Current stock count"},
                    "unit_weight": {"type": "number", "description": "Weight per unit"},
                    "unit_volume": {"type": "number", "description": "Volume per unit"},
                    "max_weight": {"type": "number", "description": "Max shelf weight capacity"},
                    "max_volume": {"type": "number", "description": "Max shelf volume capacity"}
                },
                "required": ["sku", "quantity", "current_stock", "unit_weight", "unit_volume", "max_weight", "max_volume"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "calculate_newsvendor_quantity",
            "description": "Calculates the single-period optimal order quantity (Newsvendor Model) under normal demand uncertainty.",
            "parameters": {
                "type": "object",
                "properties": {
                    "demand_mean": {"type": "number", "description": "Mean demand per period"},
                    "demand_sd": {"type": "number", "description": "Standard deviation of demand"},
                    "unit_holding_cost": {"type": "number", "description": "Holding cost per unit per period"},
                    "stockout_cost": {"type": "number", "description": "Stockout penalty cost per unit"},
                    "lead_time": {"type": "integer", "description": "Lead time (default 0)"}
                },
                "required": ["demand_mean", "demand_sd", "unit_holding_cost", "stockout_cost"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "calculate_ss_policy",
            "description": "Calculates optimal s and S (Reorder Point & Order-Up-To Level) safety stock policy using Ehrhardt Power Approximation.",
            "parameters": {
                "type": "object",
                "properties": {
                    "demand_mean": {"type": "number", "description": "Mean demand per period"},
                    "demand_sd": {"type": "number", "description": "Standard deviation of demand"},
                    "holding_cost": {"type": "number", "description": "Holding cost per unit per period"},
                    "stockout_cost": {"type": "number", "description": "Stockout penalty cost per unit"},
                    "fixed_cost": {"type": "number", "description": "Fixed setup cost per order"}
                },
                "required": ["demand_mean", "demand_sd", "holding_cost", "stockout_cost", "fixed_cost"]
            }
        }
    }
]

import urllib.request
import json
import asyncio

async def run_nvidia_agent(system_instructions: str, prompt: str, tools: list = None) -> str:
    """
    Calls Llama-3-Nemotron-70B-Instruct via Nvidia NIM.
    Implements the agentic tool-execution loop locally in Python.
    """
    api_key = os.environ.get("NVIDIA_API_KEY")
    if not api_key:
        raise ValueError("NVIDIA_API_KEY environment variable is not set.")
        
    url = "https://integrate.api.nvidia.com/v1/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}"
    }
    
    messages = [
        {"role": "system", "content": system_instructions},
        {"role": "user", "content": prompt}
    ]
    
    funcs_map = {
        "query_stock_units_agent": query_stock_units_agent,
        "query_physical_properties_agent": query_physical_properties_agent,
        "query_location_replenishment_agent": query_location_replenishment_agent,
        "calculate_eoq": calculate_eoq,
        "calculate_rop": calculate_rop,
        "check_rack_constraints": check_rack_constraints,
        "calculate_newsvendor_quantity": calculate_newsvendor_quantity,
        "calculate_ss_policy": calculate_ss_policy
    }
    
    active_tools = []
    if tools:
        tool_names = [f.__name__ for f in tools]
        active_tools = [s for s in NVIDIA_TOOLS_SCHEMAS if s["function"]["name"] in tool_names]
        
    for loop_idx in range(10):
        payload = {
            "model": "meta/llama-3.1-8b-instruct",
            "messages": messages,
            "temperature": 0.1
        }
        if active_tools:
            payload["tools"] = active_tools
            
        def call_api():
            req = urllib.request.Request(
                url, 
                data=json.dumps(payload).encode("utf-8"), 
                headers=headers, 
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
                
        loop = asyncio.get_event_loop()
        try:
            res_data = await loop.run_in_executor(None, call_api)
        except Exception as e:
            print(f"[Nvidia API Error] Call failed: {e}")
            raise e
            
        choice = res_data["choices"][0]
        message = choice["message"]
        
        # Check if the model wants to execute tools
        tool_calls = message.get("tool_calls")
        if not tool_calls:
            # Reached final text response
            return message.get("content") or ""
            
        # Extract only the first tool call (Nvidia NIM only supports sequential single tool calls)
        tc = tool_calls[0]
        clean_assistant_msg = {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": tc["id"],
                    "type": "function",
                    "function": {
                        "name": tc["function"]["name"],
                        "arguments": tc["function"]["arguments"]
                    }
                }
            ]
        }
        if message.get("content"):
            clean_assistant_msg["content"] = message["content"]
        messages.append(clean_assistant_msg)
        
        # Execute only the first tool call
        func_id = tc["id"]
        func_name = tc["function"]["name"]
        func_args_str = tc["function"]["arguments"]
        
        try:
            func_args = json.loads(func_args_str)
        except Exception as e:
            print(f"[Nvidia Parser Warning] Failed to parse tool args: {func_args_str}")
            func_args = {}
            
        func = funcs_map.get(func_name)
        if func:
            print(f"[Nvidia Tool Call] Executing local tool: {func_name}({func_args})")
            try:
                result = func(**func_args)
            except Exception as ex:
                result = f"Error executing tool: {ex}"
        else:
            result = f"Error: Tool '{func_name}' is not registered."
            
        # Append tool result message (strictly role, tool_call_id, and content)
        messages.append({
            "role": "tool",
            "tool_call_id": func_id,
            "content": str(result)
        })
            
    raise RuntimeError("Nvidia Agent exceeded maximum tool loop iterations.")

async def run_agent_with_retry(config, prompt, max_retries=3, initial_delay=2):
    # Dummy placeholder since connection functions are fully handled by Nvidia loop
    pass

# --- STEP-BY-STEP AGENT RUNNERS ---

async def run_main_agent_intent(query: str, history: str = "") -> dict:
    """Invokes Llama-3-Nemotron to extract the query intent and target SKU."""
    log_trace("User", "Main UI Agent", "user_query", {"query": query})
    
    system_instructions = """You are the primary customer-facing Warehouse Assistant. Your job is to analyze the user's natural language query and determine the required action.

You will be provided with the user's current query and a summary of the recent conversation history (if any). If the user uses pronouns or implicit references (e.g., "what about SKU 4471?" or "how much does it weigh?"), use the conversation history to resolve the target SKU.

Analyze the query and classify it into one of four actions:
1. "delegate_to_logistics": Choose this if the query explicitly asks for logistics calculations, reorder point (ROP), economic order quantity (EOQ), shelf rack capacity constraints checks, newsvendor order quantities, safety stock calculations, or asks whether we need to replenish/order more items.
2. "basic_query": Choose this if the query only asks for simple information about a specific SKU (like its current stock level, designated storage location, weight, volume, or supplier MOQ) without requiring any math calculations.
3. "list_stock": Choose this if the query asks to list all items, show the inventory catalog, search for low-stock items, or check overall count of items (when no specific SKU is targeted).
4. "cant_answer": Choose this if the query is out of scope (e.g. asking about weather, cooking, general knowledge, sports, personal questions, or anything completely unrelated to warehouse inventory management, logistics, or math).

Output a JSON object exactly in this format:
{
  "action": "delegate_to_logistics" | "basic_query" | "list_stock" | "cant_answer",
  "sku": "SKU-XXXX" | null,
  "explanation": "Brief explanation of why this action was chosen"
}

Do not output any markdown code blocks, backticks, or other text outside the JSON object."""
    
    prompt_with_history = f"Conversation History:\n{history}\n\nUser Query: {query}" if history else query

    response_text = await run_nvidia_agent(system_instructions, prompt_with_history)
    
    # Robustly parse JSON from response
    intent = {"action": "list_stock", "sku": None}
    match = re.search(r'\{.*\}', response_text, re.DOTALL)
    if match:
        try:
            parsed = json.loads(match.group(0))
            intent["action"] = parsed.get("action", "list_stock")
            intent["sku"] = parsed.get("sku")
        except Exception:
            pass
            
    # Normalize SKU extraction if any SKU string was parsed
    if intent["sku"]:
        # Standardize formatting
        sku_clean = str(intent["sku"]).strip().upper()
        if sku_clean == "NULL" or sku_clean == "NONE" or "SKU-XXXX" in sku_clean:
            intent["sku"] = None
        else:
            intent["sku"] = sku_clean
            
    # Secondary regex extraction fallback for SKU in case JSON model missed it
    if not intent["sku"]:
        sku_match = re.search(r'(?:SKU|ITM|ITEM|PROD)-\d+', query, re.IGNORECASE)
        if sku_match:
            intent["sku"] = sku_match.group(0).upper()
            
    log_trace("Main UI Agent", "Coordinator", "delegate_request", intent)
    return intent

async def run_logistics_math_agent(sku: str, original_request: str) -> str:
    """
    Invokes Llama-3-Nemotron to run calculations.
    Dynamically decides which child sub-agents to query and what math functions to call.
    """
    log_trace("Coordinator", "Logistics Math Agent", "math_request", {"sku": sku, "request": original_request})
    
    system_instructions = f"""You are the dedicated Logistics Math Processor. Your objective is to resolve the user's specific request.
You do not guess numbers. You must call your registered child sub-agent tools to lookup data for {sku}, and then call your registered mathematical Python tools to produce deterministic answers.

CRITICAL:
- Analyze the user's specific request carefully. Only call the tools and execute the calculations (EOQ, ROP, shelf constraints, Newsvendor optimal order quantity, safety stock policy, or database lookups) that are directly requested or required to satisfy the user's specific question.
- Do not perform calculations or check constraints that the user did not ask for (e.g., if they only asked to find the location or calculate EOQ, do not preemptively calculate ROP or check rack capacity).
- You must execute your tools ONE by ONE in sequence. Never call multiple tools in a single assistant turn. Call a tool, wait for the result, and then call the next tool if needed.
- Once you have gathered enough facts to answer the specific question, output a concise report explaining the answers and numbers."""

    tools = [
        query_stock_units_agent,
        query_physical_properties_agent,
        query_location_replenishment_agent,
        calculate_eoq,
        calculate_rop,
        check_rack_constraints,
        calculate_newsvendor_quantity,
        calculate_ss_policy
    ]
    
    prompt = f"Analyze SKU {sku} based on the request: \"{original_request}\""
    response_text = await run_nvidia_agent(system_instructions, prompt, tools=tools)
    
    log_trace("Logistics Math Agent", "Coordinator", "math_response", {"text": response_text})
    return response_text

async def run_main_agent_response(query: str, math_report: str) -> str:
    """Invokes Llama-3-Nemotron to format the final response politely."""
    log_trace("Coordinator", "Main UI Agent", "format_request", {"report": math_report})
    
    system_instructions = """You are the primary customer-facing Warehouse Assistant. Your job is exclusively to answer natural language user queries based strictly on the provided inventory catalog table or calculation report.
CRITICAL RULES:
- Answer the user's questions (like counting items, listing low stock, or describing storage locations) using ONLY the actual data in the provided catalog/report.
- Do NOT hallucinate, guess, or assume any SKUs, stock counts, or locations that are not explicitly present in the provided catalog/report.
- If the catalog table only contains 5 rows, then you have a total of 5 SKUs in the warehouse. Do not invent a larger number.
- Respond in a polite, professional, and clear warehouse assistant tone."""
    
    prompt = f"""The user asked: "{query}"
Here is the official data report:
{math_report}

Please respond politely to the user, answering their question directly and accurately based on the report above."""
    
    response_text = await run_nvidia_agent(system_instructions, prompt)
    log_trace("Main UI Agent", "User", "final_response", {"text": response_text})
    return response_text

# --- LOCAL REASONING ENGINE (Fallback when Gemini API is rate-limited) ---

def run_local_agent_reasoning(sku: str, user_query: str, tenant_id: str = "default_tenant") -> dict:
    """
    Executes the entire agent chain locally and programmatically.
    Queries RAG, executes math formulas, validates constraints, and compiles the trace.
    Supports dynamic queries (general lookups, lists, specific fields) even when rate-limited.
    """
    # 1. Log User Query
    log_trace("User", "Main UI Agent", "user_query", {"query": user_query})
    
    tenant_context.set(tenant_id)

    # Check for out-of-scope queries
    is_out_of_scope = not any(w in user_query.lower() for w in [
        "sku", "stock", "location", "weight", "volume", "eoq", "rop", "math", "capacity", 
        "constraint", "calculate", "replenish", "order", "list", "catalog", "itm", "prod", 
        "newsvendor", "safety stock", "s, s", "s,s"
    ])
    if is_out_of_scope:
        msg = "I'm sorry, but that request is out of scope for our Warehouse and Logistics Management System. I can only assist with inventory queries, listing stock, and logistics math calculations."
        log_trace("Main UI Agent", "User", "cant_answer", {"query": user_query})
        return {
            "query": user_query,
            "response": msg,
            "trace": trace_logs
        }

    # Check if query is a listing / catalog request
    is_list_query = any(w in user_query.lower() for w in ["list", "all", "catalog", "show me", "stock on hand", "inventory", "skus"])
    
    if is_list_query or not sku:
        # Pull all records from LanceDB scoped to tenant
        records = rag.get_all(tenant_id=tenant_id)
        records.sort(key=lambda x: x["sku"])
        
        # Build standard stock table
        table_rows = []
        for r in records:
            table_rows.append(f"| {r['sku']} | {r['name']} | {r['stock']} | {r['location']} | {r['safety_stock']} | {r['daily_demand']} |")
        table_md = "\n".join(table_rows)
        
        final_response = f"""Hello! Since we are currently operating under local database fallback mode, I have queried our inventory catalog database directly. Here is a complete list of all stock currently on hand in the warehouse:
 
| SKU | Item Name | Current Stock | Location | Safety Stock | Daily Demand |
| --- | --- | --- | --- | --- | --- |
{table_md}
 
If you would like me to calculate reorder quantities or rack capacity checks for a specific SKU, please ask a question specifying that SKU (e.g., "Calculate EOQ for SKU-XXXX")."""
        
        log_trace("Main UI Agent", "User", "final_response", {"text": final_response})
        return {
            "query": user_query,
            "response": final_response,
            "trace": trace_logs
        }

    # Simulate Main UI Agent Intent Extraction
    intent = {"action": "delegate_to_logistics", "sku": sku, "request": user_query}
    log_trace("Main UI Agent", "Coordinator", "delegate_request", intent)
    
    r = rag.get_by_sku(sku, tenant_id=tenant_id)
    if not r:
        final_response = f"Hello! I looked up SKU '{sku}' in our catalog, but it was not found. Please double check the SKU code and try again."
        log_trace("Main UI Agent", "User", "final_response", {"text": final_response})
        return {
            "query": user_query,
            "response": final_response,
            "trace": trace_logs
        }

    print(f"\n[Fallback] Executing local deterministic agent reasoning for {sku} under tenant {tenant_id}...")

    # 2. Simulate Child Data Sub-Agents Lookups
    stock_info = query_stock_units_agent(sku)
    phys_info = query_physical_properties_agent(sku)
    loc_info = query_location_replenishment_agent(sku)
    
    # 3. Simulate Logistics Math Agent reasoning and tool executions
    log_trace("Coordinator", "Logistics Math Agent", "math_request", {"sku": sku, "facts": {
        "stock": stock_info, "physicals": phys_info, "location_replenishment": loc_info
    }})

    query_lower = user_query.lower()
    
    # Check math/replenishment indicators first
    if any(w in query_lower for w in ["eoq", "rop", "calculate", "math", "order", "low", "replenish", "reorder", "newsvendor", "safety stock", "s, s", "s,s"]):
        # Check if specifically requesting Newsvendor or (s, S) policy
        if "newsvendor" in query_lower:
            holding_period = r["holding_cost"] / 365.0  # Daily holding cost
            stockout_penalty = r.get("order_cost", 50.0) / 10.0  # Simulated stockout cost per unit
            newsv_str = calculate_newsvendor_quantity(r["daily_demand"], r["daily_demand"] * 0.3, holding_period, stockout_penalty, r["lead_time"])
            math_report = f"Logistics Calculation Report for {sku}:\n1. Newsvendor Order Analysis:\n{newsv_str}"
            final_response = f"Hello! I have run the Newsvendor analysis for **{r['name']} ({sku})**:\n\n{newsv_str}"
        elif "safety stock" in query_lower or "s," in query_lower:
            holding_period = r["holding_cost"] / 365.0
            stockout_penalty = r.get("order_cost", 50.0) / 5.0
            ss_policy_str = calculate_ss_policy(r["daily_demand"], r["daily_demand"] * 0.3, holding_period, stockout_penalty, r["order_cost"])
            math_report = f"Logistics Calculation Report for {sku}:\n1. (s, S) Safety Stock Policy Analysis:\n{ss_policy_str}"
            final_response = f"Hello! I have computed the optimal (s, S) safety stock policy for **{r['name']} ({sku})**:\n\n{ss_policy_str}"
        else:
            # Default/Math/Replenishment analysis query (EOQ, ROP, shelf checks)
            rop_str = calculate_rop(r["daily_demand"], r["lead_time"], r["safety_stock"])
            rop_val = (r["daily_demand"] * r["lead_time"]) + r["safety_stock"]
            need_order = r["stock"] < rop_val
            
            annual_demand = r["daily_demand"] * 365
            eoq_str = calculate_eoq(annual_demand, r["order_cost"], r["holding_cost"])
            eoq_val = int(round(math.sqrt((2 * annual_demand * r["order_cost"]) / r["holding_cost"])))
            
            constraint_str = check_rack_constraints(sku, eoq_val, r["stock"], r["weight"], r["volume"], r["rack_max_weight"], r["rack_max_volume"])
            
            math_report = f"""Logistics Calculation Report for {sku}:
1. Reorder Point (ROP): {rop_str}. Current Stock: {r['stock']} units. Reorder Needed: {need_order} (Stock is below ROP).
2. Economic Order Quantity (EOQ): {eoq_str}.
3. Rack Constraints Check: {constraint_str}
"""
            log_trace("Logistics Math Agent", "Coordinator", "math_response", {"text": math_report})
            log_trace("Coordinator", "Main UI Agent", "format_request", {"report": math_report})
            
            remaining_weight = r["rack_max_weight"] - (r["stock"] * r["weight"])
            remaining_volume = r["rack_max_volume"] - (r["stock"] * r["volume"])
            
            final_response = f"""Hello! I have consulted our Logistics Math Agent regarding your request for **{r['name']} ({sku})**. Here is the detailed assessment:
 
1. **Inventory & Replenishment**:
   * Current Stock: **{r['stock']} units**
   * Safety Stock: **{r['safety_stock']} units**
   * Daily Usage: **{r['daily_demand']} units/day**
   * Calculated Reorder Point (ROP): **{int(round(rop_val))} units**
   * **Decision**: Since our current stock ({r['stock']}) is below the Reorder Point ({int(round(rop_val))}), **we must place a replenishment order immediately**.
 
2. **Optimal Order Size (EOQ)**:
   * Calculated Economic Order Quantity (EOQ): **{eoq_val} units** (based on annual demand of {int(annual_demand)} units, order cost of ${r['order_cost']:.2f}, and holding cost of ${r['holding_cost']:.2f}/unit/year).
   * Supplier Minimum Order Quantity (MOQ): **{r['supplier_moq']} units**.
 
3. **Storage Rack Constraints & Capacity**:
   * Storage Location: **{r['location']}**
   * Remaining Weight capacity on shelf: **{remaining_weight:.2f} kg**
   * Remaining Volume capacity on shelf: **{remaining_volume:.2f} m³**
   * The optimal order (EOQ of {eoq_val} units) requires {eoq_val * r['weight']} kg and {eoq_val * r['volume']} m³.
   * **Constraint Audit**: This optimal size **exceeds** the physical constraints of the designated shelf ({r['location']}).
   * **Recommendation**: We should either cap our immediate order at the remaining shelf capacity of **{int(remaining_volume / r['volume'])} units** (though this is below the supplier MOQ of {r['supplier_moq']}), or allocate **{int(math.ceil((eoq_val + r['stock']) * r['volume'] / r['rack_max_volume']))} shelves total** on this rack to safely accommodate the full cost-optimal EOQ order of **{eoq_val} units**.
"""
        log_trace("Main UI Agent", "User", "final_response", {"text": final_response})
        
    elif any(w in query_lower for w in ["weight", "volume", "dimension", "physical", "size"]):
        # Physical attributes query
        final_response = f"""Hello! I have retrieved the physical specifications for **{r['name']} ({sku})** from our Dimensions & Physicals Agent:
 
*   **Location**: {r['location']}
*   **Unit Weight**: {r['weight']} kg
*   **Unit Volume**: {r['volume']} m³
*   **Shelf Limits**: Max Shelf Weight: {r['rack_max_weight']} kg, Max Shelf Volume: {r['rack_max_volume']} m³"""
        log_trace("Main UI Agent", "User", "final_response", {"text": final_response})
        
    elif any(w in query_lower for w in ["location", "shelf", "rack", "where"]):
        # Location query
        final_response = f"""Hello! I have consulted the Location/Replenishment Agent regarding **{r['name']} ({sku})**:
 
*   **Designated Storage Location**: {r['location']}
*   **Rack Capacity Constraints**:
    *   Maximum Weight: **{r['rack_max_weight']} kg**
    *   Maximum Volume: **{r['rack_max_volume']} m³**
*   **Replenishment Location Specs**: Lead Time is {r['lead_time']} days from the supplier."""
        log_trace("Main UI Agent", "User", "final_response", {"text": final_response})
        
    else:
        # Basic inventory query
        final_response = f"""Hello! Here is the inventory summary for **{r['name']} ({sku})** from the Stock & Replenishment Agent:
 
*   **Current Stock**: **{r['stock']} units**
*   **Safety Stock threshold**: **{r['safety_stock']} units**
*   **Daily Demand**: **{r['daily_demand']} units/day**"""
        log_trace("Main UI Agent", "User", "final_response", {"text": final_response})
    return {
        "query": user_query,
        "response": final_response,
        "trace": trace_logs
    }

# --- MAIN RUNNER ---

def extract_sku_from_query(query: str, tenant_id: str = "default_tenant", history_sku: str = None) -> str:
    """Dynamically matches any known SKU from the database in the user query."""
    clean_query = query.upper()
    # Split query into alphanumeric words (allowing hyphens)
    words = re.findall(r'[A-Z0-9\-]+', clean_query)
    
    try:
        records = rag.get_all(tenant_id=tenant_id)
        known_skus = {r["sku"].upper().strip() for r in records if r.get("sku")}
    except Exception:
        known_skus = set()
        
    for word in words:
        word_strip = word.strip()
        if word_strip in known_skus:
            return word_strip
            
    # Fallback generic regex search if database is empty or not yet loaded
    sku_match = re.search(r'(?:SKU|ITM|ITEM|PROD)-\d+', query, re.IGNORECASE)
    if sku_match:
        return sku_match.group(0).upper()
        
    if history_sku and any(w in query.lower() for w in ["it", "this", "that"]):
        return history_sku

    return None

async def run_warehouse_system(user_query: str, tenant_id: str = "default_tenant", session_id: str = None) -> dict:
    """Run the entire tiered agent loop and return the result and execution trace."""
    global trace_logs
    trace_logs = []  # Clear log for a new run
    RATE_LIMIT_DELAY = 15  # Spacing in seconds to remain under 5 requests/min limit
    
    # Store request-scoped tenant context
    tenant_context.set(tenant_id)
    
    # Session handling
    history_context = ""
    history_sku = None
    if session_id:
        if session_id not in SESSION_STORE:
            SESSION_STORE[session_id] = {"history": [], "last_sku": None}

        session_data = SESSION_STORE[session_id]
        history_sku = session_data.get("last_sku")

        # Format recent history for the prompt (last 3 turns)
        recent_history = session_data["history"][-3:]
        if recent_history:
            history_context = "\n".join([f"User: {h['query']}\nAgent: {h['response']}" for h in recent_history])

    # Extract SKU initially as a fallback/hint, but prioritize Main Agent's classification
    hint_sku = extract_sku_from_query(user_query, tenant_id=tenant_id, history_sku=history_sku)
    sku = hint_sku
    
    try:
        # Step 1: Main UI Agent analyzes user input and classifies required action
        intent = await run_main_agent_intent(user_query, history=history_context)
        action = intent.get("action", "list_stock")
        sku = intent.get("sku") or hint_sku
        
        if sku and session_id:
            SESSION_STORE[session_id]["last_sku"] = sku

        if action == "cant_answer":
            msg = "I'm sorry, but that request is out of scope for our Warehouse and Logistics Management System. I can only assist with inventory queries, listing stock, and logistics math calculations."
            log_trace("Main UI Agent", "User", "cant_answer", {"query": user_query})
            if session_id:
                SESSION_STORE[session_id]["history"].append({"query": user_query, "response": msg})
            return {
                "query": user_query,
                "response": msg,
                "trace": trace_logs
            }
            
        elif action == "delegate_to_logistics" and sku:
            # Main UI Agent requested math calculations/replenishment auditing
            print(f"Main UI Agent requested logistics calculations for SKU {sku}. Sleeping {RATE_LIMIT_DELAY}s...")
            await asyncio.sleep(RATE_LIMIT_DELAY)
            
            # Step 2: Run Logistics Math Agent (executes ROP, EOQ, rack constraints math tools)
            math_report = await run_logistics_math_agent(sku, user_query)
            print(f"Logistics Math Agent calculations complete. Sleeping {RATE_LIMIT_DELAY}s...")
            await asyncio.sleep(RATE_LIMIT_DELAY)
            
            # Step 3: Main UI Agent formats final response
            final_response = await run_main_agent_response(user_query, math_report)
            
            if session_id:
                SESSION_STORE[session_id]["history"].append({"query": user_query, "response": final_response})

            return {
                "query": user_query,
                "response": final_response,
                "trace": trace_logs
            }
            
        elif action == "basic_query" and sku:
            # Main UI Agent requested simple database lookup without math calculations
            print(f"Main UI Agent requested database lookup for SKU {sku}. Sleeping {RATE_LIMIT_DELAY}s...")
            await asyncio.sleep(RATE_LIMIT_DELAY)
            
            r = rag.get_by_sku(sku, tenant_id=tenant_id)
            if not r:
                raw_report = f"Error: SKU {sku} was not found in the inventory database."
            else:
                raw_report = f"Database Record for SKU {sku} ({r['name']}):\n"
                raw_report += f"- Stock Level: {r['stock']} units\n"
                raw_report += f"- Safety Stock: {r['safety_stock']} units\n"
                raw_report += f"- Daily Demand: {r['daily_demand']} units/day\n"
                raw_report += f"- Designated Storage Location: {r['location']}\n"
                raw_report += f"- Unit Weight: {r['weight']} kg\n"
                raw_report += f"- Unit Volume: {r['volume']} m3\n"
                raw_report += f"- Supplier MOQ: {r['supplier_moq']} units\n"
                raw_report += f"- Order Cost: ${r['order_cost']}\n"
                raw_report += f"- Holding Cost: ${r['holding_cost']}/unit/year\n"
                raw_report += f"- Shelf Capacity Constraints: Max Weight {r['rack_max_weight']} kg, Max Volume {r['rack_max_volume']} m3\n"
            
            # Step 2: Main UI Agent formats final response directly from the record info
            final_response = await run_main_agent_response(user_query, raw_report)
            if session_id:
                SESSION_STORE[session_id]["history"].append({"query": user_query, "response": final_response})
            return {
                "query": user_query,
                "response": final_response,
                "trace": trace_logs
            }
            
        else:
            # Main UI Agent requested general catalog view or search/filter on multiple items
            print(f"Main UI Agent requested inventory catalog view. Sleeping {RATE_LIMIT_DELAY}s...")
            await asyncio.sleep(RATE_LIMIT_DELAY)
            
            records = rag.get_all(tenant_id=tenant_id)
            records.sort(key=lambda x: x["sku"])
            
            # Format raw markdown table of the entire database
            table_rows = []
            for r in records:
                table_rows.append(f"| {r['sku']} | {r['name']} | {r['stock']} | {r['location']} | {r['safety_stock']} | {r['daily_demand']} |")
            table_md = "\n".join(table_rows)
            
            raw_report = f"""Inventory Catalog Table:
| SKU | Item Name | Current Stock | Location | Safety Stock | Daily Demand |
| --- | --- | --- | --- | --- | --- |
{table_md}"""
            
            # Step 2: Main UI Agent formats final response based strictly on the table rows
            final_response = await run_main_agent_response(user_query, raw_report)
            if session_id:
                SESSION_STORE[session_id]["history"].append({"query": user_query, "response": final_response})
            return {
                "query": user_query,
                "response": final_response,
                "trace": trace_logs
            }
            
    except Exception as e:
        err_msg = str(e)
        if "429" in err_msg or "quota" in err_msg.lower() or "503" in err_msg:
            print(f"\n[Rate Limit / API Issue] Fallback triggered due to model provider error: {e}")
        else:
            print(f"\n[Processing Issue] Fallback triggered due to parsing or routing error: {e}")
            
        # Run local reasoning engine to guarantee successful trace output
        fallback_res = run_local_agent_reasoning(sku or hint_sku, user_query, tenant_id=tenant_id)
        if session_id:
            SESSION_STORE[session_id]["history"].append({"query": user_query, "response": fallback_res["response"]})
        return fallback_res
