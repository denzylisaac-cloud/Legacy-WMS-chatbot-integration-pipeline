import os
import shutil
import sqlite3
import urllib.request
import json
import yaml
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from .data_parser import parse_file

@dataclass
class RawRecord:
    data: bytes
    filename: str
    format: str
    tenant_id: str

class WMSAdapter(ABC):
    @abstractmethod
    def poll(self) -> list[RawRecord]:
        """Check source for new or updated data since last poll."""
        pass

    @abstractmethod
    def ack(self, filename: str) -> None:
        """Mark a file/record as successfully processed (checkpoint)."""
        pass

    @abstractmethod
    def source_type(self) -> str:
        """Name of the integration type."""
        pass

class SFTPAdapter(WMSAdapter):
    """
    Simulates an SFTP drop folder integration.
    Reads CSV/XML/JSON files dropped into a designated local directory.
    """
    def __init__(self, config: dict):
        self.tenant_id = config.get("tenant_id", "default_tenant")
        self.drop_dir = os.path.abspath(config.get("path", "./data/sftp_drop"))
        self.proc_dir = os.path.abspath(config.get("processed_path", "./data/sftp_processed"))
        self.format = config.get("format", "csv")
        os.makedirs(self.drop_dir, exist_ok=True)
        os.makedirs(self.proc_dir, exist_ok=True)

    def source_type(self) -> str:
        return "sftp"

    def poll(self) -> list[RawRecord]:
        records = []
        if not os.path.exists(self.drop_dir):
            return []
            
        for filename in os.listdir(self.drop_dir):
            file_path = os.path.join(self.drop_dir, filename)
            if os.path.isfile(file_path):
                # Ignore hidden files
                if filename.startswith('.'):
                    continue
                try:
                    with open(file_path, 'rb') as f:
                        data = f.read()
                    records.append(RawRecord(
                        data=data,
                        filename=filename,
                        format=self.format,
                        tenant_id=self.tenant_id
                    ))
                except Exception as e:
                    print(f"[SFTPAdapter Error] Failed to read {filename}: {e}")
        return records

    def ack(self, filename: str) -> None:
        src = os.path.join(self.drop_dir, filename)
        dst = os.path.join(self.proc_dir, filename)
        if os.path.exists(src):
            shutil.move(src, dst)

