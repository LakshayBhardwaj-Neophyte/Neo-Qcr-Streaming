import numpy as np
import cv2
from PIL import Image
import cv2
import os
# import boto3
import base64
# from botocore.exceptions import NoCredentialsError, PartialCredentialsError
# from src.data_handler.s3 import S3Bucket
from src.data_handler.azure_blob_storage import AzureBlobUploader


print('...')

# S3Bucket.connect_s3()
qcr_storage = AzureBlobUploader()

class ImageProcessor:

    @staticmethod
    def process_image(base64_image):
        """
        Converts a base64-encoded image string into a PIL image object.

        Steps:
        1. Decodes the base64 image string into binary data.
        2. Converts the binary data into a NumPy array.
        3. Decodes the NumPy array into an OpenCV image.
        4. Converts the OpenCV image into a PIL image object.

        Args:
            base64_image (str): The base64-encoded image string.

        Returns:
            PIL.Image.Image: The decoded image as a PIL object.

        Raises:
            ValueError: If the base64 string is invalid or decoding fails.
        """
        try:
            image_data = base64.b64decode(base64_image)
            np_array = np.frombuffer(image_data, np.uint8)
            image = cv2.imdecode(np_array, cv2.IMREAD_COLOR)
            image_pil = Image.fromarray(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))
            if image_pil is None:
                raise ValueError("Failed to decode image. Ensure the input data is valid image data.")
            return image_pil
        except Exception as e:
            print(f"Error decoding base64 image: {e}")
            raise ValueError("Invalid base64 image data")
        
        
    def extract_img_details(image_name):
        
        img_details = image_name.split('_')
        store_id, user_id, divice_id,  img_name = img_details
        print(img_details)
        
        if img_name.endswith(".jpg"):
            img_name = img_name.split('.jpg')
        print(img_name)
            
        return store_id, user_id, divice_id, img_name[0]
    
 

    @staticmethod
    def save_image( image, image_path, storage_flag='local'):
        """
        Saves the given image to the specified location (Local or S3) based on the storage_flag.

        Parameters:
        image (numpy.ndarray): The image to save (in BGR format).
        image_path (str): The path where the image should be saved.
        storage_flag (str): 'local' for local saving, 's3' for saving to AWS S3.
        s3_bucket (str): The name of the S3 bucket (required if storage_flag is 's3').
        s3_key (str): The S3 object key (required if storage_flag is 's3').

        Returns:
        str: The saved image path or the S3 URL.
        """
        try:
            # Check if the image is valid
            if image is None:
                raise ValueError("The provided image is invalid or empty.")
            
            # If storage_flag is 'local', save locally
            if storage_flag == 'local':
                # Check if the file path's directory exists
                directory = os.path.dirname(image_path)
                if directory and not os.path.exists(directory):
                    os.makedirs(directory)

                # Save the image locally
                success = cv2.imwrite(image_path, image)
                
                if success:
                    print(f"Image successfully saved at: {image_path}")
                    return image_path
                else:
                    raise IOError("Failed to save the image locally. Check the image format or path.")
            
            # If storage_flag is 's3', upload to S3
            elif storage_flag == 'azure_blob':
                # Upload to S3
                try:
                    # url = S3Bucket.upload_img_to_s3_n_get_ps_url(image, image_path).split('?')[0]
                    url = qcr_storage.upload_image_to_blob(image, image_path )
                    print(f"Image successfully uploaded to Azure Blob Storage: {url}")
                    return url
                except Exception as e:
                    print(f"Azure credentials / upload error: {e}")
                    raise

            else:
                raise ValueError("Invalid storage_flag. Use 'local' or 's3'.")
        
        except ValueError as ve:
            print(f"Error: {ve}")
        except IOError as ioe:
            print(f"Error: {ioe}")
        except Exception as e:
            print(f"Unexpected error: {e}")
        
        return None
    
    
    @staticmethod
    def plot_ocr_output(image: np.ndarray, blob_name : str , inference_result: str):
        
        """
        Adds bottom padding to an image with white color that automatically adjusts
        according to the OCR text, overlays the inference_result as multi-line text
        (wrapping automatically if needed), and saves the image to Azure Blob Storage.

        Parameters:
        - image (PIL.Image.Image or np.ndarray): The input image.
        - inference_result (str): The OCR output text to be plotted on the image.
        - blob_name (str): Name of the image in Azure Blob Storage.
        - qcr_storage (AzureStorageHandler): Azure storage handler to upload the image.

        Returns:
        - URL of the uploaded image.
        """
        print('Plotted image stored to Azure...')

        # Ensure image is a NumPy array (Convert from PIL if needed)
        if isinstance(image, Image.Image):  
            image = np.array(image)  # Convert PIL image to NumPy

        # Font settings for text overlay
        font = cv2.FONT_HERSHEY_SIMPLEX
        font_scale = 1
        thickness = 2
        margin_x = 10   # Left/right margin for text
        margin_y = 20   # Top margin within padding

        # Available width for text (image width minus horizontal margins)
        available_width = image.shape[1] - 2 * margin_x
        
        # Ensure inference_result is a string
        text_to_display = str(inference_result)
        words = text_to_display.split()
        lines = []
        current_line = ""

        # Text wrapping logic
        for word in words:
            test_line = current_line + (" " if current_line else "") + word
            (text_width, text_height), baseline = cv2.getTextSize(test_line, font, font_scale, thickness)
            
            if text_width > available_width:
                if current_line == "":  # Single word exceeds width, add anyway
                    lines.append(test_line)
                    current_line = ""
                else:
                    lines.append(current_line)
                    current_line = word
            else:
                current_line = test_line

        if current_line:
            lines.append(current_line)

        # Compute padding height
        (_, line_height), baseline = cv2.getTextSize("A", font, font_scale, thickness)
        line_spacing = line_height + baseline + 5  # Additional spacing between lines
        required_padding = margin_y + len(lines) * line_spacing + margin_y  # Padding size

        # Add padding at the bottom (white color)
        padded_image = np.pad(image, ((0, required_padding), (0, 0), (0, 0)), mode='constant', constant_values=255)

        # Write each line in the padded area
        for i, line in enumerate(lines):
            y = image.shape[0] + margin_y + i * line_spacing
            cv2.putText(padded_image, line, (margin_x, y), font, font_scale, (0, 0, 0), thickness, cv2.LINE_AA)

        # Convert NumPy image back to PIL format for Azure upload
        padded_pil_image = Image.fromarray(padded_image)

        # Upload to Azure Blob Storage
        url = qcr_storage.upload_image_to_blob(padded_pil_image, blob_name)
        print(f"✅ Image successfully uploaded to Azure Blob Storage: {url}")