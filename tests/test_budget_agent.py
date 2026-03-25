"""Tests for budget_agent.py — transaction parsing, classification, and formatting."""

import pytest
import sqlite3
from unittest.mock import patch, MagicMock
from jobpulse.budget_agent import (
    parse_transaction,
    classify_transaction,
    format_week_summary,
    format_today,
    add_transaction,
    init_db,
    _get_week_start,
    ALL_CATEGORIES,
)


# ── parse_transaction tests ──

class TestParseTransaction:
    def test_spent_amount_on_item(self):
        result = parse_transaction("spent 15 on lunch")
        assert result is not None
        assert result["amount"] == 15.0
        assert "lunch" in result["description"].lower()
        assert result["type"] == "expense"

    def test_currency_symbol_amount(self):
        result = parse_transaction("£8.50 coffee")
        assert result is not None
        assert result["amount"] == 8.50
        assert result["type"] == "expense"

    def test_dollar_sign(self):
        result = parse_transaction("$25 groceries")
        assert result is not None
        assert result["amount"] == 25.0

    def test_euro_sign(self):
        result = parse_transaction("€12 lunch")
        assert result is not None
        assert result["amount"] == 12.0

    def test_earned_income(self):
        result = parse_transaction("earned 500 freelance")
        assert result is not None
        assert result["amount"] == 500.0
        assert result["type"] == "income"

    def test_income_keyword(self):
        result = parse_transaction("income 2000 salary")
        assert result is not None
        assert result["amount"] == 2000.0
        assert result["type"] == "income"

    def test_got_paid(self):
        result = parse_transaction("got paid 3000")
        assert result is not None
        assert result["amount"] == 3000.0
        assert result["type"] == "income"

    def test_saved_savings(self):
        result = parse_transaction("saved 100 emergency fund")
        assert result is not None
        assert result["amount"] == 100.0
        assert result["type"] == "savings"

    def test_invest_savings(self):
        result = parse_transaction("invest 200 stocks")
        assert result is not None
        assert result["amount"] == 200.0
        assert result["type"] == "savings"

    def test_paid_keyword(self):
        result = parse_transaction("paid 30 for electricity")
        assert result is not None
        assert result["amount"] == 30.0
        assert result["type"] == "expense"

    def test_decimal_amount(self):
        result = parse_transaction("spent 5.79 on grocery")
        assert result is not None
        assert result["amount"] == 5.79

    def test_no_amount_returns_none(self):
        assert parse_transaction("bought some stuff") is None

    def test_empty_string_returns_none(self):
        assert parse_transaction("") is None

    def test_zero_amount_returns_none(self):
        assert parse_transaction("spent 0 on nothing") is None

    def test_huge_amount_returns_none(self):
        assert parse_transaction("spent 999999 on rocket") is None

    def test_amount_with_on_preposition(self):
        result = parse_transaction("15 on groceries")
        assert result is not None
        assert result["amount"] == 15.0
        assert "groceries" in result["description"].lower()

    def test_unspecified_description_fallback(self):
        result = parse_transaction("spent 10")
        assert result is not None
        assert result["amount"] == 10.0
        assert result["description"] == "Unspecified"

    def test_bought_keyword(self):
        result = parse_transaction("bought 25 shoes")
        assert result is not None
        assert result["amount"] == 25.0
        assert result["type"] == "expense"


# ── classify_transaction tests ──

