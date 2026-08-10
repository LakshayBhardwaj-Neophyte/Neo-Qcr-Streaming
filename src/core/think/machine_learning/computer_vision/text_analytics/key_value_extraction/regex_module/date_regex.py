# # Updated GetDates Class
# import re
# from typing import Tuple, List, Any
# from datetime import datetime
# # import calculator
# try:
#     # preferred relative import when used as package
#     from .expiry_period_calculator import calculate_expiry_date
# except Exception:
#     try:
#         # try absolute package import (if src is on PYTHONPATH)
#         from src.core.think.machine_learning.computer_vision.text_analytics.key_value_extraction.regex_module.expiry_period_calculator import calculate_expiry_date
#     except Exception:
#         # fallback: dynamic import using file path so this file can be run as a script
#         import importlib.util
#         import os
#         spec = importlib.util.spec_from_file_location(
#             "expiry_period_calculator",
#             os.path.join(os.path.dirname(__file__), "expiry_period_calculator.py"),
#         )
#         module = importlib.util.module_from_spec(spec)
#         spec.loader.exec_module(module)
#         calculate_expiry_date = module.calculate_expiry_date

# class GetDates:
#     def __init__(self):
#         self.mfg_phrase = "the manufacturing date of the product is"
#         self.exp_date_phrase = "the expiry date is"
#         self.exp_period_phrase = "the expiry period is"

#     def clean_date_string(self, date_str: str) -> str | None:
#         """Extract only dd-mm-yyyy format and remove any trailing strings"""
#         if not date_str:
#             return None
        
#         # Match dd-mm-yyyy pattern (exactly 10 characters) - removed word boundary at the end
#         match = re.search(r'(\d{2}-\d{2}-\d{4})', date_str)
#         if match:
#             cleaned_date = match.group(1)
#             # Validate length is exactly 10 characters (dd-mm-yyyy)
#             if len(cleaned_date) == 10:
#                 return cleaned_date
#         return None

#     def extract_dates(self, text: str) -> Tuple[str | None, str | None]:
#         if not isinstance(text, str) or not text.strip():
#             return None, None

#         mfg_date = None
#         exp_info = None

#         # 1. Extract Manufacturing Date (MFG)
#         mfg_parts = re.split(f'(?i){self.mfg_phrase}', text, maxsplit=1)
#         if len(mfg_parts) > 1:
#             mfg_content = mfg_parts[1].strip()
#             mfg_date = re.split(r'\band\b|\bthe expiry\b|,', mfg_content, maxsplit=1)[0].strip()
#             if mfg_date.lower() == 'nan':
#                 mfg_date = None
#             else:
#                 mfg_date = self.clean_date_string(mfg_date)

#         # 2. Extract Expiry Information (EXP)
#         exp_period_parts = re.split(f'(?i){self.exp_period_phrase}', text, maxsplit=1)
#         if len(exp_period_parts) > 1:
#             exp_content = exp_period_parts[1].strip()
#             words = exp_content.split()
            
#             if len(words) >= 2:
#                 number_str = words[0]
#                 unit_raw = words[1].lower()
                
#                 try:
#                     number_val = int(number_str)
#                     is_singular = (number_val == 1)
                    
#                     if unit_raw.startswith('day'):
#                         unit = 'day' if is_singular else 'days'
#                     elif unit_raw.startswith('month'):
#                         unit = 'month' if is_singular else 'months'
#                     elif unit_raw.startswith('year'):
#                         unit = 'year' if is_singular else 'years'
#                     elif unit_raw.startswith('week'):
#                         unit = 'week' if is_singular else 'weeks'
#                     else:
#                         unit = ''.join(filter(str.isalpha, unit_raw))

