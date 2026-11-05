import threading
from datetime import datetime, timedelta
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from typing import List, Optional
from pydantic import BaseModel, Field
from mock_data import inventory_items, orders, demand_forecasts, backlog_items, spending_summary, monthly_spending, category_spending, recent_transactions, purchase_orders, submitted_orders

app = FastAPI(title="Factory Inventory Management System")

# Quarter mapping for date filtering
QUARTER_MAP = {
    'Q1-2025': ['2025-01', '2025-02', '2025-03'],
    'Q2-2025': ['2025-04', '2025-05', '2025-06'],
    'Q3-2025': ['2025-07', '2025-08', '2025-09'],
    'Q4-2025': ['2025-10', '2025-11', '2025-12']
}

def filter_by_month(items: list, month: Optional[str]) -> list:
    """Filter items by month/quarter based on order_date field"""
    if not month or month == 'all':
        return items

    if month.startswith('Q'):
        # Handle quarters
        if month in QUARTER_MAP:
            months = QUARTER_MAP[month]
            return [item for item in items if any(m in item.get('order_date', '') for m in months)]
    else:
        # Direct month match
        return [item for item in items if month in item.get('order_date', '')]

    return items

def apply_filters(items: list, warehouse: Optional[str] = None, category: Optional[str] = None,
                 status: Optional[str] = None) -> list:
    """Apply common filters to a list of items"""
    filtered = items

    if warehouse and warehouse != 'all':
        filtered = [item for item in filtered if item.get('warehouse') == warehouse]

    if category and category != 'all':
        filtered = [item for item in filtered if item.get('category', '').lower() == category.lower()]

    if status and status != 'all':
        filtered = [item for item in filtered if item.get('status', '').lower() == status.lower()]

    return filtered

# Supplier lead time in days, by item category
LEAD_TIME_DAYS_BY_CATEGORY = {
    'Filtration': 7,
    'Sensors': 9,
    'Mechanical Parts': 10,
    'Power Supplies': 12,
    'Valves': 14,
    'Controllers': 18,
    'Motors': 21
}
DEFAULT_LEAD_TIME_DAYS = 14

restock_orders_lock = threading.Lock()

# Ranking adjustment for forecast trend, added to the shortfall ratio
TREND_URGENCY_WEIGHT = {'increasing': 0.25, 'stable': 0.0, 'decreasing': -0.25}

def get_lead_time_days(category: Optional[str]) -> int:
    return LEAD_TIME_DAYS_BY_CATEGORY.get(category, DEFAULT_LEAD_TIME_DAYS)

def get_quantity_on_order(restock_orders: list) -> dict:
    """Total units per SKU across submitted restocking orders"""
    on_order = {}
    for order in restock_orders:
        for item in order['items']:
            on_order[item['sku']] = on_order.get(item['sku'], 0) + item['quantity']
    return on_order

def compute_restock_candidates(forecasts: list, restock_orders: list) -> list:
    """Return forecast items that need restocking, most urgent first"""
    on_order_by_sku = get_quantity_on_order(restock_orders)
    candidates = []
    for forecast in forecasts:
        on_hand = forecast.get('quantity_on_hand') or 0
        on_order = on_order_by_sku.get(forecast['item_sku'], 0)
        unit_cost = forecast.get('unit_cost')
        forecasted = forecast['forecasted_demand']
        # Units already ordered count toward covering demand, so placing an
        # order shrinks the next recommendation instead of repeating it
        shortfall = max(0, forecasted - on_hand - on_order)
        # Items without a cost can't be priced against a budget, so skip them
        if shortfall == 0 or unit_cost is None:
            continue

        # Urgency is the share of next period's demand we can't cover, nudged
        # up for rising demand and down for falling demand, so a 40% gap on a
        # growing item outranks a 40% gap on a shrinking one.
        urgency = shortfall / forecasted + TREND_URGENCY_WEIGHT.get(forecast['trend'], 0.0)

        candidates.append({
            'sku': forecast['item_sku'],
            'name': forecast['item_name'],
            'category': forecast.get('category'),
            'trend': forecast['trend'],
            'quantity_on_hand': on_hand,
            'quantity_on_order': on_order,
            'forecasted_demand': forecasted,
            'recommended_quantity': shortfall,
            'unit_cost': unit_cost,
            'estimated_cost': round(shortfall * unit_cost, 2),
            'lead_time_days': get_lead_time_days(forecast.get('category')),
            'urgency': round(urgency, 4)
        })

    candidates.sort(key=lambda c: c['urgency'], reverse=True)
    return candidates

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Data models
class InventoryItem(BaseModel):
    id: str
    sku: str
    name: str
    category: str
    warehouse: str
    quantity_on_hand: int
    reorder_point: int
    unit_cost: float
    location: str
    last_updated: str