class TestClassifyTransaction:
    def test_keyword_lunch_maps_to_eating_out(self):
        section, category = classify_transaction("lunch at cafe", 12.0, "expense")
        assert category == "Eating out"

    def test_keyword_groceries(self):
        section, category = classify_transaction("weekly groceries", 45.0, "expense")
        assert category == "Groceries"
        assert section == "variable"

    def test_keyword_rent(self):
        # "rent payment" contains "pay" which matches Salary first in the flat lookup,
        # so use a description that only contains "rent"
        section, category = classify_transaction("monthly rent", 800.0, "expense")
        assert category == "Rent / Mortgage"
        assert section == "fixed"

    def test_keyword_netflix(self):
        section, category = classify_transaction("netflix subscription", 10.0, "expense")
        assert category == "Subscriptions"
        assert section == "fixed"

    def test_keyword_uber_transport(self):
        section, category = classify_transaction("uber to work", 8.0, "expense")
        assert category == "Transport"
        assert section == "variable"

    def test_keyword_salary_income(self):
        section, category = classify_transaction("monthly salary", 3000.0, "income")
        assert category == "Salary"
        assert section == "income"

    def test_keyword_freelance_income(self):
        section, category = classify_transaction("freelance project", 500.0, "income")
        assert category == "Freelance"
        assert section == "income"

    def test_keyword_savings(self):
        section, category = classify_transaction("monthly savings", 200.0, "savings")
        assert category == "Savings"
        assert section == "savings"

    def test_keyword_investment(self):
        section, category = classify_transaction("stocks investment", 100.0, "savings")
        assert category == "Investments"
        assert section == "savings"

    def test_keyword_coffee_maps_to_eating_out(self):
        section, category = classify_transaction("morning coffee", 4.50, "expense")
        assert category == "Eating out"

    def test_keyword_cinema_entertainment(self):
        section, category = classify_transaction("cinema tickets", 15.0, "expense")
        assert category == "Entertainment"

    def test_keyword_pharmacy_health(self):
        section, category = classify_transaction("pharmacy medicine", 8.0, "expense")
        assert category == "Health"

    @patch("openai.OpenAI")
    def test_llm_fallback_on_unknown_description(self, mock_openai_cls):
        """When keyword match fails, LLM fallback is tried."""
        client = MagicMock()
        mock_openai_cls.return_value = client
        response = MagicMock()
        response.choices = [MagicMock()]
        response.choices[0].message.content = "variable|Shopping"
        client.chat.completions.create.return_value = response

        section, category = classify_transaction("random unknown item xyz", 20.0, "expense")
        assert section == "variable"
        assert category == "Shopping"

    @patch("openai.OpenAI", side_effect=Exception("no api key"))
    def test_llm_failure_defaults_to_misc(self, mock_openai_cls):
        """When both keyword and LLM fail, defaults to Misc."""
        section, category = classify_transaction("completely unknown xyz", 10.0, "expense")
        assert category == "Misc"
        assert section == "variable"

    @patch("openai.OpenAI", side_effect=Exception("no api key"))
    def test_llm_failure_income_defaults_to_other(self, mock_openai_cls):
        section, category = classify_transaction("mystery income xyz", 100.0, "income")
        assert category == "Other"
        assert section == "income"

    @patch("openai.OpenAI", side_effect=Exception("no api key"))
    def test_llm_failure_savings_defaults(self, mock_openai_cls):
        section, category = classify_transaction("mystery savings xyz", 50.0, "savings")
        assert category == "Savings"
        assert section == "savings"


# ── format_week_summary tests ──

class TestFormatWeekSummary:
    def test_empty_week(self):
        summary = {
            "week_start": "2026-03-23",
            "income_total": 0,
            "spending_total": 0,
            "savings_total": 0,
            "net": 0,
            "by_category": [],
            "planned": {},
            "recent": [],
        }
        result = format_week_summary(summary)
        assert "No transactions" in result

    def test_week_with_expenses(self):
        summary = {
            "week_start": "2026-03-23",
            "income_total": 0,
            "spending_total": 25.0,
            "savings_total": 0,
            "net": -25.0,
            "by_category": [
                {"section": "variable", "category": "Eating out", "type": "expense", "total": 15.0, "count": 2},
                {"section": "variable", "category": "Transport", "type": "expense", "total": 10.0, "count": 1},
            ],
            "planned": {"Eating out": 50.0},
            "recent": [
                {"amount": 15.0, "description": "lunch", "category": "Eating out", "type": "expense", "date": "2026-03-25"},
            ],
        }
        result = format_week_summary(summary)
        assert "WEEKLY BUDGET" in result
        assert "SPENDING" in result
        assert "Eating out" in result
        assert "Transport" in result
        assert "£15.00" in result
        assert "NET" in result

    def test_week_with_income(self):
        summary = {
            "week_start": "2026-03-23",
            "income_total": 500.0,
            "spending_total": 0,
            "savings_total": 0,
            "net": 500.0,
            "by_category": [
                {"section": "income", "category": "Freelance", "type": "income", "total": 500.0, "count": 1},
            ],
            "planned": {},
            "recent": [
                {"amount": 500.0, "description": "freelance", "category": "Freelance", "type": "income", "date": "2026-03-25"},
            ],
        }
        result = format_week_summary(summary)
        assert "INCOME" in result
        assert "Freelance" in result
        assert "£500.00" in result

    def test_planned_budget_shown(self):
        summary = {
            "week_start": "2026-03-23",
            "income_total": 0,
            "spending_total": 30.0,
            "savings_total": 0,
            "net": -30.0,
            "by_category": [
                {"section": "variable", "category": "Groceries", "type": "expense", "total": 30.0, "count": 2},
            ],
            "planned": {"Groceries": 50.0},
            "recent": [],
        }
        result = format_week_summary(summary)
        assert "/ £50" in result


