from src.data_handler.azure_blob_storage import AzureBlobUploader


class StorageRouter:
    """
    Azure-only storage router for Reliance and Shipsy.
    Both clients upload to the same Azure Blob container.
    """

    def __init__(self):
        self.azure = AzureBlobUploader()

    def upload_image(self, image_or_bytes, key: str, client_name: str, user_id: str = None):
        return self.azure.upload_image_to_blob(image_or_bytes, key)