class Order(BaseModel):
    id: str
    order_number: str
    customer: str
    items: List[dict]
    status: str
    order_date: str
    expected_delivery: str
    total_value: float
    actual_delivery: Optional[str] = None
    warehouse: Optional[str] = None
    category: Optional[str] = None

class DemandForecast(BaseModel):
    id: str
    item_sku: str
    item_name: str
    current_demand: int
    forecasted_demand: int
    trend: str
    period: str
    quantity_on_hand: Optional[int] = None
    unit_cost: Optional[float] = None
    category: Optional[str] = None

class BacklogItem(BaseModel):
    id: str
    order_id: str
    item_sku: str
    item_name: str
    quantity_needed: int
    quantity_available: int
    days_delayed: int
    priority: str
    has_purchase_order: Optional[bool] = False

class PurchaseOrder(BaseModel):
    id: str
    backlog_item_id: str
    supplier_name: str
    quantity: int
    unit_cost: float
    expected_delivery_date: str
    status: str
    created_date: str
    notes: Optional[str] = None

class CreatePurchaseOrderRequest(BaseModel):
    backlog_item_id: str
    supplier_name: str
    quantity: int
    unit_cost: float
    expected_delivery_date: str
    notes: Optional[str] = None

class RestockCandidate(BaseModel):
    sku: str
    name: str
    category: Optional[str] = None
    trend: str
    quantity_on_hand: int
    quantity_on_order: int
    forecasted_demand: int
    recommended_quantity: int
    unit_cost: float
    estimated_cost: float
    lead_time_days: int
    urgency: float

class RestockOrderItemRequest(BaseModel):
    sku: str
    quantity: int = Field(gt=0)

class RestockOrderRequest(BaseModel):
    items: List[RestockOrderItemRequest] = Field(min_length=1)
    budget: float = Field(ge=0)

class RestockOrderItem(BaseModel):
    sku: str
    name: str
    category: Optional[str] = None
    quantity: int
    unit_cost: float
    line_total: float
    lead_time_days: int

class RestockOrder(BaseModel):
    id: str
    order_number: str
    items: List[RestockOrderItem]
    total_cost: float
    budget: float
    lead_time_days: int
    submitted_at: str
    expected_delivery: str
    status: str

# API endpoints
@app.get("/")
def root():
    return {"message": "Factory Inventory Management System API", "version": "1.0.0"}

@app.get("/api/inventory", response_model=List[InventoryItem])
def get_inventory(
    warehouse: Optional[str] = None,
    category: Optional[str] = None
):
    """Get all inventory items with optional filtering"""
    return apply_filters(inventory_items, warehouse, category)

@app.get("/api/inventory/{item_id}", response_model=InventoryItem)
def get_inventory_item(item_id: str):
    """Get a specific inventory item"""
    item = next((item for item in inventory_items if item["id"] == item_id), None)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    return item

@app.get("/api/orders", response_model=List[Order])
def get_orders(
    warehouse: Optional[str] = None,
    category: Optional[str] = None,
    status: Optional[str] = None,
    month: Optional[str] = None
):
    """Get all orders with optional filtering"""
    filtered_orders = apply_filters(orders, warehouse, category, status)
    filtered_orders = filter_by_month(filtered_orders, month)
    return filtered_orders

