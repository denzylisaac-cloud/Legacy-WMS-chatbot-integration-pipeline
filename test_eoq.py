import pytest
from src.warehouse_system.app import calculate_eoq

def test_calculate_eoq_happy_path():
    # eoq = sqrt(2 * demand * order_cost / holding_cost)
    # sqrt(2 * 1000 * 50 / 2) = sqrt(50000) = 223.6... -> 224
    result = calculate_eoq(1000, 50, 2)
    assert result == "Calculated EOQ: 224 units"

def test_calculate_eoq_string_inputs():
    # Ensure it works with string representations of floats
    result = calculate_eoq("1000", "50", "2")
    assert result == "Calculated EOQ: 224 units"

def test_calculate_eoq_invalid_string_inputs():
    result = calculate_eoq("abc", 50, 2)
    assert result.startswith("Error: Arguments must be numeric.")
    assert "could not convert string to float" in result

def test_calculate_eoq_zero_holding_cost():
    result = calculate_eoq(1000, 50, 0)
    assert result == "Error: Holding cost must be greater than zero."

def test_calculate_eoq_negative_holding_cost():
    result = calculate_eoq(1000, 50, -1)
    assert result == "Error: Holding cost must be greater than zero."

def test_calculate_eoq_negative_demand():
    result = calculate_eoq(-1000, 50, 2)
    assert result.startswith("Error calculating EOQ:")
    assert "demand_rate must be non-negative" in result
