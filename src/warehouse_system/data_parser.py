import os
import csv
import json
import re
import xml.etree.ElementTree as ET

def clean_float(val):
    if not val:
        return 0.0
    # Extract numeric part (e.g., "$50.00/order" -> 50.0, "12.0 kg" -> 12.0)
    cleaned = re.sub(r'[^\d\.]', '', str(val))
    try:
        return float(cleaned)
    except ValueError:
        return 0.0

def clean_int(val):
    if not val:
        return 0
    cleaned = re.sub(r'[^\d]', '', str(val))
    try:
        return int(cleaned)
    except ValueError:
        return 0

def standardize_record(raw):
    """Standardize the field types and names of a record using flexible alias mapping and schema validation."""
    # Lowercase all keys in raw dictionary for easy matching
    clean_raw = {str(k).lower().strip(): v for k, v in raw.items()}
    
    # 1. Match SKU
    sku_val = ""
    for k in ["sku", "sku_id", "item_id", "product_id", "id"]:
        if k in clean_raw and clean_raw[k] is not None:
            sku_val = str(clean_raw[k]).strip()
            break
            
    # Identity Field Validation
    if not sku_val:
        raise ValueError("Record validation failed: 'sku' (item_id/product_id) identity field is missing or empty.")
            
    # 2. Match Name
    name_val = ""
    for k in ["name", "item_name", "product_name", "category", "description"]:
        if k in clean_raw and clean_raw[k] is not None:
            name_val = str(clean_raw[k]).strip()
            break
    if not name_val:
        name_val = "Unnamed Item"
            
    # 3. Match Stock
    stock_val = None
    for k in ["stock", "stock_level", "current_stock", "quantity", "qty", "units"]:
        if k in clean_raw and clean_raw[k] is not None:
            stock_val = clean_int(clean_raw[k])
            break
            
    # Identity Field Validation
    if stock_val is None:
        raise ValueError(f"Record validation failed for SKU '{sku_val}': 'stock' level count is missing or empty.")
            
    # 4. Match Safety Stock
    safety_stock_val = 0
    for k in ["safety_stock", "reorder_point", "safety_stock_level", "min_stock"]:
        if k in clean_raw and clean_raw[k] is not None:
            safety_stock_val = clean_int(clean_raw[k])
            break
            
    # 5. Match Daily Demand
    daily_demand_val = 0.0
    for k in ["daily_demand", "daily_usage", "demand", "usage"]:
        if k in clean_raw and clean_raw[k] is not None:
            daily_demand_val = clean_float(clean_raw[k])
            break
            
    # 6. Match Lead Time
    lead_time_val = 0
    for k in ["lead_time", "lead_time_days", "lead_days"]:
        if k in clean_raw and clean_raw[k] is not None:
            lead_time_val = clean_int(clean_raw[k])
            break
            
    # 7. Match Weight
    weight_val = 0.0
    for k in ["weight", "unit_weight", "item_weight"]:
        if k in clean_raw and clean_raw[k] is not None:
            weight_val = clean_float(clean_raw[k])
            break
    # Default weight if missing/empty to make physical constraints check work
    if weight_val == 0.0:
        weight_val = 2.0  # standard default item weight
        
    # 8. Match Volume
    volume_val = 0.0
    for k in ["volume", "unit_volume", "item_volume"]:
        if k in clean_raw and clean_raw[k] is not None:
            volume_val = clean_float(clean_raw[k])
            break
    if volume_val == 0.0:
        volume_val = 0.05  # standard default item volume
        
    # 9. Match Location
    location_val = ""
    for k in ["location", "storage_location", "storage_location_id", "location_id"]:
        if k in clean_raw and clean_raw[k] is not None:
            location_val = str(clean_raw[k]).strip()
            break
    # If zone is in raw data, let's append it
    zone_val = clean_raw.get("zone", "")
    if zone_val and location_val:
        location_val = f"Zone {zone_val}, Position {location_val}"
    elif zone_val:
        location_val = f"Zone {zone_val}"
    if not location_val:
        location_val = "Unknown Location"
        
    # 10. Match Rack Max Weight
    rack_max_weight_val = 0.0
    for k in ["rack_max_weight", "max_weight", "shelf_max_weight"]:
        if k in clean_raw and clean_raw[k] is not None:
            rack_max_weight_val = clean_float(clean_raw[k])
            break
    if rack_max_weight_val == 0.0:
        rack_max_weight_val = 500.0  # default capacity limit
        
    # 11. Match Rack Max Volume
    rack_max_volume_val = 0.0
    for k in ["rack_max_volume", "max_volume", "shelf_max_volume"]:
        if k in clean_raw and clean_raw[k] is not None:
            rack_max_volume_val = clean_float(clean_raw[k])
            break
    if rack_max_volume_val == 0.0:
        rack_max_volume_val = 5.0  # default capacity limit
        
    # 12. Match Order Cost
    order_cost_val = 0.0
    for k in ["order_cost", "ordering_cost", "setup_cost", "handling_cost_per_unit"]:
        if k in clean_raw and clean_raw[k] is not None:
            order_cost_val = clean_float(clean_raw[k])
            break
    if order_cost_val == 0.0:
        order_cost_val = 50.0  # default setup cost
        
    # 13. Match Holding Cost
    holding_cost_val = 0.0
    is_daily_holding = False
    for k in ["holding_cost", "holding_cost_rate", "holding_cost_per_unit_day", "holding_cost_per_unit_year"]:
        if k in clean_raw and clean_raw[k] is not None:
            holding_cost_val = clean_float(clean_raw[k])
            if "day" in k:
                is_daily_holding = True
            break
    if is_daily_holding:
        holding_cost_val = holding_cost_val * 365.0  # convert daily to yearly
    if holding_cost_val == 0.0:
        holding_cost_val = 5.0  # default holding cost
        
    # 14. Match Supplier MOQ
    supplier_moq_val = 0
    for k in ["supplier_moq", "moq", "minimum_order_quantity", "reorder_frequency_days"]:
        if k in clean_raw and clean_raw[k] is not None:
            supplier_moq_val = clean_int(clean_raw[k])
            break
    if supplier_moq_val == 0:
        supplier_moq_val = 10  # default MOQ
        
    return {
        "sku": sku_val,
        "name": name_val,
        "stock": stock_val,
        "safety_stock": safety_stock_val,
        "daily_demand": daily_demand_val,
        "lead_time": lead_time_val,
        "weight": weight_val,
        "volume": volume_val,
        "location": location_val,
        "rack_max_weight": rack_max_weight_val,
        "rack_max_volume": rack_max_volume_val,
        "order_cost": order_cost_val,
        "holding_cost": holding_cost_val,
        "supplier_moq": supplier_moq_val,
        "schema_version": "schema_v1"
    }

