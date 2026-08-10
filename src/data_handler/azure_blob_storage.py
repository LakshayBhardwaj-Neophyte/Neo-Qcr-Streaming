from azure.storage.blob import BlobServiceClient
import traceback
from PIL import Image
from io import BytesIO
import os 


class AzureBlobUploader:
    def __init__(self):
        self.connection_string = os.getenv('connection_string')
        self.container_name = os.getenv('container_name')
        self.blob_service_client = BlobServiceClient.from_connection_string(self.connection_string)
        self.container_client = self.blob_service_client.get_container_client(self.container_name)

    def upload_image_to_blob(self, image_data, blob_name):
        try:
            print(f'blob name:{blob_name}')
            # Upload image directly from binary data
            

            print(self.blob_service_client.account_name)
            blob_client = self.container_client.get_blob_client(blob_name)
            if isinstance(image_data, Image.Image):  # If it's a PIL image
                img_bytes = BytesIO()
                image_data.save(img_bytes, format="JPEG")  # Convert to bytes
                img_bytes.seek(0)
                image_data = img_bytes
            blob_client.upload_blob(image_data, overwrite=True)

            # Get the blob URL
            
            blob_url = f"https://{self.blob_service_client.account_name}.blob.core.windows.net/{self.container_name}/{blob_name}"
            print("✅ Upload Successful!")
            print("🌍 Blob URL:", blob_url)

            return blob_url

        except Exception as e:
            print("❌ Error:", e)
            traceback.print_exc()

# # Example usage
# if __name__ == "__main__":

#     from dotenv import load_dotenv

#     # Load environment variables
#     load_dotenv('src/configs/.env')

#     # Azure Storage Configuration
#     CONNECTION_STRING = ""
#     CONTAINER_NAME = "test"

#     # Initialize the class
#     uploader = AzureBlobUploader()

#     # Read image as binary data
#     FILE_PATH = '/home/ujjwal/projects/ambient-machine/src/experiments/data/smart_visioncave_jetson_image2_cropped.jpg'
#     with open(FILE_PATH, "rb") as f:
#         image_data = f.read()

#     BLOB_NAME = "sdk/image.jpg"

#     # Run the upload function
#     uploader.upload_image_to_blob(image_data, BLOB_NAME)

#     # TODO: PRE SIGNED URL TEST
