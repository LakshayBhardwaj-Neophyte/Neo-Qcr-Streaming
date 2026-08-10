import os
import time
import base64
import io
import torch
import traceback
import asyncio
from typing import List, Dict, Any, Optional
from PIL import Image
from transformers import AutoProcessor
from vllm.engine.async_llm_engine import AsyncLLMEngine
from vllm.engine.arg_utils import AsyncEngineArgs
from vllm import SamplingParams
from qwen_vl_utils import process_vision_info


class Vllm_inference:
    def __init__(self, model_path, image_dir=None, output_csv=None):
        self.model_path = model_path
        self.image_dir = image_dir
        self.output_csv = output_csv
        self.engine = None # <-- Replaced self.llm
        self.processor = None
        self.default_sampling_params = None
        
        # --- !! MODIFIED !! ---
        self.max_num_seqs = 32 # Store the value

    def initialize(self):
        """Initialize the AsyncLLMEngine."""
        print("Initializing the vLLM AsyncLLMEngine...")

        # --- !! MODIFIED !! ---
        # Use AsyncEngineArgs to configure the engine
        engine_args = AsyncEngineArgs(
            model=self.model_path,
            gpu_memory_utilization=0.15,
            max_model_len=4096,
            max_num_seqs=self.max_num_seqs,
            max_num_batched_tokens=4096,

            swap_space=8,

            enforce_eager=False,
            limit_mm_per_prompt={"image": 1, "video": 0},
            trust_remote_code=True,
            dtype=torch.float16,
        )
        
        self.engine = AsyncLLMEngine.from_engine_args(engine_args)
        # --- End of Modification ---

        self.processor = AutoProcessor.from_pretrained(
            self.model_path, trust_remote_code=True
        )

        self.default_sampling_params = SamplingParams(
            temperature=0.1,
            top_p=0.001,
            repetition_penalty=1.05,
            max_tokens=64, # Default max_tokens
            stop_token_ids=[],
        )

        # --- !! MODIFIED !! ---
        # The warmup is no longer needed as the engine starts
        # its processing loop automatically.
        print("✅ AsyncLLMEngine initialized. Model ready for continuous batching.")

    def resize_image(self, img, max_size=1024):
        """Resize image to fit within max_size while maintaining aspect ratio."""
        width, height = img.size
        scaling_factor = max_size / max(width, height)
        new_width = int(width * scaling_factor)
        new_height = int(height * scaling_factor)
        return img.resize((new_width, new_height))

    def _prepare_single_input(self, image_data, query: str) -> Dict[str, Any]:
        img = image_data.convert("RGB")

        messages = [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": [
                {"type": "image", "image": img},
                {"type": "text", "text": query}
            ]},
        ]
        
        prompt = self.processor.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True
        )
        image_inputs, video_inputs = process_vision_info(messages)
        
        mm_data = {}
        if image_inputs is not None:
            mm_data["image"] = image_inputs
        if video_inputs is not None:
            mm_data["video"] = video_inputs

        return {
            "prompt": prompt,
            "multi_modal_data": mm_data,
        }

            
        

    # --- !! THIS FUNCTION IS DELETED !! ---
    # async def batch_worker(self):
    # (The AsyncLLMEngine handles this internally)


    # --- !! MODIFIED /process HANDLER !! ---
    async def process_batch(
        self, 
        image_data, 
        query: str, 
        max_tokens: Optional[int] = None
    ) -> List[str]:
        """
        NEW: This function now uses the AsyncLLMEngine.
        It adds a request and iterates over the async generator
        to get the final result.
        """
        try:
            llm_input = self._prepare_single_input(image_data, query)
            
            # Use custom sampling_params if max_tokens is set
            if max_tokens is not None:
                current_sampling_params = SamplingParams(
                    temperature=self.default_sampling_params.temperature,
                    top_p=self.default_sampling_params.top_p,
                    repetition_penalty=self.default_sampling_params.repetition_penalty,
                    max_tokens=max_tokens,
                    stop_token_ids=self.default_sampling_params.stop_token_ids,
                )
            else:
                current_sampling_params = self.default_sampling_params
            
            # Generate a unique request ID
            request_id = f"req-{time.time_ns()}"
            
            # This returns an async generator
            results_generator = self.engine.generate(
                llm_input, 
                current_sampling_params, 
                request_id
            )
            
            # Iterate to get the final result
            # The engine will stream tokens, we just want the last one
            final_output = None
            start_time = time.perf_counter()
            async for request_output in results_generator:
                final_output = request_output
            
            duration = time.perf_counter() - start_time
            print(f"Request {request_id} processed in {duration:.2f}s")
            
            if final_output is None:
                raise Exception("No output generated")
                
            generated_text = final_output.outputs[0].text.strip()
            torch.cuda.empty_cache()
            return [generated_text]

        except Exception as e:
            print(f"Error processing image: {e}")
            traceback.print_exc()
            return [f"ERROR: {str(e)}"]

    # --- !! THIS FUNCTION IS NOW DEPRECATED/UNUSED !! ---
    # The new orchestrator will call process_batch in a loop instead.
    # We leave it here to avoid breaking old code, but it is not
    # recommended as it's a blocking call.
    async def process_batch_multiple(
        self, 
        image_data_list: List[Any], 
        query: str
    ) -> List[str]:
        """
        DEPRECATED: This is a blocking, static batch call.
        It's better to call process_batch() concurrently.
        """
        print(f"WARNING: Using deprecated process_batch_multiple (static batch)")
        try:
            llm_inputs = []
            for i, image_data in enumerate(image_data_list):
                try:
                    llm_input = self._prepare_single_input(image_data, query)
                    llm_inputs.append(llm_input)
                except Exception as e:
                    llm_inputs.append(None)
            
            valid_inputs = [inp for inp in llm_inputs if inp is not None]
            valid_indices = [i for i, inp in enumerate(llm_inputs) if inp is not None]
            
            if not valid_inputs:
                return [f"ERROR: All images failed to prepare" for _ in image_data_list]
            
            print(f"Processing {len(valid_inputs)} valid inputs in static batch...")
            
            # This is a blocking call and does not use the async engine
            start_time = time.perf_counter()
            # We need to create a temporary LLM instance for this, or use the engine
            # This is complex. For now, let's just raise an error.
            raise NotImplementedError("process_batch_multiple is not supported with AsyncLLMEngine. Use asyncio.gather on process_batch instead.")

        except Exception as e:
            print(f"Error processing batch: {e}")
            traceback.print_exc()
            return [f"ERROR: {str(e)}"] * len(image_data_list)