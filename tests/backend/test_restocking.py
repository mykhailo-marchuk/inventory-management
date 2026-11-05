"""
Tests for restocking API endpoints.
"""
from datetime import datetime

import pytest

import mock_data


@pytest.fixture(autouse=True)
def reset_submitted_orders():
    """Submitted orders live in a module-level list, so clear it around each test."""
    mock_data.submitted_orders.clear()
    yield
    mock_data.submitted_orders.clear()


class TestRestockCandidates:
    """Test suite for the restock candidates endpoint."""

    def test_get_restock_candidates(self, client):
        """Test getting restock candidates."""
        response = client.get("/api/restocking/candidates")
        assert response.status_code == 200

        data = response.json()
        assert isinstance(data, list)
        assert len(data) > 0

        first = data[0]
        for field in [
            "sku", "name", "category", "trend", "quantity_on_hand",
            "quantity_on_order", "forecasted_demand", "recommended_quantity", "unit_cost",
            "estimated_cost", "lead_time_days", "urgency"
        ]:
            assert field in first

    def test_restock_candidates_sorted_by_urgency(self, client):
        """Test that candidates come back most urgent first."""
        data = client.get("/api/restocking/candidates").json()
        urgencies = [c["urgency"] for c in data]
        assert urgencies == sorted(urgencies, reverse=True)

    def test_restock_candidates_quantity_calculation(self, client):
        """Test that recommended quantity is the forecast shortfall and cost matches."""
        data = client.get("/api/restocking/candidates").json()

        for candidate in data:
            shortfall = (
                candidate["forecasted_demand"]
                - candidate["quantity_on_hand"]
                - candidate["quantity_on_order"]
            )
            assert candidate["recommended_quantity"] == shortfall
            assert candidate["recommended_quantity"] > 0
            expected_cost = candidate["recommended_quantity"] * candidate["unit_cost"]
            assert abs(candidate["estimated_cost"] - expected_cost) < 0.01

    def test_restock_candidates_exclude_items_without_shortfall(self, client):
        """Test that forecast items with enough stock are not recommended."""
        forecasts = client.get("/api/demand").json()
        candidates = client.get("/api/restocking/candidates").json()
        candidate_skus = {c["sku"] for c in candidates}

        covered = [
            f["item_sku"] for f in forecasts
            if f["quantity_on_hand"] >= f["forecasted_demand"]
        ]
        assert len(covered) > 0
        for sku in covered:
            assert sku not in candidate_skus

    def test_restock_candidates_lead_time_by_category(self, client):
        """Test that each candidate's lead time comes from its category."""
        from main import LEAD_TIME_DAYS_BY_CATEGORY

        data = client.get("/api/restocking/candidates").json()
        for candidate in data:
            assert candidate["lead_time_days"] == LEAD_TIME_DAYS_BY_CATEGORY[candidate["category"]]

    def test_demand_forecasts_include_restock_fields(self, client):
        """Test that /api/demand still validates and exposes the new fields."""
        response = client.get("/api/demand")
        assert response.status_code == 200

        for forecast in response.json():
            assert isinstance(forecast["quantity_on_hand"], int)
            assert isinstance(forecast["unit_cost"], (int, float))
            assert isinstance(forecast["category"], str)