#                     exp_info = f"{number_str} {unit}"
#                 except ValueError:
#                     exp_info = exp_content
#             else:
#                 exp_info = exp_content
#         else:
#             exp_date_parts = re.split(f'(?i){self.exp_date_phrase}', text, maxsplit=1)
#             if len(exp_date_parts) > 1:
#                 exp_content = exp_date_parts[1].strip()
#                 exp_info = exp_content.split(' and')[0].strip().rstrip('.,')
#                 if exp_info.lower() == 'nan':
#                     exp_info = None
#                 else:
#                     # Check if it's a period format first
#                     period_match = re.match(r'(\d+)\s+(day|week|month|year)', exp_info, re.IGNORECASE)
#                     if period_match:
#                         number_str = period_match.group(1)
#                         unit_raw = period_match.group(2).lower()
                        
#                         try:
#                             number_val = int(number_str)
#                             is_singular = (number_val == 1)
                            
#                             if unit_raw == 'day':
#                                 unit = 'day' if is_singular else 'days'
#                             elif unit_raw == 'month':
#                                 unit = 'month' if is_singular else 'months'
#                             elif unit_raw == 'year':
#                                 unit = 'year' if is_singular else 'years'
#                             elif unit_raw == 'week':
#                                 unit = 'week' if is_singular else 'weeks'
                            
#                             exp_info = f"{number_str} {unit}"
#                         except ValueError:
#                             pass
#                     else:
#                         # Try to clean as date
#                         exp_info = self.clean_date_string(exp_info)
#                         if not exp_info:
#                             # If no valid date found, keep original for period checking
#                             exp_info = exp_content.split(' and')[0].strip().rstrip('.,')

#         return mfg_date, exp_info

#     def process_dates(self, ocr_result: List[str]) -> List[Any]:
#         if not ocr_result or not isinstance(ocr_result, list):
#             return [None, None, None]

#         text = str(ocr_result[0])
#         mfg_date_str, exp_info_str = self.extract_dates(text)
        
#         exp_date_val = None
#         exp_period_val = None
#         current_date = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        
#         # current_date = datetime.strptime(current_date, '%Y-%m-%dT%H:%M:%S')
#         # Validate MFG date length
#         if mfg_date_str and len(mfg_date_str) != 10:
#             mfg_date_str = None

#         # Handle expiry info
#         if exp_info_str:
#             # Check for expiry period patterns with fuzzy matching for OCR errors
#             if re.search(r'\b(months?|years?|days?|weeks?)\b', exp_info_str, re.IGNORECASE):
#                 exp_period_val = exp_info_str
#                 # Try to compute expiry date from MFG using expiry_period_calculator
#                 computed = None
#                 try:
#                     computed = calculate_expiry_date(mfg_date_str, exp_period_val)
#                 except Exception:
#                     computed = None

#                 # If calculator returns a valid date string, set exp_date_val and clear exp_period_val
#                 if isinstance(computed, str) and re.match(r"^\d{2}-\d{2}-\d{4}$", computed):
#                     exp_date_val = computed
#                     exp_period_val = None
#                 else:
#                     # If calculator returned None, keep exp_period_val as None
#                     if computed is None:
#                         exp_period_val = None
#             else:
#                 # Validate it's a proper date with length 10
#                 if exp_info_str and len(exp_info_str) == 10:
#                     exp_date_val = exp_info_str

#         # If only one date is present and it's greater than or equal to current date, treat it as expiry
#         # If only one date is present and it's less than current date, treat it as MFG
#         if mfg_date_str and not exp_info_str:
#             if len(mfg_date_str) == 10:
#                 try:
#                     parsed_date = datetime.strptime(mfg_date_str, '%d-%m-%Y')
#                     if parsed_date >= current_date:
#                         # This is actually an expiry date (future or today)
#                         exp_date_val = mfg_date_str
#                         mfg_date_str = None
#                     else:
#                         # This is a manufacturing date (past)
#                         exp_date_val = None
#                         # mfg_date_str remains as is
#                 except (ValueError, AttributeError):
#                     mfg_date_str = None

