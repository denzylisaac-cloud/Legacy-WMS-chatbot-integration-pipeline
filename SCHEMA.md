# Canonical Warehouse Inventory Schema (schema_v1)

This document describes the canonical database schema for the Warehouse Management System Chatbot. Every incoming record processed by the ingestion adapters is mapped to this schema inside `data_parser.py`.

---

## 1. Schema Specifications

Any record must comply with these target field names and types:

| Field Name | Type | Description | Required? | Defaults |
| :--- | :--- | :--- | :--- | :--- |
| `sku` | `TEXT` | Unique identifier for the product (e.g. `SKU-8821`, `ITM10000`) | **YES** | *None (Throws validation error)* |
| `name` | `TEXT` | Human-readable name or category description | No | `"Unnamed Item"` |
| `stock` | `INTEGER` | Current quantity on hand | **YES** | *None (Throws validation error if missing)* |
| `safety_stock` | `INTEGER` | Safety threshold quantity | No | `0` |
| `daily_demand` | `REAL` | Average daily units consumed/ordered | No | `0.0` |
| `lead_time` | `INTEGER` | Supplier lead time in days | No | `0` |
| `weight` | `REAL` | Unit weight in kilograms (kg) | No | `2.0` (standard unit default) |
| `volume` | `REAL` | Unit volume in cubic meters (m³) | No | `0.05` (standard unit default) |
| `location` | `TEXT` | Designated shelf storage location | No | `"Unknown Location"` |
| `rack_max_weight` | `REAL` | Maximum shelf weight limit in kg | No | `500.0` (standard limit default) |
| `rack_max_volume` | `REAL` | Maximum shelf volume limit in m³ | No | `5.0` (standard limit default) |
| `order_cost` | `REAL` | Setup/ordering cost per order in dollars | No | `50.0` |
| `holding_cost` | `REAL` | Annual holding cost per unit in dollars | No | `5.0` |
| `supplier_moq` | `INTEGER` | Supplier Minimum Order Quantity in units | No | `10` |

---

## 2. Ingest Mapping Rules (Header Aliases)

The parser maps input records case-insensitively using these aliases:

*   **`sku`**: `sku`, `sku_id`, `item_id`, `product_id`, `id`
*   **`name`**: `name`, `item_name`, `product_name`, `category`, `description`
*   **`stock`**: `stock`, `stock_level`, `current_stock`, `quantity`, `qty`, `units`
*   **`safety_stock`**: `safety_stock`, `reorder_point`, `safety_stock_level`, `min_stock`
*   **`daily_demand`**: `daily_demand`, `daily_usage`, `demand`, `usage`
*   **`lead_time`**: `lead_time`, `lead_time_days`, `lead_days`
*   **`weight`**: `weight`, `unit_weight`, `item_weight`
*   **`volume`**: `volume`, `unit_volume`, `item_volume`
*   **`location`**: `location`, `storage_location`, `storage_location_id`, `location_id` *(If `zone` is present, it is combined: `Zone {zone}, Position {location}`)*
*   **`order_cost`**: `order_cost`, `ordering_cost`, `setup_cost`, `handling_cost_per_unit`
*   **`holding_cost`**: `holding_cost`, `holding_cost_rate`, `holding_cost_per_unit_day` (converted to annual), `holding_cost_per_unit_year`
*   **`supplier_moq`**: `supplier_moq`, `moq`, `minimum_order_quantity`, `reorder_frequency_days`

---

## 3. Validation and Filtering

1.  **Identity Enforcement**: If `sku` or `stock` is missing or resolves to an empty value, the parser throws a validation exception. The record is routed to the **Dead Letter Queue (DLQ)**.
2.  **Deduplication/Upserts**: All records are upserted into SQLite and LanceDB using the composite primary key `(sku, tenant_id)`.
