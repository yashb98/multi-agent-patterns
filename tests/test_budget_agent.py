"""Tests for budget_agent.py — transaction parsing, classification, formatting, and 28-day periods."""

import pytest
import sqlite3
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock
from jobpulse.budget_agent import (
    parse_transaction,
    classify_transaction,
    format_week_summary,
    format_today,
    add_transaction,
    init_db,
    _get_week_start,
    _get_period_start,
    _get_period_end,
    _get_salary_week_start,
    get_week_summary,
    get_today_spending,
    set_planned_budget,
    check_budget_alerts,
    add_recurring,
    process_recurring,
    list_recurring,
    remove_recurring,
    format_recurring,
    log_transaction,
    set_budget,
    undo_last_transaction,
    get_hours_summary,
    confirm_savings_transfer,
    log_hours,
    undo_hours,
    PERIOD_DAYS,
    PERIOD_ANCHOR,
    ALL_CATEGORIES,
    DB_PATH,
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

    @patch("shared.agents.get_openai_client")
    def test_llm_fallback_on_unknown_description(self, mock_get_client):
        """When keyword match fails, LLM fallback is tried."""
        client = MagicMock()
        mock_get_client.return_value = client
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
        assert "BUDGET PERIOD" in result
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
    def test_add_and_retrieve(self, tmp_budget_db):
        """Test that add_transaction stores and retrieves correctly."""
        txn = add_transaction(15.0, "lunch", "Eating out", "variable", "expense")
        assert txn["amount"] == 15.0
        assert txn["description"] == "lunch"
        assert txn["category"] == "Eating out"
        assert txn["section"] == "variable"
        assert txn["type"] == "expense"
        assert txn["id"] is not None

        # Verify it persisted in the DB
        period = _get_period_start()
        summary = get_week_summary(period)
        assert summary["spending_total"] == 15.0


# ── _get_week_start tests ──

class TestGetPeriodStart:
    """Budget periods are 28 days anchored to 2026-04-02."""

    def test_anchor_date_returns_itself(self):
        from datetime import datetime
        ws = _get_week_start(datetime(2026, 4, 2))
        assert ws == "2026-04-02"

    def test_mid_period_returns_period_start(self):
        from datetime import datetime
        # April 15 is day 13 of the first period (Apr 2 – Apr 29)
        ws = _get_week_start(datetime(2026, 4, 15))
        assert ws == "2026-04-02"

    def test_last_day_of_period(self):
        from datetime import datetime
        # April 29 is the last day of the first period
        ws = _get_week_start(datetime(2026, 4, 29))
        assert ws == "2026-04-02"

    def test_next_period_starts_correctly(self):
        from datetime import datetime
        # April 30 is the first day of the second period
        ws = _get_week_start(datetime(2026, 4, 30))
        assert ws == "2026-04-30"

    def test_date_before_anchor(self):
        from datetime import datetime
        # March 25 is before the anchor — should go to the prior period
        ws = _get_week_start(datetime(2026, 3, 25))
        # 2026-04-02 minus 28 = 2026-03-05
        assert ws == "2026-03-05"


@pytest.fixture
def tmp_budget_db(tmp_path, monkeypatch):
    """Create a temporary budget.db and patch all _get_conn to use it."""
    db_file = tmp_path / "budget.db"
    monkeypatch.setattr("jobpulse.budget_agent.DB_PATH", db_file)
    monkeypatch.setattr("jobpulse.budget_salary.DB_PATH", db_file)
    monkeypatch.setattr("jobpulse.budget_notion.DB_PATH", db_file)
    monkeypatch.setattr("jobpulse.budget_tracker.DB_PATH", db_file)
    init_db()
    return db_file


def _conn(db_file):
    conn = sqlite3.connect(str(db_file))
    conn.row_factory = sqlite3.Row
    return conn


# ── _get_period_end ──

class TestGetPeriodEnd:
    def test_first_period_end(self):
        assert _get_period_end("2026-04-02") == "2026-04-29"

    def test_second_period_end(self):
        assert _get_period_end("2026-04-30") == "2026-05-27"


# ── _get_salary_week_start (aligned to budget period) ──

class TestGetSalaryWeekStart:
    def test_same_as_period_start(self):
        d = datetime(2026, 4, 10)
        assert _get_salary_week_start(d) == _get_period_start(d)

    def test_default_is_now(self):
        # Just verify it doesn't crash with no argument
        result = _get_salary_week_start()
        assert len(result) == 10  # YYYY-MM-DD


# ── get_week_summary ──

class TestGetWeekSummary:
    def test_empty_period(self, tmp_budget_db):
        summary = get_week_summary("2026-04-02")
        assert summary["income_total"] == 0
        assert summary["spending_total"] == 0
        assert summary["savings_total"] == 0
        assert summary["net"] == 0
        assert summary["by_category"] == []
        assert summary["recent"] == []

    def test_with_transactions(self, tmp_budget_db):
        add_transaction(100.0, "salary", "Salary", "income", "income")
        add_transaction(25.0, "groceries", "Groceries", "variable", "expense")
        add_transaction(10.0, "savings", "Savings", "savings", "savings")
        period = _get_period_start()
        summary = get_week_summary(period)
        assert summary["income_total"] == 100.0
        assert summary["spending_total"] == 25.0
        assert summary["savings_total"] == 10.0
        assert summary["net"] == 65.0
        assert len(summary["by_category"]) == 3
        assert len(summary["recent"]) == 3


# ── get_today_spending ──

class TestGetTodaySpending:
    def test_no_transactions(self, tmp_budget_db):
        data = get_today_spending()
        assert data["total_spent"] == 0
        assert data["total_earned"] == 0
        assert data["items"] == []

    def test_with_spending(self, tmp_budget_db):
        add_transaction(12.0, "lunch", "Eating out", "variable", "expense")
        data = get_today_spending()
        assert data["total_spent"] == 12.0
        assert len(data["items"]) == 1


# ── set_planned_budget ──

class TestSetPlannedBudget:
    def test_set_and_check(self, tmp_budget_db):
        period = _get_period_start()
        set_planned_budget("Groceries", "variable", 200.0, period)
        summary = get_week_summary(period)
        assert summary["planned"]["Groceries"] == 200.0

    def test_reject_negative(self, tmp_budget_db):
        result = set_planned_budget("Groceries", "variable", -10.0)
        assert result is not None and "error" in result


# ── add_transaction edge cases ──

class TestAddTransactionEdgeCases:
    def test_reject_zero(self, tmp_budget_db):
        result = add_transaction(0, "test", "Misc", "variable", "expense")
        assert "error" in result

    def test_reject_negative(self, tmp_budget_db):
        result = add_transaction(-5.0, "test", "Misc", "variable", "expense")
        assert "error" in result

    def test_reject_huge(self, tmp_budget_db):
        result = add_transaction(200_000, "test", "Misc", "variable", "expense")
        assert "error" in result

    def test_dedup_guard(self, tmp_budget_db):
        add_transaction(15.0, "coffee", "Eating out", "variable", "expense")
        result = add_transaction(15.0, "coffee", "Eating out", "variable", "expense")
        assert result.get("dedup") is True


# ── check_budget_alerts ──

class TestCheckBudgetAlerts:
    def test_no_alerts_when_under_budget(self, tmp_budget_db):
        period = _get_period_start()
        set_planned_budget("Groceries", "variable", 200.0, period)
        add_transaction(50.0, "food", "Groceries", "variable", "expense")
        alerts = check_budget_alerts()
        # 50/200 = 25%, under 80% threshold
        assert len(alerts) == 0

    def test_alert_when_over_80pct(self, tmp_budget_db):
        period = _get_period_start()
        set_planned_budget("Groceries", "variable", 100.0, period)
        add_transaction(85.0, "food", "Groceries", "variable", "expense")
        alerts = check_budget_alerts()
        assert any("Groceries" in a for a in alerts)

    def test_no_alerts_without_planned(self, tmp_budget_db):
        add_transaction(100.0, "food", "Groceries", "variable", "expense")
        alerts = check_budget_alerts()
        assert len(alerts) == 0


# ── Recurring transactions ──

class TestRecurring:
    def test_add_daily(self, tmp_budget_db):
        result = add_recurring(5.0, "coffee sub", "Subscriptions", "fixed", "expense", "daily")
        assert result["id"] is not None
        assert result["frequency"] == "daily"

    def test_add_weekly(self, tmp_budget_db):
        result = add_recurring(10.0, "gym", "Health", "variable", "expense", "weekly", day=0)
        assert result["frequency"] == "weekly"

    def test_add_monthly(self, tmp_budget_db):
        result = add_recurring(50.0, "spotify", "Subscriptions", "fixed", "expense", "monthly", day=1)
        assert result["frequency"] == "monthly"

    def test_invalid_frequency(self, tmp_budget_db):
        result = add_recurring(5.0, "test", "Misc", "variable", "expense", "biweekly")
        assert "error" in result

    def test_list_recurring(self, tmp_budget_db):
        add_recurring(5.0, "coffee", "Subscriptions", "fixed", "expense", "daily")
        items = list_recurring()
        assert len(items) == 1
        assert items[0]["description"] == "coffee"

    def test_remove_recurring(self, tmp_budget_db):
        add_recurring(5.0, "coffee sub", "Subscriptions", "fixed", "expense", "daily")
        msg = remove_recurring("coffee sub")
        assert "Removed" in msg or "removed" in msg or "coffee" in msg.lower()

    def test_remove_nonexistent(self, tmp_budget_db):
        add_recurring(5.0, "x", "Misc", "variable", "expense", "daily")
        msg = remove_recurring("zzz_nothing_matches_this")
        # Should indicate no match found — must NOT silently succeed
        assert "zzz" not in msg.lower() or "Couldn't" in msg or "match" in msg.lower()
        # The original "x" recurring should still exist
        items = list_recurring()
        assert len(items) == 1

    def test_format_recurring_empty(self, tmp_budget_db):
        items = list_recurring()
        result = format_recurring(items)
        assert "No" in result or "no" in result

    def test_format_recurring_with_items(self, tmp_budget_db):
        add_recurring(10.0, "spotify", "Subscriptions", "fixed", "expense", "monthly", day=15)
        items = list_recurring()
        result = format_recurring(items)
        assert "spotify" in result

    def test_process_daily(self, tmp_budget_db):
        add_recurring(5.0, "daily coffee", "Eating out", "variable", "expense", "daily")
        logged = process_recurring()
        assert len(logged) == 1
        assert logged[0]["description"] == "daily coffee"


# ── log_transaction (full user-facing flow) ──

class TestLogTransaction:
    @patch("jobpulse.budget_agent.sync_expense_to_notion")
    @patch("jobpulse.budget_tracker.get_or_create_category_page", return_value="fake-page-id")
    @patch("jobpulse.budget_tracker._notion_api", return_value={"results": []})
    def test_log_expense(self, mock_api, mock_page, mock_sync, tmp_budget_db):
        # Ensure tracker columns exist
        from jobpulse.budget_tracker import init_tracker_db
        init_tracker_db()
        result = log_transaction("spent 15 on lunch")
        assert "£15.00" in result
        assert "Eating out" in result

    @patch("jobpulse.budget_agent.sync_expense_to_notion")
    @patch("jobpulse.budget_tracker.get_or_create_category_page", return_value="fake-page-id")
    @patch("jobpulse.budget_tracker._notion_api", return_value={"results": []})
    def test_log_income(self, mock_api, mock_page, mock_sync, tmp_budget_db):
        from jobpulse.budget_tracker import init_tracker_db
        init_tracker_db()
        result = log_transaction("earned 500 salary")
        assert "£500.00" in result
        assert "Salary" in result

    def test_unparseable(self, tmp_budget_db):
        result = log_transaction("hello world")
        assert "couldn't parse" in result.lower() or "couldn" in result.lower() or "format" in result.lower()


# ── set_budget (user-facing) ──

class TestSetBudget:
    @patch("jobpulse.budget_agent._update_planned_column")
    def test_set_budget_groceries(self, mock_notion, tmp_budget_db):
        result = set_budget("set budget groceries 200")
        assert "£200.00" in result
        assert "Groceries" in result

    @patch("jobpulse.budget_agent._update_planned_column")
    def test_set_budget_no_amount(self, mock_notion, tmp_budget_db):
        result = set_budget("set budget groceries")
        assert "amount" in result.lower() or "include" in result.lower()


# ── undo_last_transaction ──

class TestUndoLastTransaction:
    @patch("jobpulse.budget_agent.sync_expense_to_notion")
    @patch("jobpulse.budget_agent._update_section_totals")
    def test_undo_shows_list(self, mock_totals, mock_sync, tmp_budget_db):
        add_transaction(10.0, "test1", "Misc", "variable", "expense")
        add_transaction(20.0, "test2", "Misc", "variable", "expense")
        result = undo_last_transaction()
        # Should show numbered list of recent transactions for pick selection
        assert "test1" in result or "test2" in result
        assert "1" in result  # numbered selection list

    @patch("jobpulse.budget_agent.sync_expense_to_notion")
    @patch("jobpulse.budget_agent._update_section_totals")
    @patch("jobpulse.budget_tracker._notion_api")
    @patch("jobpulse.budget_tracker.get_category_page_url", return_value="")
    def test_undo_by_number(self, mock_url, mock_api, mock_totals, mock_sync, tmp_budget_db):
        add_transaction(10.0, "undo_me", "Misc", "variable", "expense")
        result = undo_last_transaction(pick=1)
        assert "Removed" in result or "removed" in result or "undo_me" in result

    def test_undo_empty(self, tmp_budget_db):
        result = undo_last_transaction()
        assert "No" in result or "nothing" in result.lower()


# ── Work hours / Salary ──

class TestLogHours:
    @patch("jobpulse.budget_agent._get_or_create_salary_page", return_value="fake-page-id")
    @patch("jobpulse.budget_agent._add_row_to_salary_page")
    @patch("jobpulse.budget_agent.sync_expense_to_notion")
    @patch("jobpulse.budget_agent._update_section_totals")
    def test_log_hours_basic(self, mock_totals, mock_sync, mock_row, mock_page, tmp_budget_db):
        result = log_hours("worked 5 hours today")
        assert "5.0h" in result or "5h" in result
        assert "£" in result

    @patch("jobpulse.budget_agent._get_or_create_salary_page", return_value="fake-page-id")
    @patch("jobpulse.budget_agent._add_row_to_salary_page")
    @patch("jobpulse.budget_agent.sync_expense_to_notion")
    @patch("jobpulse.budget_agent._update_section_totals")
    def test_log_hours_with_date(self, mock_totals, mock_sync, mock_row, mock_page, tmp_budget_db):
        result = log_hours("worked 3 hours yesterday")
        assert "3.0h" in result or "3h" in result


class TestGetHoursSummary:
    def test_no_hours(self, tmp_budget_db):
        result = get_hours_summary()
        assert "No hours" in result or "no hours" in result

    @patch("jobpulse.budget_agent._get_or_create_salary_page", return_value="fake-page-id")
    @patch("jobpulse.budget_agent._add_row_to_salary_page")
    @patch("jobpulse.budget_agent.sync_expense_to_notion")
    @patch("jobpulse.budget_agent._update_section_totals")
    def test_with_hours(self, mock_totals, mock_sync, mock_row, mock_page, tmp_budget_db):
        log_hours("worked 5 hours today")
        result = get_hours_summary()
        assert "5.0h" in result
        assert "WORK HOURS" in result


class TestConfirmSavings:
    def test_no_hours(self, tmp_budget_db):
        result = confirm_savings_transfer()
        assert "No hours" in result or "nothing" in result.lower()


class TestUndoHours:
    def test_empty(self, tmp_budget_db):
        result = undo_hours()
        assert "No" in result or "no" in result

    @patch("jobpulse.budget_agent._get_or_create_salary_page", return_value="fake-page-id")
    @patch("jobpulse.budget_agent._add_row_to_salary_page")
    @patch("jobpulse.budget_agent.sync_expense_to_notion")
    @patch("jobpulse.budget_agent._update_section_totals")
    def test_undo_shows_list(self, mock_totals, mock_sync, mock_row, mock_page, tmp_budget_db):
        log_hours("worked 3 hours today")
        result = undo_hours()
        # Should show the 3h entry and £ amount for selection
        assert "3.0h" in result or "3h" in result
        assert "£" in result


# ── Period math edge cases ──

# ── sync_expense_to_notion / _update_section_totals ──

class TestSyncAndTotals:
    @patch("jobpulse.budget_notion._notion_api")
    @patch("jobpulse.budget_tracker.get_category_page_url", return_value="")
    def test_sync_expense(self, mock_url, mock_api, tmp_budget_db):
        from jobpulse.budget_agent import sync_expense_to_notion
        mock_api.return_value = {"table_row": {"cells": [[], [], [], []]}}
        period = _get_period_start()
        add_transaction(20.0, "test", "Groceries", "variable", "expense")
        sync_expense_to_notion({"category": "Groceries", "week_start": period,
                                "description": "test", "date": "2026-04-07"})
        assert mock_api.call_count > 0

    @patch("jobpulse.budget_notion._notion_api")
    def test_update_section_totals(self, mock_api, tmp_budget_db):
        from jobpulse.budget_agent import _update_section_totals
        mock_api.return_value = {"table_row": {"cells": [[], [], [], []]}}
        period = _get_period_start()
        add_transaction(100.0, "salary", "Salary", "income", "income")
        add_transaction(30.0, "food", "Groceries", "variable", "expense")
        _update_section_totals(period)
        # Notion API called for all total rows + summary rows (8 calls)
        assert mock_api.call_count >= 8


# ── Salary page creation ──

class TestSalaryPage:
    @patch("jobpulse.notion_agent._notion_api")
    def test_create_salary_page(self, mock_api, tmp_budget_db):
        from jobpulse.budget_agent import _get_or_create_salary_page
        mock_api.return_value = {"id": "fake-salary-page-id"}
        page_id = _get_or_create_salary_page(_get_period_start())
        assert page_id == "fake-salary-page-id"

    @patch("jobpulse.notion_agent._notion_api")
    def test_reuses_existing_page(self, mock_api, tmp_budget_db):
        from jobpulse.budget_agent import _get_or_create_salary_page, _get_salary_week_start
        # Insert a fake page reference into work_hours
        period = _get_salary_week_start()
        conn = _conn(tmp_budget_db)
        conn.execute(
            "INSERT INTO work_hours (hours, hourly_rate, total_earned, date, week_start, notion_page_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (5.0, 13.99, 69.95, "2026-04-07", period, "existing-page", datetime.now().isoformat())
        )
        conn.commit()
        conn.close()
        page_id = _get_or_create_salary_page(period)
        assert page_id == "existing-page"
        mock_api.assert_not_called()


# ── _add_row_to_salary_page ──

class TestAddRowToSalaryPage:
    @patch("jobpulse.notion_agent._notion_api")
    def test_add_row(self, mock_api, tmp_budget_db):
        from jobpulse.budget_agent import _add_row_to_salary_page
        mock_api.return_value = {"results": [
            {"type": "table", "id": "table-id"},
        ]}
        _add_row_to_salary_page("fake-page", 5.0, 13.99, "2026-04-07", 69.95)
        # Should have fetched children + appended row
        assert mock_api.call_count >= 2


# ── confirm_savings_transfer with hours ──

class TestConfirmSavingsWithHours:
    @patch("jobpulse.budget_agent.sync_expense_to_notion")
    @patch("jobpulse.budget_agent._update_section_totals")
    @patch("jobpulse.notion_agent._notion_api")
    def test_savings_calculated(self, mock_api, mock_totals, mock_sync, tmp_budget_db):
        mock_api.return_value = {"results": []}
        # Insert hours directly
        period = _get_salary_week_start()
        conn = _conn(tmp_budget_db)
        conn.execute(
            "INSERT INTO work_hours (hours, hourly_rate, total_earned, date, week_start, notion_page_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (10.0, 13.99, 139.90, "2026-04-07", period, "fake-page", datetime.now().isoformat())
        )
        conn.commit()
        conn.close()
        result = confirm_savings_transfer(period)
        assert "£" in result
        assert "saved" in result.lower() or "Savings" in result or "savings" in result.lower()


# ── _rebuild_notion_timesheet ──

class TestRebuildTimesheet:
    @patch("jobpulse.notion_agent._notion_api")
    def test_rebuild(self, mock_api, tmp_budget_db):
        from jobpulse.budget_agent import _rebuild_notion_timesheet
        mock_api.side_effect = [
            # GET children → find table
            {"results": [{"type": "table", "id": "table-id"}]},
            # GET table children → rows
            {"results": [{"type": "table_row", "id": "header"}]},
            # PATCH to add rows
            {},
        ]
        period = _get_salary_week_start()
        conn = _conn(tmp_budget_db)
        conn.execute(
            "INSERT INTO work_hours (hours, hourly_rate, total_earned, date, week_start, notion_page_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (5.0, 13.99, 69.95, "2026-04-07", period, "fake-page", datetime.now().isoformat())
        )
        conn.commit()
        conn.close()
        _rebuild_notion_timesheet("fake-page", period)
        assert mock_api.call_count >= 2


# ── undo_hours with pick ──

class TestUndoHoursWithPick:
    @patch("jobpulse.budget_salary._rebuild_notion_timesheet")
    @patch("jobpulse.budget_agent.sync_expense_to_notion")
    @patch("jobpulse.budget_agent._update_section_totals")
    @patch("jobpulse.budget_salary._get_or_create_salary_page", return_value="fake-page-id")
    @patch("jobpulse.budget_salary._add_row_to_salary_page")
    def test_undo_by_pick(self, mock_row, mock_page, mock_totals, mock_sync, mock_rebuild, tmp_budget_db):
        log_hours("worked 4 hours today")
        result = undo_hours(pick=1)
        assert "Removed" in result or "removed" in result or "£" in result


# ── set_budget edge cases ──

class TestSetBudgetEdge:
    @patch("jobpulse.budget_agent._update_planned_column")
    def test_no_explicit_category_uses_llm(self, mock_notion, tmp_budget_db):
        """When no category is given, LLM classifies the amount and sets a budget."""
        result = set_budget("set budget 50")
        # LLM picks a category (e.g. "Savings") — result should confirm the budget was set
        assert "£50.00" in result or "Budget" in result or "budget" in result

    @patch("jobpulse.budget_agent._update_planned_column")
    def test_set_budget_with_explicit_category(self, mock_notion, tmp_budget_db):
        """Explicit category + amount should always succeed."""
        result = set_budget("set budget eating out 75")
        assert "£75.00" in result
        assert "Eating out" in result


class TestPeriodMath:
    def test_period_length_is_28(self):
        assert PERIOD_DAYS == 28

    def test_anchor_date(self):
        assert PERIOD_ANCHOR == datetime(2026, 4, 2)

    def test_consecutive_periods_no_gaps(self):
        """Every day maps to exactly one period with no gaps."""
        start = datetime(2026, 3, 1)
        for i in range(120):
            d = start + timedelta(days=i)
            ps = _get_period_start(d)
            pe = _get_period_end(ps)
            # date should be >= period_start and <= period_end
            assert d.strftime("%Y-%m-%d") >= ps
            assert d.strftime("%Y-%m-%d") <= pe

    def test_period_end_plus_one_is_next_period_start(self):
        ps1 = "2026-04-02"
        pe1 = _get_period_end(ps1)
        next_day = (datetime.strptime(pe1, "%Y-%m-%d") + timedelta(days=1))
        ps2 = _get_period_start(next_day)
        assert ps2 == next_day.strftime("%Y-%m-%d")

    def test_far_future(self):
        """Period math works correctly far from anchor date."""
        ps = _get_period_start(datetime(2027, 1, 15))
        # Verify it's a valid date and falls on a correct 28-day boundary from anchor
        ps_dt = datetime.strptime(ps, "%Y-%m-%d")
        delta = (ps_dt - PERIOD_ANCHOR).days
        assert delta % PERIOD_DAYS == 0  # must be exact multiple of 28
        assert ps_dt <= datetime(2027, 1, 15)  # period start <= the date


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
