import io
import httpx
from PIL import Image


class Vllm_inference:

    def __init__(
        self,
        api_url,
        image_dir=None,
        output_csv=None
    ):
        self.api_url = api_url

    def initialize(self):
        print(
            f"Using hosted model API: {self.api_url}"
        )

    async def process_batch(
        self,
        image_data,
        query,
        max_tokens=None
    ):

        buffer = io.BytesIO()

        image_data.save(
            buffer,
            format="JPEG"
        )

        buffer.seek(0)

        async with httpx.AsyncClient(
            timeout=120
        ) as client:

            response = await client.post(
                f"{self.api_url}/predict",
                files={
                    "file": (
                        "image.jpg",
                        buffer,
                        "image/jpeg"
                    )
                },
                data={
                    "query": query,
                    "max_tokens": max_tokens or 150
                }
            )

        response.raise_for_status()

        result = response.json()

        return [result["result"]]