#         # # If only expiry date is present and it's less than current date, treat it as MFG
#         # if exp_date_val and not mfg_date_str and not exp_period_val:
#         #     try:
#         #         parsed_date = datetime.strptime(exp_date_val, '%d-%m-%Y')
#         #         if parsed_date < current_date:
#         #             mfg_date_str = exp_date_val
#         #             exp_date_val = None
#         #     except (ValueError, AttributeError):
#         #         pass

#         # Final validation: If both dates are present, ensure MFG < EXP
#         if mfg_date_str and exp_date_val:
#             try:
#                 mfg_parsed = datetime.strptime(mfg_date_str, '%d-%m-%Y')
#                 exp_parsed = datetime.strptime(exp_date_val, '%d-%m-%Y')
                
#                 if mfg_parsed > exp_parsed:
#                     # Manufacturing date is greater than expiry date - swap them
#                     mfg_date_str, exp_date_val = exp_date_val, mfg_date_str
#             except (ValueError, AttributeError):
#                 # If parsing fails, set both to None
#                 mfg_date_str = None
#                 exp_date_val = None
        
#         return [mfg_date_str, exp_date_val, exp_period_val]


# # --- Testing ---

# if __name__ == "__main__":
#     date_extractor = GetDates()

#     test_cases = [
#         # 1. MFG + period (months, plural)
#         ("The maximum retail price of the product is 45, the manufacturing date of the product is 07-10-2025, the expiry date is 3 months, and the batch number is M2L273B1.", "exp date with period")
#     ]
#     #     # 2. MFG + period (month, singular)
#     #     ("The manufacturing date of the product is 01-01-2025, the expiry date is 1 month", "MFG + 1 month"),
#     #     # 3. MFG + period (weeks, plural)
#     #     ("The manufacturing date of the product is 15-03-2024, the expiry date is 2 weeks", "MFG + 2 weeks"),
#     #     # 4. MFG + period (week, singular)
#     #     ("The manufacturing date of the product is 15-03-2024, the expiry date is 1 week", "MFG + 1 week"),
#     #     # 5. MFG + period (days, plural)
#     #     ("The manufacturing date of the product is 28-02-2024, the expiry date is 10 days", "MFG + 10 days"),
#     #     # 6. MFG + period (day, singular)
#     #     ("The manufacturing date of the product is 28-02-2024, the expiry date is 1 day", "MFG + 1 day"),
#     #     # 7. MFG + period (years, plural)
#     #     ("The manufacturing date of the product is 01-01-2025, the expiry date is 2 years", "MFG + 2 years"),
#     #     # 8. MFG + period (year, singular)
#     #     ("The manufacturing date of the product is 01-01-2025, the expiry period is 9 months", "MFG + 1 year"),
#     #     # 9. MFG + period (missing number, should default to 1)
#     #     ("The manufacturing date of the product is 10-10-2023, the expiry period is 11 months", "MFG + months (default 1)"),
#     #     # 10. MFG + period (hyphenated)
#     #     ("The manufacturing date of the product is 05-05-2022, the expiry date is 3-months", "MFG + 3-months (hyphenated)"),
#     #     # 11. MFG + period (unparsable)
#     #     ("The manufacturing date of the product is 01-01-2024, the expiry date is approx 6", "MFG + approx 6 (unparsable)"),
#     #     # 12. Only expiry date (future)
#     #     ("The manufacturing date of the product is nan, the expiry date is 31-12-2025", "Only expiry date (future)"),
#     #     # 13. Only expiry date (past)
#     #     ("The manufacturing date of the product is nan, the expiry date is 15-11-2024", "Only expiry date (past)"),
#     #     # 14. Only MFG date (future)
#     #     ("The manufacturing date of the product is 10-12-2025, the expiry date is nan", "Only MFG date (future)") ,
#     #     # 15. Only MFG date (past)
#     #     ("The manufacturing date of the product is 01-01-2024, the expiry date is nan", "Only MFG date (past)") ,
#     # ]

