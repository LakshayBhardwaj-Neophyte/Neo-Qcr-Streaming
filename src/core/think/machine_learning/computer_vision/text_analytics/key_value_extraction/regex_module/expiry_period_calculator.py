from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta
import re
from typing import Optional


def parse_period(period_str: str) -> Optional[tuple[int, str]]:
    """Parse expiry period strings like '1 month', 'months', '2 years', '30 days'.

    Returns a tuple (value:int, unit:str) where unit is normalized to one of
    'day', 'week', 'month', 'year'. If parsing fails, returns None.
    If number is missing (e.g. 'month' or 'months'), assume 1.
    """
    if not period_str or not isinstance(period_str, str):
        return None

    s = period_str.strip().lower()

    # If the string looks like a date, don't parse as period
    if re.match(r"^\d{2}-\d{2}-\d{4}$", s):
        return None

    # Try to find a number and unit
    m = re.match(r"^(\d{1,4})\s*[- ]?\s*([a-zA-Z]+)s?$", s)
    if m:
        val = int(m.group(1))
        unit = m.group(2)
    else:
        # maybe number missing, e.g. 'month' or 'months'
        m2 = re.match(r"^([a-zA-Z]+)s?$", s)
        if m2:
            val = 1
            unit = m2.group(1)
        else:
            return None

    # normalize unit
    if unit.startswith('day'):
        unit_n = 'day'
    elif unit.startswith('week'):
        unit_n = 'week'
    elif unit.startswith('month'):
        unit_n = 'month'
    elif unit.startswith('year'):
        unit_n = 'year'
    else:
        return None

    return val, unit_n


def calculate_expiry_date(mfg_date: str, expiry_period: str) -> str:
    """
    Calculate expiry date based on manufacturing date and expiry period.

    Behavior per instructions:
    - Do not change `date_regex.py` behavior; here we compute expiry date when
      an expiry period (like '1 month', '2 years', '30 days') is provided.
    - If expiry_period is actually a date string (dd-mm-yyyy) and mfg_date is
      missing, return the expiry_period unchanged (preserve default behavior).
    - If mfg_date is provided and expiry_period is a period string, compute
      expiry date from mfg_date and return in 'dd-mm-YYYY' format.
    - If parsing fails, return the original expiry_period (so calling code keeps default).
    """
    # If expiry_period is empty or None, nothing to compute
    if not expiry_period:
        return None

    # If expiry_period already looks like a date -> return the date
    if re.match(r"^\d{2}-\d{2}-\d{4}$", expiry_period.strip()):
        return expiry_period.strip()

    # Parse mfg date
    mfg_obj = None
    if mfg_date and isinstance(mfg_date, str):
        try:
            mfg_obj = datetime.strptime(mfg_date.strip(), "%d-%m-%Y")
        except Exception:
            mfg_obj = None

    # Parse period
    parsed = parse_period(expiry_period)
    if not parsed:
        # Can't parse period; return None (user requested unparsable -> null)
        return None

    value, unit = parsed

    if not mfg_obj:
        # No mfg date to calculate from -> cannot compute expiry -> return None
        return None

    # Compute expiry date
    if unit == 'day':
        expiry = mfg_obj + timedelta(days=value)
    elif unit == 'week':
        expiry = mfg_obj + timedelta(weeks=value)
    elif unit == 'month':
        expiry = mfg_obj + relativedelta(months=value)
    elif unit == 'year':
        expiry = mfg_obj + relativedelta(years=value)
    else:
        return None

    return expiry.strftime("%d-%m-%Y")


if __name__ == "__main__":
    # Test cases (8-10 examples) covering plural/singular, missing number, and missing MFG
    tests = [
        # mfg present, simple months
        ("15-03-2024", "1 month"),
        ("15-03-2024", "2 months"),
        ("01-01-2025", "1 year"),
        ("28-02-2024", "1 day"),
        ("01-07-2025", "2 weeks"),
        # missing number -> assume 1
        ("10-10-2023", "months"),
        # expiry_period is a date and mfg missing -> return expiry as is
        (None, "07-10-2025"),
        # mfg missing and period string -> return original (preserve default)
        (None, "6 months"),
        # invalid period -> return original
        ("01-01-2024", "approx 6") ,
        # hyphenated or fuzzy formats
        ("05-05-2022", "3-months"),
    ]

    for idx, (mfg, period) in enumerate(tests, 1):
        result = calculate_expiry_date(mfg, period)
        print(f"Test {idx}: MFG={mfg!r}, Period={period!r} -> Expiry={result}")

