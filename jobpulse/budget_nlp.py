"""Budget NLP — transaction parsing and classification.

4-stage classification pipeline:
  1. Store→category inference (Tesco → Groceries)
  2. Multi-word phrase match (longest match wins)
  3. Single-word keyword match
  4. LLM fallback (GPT-4.1-mini)
"""
import re

from shared.logging_config import get_logger
from jobpulse.budget_constants import ALL_CATEGORIES

logger = get_logger(__name__)


def classify_transaction(description: str, amount: float, txn_type: str = "expense") -> tuple[str, str]:
    """Classify into (section, category) with reason tracking.

    4-stage pipeline:
    1. Store→category inference first (Tesco → Groceries, overrides generic keywords)
    2. Multi-word phrase match (longest match wins, word-boundary safe)
    3. Single-word keyword match (word-boundary safe)
    4. LLM fallback
    """
    desc_lower = description.lower()

    # Stage 1: Store→category inference FIRST (store is strongest signal)
    # If someone says "drinks from tesco", Tesco = Groceries beats "drinks" = Entertainment
    store_category_map = {
        # Grocery stores → Groceries
        "tesco": ("variable", "Groceries"), "aldi": ("variable", "Groceries"),
        "lidl": ("variable", "Groceries"), "sainsbury": ("variable", "Groceries"),
        "asda": ("variable", "Groceries"), "morrisons": ("variable", "Groceries"),
        "waitrose": ("variable", "Groceries"), "co-op": ("variable", "Groceries"),
        "iceland": ("variable", "Groceries"), "m&s food": ("variable", "Groceries"),
        # Eating out stores → Eating out
        "pret": ("variable", "Eating out"), "costa": ("variable", "Eating out"),
        "starbucks": ("variable", "Eating out"), "greggs": ("variable", "Eating out"),
        "mcdonald": ("variable", "Eating out"), "kfc": ("variable", "Eating out"),
        "nando": ("variable", "Eating out"), "subway": ("variable", "Eating out"),
        "wagamama": ("variable", "Eating out"), "wetherspoon": ("variable", "Eating out"),
        "domino": ("variable", "Eating out"), "pizza hut": ("variable", "Eating out"),
        # Health stores → Health
        "boots": ("variable", "Health"), "superdrug": ("variable", "Health"),
        "holland and barrett": ("variable", "Health"),
        # Shopping stores → Shopping
        "argos": ("variable", "Shopping"), "jd sports": ("variable", "Shopping"),
        "primark": ("variable", "Shopping"), "tk maxx": ("variable", "Shopping"),
        "next": ("variable", "Shopping"), "asos": ("variable", "Shopping"),
        "zara": ("variable", "Shopping"),
    }
    for store, (section, category) in store_category_map.items():
        if re.search(rf"\b{re.escape(store)}\b", desc_lower):
            if txn_type == "expense":
                return section, category

    # Stage 2: Keyword match (longest-first, word-boundary safe)
    sorted_keywords = sorted(ALL_CATEGORIES.keys(), key=len, reverse=True)

    for keyword in sorted_keywords:
        section, category = ALL_CATEGORIES[keyword]
        if re.search(rf"\b{re.escape(keyword)}\b", desc_lower):
            if txn_type == "income" and section == "income":
                return section, category
            elif txn_type == "expense" and section in ("fixed", "variable"):
                return section, category
            elif txn_type == "savings" and section == "savings":
                return section, category
            elif txn_type == "expense":
                return section, category

    # Stage 3: LLM fallback
    try:
        from shared.agents import get_openai_client

        categories_list = """
INCOME: Salary, Freelance, Other
FIXED EXPENSES: Rent / Mortgage, Utilities, Phone / Internet, Subscriptions, Insurance
VARIABLE: Groceries, Eating out, Transport, Shopping, Entertainment, Health, Misc
SAVINGS: Savings, Investments, Credit card / Loan payment"""

        client = get_openai_client()
        response = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[{"role": "user", "content": f"""Classify this {txn_type} into one category:
{categories_list}

Transaction: £{amount:.2f} — "{description}"

Respond with ONLY: section|category
Example: variable|Eating out
Example: income|Salary
Example: fixed|Subscriptions"""}],
            max_tokens=15, temperature=0,
        )
        raw = response.choices[0].message.content.strip()
        parts = raw.split("|")
        if len(parts) == 2:
            llm_section = parts[0].strip().lower()
            llm_category = parts[1].strip()
            # Validate LLM response against real categories
            valid_categories = set()
            for _, (s, c) in ALL_CATEGORIES.items():
                valid_categories.add(c)
            if llm_category in valid_categories:
                return llm_section, llm_category
            # Try fuzzy match (LLM might say "Eating Out" vs "Eating out")
            for vc in valid_categories:
                if vc.lower() == llm_category.lower():
                    return llm_section, vc
            logger.warning("LLM returned invalid category: %s", llm_category)
    except Exception as e:
        logger.warning("LLM classify failed: %s", e)

    # Default
    if txn_type == "income":
        return "income", "Other"
    elif txn_type == "savings":
        return "savings", "Savings"
    return "variable", "Misc"


def parse_transaction(text: str) -> dict | None:
    """Parse natural language into {amount, description, type}.

    Handles:
      "spent 15 on lunch" → expense
      "earned 500 freelance" → income
      "saved 100" → savings
      "£8.50 coffee" → expense
      "income 2000 salary" → income
    """
    text = text.strip()

    # Detect type from keywords
    txn_type = "expense"
    if re.match(r"^(earned|income|received|got paid|salary|freelance)", text, re.IGNORECASE):
        txn_type = "income"
        text = re.sub(r"^(earned|income|received|got paid)\s+", "", text, flags=re.IGNORECASE)
    elif re.match(r"^(saved|saving|invest|debt|loan|repay|credit card)", text, re.IGNORECASE):
        txn_type = "savings"
        text = re.sub(r"^(saved|saving)\s+", "", text, flags=re.IGNORECASE)
    else:
        text = re.sub(r"^(spent|spend|paid|bought|got)\s+", "", text, flags=re.IGNORECASE)

    # Extract amount — supports: 15, 15.99, .50, £1,000, $1,000.50
    # First strip commas from numbers like 1,000
    text_clean = re.sub(r"(\d),(\d{3})", r"\1\2", text)
    match = re.search(r"[£$€]?\s*(\d*\.?\d+)", text_clean)
    if not match:
        return None

    amount = float(match.group(1))
    if amount <= 0 or amount > 100000:
        return None

    # Extract description
    start = match.start()
    if start > 0 and text[start - 1] in "£$€":
        start -= 1
    desc = text[:start] + " " + text[match.end():]
    desc = re.sub(r"\s+", " ", desc).strip()
    desc = re.sub(r"^(on|for|at|to)\s+", "", desc, flags=re.IGNORECASE)
    desc = desc.strip() or "Unspecified"

    return {"amount": amount, "description": desc, "type": txn_type}