#     for idx, (ocr_text, label) in enumerate(test_cases, 1):
#         result = date_extractor.process_dates([ocr_text])
#         print(f"Test Case {idx} ({label}): {result}")


# Updated GetDates Class
import re
from typing import Tuple, List, Any
from datetime import datetime
from calendar import monthrange

def convert_month_year(date_str: str, is_mfg: bool) -> str | None:
    """
    Convert 'dec 2025', 'December 2025', '12/2025', '12-2025' into dd-mm-yyyy.
    
    MFG  -> first day of month (01)
    EXP  -> (last_day - 1)
    """
    if not isinstance(date_str, str):
        return None

    s = date_str.lower().strip().replace('.', '').replace('/', '-')

    # month names mapping
    month_map = {
        'jan':1, 'january':1,
        'feb':2, 'february':2,
        'mar':3, 'march':3,
        'apr':4, 'april':4,
        'may':5,
        'jun':6, 'june':6,
        'jul':7, 'july':7,
        'aug':8, 'august':8,
        'sep':9, 'sept':9, 'september':9,
        'oct':10, 'october':10,
        'nov':11, 'november':11,
        'dec':12, 'december':12,
    }

    # Format: december 2025, dec 2025, oct-2025, 12-2025, 2025-12
    parts = s.split('-')
    if len(parts) == 2:
        a, b = parts[0], parts[1]
        # check if month name
        if a in month_map and b.isdigit():
            month = month_map[a]
            year = int(b)
        elif b in month_map and a.isdigit():
            month = month_map[b]
            year = int(a)
        # check if numeric month/year
        elif a.isdigit() and b.isdigit():
            val_a, val_b = int(a), int(b)
            if len(a) == 4 and 1 <= val_b <= 12: # YYYY-MM
                year = val_a
                month = val_b
            elif len(b) == 4 and 1 <= val_a <= 12: # MM-YYYY
                month = val_a
                year = val_b
            elif len(a) <= 2 and len(b) == 2: # MM-YY
                month = val_a
                year = 2000 + val_b
            else:
                return None
        else:
            return None
    else:
        # fallback: "dec 2025" or "2025 dec" (space-separated)
        tokens = s.split()
        if len(tokens) == 2 and tokens[0] in month_map and tokens[1].isdigit():
            month = month_map[tokens[0]]
            year = int(tokens[1])
        elif len(tokens) == 2 and tokens[1] in month_map and tokens[0].isdigit():
            month = month_map[tokens[1]]
            year = int(tokens[0])
        else:
            return None

    # manufacturing → first day of month
    if is_mfg:
        return f"01-{month:02d}-{year}"

    # expiry → (last day of month) - 1
    last_day = monthrange(year, month)[1]
    exp_day = last_day - 1  # CUSTOM RULE

    return f"{exp_day:02d}-{month:02d}-{year}"

# import calculator
try:
    # preferred relative import when used as package
    from .expiry_period_calculator import calculate_expiry_date
