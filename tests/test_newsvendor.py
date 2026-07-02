import pytest
from unittest import mock
from src.warehouse_system.app import calculate_newsvendor_quantity

def test_calculate_newsvendor_quantity_happy_path():
    result = calculate_newsvendor_quantity(
        demand_mean=100.0,
        demand_sd=20.0,
        unit_holding_cost=10.0,
        stockout_cost=90.0,
        lead_time=0
    )
    assert "Calculated Newsvendor Base Stock Level: 126 units" in result
    assert "Expected Period Cost: $351.0" in result
    assert "Target Service Level (Critical Ratio): 90.00%" in result

def test_calculate_newsvendor_quantity_invalid_arguments():
    result = calculate_newsvendor_quantity(
        demand_mean="not_a_number",
        demand_sd=20.0,
        unit_holding_cost=10.0,
        stockout_cost=90.0,
        lead_time=0
    )
    assert "Error: Arguments must be numeric" in result

def test_calculate_newsvendor_quantity_execution_error():
    with mock.patch("stockpyl.newsvendor.newsvendor_normal", side_effect=Exception("Mocked error")):
        result = calculate_newsvendor_quantity(
            demand_mean=100.0,
            demand_sd=20.0,
            unit_holding_cost=10.0,
            stockout_cost=90.0,
            lead_time=0
        )
        assert "Error executing Newsvendor calculation: Mocked error" in result
