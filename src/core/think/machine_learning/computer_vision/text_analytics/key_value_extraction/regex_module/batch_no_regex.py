# import os
# import re
# # from dateutil import parser
# from datetime import datetime


# class GetBatchno:
#     def __init__(self):
#         self.bn_pattern = r"batch number(?:\s+in the retail)?(?:\s+is)?\s+([\w/-]+)" #r"batch number(?:\s+in the retail)?(?:\s+is)?\s+([\w-]+)"
#         self.alphanumeric_patterns = [
#             # Pattern for longer alphanumeric batch numbers (6-10 characters)
#             r'\b(?:B|LOT)?[A-Z0-9]{6,10}\b',

#             # Pattern for shorter batch numbers like GT147 or B037
#             r'\b(?:B|LOT)?[A-Z]{1,2}[0-9]{3,4}\b'
#         ]
#     def get_batch_number(self, text):
#         if 'batch number is' in text:
#             text_list = text.split('batch number is ')
#             batch_number_sentence = text_list[-1]
#             batch_number = batch_number_sentence
#             # print(batch_number)
#             if 'and' in batch_number:
#                 batch_number = batch_number.split('and')[0]
#             if ',' in batch_number:
#                 batch_number = batch_number.split(',')[0]
#             if '. ' in batch_number:
#                 batch_number = batch_number.split('. ')[0]
#         else:
#             text_list = text.split('batch number ')
#             batch_number_sentence = text_list[-1]
#             batch_number_sentence_list = batch_number_sentence.split(' ')
#             batch_number = batch_number_sentence_list[-1]
     
#         if batch_number[-1]=='.':
#             batch_number = batch_number[0:-1]
 
#         try:
#             if batch_number == 'nan ':
#                 batch_number = None
#             if batch_number == "not visible":
#                 batch_number = None
#             if batch_number == 'not available ' or batch_number == 'not available':
#                 batch_number = None
#             if batch_number == 'not':
#                 batch_number = None
#             if batch_number == None or len(batch_number)<=1: #or len(batch_number.split(' '))>1:
#                 batch_number = self.process_batch_number(batch_number_sentence)
#         except:
#             pass
#         return batch_number

#     def process_batch_number(self, text):
 
#         bn_match = re.search(self.bn_pattern, text, re.IGNORECASE)

#         if bn_match:
#             return bn_match.group(1)  # Return the captured batch number
#         combined_pattern = '|'.join(self.alphanumeric_patterns)
#         matches = re.findall(combined_pattern, text)
#         # Filter matches to ensure they contain both uppercase letters and numbers
#         valid_matches = [match for match in matches if re.search(r'\d', match) and re.search(r'[A-Z]', match)]
#         # Return the longest valid match, or None if no valid matches
#         return max(valid_matches, key=len) if valid_matches else None




# if __name__ == "__main__":

#     text = "The maximum retail price is Rs 115.00, the manufacturing date is 20.11.2024, and the expiry date is 19.11.2025 and the batch number is not available."
#     extractor = GetBatchno()
#     print(extractor.get_batch_number(text))

# santanu
import re

class GetBatchno:
    def __init__(self):
        """
        Initializes the GetBatchno class.
        """
        # The specific phrase to split the string on, case-insensitively.
        self.split_phrase = "batch number is"

        # Fallback regex patterns for cases where the split_phrase isn't found.
        self.fallback_patterns = [
            re.compile(r'\b(?:Batch(?:\s*No)?|B\.\s*No|Lot(?:\s*No)?)\.?\s*:?\s+([\w/.-]+)\b', re.IGNORECASE),
            re.compile(r'\b([A-Z0-9/.-]{4,15})\b')
        ]

    def get_batch_number(self, text: str) -> str | None:
        """
        Extracts the batch number from text.
        
        It first tries to split the string by "batch number is" and returns
        everything after it. If that phrase is not found, it uses fallback
        regex patterns to find the batch number.
        """
        if not isinstance(text, str) or not text.strip():
            return None

        # Use a case-insensitive split.
        parts = re.split(f'(?i){self.split_phrase}', text, maxsplit=1)

        if len(parts) > 1:
            raw_batch = parts[1].strip()
            # Truncate at comma, period, or starting of other keywords/clauses
            clean_batch = re.split(r'[,.\n]|\band\b|\bthe\b|\bmfg\b|\bexp\b|\bprice\b', raw_batch, maxsplit=1, flags=re.IGNORECASE)[0].strip()
            clean_batch = clean_batch.rstrip('.,;')
            if clean_batch.lower() in ['nan', 'none', 'null', 'n/a', 'na', 'not available', 'not visible', 'nil', '']:
                return None
            return clean_batch if clean_batch else None

        # --- Fallback logic if "batch number is" is not found ---
        for pattern in self.fallback_patterns:
            matches = pattern.findall(text)
            for match in matches:
                if any(char.isalpha() for char in match) and any(char.isdigit() for char in match):
                    clean_match = match.strip().rstrip('.,;')
                    if clean_match.lower() not in ['nan', 'none', 'null', 'n/a', 'na', 'not available', 'not visible', 'nil', '']:
                        return clean_match
        
        return None

# Example usage to demonstrate the class functionality
if __name__ == "__main__":
    extractor = GetBatchno()

    # --- Test Cases ---
    
    # User's original test case
    text1 = "The maximum retail price is Rs 115.00, the manufacturing date is 20.11.2024, and the expiry date is 19.11.2025 and the batch number is not available."
    print(f"Text: \"{text1}\"")
    print(f"Extracted Batch No: {extractor.get_batch_number(text1)}\n") # Expected: None

    # Standard test case with 'Batch No:'
    text2 = "Some random text here. Batch No: AB12345/C. More text."
    print(f"Text: \"{text2}\"")
    print(f"Extracted Batch No: {extractor.get_batch_number(text2)}\n") # Expected: AB12345/C

    # Test case with 'B. No.' and different casing
    text3 = "mfg date: 10/2025, b. no. is T-9876."
    print(f"Text: \"{text3}\"")
    print(f"Extracted Batch No: {extractor.get_batch_number(text3)}\n") # Expected: T-9876

    # Test case without explicit keywords (fallback pattern)
    text4 = "EXP 12/26 MRP Rs. 99.00 GT1478"
    print(f"Text: \"{text4}\"")
    print(f"Extracted Batch No: {extractor.get_batch_number(text4)}\n") # Expected: GT1478

    # Test case where a potential match should be ignored (e.g., a date)
    text5 = "Product manufactured on 2024 with code XYZ."
    print(f"Text: \"{text5}\"")
    print(f"Extracted Batch No: {extractor.get_batch_number(text5)}\n") # Expected: None