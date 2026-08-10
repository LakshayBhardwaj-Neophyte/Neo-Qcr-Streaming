from transformers import Qwen2VLForConditionalGeneration, AutoProcessor
from qwen_vl_utils import process_vision_info
import torch
from PIL import Image, ImageDraw, ImageFont
import glob
import time
import os
import json

class Qwen:
    def __init__(self, local_model_path, model_name="Qwen/Qwen2-VL-2B-Instruct"):
        """
        Initializes the Inference Machine with the model and processor.

        Args:
            local_model_path (str): The local path of the model to load.
            model_name (str): The name of the model to load.
        """
        # Load the model for conditional generation
        self.model = Qwen2VLForConditionalGeneration.from_pretrained(
            local_model_path,
            torch_dtype=torch.bfloat16,
            device_map="cuda",
            # low_cpu_mem_usage=True
        )

        # Load the processor for handling images and text
        self.processor = AutoProcessor.from_pretrained(local_model_path)
        
        # For output saving
        self.font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        self.font_size = 30
        self.current_output_dir = None
        self.current_json_output_dir = None
        self.model_name = model_name

    def wrap_text(self, text, font, max_width):
        lines = []
        for line in text.split('\n'):
            words = line.split()
            current_line = ""
            for word in words:
                test_line = f"{current_line} {word}".strip()
                bbox = font.getbbox(test_line)
                line_width = bbox[2] - bbox[0]
                if line_width <= max_width:
                    current_line = test_line
                else:
                    lines.append(current_line)
                    current_line = word
            lines.append(current_line)
        return lines

    def resize_image(self, img, max_size=1024):
        width, height = img.size
        if max(width, height) <= max_size:
            return img  # Return the original image without resizing
        scaling_factor = max_size / max(width, height)
        new_width = int(width * scaling_factor)
        new_height = int(height * scaling_factor)
        return img.resize((new_width, new_height))

    def process_image(self, img: Image.Image, prompt: str):
        """
        Processes an image and generates output based on a prompt.

        Args:
            img (Image.Image): The input image.
            prompt (str): The textual prompt for the model.

        Returns:
            str: The generated text result from the model.
        """
        # Prepare messages for processing
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "image", "image": img},
                    {"type": "text", "text": prompt}
                ],
            }
        ]

        # Generate text input for the model
        text = self.processor.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True
        )

        # Process vision inputs (image/video) for the model
        image_inputs, video_inputs = process_vision_info(messages)
        inputs = self.processor(
            text=[text],
            images=image_inputs,
            videos=video_inputs,
            padding=True,
            return_tensors="pt",
        )
        inputs = inputs.to("cuda")  # Move to GPU

        # Run inference with the model

        generated_ids = self.model.generate(**inputs, max_new_tokens=128)
        generated_ids_trimmed = [
            out_ids[len(in_ids):] for in_ids, out_ids in zip(inputs.input_ids, generated_ids)
        ]
        output_text = self.processor.batch_decode(
            generated_ids_trimmed, skip_special_tokens=True, clean_up_tokenization_spaces=False
        )

        # Free GPU memory
        # del inputs, image_inputs, video_inputs, generated_ids, generated_ids_trimmed
        torch.cuda.empty_cache()

        return output_text

    def run(self, img, prompt: str):
        """
        Loads an image and processes it with the prompt.

        Returns:
            Tuple[List[str], Image.Image]: (model output, resized image actually used)
        """
        # Log what we got
        if isinstance(img, Image.Image):
            print(f"[Qwen] received image size: {img.size} (width x height)")

        # Resize (copy)
        img_resized = self.resize_image(img, max_size=1024)
        if isinstance(img_resized, Image.Image):
            print(f"[Qwen] resized image size:  {img_resized.size} (width x height)")

        # IMPORTANT: run inference on the RESIZED image
        result = self.process_image(img_resized, prompt)

        # IMPORTANT: return BOTH so caller can use the exact image the model saw
        return result, img_resized


    # def run(self, img, prompt: str):
    #     """
    #     Loads an image and processes it with the prompt.

    #     Args:
    #         img (Image.Image): The input image.
    #         prompt (str): The prompt for processing the image.

    #     Returns:
    #         str: The final result after processing the image and generating output.
    #     """
    #     if isinstance(img, Image.Image):
    #         print(f"[Qwen] received image size: {img.size} (width x height)")

    #     # resize (copy)
    #     img_resized = self.resize_image(img, max_size=1024)

    #     # LOG what we actually send to the model
    #     if isinstance(img_resized, Image.Image):
    #         print(f"[Qwen] resized image size:  {img_resized.size} (width x height)")
    #     result = self.process_image(img, prompt)
    #     return result

    def save_outputs(self, img, output_text, img_path):
        image_file_name = os.path.splitext(os.path.basename(img_path))[0]
        if self.current_json_output_dir is None or self.current_output_dir is None:
            # Setup output dirs if not set
            base_dir = "./outputs"
            self.current_output_dir = os.path.join(base_dir, "output_images", self.model_name)
            self.current_json_output_dir = os.path.join(base_dir, "json_output", self.model_name)
            os.makedirs(self.current_output_dir, exist_ok=True)
            os.makedirs(self.current_json_output_dir, exist_ok=True)

        json_output_path = os.path.join(self.current_json_output_dir, f"{image_file_name}.json")
        with open(json_output_path, 'w') as json_file:
            json.dump({"text": output_text}, json_file, indent=4)

        font = ImageFont.truetype(self.font_path, size=self.font_size)
        wrapped_text = self.wrap_text(output_text, font, img.width)
        text_height = sum([font.getbbox(line)[3] - font.getbbox(line)[1] for line in wrapped_text])
        total_height = img.height + text_height + 20

        new_image = Image.new("RGB", (img.width, total_height), (255, 255, 255))
        new_image.paste(img, (0, 0))

        draw = ImageDraw.Draw(new_image)
        y_text = img.height + 10
        for line in wrapped_text:
            draw.text((10, y_text), line, font=font, fill="black")
            y_text += font.getbbox(line)[3] - font.getbbox(line)[1]

        output_image_path = os.path.join(self.current_output_dir, os.path.basename(img_path))
        new_image.save(output_image_path)