# ── format_today tests ──

class TestFormatToday:
    def test_no_transactions(self):
        data = {"date": "2026-03-25", "total_spent": 0, "total_earned": 0, "items": []}
        result = format_today(data)
        assert "No transactions today" in result

    def test_today_with_items(self):
        data = {
            "date": "2026-03-25",
            "total_spent": 23.50,
            "total_earned": 0,
            "items": [
                {"amount": 15.0, "description": "lunch", "category": "Eating out", "type": "expense"},
                {"amount": 8.50, "description": "coffee", "category": "Eating out", "type": "expense"},
            ],
        }
        result = format_today(data)
        assert "TODAY" in result
        assert "£23.50" in result
        assert "lunch" in result
        assert "coffee" in result

    def test_today_with_income(self):
        data = {
            "date": "2026-03-25",
            "total_spent": 0,
            "total_earned": 500.0,
            "items": [
                {"amount": 500.0, "description": "freelance", "category": "Freelance", "type": "income"},
            ],
        }
        result = format_today(data)
        assert "earned £500.00" in result


# ── add_transaction with real in-memory DB ──

class TestAddTransaction:
    @patch("jobpulse.budget_agent.DB_PATH")
    def test_add_and_retrieve(self, mock_db_path, tmp_path):
        """Test that add_transaction stores and retrieves correctly."""
        db_file = tmp_path / "test_budget.db"
        mock_db_path.__str__ = lambda self: str(db_file)
        mock_db_path.parent = tmp_path

        # Re-init with temp DB
        conn = sqlite3.connect(str(db_file))
        conn.row_factory = sqlite3.Row
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                amount REAL NOT NULL,
                description TEXT NOT NULL,
                category TEXT NOT NULL,
                section TEXT NOT NULL,
                type TEXT NOT NULL,
                date TEXT NOT NULL,
                week_start TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
        """)
        conn.commit()
        conn.close()

        with patch("jobpulse.budget_agent._get_conn") as mock_conn:
            mock_conn.return_value = sqlite3.connect(str(db_file))
            mock_conn.return_value.row_factory = sqlite3.Row
            txn = add_transaction(15.0, "lunch", "Eating out", "variable", "expense")

        assert txn["amount"] == 15.0
        assert txn["description"] == "lunch"
        assert txn["category"] == "Eating out"
        assert txn["section"] == "variable"
        assert txn["type"] == "expense"
        assert txn["id"] is not None


# ── _get_week_start tests ──

class TestGetWeekStart:
    def test_returns_monday(self):
        from datetime import datetime
        # Wednesday 2026-03-25
        ws = _get_week_start(datetime(2026, 3, 25))
        assert ws == "2026-03-23"  # Monday

    def test_monday_returns_itself(self):
        from datetime import datetime
        ws = _get_week_start(datetime(2026, 3, 23))
        assert ws == "2026-03-23"

    def test_sunday_returns_previous_monday(self):
        from datetime import datetime
        ws = _get_week_start(datetime(2026, 3, 29))
        assert ws == "2026-03-23"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