except Exception:
    try:
        # try absolute package import (if src is on PYTHONPATH)
        from src.core.think.machine_learning.computer_vision.text_analytics.key_value_extraction.regex_module.expiry_period_calculator import calculate_expiry_date
    except Exception:
        # fallback: dynamic import using file path so this file can be run as a script
        import importlib.util
        import os
        spec = importlib.util.spec_from_file_location(
            "expiry_period_calculator",
            os.path.join(os.path.dirname(__file__), "expiry_period_calculator.py"),
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        calculate_expiry_date = module.calculate_expiry_date

class GetDates:
    def __init__(self):
        self.mfg_phrase = r'(?:the\s+)?(?:manufacturing\s+date|mfg\s*[\.\-]?\s*date|date\s+of\s+mfg|mfd|mfg|pkd)(?:\s+of\s+the\s+product)?\s*(?:is|:|=)?'
        self.exp_period_phrase = r'(?:the\s+)?(?:expiry\s+period|exp\s*[\.\-]?\s*period)(?:\s+of\s+the\s+product)?\s*(?:is|:|=)?'
        self.exp_date_phrase = r'(?:the\s+)?(?:expiry\s+date|exp\s*[\.\-]?\s*date|use\s+before|best\s+before|use\s+by|exp|expiry)(?:\s+of\s+the\s+product)?\s*(?:is|:|=)?'

    def clean_date_string(self, date_str: str) -> str | None:
        """Extract dd-mm-yyyy or yyyy-mm-dd format and normalize to dd-mm-yyyy"""
        if not date_str:
            return None
        
        # Match dd-mm-yyyy, dd/mm/yyyy, or dd.mm.yyyy pattern
        match_dmy = re.search(r'(\d{2}[-/\.]\d{2}[-/\.]\d{4})', date_str)
        if match_dmy:
            cleaned_date = match_dmy.group(1).replace('.', '-').replace('/', '-')
            if len(cleaned_date) == 10:
                return cleaned_date

        # Match yyyy-mm-dd, yyyy/mm/dd, or yyyy.mm.dd pattern
        match_ymd = re.search(r'(\d{4}[-/\.]\d{2}[-/\.]\d{2})', date_str)
        if match_ymd:
            raw_date = match_ymd.group(1).replace('.', '-').replace('/', '-')
            if len(raw_date) == 10:
                parts = raw_date.split('-')
                return f"{parts[2]}-{parts[1]}-{parts[0]}"

        return None

    def extract_dates(self, text: str) -> Tuple[str | None, str | None]:
        if not isinstance(text, str) or not text.strip():
            return None, None

        mfg_date = None
        exp_info = None

        # 1. Extract Manufacturing Date (MFG)
        mfg_parts = re.split(self.mfg_phrase, text, maxsplit=1, flags=re.IGNORECASE)
        if len(mfg_parts) > 1:
            mfg_content = mfg_parts[1].strip()
            mfg_date = re.split(r'\band\b|\bthe\b|\bexp\b|\bexpiry\b|,', mfg_content, maxsplit=1, flags=re.IGNORECASE)[0].strip()
            if mfg_date.lower() in ['nan', 'none', 'null', 'n/a', 'na', 'not available', 'not visible']:
                mfg_date = None
            else:
                cleaned = self.clean_date_string(mfg_date)
                if cleaned:
                    mfg_date = cleaned
                else:
                    # Try month-year conversion
                    mfg_date = convert_month_year(mfg_date, is_mfg=True)

        # 2. Extract Expiry Information (EXP)
        exp_period_parts = re.split(self.exp_period_phrase, text, maxsplit=1, flags=re.IGNORECASE)
        if len(exp_period_parts) > 1:
            exp_content = exp_period_parts[1].strip()
            exp_candidate = re.split(r'\band\b|,|\bthe\b|\bbatch\b', exp_content, maxsplit=1, flags=re.IGNORECASE)[0].strip().rstrip('.,')
            
            cleaned = self.clean_date_string(exp_candidate)
            if cleaned:
                exp_info = cleaned
            else:
                words = exp_content.split()
                
                if len(words) >= 2:
                    number_str = words[0]
                    unit_raw = words[1].lower()
                    
                    try:
                        number_val = int(number_str)
                        is_singular = (number_val == 1)
                        
                        if unit_raw.startswith('day'):
                            unit = 'day' if is_singular else 'days'
                        elif unit_raw.startswith('month'):
                            unit = 'month' if is_singular else 'months'
                        elif unit_raw.startswith('year'):
                            unit = 'year' if is_singular else 'years'
                        elif unit_raw.startswith('week'):
                            unit = 'week' if is_singular else 'weeks'
                        else:
                            unit = ''.join(filter(str.isalpha, unit_raw))

                        exp_info = f"{number_str} {unit}"
                    except ValueError:
                        exp_info = exp_candidate
                else:
                    exp_info = exp_candidate
        else:
            exp_date_parts = re.split(self.exp_date_phrase, text, maxsplit=1, flags=re.IGNORECASE)
            if len(exp_date_parts) > 1:
                exp_content = exp_date_parts[1].strip()
                exp_info = re.split(r'\band\b|,|\bthe\b|\bbatch\b', exp_content, maxsplit=1, flags=re.IGNORECASE)[0].strip().rstrip('.,')
                if exp_info.lower() in ['nan', 'none', 'null', 'n/a', 'na', 'not available', 'not visible']:
                    exp_info = None
                else:
                    # Check if it's a period format first
                    period_match = re.match(r'(\d+)\s+(day|week|month|year)', exp_info, re.IGNORECASE)
                    if period_match:
                        number_str = period_match.group(1)
                        unit_raw = period_match.group(2).lower()
                        
                        try:
                            number_val = int(number_str)
                            is_singular = (number_val == 1)
                            
                            if unit_raw == 'day':
                                unit = 'day' if is_singular else 'days'
                            elif unit_raw == 'month':
                                unit = 'month' if is_singular else 'months'
                            elif unit_raw == 'year':
                                unit = 'year' if is_singular else 'years'
                            elif unit_raw == 'week':
                                unit = 'week' if is_singular else 'weeks'
                            
                            exp_info = f"{number_str} {unit}"
                        except ValueError:
                            pass
                    else:
                        # Try to clean as date
                        # exp_info = self.clean_date_string(exp_info)
                        cleaned = self.clean_date_string(exp_info)
                        if cleaned:
                            exp_info = cleaned
                        else:
                            exp_info = convert_month_year(exp_info, is_mfg=False)
                        if not exp_info:
                            # If no valid date found, keep original for period checking
                            exp_info = exp_content.split(' and')[0].strip().rstrip('.,')

        return mfg_date, exp_info

    def process_dates(self, ocr_result: List[str]) -> List[Any]:
        if not ocr_result or not isinstance(ocr_result, list):
            return [None, None, None]

        text = str(ocr_result[0])
        mfg_date_str, exp_info_str = self.extract_dates(text)
        
        exp_date_val = None
        exp_period_val = None
        current_date = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        
        # current_date = datetime.strptime(current_date, '%Y-%m-%dT%H:%M:%S')
        # Validate MFG date length
        if mfg_date_str and len(mfg_date_str) != 10:
            mfg_date_str = None

        # Handle expiry info
        if exp_info_str:
            # Check for expiry period patterns with fuzzy matching for OCR errors
            if re.search(r'\b(months?|years?|days?|weeks?)\b', exp_info_str, re.IGNORECASE):
                exp_period_val = exp_info_str
                # Try to compute expiry date from MFG using expiry_period_calculator
                computed = None
                try:
                    computed = calculate_expiry_date(mfg_date_str, exp_period_val)
                except Exception:
                    computed = None

                # If calculator returns a valid date string, set exp_date_val and clear exp_period_val
                if isinstance(computed, str) and re.match(r"^\d{2}-\d{2}-\d{4}$", computed):
                    exp_date_val = computed
                    exp_period_val = None
                else:
                    # If calculator returned None, keep exp_period_val as None
                    if computed is None:
                        exp_period_val = None
            else:
                # Validate it's a proper date with length 10
                if exp_info_str and len(exp_info_str) == 10:
                    exp_date_val = exp_info_str

        # If only one date is present and it's greater than or equal to current date, treat it as expiry
        # If only one date is present and it's less than current date, treat it as MFG
        if mfg_date_str and not exp_info_str:
            if len(mfg_date_str) == 10:
                try:
                    parsed_date = datetime.strptime(mfg_date_str, '%d-%m-%Y')
                    if parsed_date >= current_date:
                        # This is actually an expiry date (future or today)
                        exp_date_val = mfg_date_str
                        mfg_date_str = None
                    else:
                        # This is a manufacturing date (past)
                        exp_date_val = None
                        # mfg_date_str remains as is
                except (ValueError, AttributeError):
                    mfg_date_str = None

        # If only expiry date is present and it's less than current date, treat it as MFG
        if exp_date_val and not mfg_date_str and not exp_period_val:
            try:
                parsed_date = datetime.strptime(exp_date_val, '%d-%m-%Y')
                if parsed_date < current_date:
                    mfg_date_str = exp_date_val
                    exp_date_val = None
            except (ValueError, AttributeError):
                pass

        # Final validation: If both dates are present, ensure MFG < EXP
        if mfg_date_str and exp_date_val:
            try:
                mfg_parsed = datetime.strptime(mfg_date_str, '%d-%m-%Y')
                exp_parsed = datetime.strptime(exp_date_val, '%d-%m-%Y')
                
                if mfg_parsed > exp_parsed:
                    # Manufacturing date is greater than expiry date - swap them
                    mfg_date_str, exp_date_val = exp_date_val, mfg_date_str
            except (ValueError, AttributeError):
                # If parsing fails, set both to None
                mfg_date_str = None
                exp_date_val = None
        
        return [mfg_date_str, exp_date_val, exp_period_val]


# --- Testing ---

if __name__ == "__main__":
    date_extractor = GetDates()

    test_cases = [
        # 1. MFG + period (months, plural)
        ("The maximum retail price of the product is 45, the manufacturing date of the product is 07-10-2025, the expiry date is 24 months, and the batch number is M2L273B1.", "exp date with period")
    ]
    #     # 2. MFG + period (month, singular)
    #     ("The manufacturing date of the product is 01-01-2025, the expiry date is 1 month", "MFG + 1 month"),
    #     # 3. MFG + period (weeks, plural)
    #     ("The manufacturing date of the product is 15-03-2024, the expiry date is 2 weeks", "MFG + 2 weeks"),
    #     # 4. MFG + period (week, singular)
    #     ("The manufacturing date of the product is 15-03-2024, the expiry date is 1 week", "MFG + 1 week"),
    #     # 5. MFG + period (days, plural)
    #     ("The manufacturing date of the product is 28-02-2024, the expiry date is 10 days", "MFG + 10 days"),
    #     # 6. MFG + period (day, singular)
    #     ("The manufacturing date of the product is 28-02-2024, the expiry date is 1 day", "MFG + 1 day"),
    #     # 7. MFG + period (years, plural)
    #     ("The manufacturing date of the product is 01-01-2025, the expiry date is 2 years", "MFG + 2 years"),
    #     # 8. MFG + period (year, singular)
    #     ("The manufacturing date of the product is 01-01-2025, the expiry period is 9 months", "MFG + 1 year"),
    #     # 9. MFG + period (missing number, should default to 1)
    #     ("The manufacturing date of the product is 10-10-2023, the expiry period is 11 months", "MFG + months (default 1)"),
    #     # 10. MFG + period (hyphenated)
    #     ("The manufacturing date of the product is 05-05-2022, the expiry date is 3-months", "MFG + 3-months (hyphenated)"),
    #     # 11. MFG + period (unparsable)
    #     ("The manufacturing date of the product is 01-01-2024, the expiry date is approx 6", "MFG + approx 6 (unparsable)"),
    #     # 12. Only expiry date (future)
    #     ("The manufacturing date of the product is nan, the expiry date is 31-12-2025", "Only expiry date (future)"),
    #     # 13. Only expiry date (past)
    #     ("The manufacturing date of the product is nan, the expiry date is 15-11-2024", "Only expiry date (past)"),
    #     # 14. Only MFG date (future)
    #     ("The manufacturing date of the product is 10-12-2025, the expiry date is nan", "Only MFG date (future)") ,
    #     # 15. Only MFG date (past)
    #     ("The manufacturing date of the product is 01-01-2024, the expiry date is nan", "Only MFG date (past)") ,
    # ]

    for idx, (ocr_text, label) in enumerate(test_cases, 1):
        result = date_extractor.process_dates([ocr_text])
        print(f"Test Case {idx} ({label}): {result}")