def parse_json(file_path):
    with open(file_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    if isinstance(data, list):
        return [standardize_record(item) for item in data]
    elif isinstance(data, dict):
        return [standardize_record(data)]
    return []

def parse_csv(file_path):
    records = []
    with open(file_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            records.append(standardize_record(row))
    return records

def parse_xml(file_path):
    records = []
    tree = ET.parse(file_path)
    root = tree.getroot()
    # Support both <catalog><sku_item>... and <catalog><item>...
    for item in root.findall('.//sku_item') + root.findall('.//item'):
        raw = {}
        for child in item:
            raw[child.tag] = child.text
        records.append(standardize_record(raw))
    return records

def parse_edi(file_path):
    """
    Parses a simplified ANSI X12-inspired segment structure:
    Segments split by '~', elements split by '*'.
    Example:
    SKU*SKU-8825*Super Industrial Bracket~
    QTY*stock*50*safety_stock*15*daily_demand*8.0*lead_time*5~
    PHY*weight*15.0*volume*0.12*location*Zone B, Rack 4, Shelf 6~
    CST*order_cost*60.0*holding_cost*10.0*supplier_moq*25~
    ST*rack_limit*250.0*2.0~
    """
    records = []
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    segments = [s.strip() for s in content.split('~') if s.strip()]
    current_record = {}
    
    for segment in segments:
        elements = [e.strip() for e in segment.split('*') if e.strip()]
        if not elements:
            continue
        
        tag = elements[0]
        
        if tag == "SKU":
            if current_record:
                records.append(standardize_record(current_record))
            current_record = {
                "sku": elements[1],
                "name": elements[2] if len(elements) > 2 else ""
            }
        elif tag == "QTY":
            # elements: QTY*stock*50*safety_stock*15*daily_demand*8.0*lead_time*5
            for i in range(1, len(elements) - 1, 2):
                key = elements[i]
                val = elements[i+1]
                current_record[key] = val
        elif tag == "PHY":
            # elements: PHY*weight*15.0*volume*0.12*location*Zone B, Rack 4, Shelf 6
            for i in range(1, len(elements) - 1, 2):
                key = elements[i]
                val = elements[i+1]
                current_record[key] = val
        elif tag == "CST":
            # elements: CST*order_cost*60.0*holding_cost*10.0*supplier_moq*25
            for i in range(1, len(elements) - 1, 2):
                key = elements[i]
                val = elements[i+1]
                current_record[key] = val
        elif tag == "ST":
            # elements: ST*rack_limit*250.0*2.0
            if len(elements) >= 4 and elements[1] == "rack_limit":
                current_record["rack_max_weight"] = elements[2]
                current_record["rack_max_volume"] = elements[3]
                
    if current_record:
        records.append(standardize_record(current_record))
        
    return records

def parse_markdown(file_path):
    """
    Parses catalog items structured by markdown headings.
    Looking for patterns:
    ## SKU-[SKU_ID]: [Name]
    - **Product Name**: ...
    - **Current Stock**: ...
    """
    records = []
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()
        
    # Split by heading level 2 (##)
    sections = re.split(r'^##\s+', content, flags=re.MULTILINE)
    
    for section in sections:
        if not section.strip():
            continue
        
        lines = section.split('\n')
        header = lines[0].strip()
        
        # Check if header matches SKU pattern (e.g. SKU-8821: Heavy Industrial Bracket)
        match = re.match(r'^(SKU-\d+)\s*:\s*(.*)', header, re.IGNORECASE)
        if not match:
            continue
            
        sku = match.group(1).strip()
        name = match.group(2).strip()
        
        record = {"sku": sku, "name": name}
        
        for line in lines[1:]:
            line = line.strip()
            if not line.startswith('- '):
                continue
            
            # Match bullet points like: - **Current Stock**: 8 units
            bullet_match = re.match(r'^-\s*\*\*(.*?)\*\*:\s*(.*)', line)
            if not bullet_match:
                continue
                
            label = bullet_match.group(1).strip().lower()
            val = bullet_match.group(2).strip()
            
            if "product name" in label:
                record["name"] = val
            elif "current stock" in label:
                record["stock"] = val
            elif "safety stock" in label:
                record["safety_stock"] = val
            elif "daily demand" in label or "usage" in label:
                record["daily_demand"] = val
            elif "lead time" in label:
                record["lead_time"] = val
            elif "weight" in label:
                record["weight"] = val
            elif "dimensions" in label or "volume" in label:
                # Look for volume: e.g. "(Volume: 0.1 m³)"
                vol_match = re.search(r'volume:\s*([\d\.]+)', val, re.IGNORECASE)
                if vol_match:
                    record["volume"] = vol_match.group(1)
            elif "location" in label:
                record["location"] = val
            elif "constraints" in label or "rack" in label:
                # Extract weight limit: Max Shelf Weight: 250.0 kg
                w_limit = re.search(r'weight:\s*([\d\.]+)', val, re.IGNORECASE)
                v_limit = re.search(r'volume:\s*([\d\.]+)', val, re.IGNORECASE)
                if w_limit:
                    record["rack_max_weight"] = w_limit.group(1)
                if v_limit:
                    record["rack_max_volume"] = v_limit.group(1)
            elif "cost" in label:
                # Order Cost: $50.00/order, Annual Holding Cost: $8.00/unit/year
                oc_match = re.search(r'order cost:\s*\$?([\d\.]+)', val, re.IGNORECASE)
                hc_match = re.search(r'holding cost:\s*\$?([\d\.]+)', val, re.IGNORECASE)
                if oc_match:
                    record["order_cost"] = oc_match.group(1)
                if hc_match:
                    record["holding_cost"] = hc_match.group(1)
            elif "moq" in label:
                record["supplier_moq"] = val
                
        records.append(standardize_record(record))
        
    return records

def parse_file(file_path):
    """Router that detects extension and returns list of standardized dicts."""
    _, ext = os.path.splitext(file_path)
    ext = ext.lower()
    
    if ext == '.json':
        return parse_json(file_path)
    elif ext == '.csv':
        return parse_csv(file_path)
    elif ext in ('.xml', '.xhtml'):
        return parse_xml(file_path)
    elif ext == '.edi':
        return parse_edi(file_path)
    elif ext in ('.md', '.markdown'):
        return parse_markdown(file_path)
    else:
        raise ValueError(f"Unsupported file format: {ext}")