class TestRestockOrders:
    """Test suite for submitting and listing restocking orders."""

    def test_get_restock_orders_empty(self, client):
        """Test that no orders are listed before any are submitted."""
        response = client.get("/api/restocking/orders")
        assert response.status_code == 200
        assert response.json() == []

    def test_create_restock_order(self, client):
        """Test submitting a restocking order within budget."""
        payload = {
            "items": [
                {"sku": "WDG-001", "quantity": 10},
                {"sku": "CTL-330", "quantity": 2}
            ],
            "budget": 500
        }
        response = client.post("/api/restocking/orders", json=payload)
        assert response.status_code == 201

        order = response.json()
        assert order["order_number"] == "RST-0001"
        assert order["status"] == "Submitted"
        assert order["budget"] == 500
        assert len(order["items"]) == 2

    def test_create_restock_order_total_calculation(self, client):
        """Test that the total is computed from server-side unit costs."""
        candidates = {c["sku"]: c for c in client.get("/api/restocking/candidates").json()}
        payload = {
            "items": [
                {"sku": "WDG-001", "quantity": 10},
                {"sku": "CTL-330", "quantity": 2}
            ],
            "budget": 1000
        }
        order = client.post("/api/restocking/orders", json=payload).json()

        expected_total = 10 * candidates["WDG-001"]["unit_cost"] + 2 * candidates["CTL-330"]["unit_cost"]
        assert abs(order["total_cost"] - expected_total) < 0.01
        for item in order["items"]:
            assert abs(item["line_total"] - item["quantity"] * item["unit_cost"]) < 0.01

    def test_create_restock_order_lead_time_is_slowest_item(self, client):
        """Test that the order lead time is the longest item lead time."""
        payload = {
            "items": [
                {"sku": "FLT-405", "quantity": 5},   # Filtration
                {"sku": "CTL-330", "quantity": 1}    # Controllers
            ],
            "budget": 1000
        }
        order = client.post("/api/restocking/orders", json=payload).json()

        item_lead_times = [item["lead_time_days"] for item in order["items"]]
        assert order["lead_time_days"] == max(item_lead_times)

        submitted = datetime.fromisoformat(order["submitted_at"]).date()
        expected = datetime.fromisoformat(order["expected_delivery"]).date()
        assert (expected - submitted).days == order["lead_time_days"]

    def test_get_restock_orders_after_create(self, client):
        """Test that submitted orders are listed newest first."""
        item = {"sku": "GSK-203", "quantity": 1}
        client.post("/api/restocking/orders", json={"items": [item], "budget": 100})
        client.post("/api/restocking/orders", json={"items": [item], "budget": 100})

        data = client.get("/api/restocking/orders").json()
        assert [o["order_number"] for o in data] == ["RST-0002", "RST-0001"]

    def test_create_restock_order_over_budget(self, client):
        """Test that an order costing more than the budget is rejected."""
        payload = {"items": [{"sku": "CTL-330", "quantity": 10}], "budget": 100}
        response = client.post("/api/restocking/orders", json=payload)
        assert response.status_code == 400
        assert "exceeds budget" in response.json()["detail"]
        assert client.get("/api/restocking/orders").json() == []

    def test_create_restock_order_unknown_sku(self, client):
        """Test that an unknown SKU returns 404."""
        payload = {"items": [{"sku": "NOPE-999", "quantity": 1}], "budget": 100}
        response = client.post("/api/restocking/orders", json=payload)
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    def test_create_restock_order_duplicate_sku(self, client):
        """Test that the same SKU twice in one order is rejected."""
        payload = {
            "items": [{"sku": "GSK-203", "quantity": 1}, {"sku": "GSK-203", "quantity": 2}],
            "budget": 100
        }
        response = client.post("/api/restocking/orders", json=payload)
        assert response.status_code == 400

    @pytest.mark.parametrize("payload", [
        {"items": [], "budget": 100},
        {"items": [{"sku": "GSK-203", "quantity": 0}], "budget": 100},
        {"items": [{"sku": "GSK-203", "quantity": -3}], "budget": 100},
        {"items": [{"sku": "GSK-203", "quantity": 1}], "budget": -1},
        {"items": [{"sku": "GSK-203", "quantity": 1}]},
    ])
    def test_create_restock_order_invalid_input(self, client, payload):
        """Test that invalid payloads fail validation."""
        response = client.post("/api/restocking/orders", json=payload)
        assert response.status_code == 422

    def test_restock_orders_do_not_affect_customer_orders(self, client):
        """Test that restocking orders stay out of the customer orders list and dashboard."""
        orders_before = len(client.get("/api/orders").json())
        summary_before = client.get("/api/dashboard/summary").json()

        client.post("/api/restocking/orders", json={
            "items": [{"sku": "GSK-203", "quantity": 1}], "budget": 100
        })

        assert len(client.get("/api/orders").json()) == orders_before
        assert client.get("/api/dashboard/summary").json() == summary_before

    def test_create_restock_order_at_exact_budget(self, client):
        """Test that a budget equal to the order total is accepted."""
        payload = {"items": [{"sku": "FLT-405", "quantity": 3}], "budget": 24.6}
        response = client.post("/api/restocking/orders", json=payload)
        assert response.status_code == 201
        assert abs(response.json()["total_cost"] - 24.6) < 0.01

    def test_create_restock_order_item_without_shortfall(self, client):
        """Test that ordering a forecast item that has enough stock is rejected."""
        payload = {"items": [{"sku": "MTR-304", "quantity": 1}], "budget": 10000}
        response = client.post("/api/restocking/orders", json=payload)
        assert response.status_code == 400
        assert "does not need restocking" in response.json()["detail"]

    def test_create_restock_order_quantity_over_shortfall(self, client):
        """Test that ordering more than the shortfall is rejected."""
        candidates = {c["sku"]: c for c in client.get("/api/restocking/candidates").json()}
        quantity = candidates["GSK-203"]["recommended_quantity"] + 1
        payload = {"items": [{"sku": "GSK-203", "quantity": quantity}], "budget": 100000}
        response = client.post("/api/restocking/orders", json=payload)
        assert response.status_code == 400
        assert "exceeds the remaining shortfall" in response.json()["detail"]

    def test_submitted_orders_reduce_recommendations(self, client):
        """Test that ordered units count toward the shortfall."""
        before = {c["sku"]: c for c in client.get("/api/restocking/candidates").json()}
        client.post("/api/restocking/orders", json={
            "items": [{"sku": "GSK-203", "quantity": 30}], "budget": 1000
        })
        after = {c["sku"]: c for c in client.get("/api/restocking/candidates").json()}

        assert after["GSK-203"]["quantity_on_order"] == 30
        assert after["GSK-203"]["recommended_quantity"] == before["GSK-203"]["recommended_quantity"] - 30

    def test_fully_ordered_item_drops_out_of_candidates(self, client):
        """Test that ordering an item's whole shortfall removes it from recommendations."""
        candidate = next(c for c in client.get("/api/restocking/candidates").json() if c["sku"] == "FLT-405")
        client.post("/api/restocking/orders", json={
            "items": [{"sku": "FLT-405", "quantity": candidate["recommended_quantity"]}],
            "budget": candidate["estimated_cost"]
        })
        skus = {c["sku"] for c in client.get("/api/restocking/candidates").json()}
        assert "FLT-405" not in skus