class DBPollAdapter(WMSAdapter):
    """
    Simulates a read-replica database poll adapter.
    Executes a configured query against an inventory replica DB (SQLite).
    """
    def __init__(self, config: dict):
        self.tenant_id = config.get("tenant_id", "default_tenant")
        self.db_conn_str = os.path.abspath(config.get("connection", "./data/wms_replica.db"))
        self.query = config.get("query", "SELECT * FROM wms_inventory")
        self.format = "json"
        
        # Initialize mock replica DB table for demonstration/testing if missing
        os.makedirs(os.path.dirname(self.db_conn_str), exist_ok=True)
        conn = sqlite3.connect(self.db_conn_str)
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS wms_inventory (
                item_id TEXT PRIMARY KEY,
                item_name TEXT,
                stock_level INTEGER,
                reorder_point INTEGER,
                daily_demand REAL,
                lead_time_days INTEGER,
                weight_kg REAL,
                volume_m3 REAL,
                zone TEXT,
                storage_location_id TEXT,
                handling_cost REAL,
                holding_cost REAL,
                moq INTEGER
            )
        """)
        conn.commit()
        conn.close()

    def source_type(self) -> str:
        return "db_poll"

    def poll(self) -> list[RawRecord]:
        conn = sqlite3.connect(self.db_conn_str)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        records = []
        try:
            cursor.execute(self.query)
            rows = cursor.fetchall()
            if rows:
                # Convert SQLite rows to a standard JSON dump raw string
                data_list = [dict(r) for r in rows]
                json_data = json.dumps(data_list).encode('utf-8')
                filename = f"db_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
                
                records.append(RawRecord(
                    data=json_data,
                    filename=filename,
                    format=self.format,
                    tenant_id=self.tenant_id
                ))
        except Exception as e:
            print(f"[DBPollAdapter Error] Failed database poll query: {e}")
        finally:
            conn.close()
            
        return records

    def ack(self, filename: str) -> None:
        # Checkpointing: In a real WMS replica we might write to a watermark table or log table.
        # For simulation, we'll log successful poll
        pass

class EDIReceiverAdapter(WMSAdapter):
    """
    Simulates receiving EDI segments via AS2/VAN dropboxes.
    Reads EDI catalog segments dropped into a designated local inbox folder.
    """
    def __init__(self, config: dict):
        self.tenant_id = config.get("tenant_id", "default_tenant")
        self.inbox_dir = os.path.abspath(config.get("path", "./data/edi_inbox"))
        self.proc_dir = os.path.abspath(config.get("processed_path", "./data/edi_processed"))
        self.format = "edi"
        os.makedirs(self.inbox_dir, exist_ok=True)
        os.makedirs(self.proc_dir, exist_ok=True)

    def source_type(self) -> str:
        return "edi_as2"

    def poll(self) -> list[RawRecord]:
        records = []
        if not os.path.exists(self.inbox_dir):
            return []
            
        for filename in os.listdir(self.inbox_dir):
            file_path = os.path.join(self.inbox_dir, filename)
            if os.path.isfile(file_path):
                if filename.startswith('.'):
                    continue
                try:
                    with open(file_path, 'rb') as f:
                        data = f.read()
                    records.append(RawRecord(
                        data=data,
                        filename=filename,
                        format=self.format,
                        tenant_id=self.tenant_id
                    ))
                except Exception as e:
                    print(f"[EDIReceiverAdapter Error] Failed to read EDI: {e}")
        return records

    def ack(self, filename: str) -> None:
        src = os.path.join(self.inbox_dir, filename)
        dst = os.path.join(self.proc_dir, filename)
        if os.path.exists(src):
            shutil.move(src, dst)

class RESTPullAdapter(WMSAdapter):
    """
    Simulates pulling inventory data from a REST endpoint.
    Exposes configurable WMS APIs. Falls back to generating a mock API payload.
    """
    def __init__(self, config: dict):
        self.tenant_id = config.get("tenant_id", "default_tenant")
        self.url = config.get("url", "https://api.mockwms.local/inventory")
        self.format = config.get("format", "json")

    def source_type(self) -> str:
        return "rest_pull"

    def poll(self) -> list[RawRecord]:
        # Perform mock HTTP query response simulation
        # In a real environment, we would do urllib.request.urlopen(self.url)
        mock_payload = [
            {
                "id": "SKU-9990",
                "category": "Industrial Tools",
                "stock_level": 450,
                "reorder_point": 50,
                "daily_demand": 12.5,
                "lead_time_days": 7,
                "weight_kg": 4.5,
                "volume_m3": 0.08,
                "zone": "Zone A",
                "location_id": "Shelf-C5"
            }
        ]
        
        json_bytes = json.dumps(mock_payload).encode('utf-8')
        filename = f"rest_api_pull_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        
        return [RawRecord(
            data=json_bytes,
            filename=filename,
            format=self.format,
            tenant_id=self.tenant_id
        )]

    def ack(self, filename: str) -> None:
        pass

class IngestionManager:
    def __init__(self, config_path: str):
        self.config_path = os.path.abspath(config_path)
        self.adapters: list[WMSAdapter] = []
        self.dlq_dir = os.path.abspath("./data/dlq")
        self.log_file = os.path.abspath("./logs/ingestion.log")
        
        os.makedirs(self.dlq_dir, exist_ok=True)
        os.makedirs(os.path.dirname(self.log_file), exist_ok=True)
        
        self.load_config()

    def load_config(self) -> None:
        if not os.path.exists(self.config_path):
            print(f"[IngestionManager Warning] Config file not found at {self.config_path}")
            return
            
        with open(self.config_path, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)
            
        sources = config.get("sources", [])
        self.adapters = []
        for src in sources:
            if not src.get("enabled", True):
                continue
            t = src.get("type", "").lower()
            if t == "sftp":
                self.adapters.append(SFTPAdapter(src))
            elif t == "db_poll":
                self.adapters.append(DBPollAdapter(src))
            elif t == "edi_as2":
                self.adapters.append(EDIReceiverAdapter(src))
            elif t == "rest_pull":
                self.adapters.append(RESTPullAdapter(src))

    def log_ingest_event(self, source: str, tenant_id: str, filename: str, status: str, row_count: int, err_msg: str = ""):
        log_entry = {
            "timestamp": datetime.utcnow().isoformat(),
            "source_type": source,
            "tenant_id": tenant_id,
            "filename": filename,
            "status": status,
            "row_count": row_count,
            "error": err_msg
        }
        with open(self.log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(log_entry) + "\n")
        
        if status == "FAILED":
            print(f"[Ingestion Alert] Repeated failure from WMS source '{source}' for tenant '{tenant_id}': {err_msg}")

    def run_ingest(self, rag) -> int:
        """Poll all active WMS adapters, parse data, route to SQLite & LanceDB, and handle DLQ routing."""
        self.load_config()  # Dynamic reload config
        total_ingested = 0
        
        temp_dir = os.path.abspath("./data/temp_intake")
        os.makedirs(temp_dir, exist_ok=True)
        
        for adapter in self.adapters:
            records = adapter.poll()
            for rec in records:
                temp_file = os.path.join(temp_dir, rec.filename)
                try:
                    # Write raw bytes to temporary file for parser router
                    with open(temp_file, 'wb') as f:
                        f.write(rec.data)
                        
                    # Parse through our schema-flexible alias parsing engine
                    parsed_records = parse_file(temp_file)
                    
                    if not parsed_records:
                        raise ValueError("No records parsed from WMS export.")
                        
                    # Batch Ingest into Dual Stores
                    rag.ingest_records(parsed_records, tenant_id=rec.tenant_id)
                    adapter.ack(rec.filename)
                    
                    self.log_ingest_event(
                        source=adapter.source_type(),
                        tenant_id=rec.tenant_id,
                        filename=rec.filename,
                        status="SUCCESS",
                        row_count=len(parsed_records)
                    )
                    total_ingested += len(parsed_records)
                    
                except Exception as e:
                    err_msg = str(e)
                    # DLQ Routing
                    tenant_dlq = os.path.join(self.dlq_dir, rec.tenant_id)
                    os.makedirs(tenant_dlq, exist_ok=True)
                    
                    dlq_src = os.path.join(tenant_dlq, rec.filename)
                    dlq_err = os.path.join(tenant_dlq, f"{rec.filename}.err")
                    
                    # Save raw bytes to DLQ
                    with open(dlq_src, 'wb') as f:
                        f.write(rec.data)
                        
                    # Write failure log
                    with open(dlq_err, 'w', encoding='utf-8') as f:
                        f.write(f"Timestamp: {datetime.utcnow().isoformat()}\n")
                        f.write(f"Source: {adapter.source_type()}\n")
                        f.write(f"Error: {err_msg}\n")
                        
                    self.log_ingest_event(
                        source=adapter.source_type(),
                        tenant_id=rec.tenant_id,
                        filename=rec.filename,
                        status="FAILED",
                        row_count=0,
                        err_msg=err_msg
                    )
                    
                    # Clean/remove file from drop folder if SFTP/EDI to avoid infinite failure looping
                    adapter.ack(rec.filename)
                    
                finally:
                    if os.path.exists(temp_file):
                        os.remove(temp_file)
                        
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir, ignore_errors=True)
            
        return total_ingested
