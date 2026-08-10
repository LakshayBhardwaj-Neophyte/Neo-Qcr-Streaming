from datetime import datetime
import re

class Datetime:
    


    def format_datetime_array(self, dates):
        """
        Convert various datetime string formats to a unified datetime object.

        Args:
        - dates (list): A list of datetime strings or datetime objects.

        Returns:
        - list: A list of datetime objects or None for invalid inputs.
        """
        formatted_dates = []
        patterns = [
            r"^(\d{2})-(\d{2})-(\d{4})$",  # DD-MM-YYYY
            r"^(\d{2})/(\d{2})/(\d{4})$",  # DD/MM/YYYY
            r"^(\d{4})-(\d{2})-(\d{2})$",  # YYYY-MM-DD
            r"^(\d{4})/(\d{2})/(\d{2})$",  # YYYY/MM/DD
            r"^(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2}):(\d{2})$",  # YYYY-MM-DD HH:MM:SS
        ]
        
        format_patterns = {
            patterns[0]: "%d-%m-%Y",
            patterns[1]: "%d/%m/%Y",
            patterns[2]: "%Y-%m-%d",
            patterns[3]: "%Y/%m/%d",
            patterns[4]: "%Y-%m-%d %H:%M:%S"
        }

        for date_input in dates:
            try:
                if isinstance(date_input, datetime):
                    # If already a datetime object, append it as-is
                    formatted_dates.append(date_input)
                    continue

                if isinstance(date_input, str):
                    for pattern in patterns:
                        match = re.match(pattern, date_input)
                        if match:
                            try:
                                # Parse the date string into a datetime object
                                parsed_date = datetime.strptime(date_input, format_patterns[pattern])
                                formatted_dates.append(parsed_date)
                                break
                            except ValueError:
                                continue
                    else:
                        formatted_dates.append(None)  # No pattern matched
                else:
                    formatted_dates.append(None)  # Invalid input type
            except Exception:
                formatted_dates.append(None)  # Catch unexpected errors

        return formatted_dates

        
        
        
        
    def convert_to_datetime(self, date_list):
        converted_dates = []
        for date in date_list:
            if date:
                try:
                    converted_date = datetime.strptime(date, "%d-%m-%Y")
                    converted_dates.append(converted_date)
                except ValueError as e:
                    print(f"Error converting date '{date}': {e}")
                    converted_dates.append(None)
            else:
                converted_dates.append(None)
        return converted_dates


