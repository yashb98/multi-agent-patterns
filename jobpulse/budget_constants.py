"""Budget constants and period utilities — shared between budget_agent and budget_tracker.

Extracted to break the circular dependency where budget_tracker.py lazy-imports
constants and period functions from budget_agent.py, and budget_agent.py lazy-imports
Notion sync functions from budget_tracker.py.
"""

from datetime import datetime, timedelta
from pathlib import Path

from jobpulse.config import DATA_DIR

# ── Notion Budget Period Sheet ──
BUDGET_PAGE_ID = "50f750e4-9369-4f5e-91e4-f1680e7192fd"

TABLE_IDS = {
    "income": "c5d42e98-fdbe-4ded-9628-fcf0d974707b",
    "fixed": "e73bef32-25a6-4331-ad5f-6f4a101e426b",
    "variable": "0d77a285-e62b-4190-9e5e-7e39bb025c3f",
    "savings": "52a95f38-273f-4cb1-a586-71a359c80912",
    "summary": "04eef488-e09a-4cbe-9177-44252f4c06de",
}

ROW_IDS = {
    # Income
    "Salary": "3fba291a-3edf-498c-b5fc-c2445db935c9",
    "Freelance": "a20ee3b5-861d-478d-b01a-1583bc99bab1",
    "Other": "15ca2782-800c-4fef-915c-088ec71e1a2a",
    "Total income": "0d6e9a9e-09d1-454f-a90a-25f3c39bf1b6",
    # Fixed expenses
    "Rent / Mortgage": "d0c830bc-2e09-4d3e-8ea6-8c9bee0d111f",
    "Utilities": "752a0174-e663-42b2-b0c2-401476892ec5",
    "Phone / Internet": "6dd0cce5-7b12-41b3-8c45-b26dcc34f3fe",
    "Subscriptions": "1983caf4-69cd-4a21-9d3f-eb36f1a9cbd4",
    "Insurance": "b1e1ee11-03d9-4ba9-bf2c-8c85f9d06a5e",
    "Total fixed": "345049f7-ba91-4bef-ad79-86ff1420524f",
    # Variable spending
    "Groceries": "abd561c5-6453-49b3-9690-41e1ad6b2f53",
    "Eating out": "908e9fc8-464e-4b19-8393-84f4597beab3",
    "Transport": "69be65a1-0a68-4bb1-95f2-35d73341876d",
    "Shopping": "1b430b3a-992e-4591-a61e-c6aee7ed69f2",
    "Entertainment": "b5c87445-8f5b-4526-bd57-d32f341be02c",
    "Health": "ec7677c1-d00c-4e04-b53a-2aa49ec4a732",
    "Misc": "7ed615cc-98c7-4f0f-a0bf-7c76905a7a09",
    "Total variable": "719e132c-755c-43a4-a7cf-ec8a3534e037",
    # Savings + debt
    "Savings": "5486409b-07dc-47cc-9250-a25ae0552c31",
    "Investments": "21d0a510-24c8-4084-b532-423b7993911d",
    "Credit card / Loan payment": "0f318b42-3fbf-4141-b63b-bbefd2bc1ecf",
    "Total savings + debt": "9dd850ee-6c38-42dd-8b87-82dd59b17310",
    # Summary
    "Total income (summary)": "7a71026f-daca-4387-b34e-1386062a7505",
    "Total spending (fixed + variable)": "9c4df293-1f90-43d8-8724-98b8a9875a7d",
    "Total savings + debt (summary)": "36aec6cb-ac1e-48da-9b78-779007192a9b",
    "Net (income - spending - savings/debt)": "8867602a-9249-49ad-b316-f03321875c02",
}

DB_PATH = DATA_DIR / "budget.db"

# ── Categories matching the Notion sheet exactly ──

INCOME_CATEGORIES = {
    "salary": "Salary",
    "wage": "Salary",
    "pay": "Salary",
    "paycheck": "Salary",
    "freelance": "Freelance",
    "contract": "Freelance",
    "gig": "Freelance",
    "side hustle": "Freelance",
    "other income": "Other",
    "refund": "Other",
    "gift received": "Other",
    "cashback": "Other",
}