@app.get("/api/orders/{order_id}", response_model=Order)
def get_order(order_id: str):
    """Get a specific order"""
    order = next((order for order in orders if order["id"] == order_id), None)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    return order

@app.get("/api/demand", response_model=List[DemandForecast])
def get_demand_forecasts():
    """Get demand forecasts"""
    return demand_forecasts

@app.get("/api/backlog", response_model=List[BacklogItem])
def get_backlog():
    """Get backlog items with purchase order status"""
    # Add has_purchase_order flag to each backlog item
    result = []
    for item in backlog_items:
        item_dict = dict(item)
        # Check if this backlog item has a purchase order
        has_po = any(po["backlog_item_id"] == item["id"] for po in purchase_orders)
        item_dict["has_purchase_order"] = has_po
        result.append(item_dict)
    return result

@app.get("/api/dashboard/summary")
def get_dashboard_summary(
    warehouse: Optional[str] = None,
    category: Optional[str] = None,
    status: Optional[str] = None,
    month: Optional[str] = None
):
    """Get summary statistics for dashboard with optional filtering"""
    # Filter inventory
    filtered_inventory = apply_filters(inventory_items, warehouse, category)

    # Filter orders
    filtered_orders = apply_filters(orders, warehouse, category, status)
    filtered_orders = filter_by_month(filtered_orders, month)

    total_inventory_value = sum(item["quantity_on_hand"] * item["unit_cost"] for item in filtered_inventory)
    low_stock_items = len([item for item in filtered_inventory if item["quantity_on_hand"] <= item["reorder_point"]])
    pending_orders = len([order for order in filtered_orders if order["status"] in ["Processing", "Backordered"]])
    total_backlog_items = len(backlog_items)

    return {
        "total_inventory_value": round(total_inventory_value, 2),
        "low_stock_items": low_stock_items,
        "pending_orders": pending_orders,
        "total_backlog_items": total_backlog_items,
        "total_orders_value": sum(order["total_value"] for order in filtered_orders)
    }

@app.get("/api/spending/summary")
def get_spending_summary():
    """Get spending summary statistics"""
    return spending_summary

@app.get("/api/spending/monthly")
def get_monthly_spending():
    """Get monthly spending breakdown"""
    return monthly_spending

@app.get("/api/spending/categories")
def get_category_spending():
    """Get spending by category"""
    return category_spending

@app.get("/api/spending/transactions")
def get_recent_transactions():
    """Get recent transactions"""
    return recent_transactions

@app.get("/api/restocking/candidates", response_model=List[RestockCandidate])
def get_restock_candidates():
    """Get forecast items with a stock shortfall, most urgent first"""
    return compute_restock_candidates(demand_forecasts, submitted_orders)

@app.get("/api/restocking/orders", response_model=List[RestockOrder])
def get_restock_orders():
    """Get submitted restocking orders, newest first"""
    return list(reversed(submitted_orders))

