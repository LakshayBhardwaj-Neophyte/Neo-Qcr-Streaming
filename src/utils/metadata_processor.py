from datetime import datetime
class MetadataProcessor:
    @staticmethod
    def create_metadata( mrp, mfg_exp_dates_str, batch_no):
        """
        Create metadata dictionary from the provided information.
        
        Args:
            mrp (str): Maximum retail price extracted.
            mfg_exp_dates_str (list): Manufacturing and expiry dates as strings.
            batch_no (str): Batch number extracted.
            processing_start (float): Start time of processing.
            ocr_result (list): OCR results from inference.

        Returns:
            dict: Metadata dictionary with processed information.
        """
        # return {
        #     'mrp': mrp,
        #     'mfg_date': mfg_exp_dates_str[0] if mfg_exp_dates_str else None,
        #     'expiry_date': mfg_exp_dates_str[1] if len(mfg_exp_dates_str) > 1 and mfg_exp_dates_str[0] != mfg_exp_dates_str[1] else None,
        #     'batch_no': batch_no
            
        # }

        # if not isinstance(mfg_exp_dates_str, list) or len(mfg_exp_dates_str) < 2:
        #     mfg_date = None
        #     expiry_date = None
        # else:
        #     mfg_date = mfg_exp_dates_str[0]
        #     expiry_date = mfg_exp_dates_str[1]
        
        # return {
        #     'mrp': mrp,
        #     'mfg_date': mfg_date,
        #     'expiry_date': expiry_date,
        #     'batch_no': batch_no,
        # }
        
        mfg_date = mfg_exp_dates_str[0] if len(mfg_exp_dates_str) > 0 else None
        expiry_date = mfg_exp_dates_str[1] if len(mfg_exp_dates_str) > 1 else None
        expiry_period = mfg_exp_dates_str[2] if len(mfg_exp_dates_str) > 2 else None

        # Determine which expiry value to use
        final_expiry_value = expiry_date if expiry_date is not None else expiry_period

        return {
            'mrp': mrp,
            'mfg_date': mfg_date,
            'expiry_date': final_expiry_value,  # This field will now hold either a date or a period
            'batch_no': batch_no,
        }
     
    @staticmethod
    def create_env_data( store_id, device_id, user_id, env_id):
        """
        Create environment data dictionary.

        Args:
            store_id (str): Store ID.
            warehouse_id (str): Warehouse ID.
            device_id (str): Device ID.
            user_id (str): User ID.
            env_id (str): Environment ID.
            original_img_url (str): URL of the original image.
            img_url (str): URL of the processed image.

        Returns:
            dict: Environment data dictionary with processed information.
        """
        return {
            'timestamp': datetime.now(),
            'store_id': store_id,
            'device_id': device_id,
            'user_id': user_id,
            'env_id': env_id,
            'sap_status':False,
            'present': True,
            '__v': 1
        } 