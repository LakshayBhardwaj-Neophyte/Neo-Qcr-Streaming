import re 


class Get_mrp:
    
    def __init__(self):
        # Regular expression pattern to match various MRP patterns
        self.mrp_pattern = re.compile(r'''
            (?:
                # 1. Phrases like "maximum retail price of the product is 345.0" or "mrp is 345"
                (?:the\s+)?(?:maximum\s+retail\s+price|retail\s+price|mrp|price)\s*(?:of\s+the\s+product)?\s*(?:is|:|=)?\s*
                (?:Rs\.?|₹|\$|\u20ac)?\s*
                [\*\$₹\-]?\s*
                (\d{1,5}(?:,\d{3})*(?:\.\d{1,2})?)
                (?=[,.\s]|$|\band\b)
            |
                # 2. Standard MRP: MRP Rs. 179.00 / MRP 179.00 / MRP: 179.00 / Price: 180
                (?:MRP|Price)\s*(?:incl\.?\s*of\s*all\s*taxes)?\s*[:\-=]?\s*
                (?:Rs\.?|₹|\$|\u20ac)?\s*
                (?:Rs\.?|₹)?\s*
                [\*\$₹\-]?\s*
                (\d{1,5}(?:,\d{3})*(?:\.\d{1,2})?)
                (?:/[\d\.]+)?
            |
                # 3. Currency symbol followed by amount: Rs. 179.00 / ₹ 179 / $50
                (?:Rs\.?|₹|\$|\u20ac)\s*(\d{1,5}(?:,\d{3})*(?:\.\d{1,2})?)
            |
                # 4. Amount followed by MRP or /- : 180 MRP / 180/-
                (\d{1,5}(?:,\d{3})*(?:\.\d{1,2})?)\s*(?:MRP|/-)
            |
                # 5. Star or asterisk followed by MRP amount: * 179.00
                \*\s*(\d{1,5}(?:,\d{3})*(?:\.\d{1,2})?)\s*(?:MRP)?
            )
            ''', re.VERBOSE | re.IGNORECASE)

    def process_mrp(self, ocr_text: str) -> float | None:
        """
        Extracts the maximum MRP value from OCR text using predefined regex patterns.
        """
        if not isinstance(ocr_text, str) or not ocr_text.strip():
            return None

        # Find all matches in the OCR text
        matches = self.mrp_pattern.findall(ocr_text)

        mrp_values = []
        for match in matches:
            for value in match:
                if value:
                    cleaned_value = re.sub(r'[^\d\.]', '', value).replace(',', '')
                    if cleaned_value:
                        try:
                            val = float(cleaned_value)
                            mrp_values.append(val)
                        except ValueError:
                            pass

        if mrp_values:
            return max(mrp_values)
        return None


