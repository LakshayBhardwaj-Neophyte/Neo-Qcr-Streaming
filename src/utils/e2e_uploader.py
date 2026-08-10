# import os, traceback
# from io import BytesIO
# from PIL import Image
# import boto3
# from botocore.client import Config

# class E2EUploader:
#     def __init__(self):
#         self.endpoint = os.getenv("endpoint_url")      
#         self.access   = os.getenv("access_key")
#         self.secret   = os.getenv("secret_key")
#         self.bucket   = os.getenv("bucket_name")            
#         self.prefix   = os.getenv("dir_name", "")     
#         self.public   = os.getenv("E2E_PUBLIC_BASE", self.endpoint.rstrip("/"))

#         self.s3 = boto3.client(
#             "s3",
#             endpoint_url=self.endpoint,
#             aws_access_key_id=self.access,
#             aws_secret_access_key=self.secret,
#             config=Config(signature_version="s3v4"),
#             region_name=os.getenv("E2E_REGION", "auto"),
#         )

#     # def upload_image(self, image_data, key: str):
#     #     try:
#     #         # normalize to bytes
#     #         if isinstance(image_data, Image.Image):
#     #             buf = BytesIO()
#     #             image_data.save(buf, format="JPEG")
#     #             buf.seek(0)
#     #             data = buf
#     #         elif hasattr(image_data, "read"):
#     #             data = image_data
#     #         else:
#     #             data = BytesIO(image_data)

#     #         full_key = f"{self.prefix}/{key}" if self.prefix else key
#     #         self.s3.upload_fileobj(data, self.bucket, full_key, ExtraArgs={"ContentType": "image/jpeg"})
#     #         # path-style URL (works on most S3-compatible endpoints)
#     #         return f"{self.public}/{self.bucket}/{full_key}"
#     def upload_image(self, image_data, key: str):
#         try:
#             # normalize to bytes
#             if isinstance(image_data, Image.Image):
#                 buf = BytesIO()
#                 image_data.save(buf, format="JPEG")
#                 buf.seek(0)
#                 data = buf
#             elif hasattr(image_data, "read"):
#                 data = image_data
#             else:
#                 data = BytesIO(image_data)

#             # use flat key (ignore self.prefix)
#             full_key = key

#             self.s3.upload_fileobj(
#                 data,
#                 self.bucket,
#                 full_key,
#                 ExtraArgs={"ContentType": "image/jpeg"}
#             )

#             # return a path-style URL
#             return f"{self.public}/{self.bucket}/{full_key}"

#         except Exception as e:
#             print("❌ E2E upload error:", e)
#             traceback.print_exc()
#             return None


import os, traceback
from io import BytesIO
from PIL import Image
import boto3
from botocore.client import Config

class E2EUploader:
    def __init__(self):
        self.endpoint = os.getenv("endpoint_url")
        self.access   = os.getenv("access_key")
        self.secret   = os.getenv("secret_key")
        self.bucket   = os.getenv("bucket_name")
        self.prefix   = os.getenv("dir_name", "")
        self.public   = os.getenv("E2E_PUBLIC_BASE", self.endpoint.rstrip("/"))

        self.s3 = boto3.client(
            "s3",
            endpoint_url=self.endpoint,
            aws_access_key_id=self.access,
            aws_secret_access_key=self.secret,
            config=Config(signature_version="s3v4"),
            region_name=os.getenv("E2E_REGION", "auto"),
        )

    def upload_image(self, image_data, key: str, client_name: str = None, user_id: str = None):
        try:
            # normalize to bytes
            if isinstance(image_data, Image.Image):
                buf = BytesIO()
                image_data.save(buf, format="JPEG")
                buf.seek(0)
                data = buf
            elif hasattr(image_data, "read"):
                data = image_data
            else:
                data = BytesIO(image_data)

            # Organize by Neoqcr/client_name/user_id if provided
            if client_name:
                if user_id:
                    full_key = f"Neoqcr/{client_name}/{user_id}/{key}"
                else:
                    full_key = f"Neoqcr/{client_name}/{key}"
            else:
                full_key = key

            # Upload to S3
            self.s3.upload_fileobj(
                data,
                self.bucket,
                full_key,
                ExtraArgs={"ContentType": "image/jpeg"}
            )

            # Permanent public URL (path-style)
            public_url = f"{self.public}/{self.bucket}/{full_key}"

            # 7-day CDN signed URL
            presigned_url = self.s3.generate_presigned_url(
                "get_object",
                Params={"Bucket": self.bucket, "Key": full_key},
                ExpiresIn=7 * 24 * 60 * 60  # 7 days
            )

            return {
                "public_url": public_url,
                "cdn_url": presigned_url
            }

        except Exception as e:
            print("❌ E2E upload error:", e)
            traceback.print_exc()
            return None