FIXED_EXPENSE_CATEGORIES = {
    "rent": "Rent / Mortgage",
    "mortgage": "Rent / Mortgage",
    "utilities": "Utilities",
    "electricity": "Utilities",
    "water": "Utilities",
    "gas bill": "Utilities",
    "phone": "Phone / Internet",
    "internet": "Phone / Internet",
    "broadband": "Phone / Internet",
    "mobile": "Phone / Internet",
    "subscription": "Subscriptions",
    "netflix": "Subscriptions",
    "spotify": "Subscriptions",
    "gym membership": "Subscriptions",
    "apple music": "Subscriptions",
    "apple tv": "Subscriptions",
    "apple one": "Subscriptions",
    "icloud": "Subscriptions",
    "amazon prime": "Subscriptions",
    "insurance": "Insurance",
    "car insurance": "Insurance",
    "health insurance": "Insurance",
}

VARIABLE_EXPENSE_CATEGORIES = {
    "groceries": "Groceries",
    "supermarket": "Groceries",
    "tesco": "Groceries",
    "aldi": "Groceries",
    "lidl": "Groceries",
    "sainsbury": "Groceries",
    "eating out": "Eating out",
    "restaurant": "Eating out",
    "takeaway": "Eating out",
    "coffee": "Eating out",
    "lunch": "Eating out",
    "dinner": "Eating out",
    "breakfast": "Eating out",
    "food": "Eating out",
    "uber eats": "Eating out",
    "deliveroo": "Eating out",
    "transport": "Transport",
    "uber": "Transport",
    "taxi": "Transport",
    "bus": "Transport",
    "train": "Transport",
    "fuel": "Transport",
    "petrol": "Transport",
    "parking": "Transport",
    "oyster": "Transport",
    "shopping": "Shopping",
    "clothes": "Shopping",
    "amazon": "Shopping",
    "electronics": "Shopping",
    "shoes": "Shopping",
    "entertainment": "Entertainment",
    "cinema": "Entertainment",
    "movie": "Entertainment",
    "game": "Entertainment",
    "concert": "Entertainment",
    "drinks": "Entertainment",
    "pub": "Entertainment",
    "bar": "Entertainment",
    "health": "Health",
    "pharmacy": "Health",
    "doctor": "Health",
    "dentist": "Health",
    "medicine": "Health",
    "gym": "Health",
    "haircut": "Health",
    "barber": "Health",
    "misc": "Misc",
}

SAVINGS_CATEGORIES = {
    "savings": "Savings",
    "save": "Savings",
    "emergency fund": "Savings",
    "investment": "Investments",
    "invest": "Investments",
    "stocks": "Investments",
    "crypto": "Investments",
    "isa": "Investments",
    "pension": "Investments",
    "credit card": "Credit card / Loan payment",
    "loan": "Credit card / Loan payment",
    "debt": "Credit card / Loan payment",
    "repayment": "Credit card / Loan payment",
}

# Flat lookup: category_name -> (section, display_name)
ALL_CATEGORIES: dict[str, tuple[str, str]] = {}
for _k, _v in INCOME_CATEGORIES.items():
    ALL_CATEGORIES[_k] = ("income", _v)
for _k, _v in FIXED_EXPENSE_CATEGORIES.items():
    ALL_CATEGORIES[_k] = ("fixed", _v)
for _k, _v in VARIABLE_EXPENSE_CATEGORIES.items():
    ALL_CATEGORIES[_k] = ("variable", _v)
for _k, _v in SAVINGS_CATEGORIES.items():
    ALL_CATEGORIES[_k] = ("savings", _v)


# ── Period Functions ──

PERIOD_DAYS = 28
PERIOD_ANCHOR = datetime(2026, 4, 2)  # First period starts April 2, 2026


def get_period_start(date: datetime = None) -> str:
    """Return the start of the 28-day budget period containing *date*.

    Periods are anchored to 2026-04-02 (salary cycle start) and repeat
    every 28 days forward and backward from that anchor.
    """
    date = date or datetime.now()
    d = datetime(date.year, date.month, date.day)
    delta_days = (d - PERIOD_ANCHOR).days
    period_num = delta_days // PERIOD_DAYS
    start = PERIOD_ANCHOR + timedelta(days=period_num * PERIOD_DAYS)
    return start.strftime("%Y-%m-%d")


def get_period_end(period_start: str) -> str:
    """Return the last day of the 28-day period (inclusive)."""
    start = datetime.strptime(period_start, "%Y-%m-%d")
    return (start + timedelta(days=PERIOD_DAYS - 1)).strftime("%Y-%m-%d")