@app.post("/api/restocking/orders", response_model=RestockOrder, status_code=201)
def create_restock_order(request: RestockOrderRequest):
    """Submit a restocking order for recommended items within a budget"""
    forecast_skus = {f['item_sku'] for f in demand_forecasts}

    skus = [item.sku for item in request.items]
    if len(skus) != len(set(skus)):
        raise HTTPException(status_code=400, detail="Each SKU may appear only once per order")

    # Sync endpoints run in a threadpool; the lock keeps the shortfall check,
    # order numbering and append atomic so concurrent submits can't both pass
    # validation against the same shortfall or reuse an order number
    with restock_orders_lock:
        candidates_by_sku = {
            c['sku']: c for c in compute_restock_candidates(demand_forecasts, submitted_orders)
        }

        line_items = []
        for item in request.items:
            if item.sku not in forecast_skus:
                raise HTTPException(status_code=404, detail=f"Item {item.sku} not found")
            candidate = candidates_by_sku.get(item.sku)
            if not candidate:
                raise HTTPException(status_code=400, detail=f"Item {item.sku} does not need restocking")
            if item.quantity > candidate['recommended_quantity']:
                raise HTTPException(
                    status_code=400,
                    detail=f"Quantity {item.quantity} for {item.sku} exceeds the remaining shortfall of {candidate['recommended_quantity']}"
                )

            # Prices come from server data, never from the client
            unit_cost = candidate['unit_cost']
            line_items.append({
                'sku': item.sku,
                'name': candidate['name'],
                'category': candidate['category'],
                'quantity': item.quantity,
                'unit_cost': unit_cost,
                'line_total': round(item.quantity * unit_cost, 2),
                'lead_time_days': candidate['lead_time_days']
            })

        total_cost = round(sum(line['line_total'] for line in line_items), 2)
        # Half-cent tolerance so a budget equal to the displayed total isn't
        # rejected over float rounding
        if total_cost > request.budget + 0.005:
            raise HTTPException(
                status_code=400,
                detail=f"Order total {total_cost:.2f} exceeds budget {request.budget:.2f}"
            )

        # The order arrives when its slowest item does
        lead_time_days = max(line['lead_time_days'] for line in line_items)
        submitted_at = datetime.now().replace(microsecond=0)
        order_number = len(submitted_orders) + 1

        order = {
            'id': str(order_number),
            'order_number': f"RST-{order_number:04d}",
            'items': line_items,
            'total_cost': total_cost,
            'budget': request.budget,
            'lead_time_days': lead_time_days,
            'submitted_at': submitted_at.isoformat(),
            # Full local datetime like the other order dates; a bare date is
            # parsed as UTC by the browser and can display a day early
            'expected_delivery': (submitted_at + timedelta(days=lead_time_days)).isoformat(),
            'status': 'Submitted'
        }
        submitted_orders.append(order)
        return order

@app.get("/api/reports/quarterly")
def get_quarterly_reports():
    """Get quarterly performance reports"""
    # Calculate quarterly statistics from orders
    quarters = {}

    for order in orders:
        order_date = order.get('order_date', '')
        # Determine quarter
        if '2025-01' in order_date or '2025-02' in order_date or '2025-03' in order_date:
            quarter = 'Q1-2025'
        elif '2025-04' in order_date or '2025-05' in order_date or '2025-06' in order_date:
            quarter = 'Q2-2025'
        elif '2025-07' in order_date or '2025-08' in order_date or '2025-09' in order_date:
            quarter = 'Q3-2025'
        elif '2025-10' in order_date or '2025-11' in order_date or '2025-12' in order_date:
            quarter = 'Q4-2025'
        else:
            continue

        if quarter not in quarters:
            quarters[quarter] = {
                'quarter': quarter,
                'total_orders': 0,
                'total_revenue': 0,
                'delivered_orders': 0,
                'avg_order_value': 0
            }

        quarters[quarter]['total_orders'] += 1
        quarters[quarter]['total_revenue'] += order.get('total_value', 0)
        if order.get('status') == 'Delivered':
            quarters[quarter]['delivered_orders'] += 1

    # Calculate averages and fulfillment rate
    result = []
    for q, data in quarters.items():
        if data['total_orders'] > 0:
            data['avg_order_value'] = round(data['total_revenue'] / data['total_orders'], 2)
            data['fulfillment_rate'] = round((data['delivered_orders'] / data['total_orders']) * 100, 1)
        result.append(data)

    # Sort by quarter
    result.sort(key=lambda x: x['quarter'])
    return result

@app.get("/api/reports/monthly-trends")
def get_monthly_trends():
    """Get month-over-month trends"""
    months = {}

    for order in orders:
        order_date = order.get('order_date', '')
        if not order_date:
            continue

        # Extract month (format: YYYY-MM-DD)
        month = order_date[:7]  # Gets YYYY-MM

        if month not in months:
            months[month] = {
                'month': month,
                'order_count': 0,
                'revenue': 0,
                'delivered_count': 0
            }

        months[month]['order_count'] += 1
        months[month]['revenue'] += order.get('total_value', 0)
        if order.get('status') == 'Delivered':
            months[month]['delivered_count'] += 1

    # Convert to list and sort
    result = list(months.values())
    result.sort(key=lambda x: x['month'])
    return result

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
