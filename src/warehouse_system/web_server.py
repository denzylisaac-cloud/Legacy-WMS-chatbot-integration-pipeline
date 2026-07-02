import os
import sys
import json
import base64
import asyncio
from fastapi import FastAPI, HTTPException, Header, Depends, BackgroundTasks
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from datetime import datetime

# Add root folder to sys.path to enable clean absolute package imports
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from src.warehouse_system.app import run_warehouse_system, rag
from src.warehouse_system.adapters import IngestionManager

app = FastAPI(title="AI Warehouse Chatbot & WMS Dashboard")

CONFIG_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "wms_config.yaml"))
ingestion_manager = IngestionManager(CONFIG_PATH)

# In-memory mock accounts for basic authentication validation
VALID_CREDENTIALS = {
    "default_tenant": "default_pass",
    "client_a": "client_a_pass",
    "client_b": "client_b_pass",
    "client_c": "client_c_pass"
}

class ChatRequest(BaseModel):
    message: str
    session_id: str = None

def authenticate_tenant(authorization: str = Header(None)) -> str:
    """Validates basic authorization header and returns the authenticated tenant ID."""
    if not authorization:
        # Fallback for easy testing in browser: default to default_tenant if no header
        return "default_tenant"
        
    if not authorization.startswith("Basic "):
        raise HTTPException(status_code=401, detail="Invalid authentication scheme. Basic Auth required.")
        
    try:
        encoded = authorization.split(" ")[1]
        decoded = base64.b64decode(encoded).decode("utf-8")
        username, password = decoded.split(":")
    except Exception:
        raise HTTPException(status_code=401, detail="Failed to decode credentials.")
        
    if username not in VALID_CREDENTIALS or VALID_CREDENTIALS[username] != password:
        raise HTTPException(status_code=401, detail="Unauthorized tenant ID or invalid token.")
        
    return username

# Serve static dashboard UI
STATIC_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "static"))
os.makedirs(STATIC_DIR, exist_ok=True)

@app.get("/", response_class=HTMLResponse)
def read_root():
    index_path = os.path.join(STATIC_DIR, "index.html")
    if os.path.exists(index_path):
        with open(index_path, "r", encoding="utf-8") as f:
            return f.read()
    return "<h3>Error: index.html not found. Place it in the static/ folder.</h3>"

@app.post("/api/chat")
async def chat_endpoint(req: ChatRequest, tenant_id: str = Depends(authenticate_tenant)):
    """Executes chatbot request under the scope of the authenticated tenant."""
    if not req.message.strip():
        raise HTTPException(status_code=400, detail="Empty query string.")
        
    try:
        result = await run_warehouse_system(req.message, tenant_id=tenant_id, session_id=req.session_id)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Agent Execution Failure: {e}")

@app.get("/api/status")
def status_endpoint(tenant_id: str = Depends(authenticate_tenant)):
    """Retrieves config, ingestion logs, and DLQ errors for the tenant's channels."""
    # 1. Parse active config sources
    active_sources = []
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            try:
                import yaml
                config = yaml.safe_load(f)
                active_sources = config.get("sources", [])
            except Exception:
                pass
                
    # Filter config by tenant
    tenant_sources = [s for s in active_sources if s.get("tenant_id") == tenant_id]

    # 2. Parse Ingestion Log Events
    log_path = os.path.abspath("./logs/ingestion.log")
    recent_ingestions = []
    success_count = 0
    fail_count = 0
    
    if os.path.exists(log_path):
        with open(log_path, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    entry = json.loads(line)
                    if entry.get("tenant_id") == tenant_id:
                        recent_ingestions.append(entry)
                        if entry.get("status") == "SUCCESS":
                            success_count += entry.get("row_count", 0)
                        else:
                            fail_count += 1
                except Exception:
                    pass
                    
    # Reverse to show most recent first
    recent_ingestions = recent_ingestions[-20:][::-1]

    # 3. Scan DLQ Directory for client failures
    dlq_dir = os.path.abspath(f"./data/dlq/{tenant_id}")
    dlq_failures = []
    if os.path.exists(dlq_dir):
        for fname in os.listdir(dlq_dir):
            if fname.endswith(".err"):
                err_path = os.path.join(dlq_dir, fname)
                try:
                    with open(err_path, "r", encoding="utf-8") as f:
                        err_content = f.read()
                    dlq_failures.append({
                        "filename": fname.replace(".err", ""),
                        "details": err_content
                    })
                except Exception:
                    pass

    return {
        "tenant_id": tenant_id,
        "active_sources": tenant_sources,
        "recent_ingestions": recent_ingestions,
        "dlq_failures": dlq_failures,
        "success_records_count": success_count,
        "failed_jobs_count": fail_count
    }

@app.post("/api/sync")
def sync_endpoint(background_tasks: BackgroundTasks, tenant_id: str = Depends(authenticate_tenant)):
    """Triggers immediate WMS adapter polling and file ingestion."""
    # We execute immediately and return count for instant feedback
    try:
        records_ingested = ingestion_manager.run_ingest(rag)
        return {
            "status": "COMPLETED",
            "message": f"Successfully synced active channels. Ingested {records_ingested} records.",
            "records_ingested": records_ingested
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Manual sync execution failed: {e}")
