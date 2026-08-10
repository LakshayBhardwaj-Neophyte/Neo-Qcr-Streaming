import functools
import time
import os
import asyncio
from typing import Callable, Any

def time_calculate(log_file="logs/function_timing_logs.txt"):
    """
    A decorator to calculate and log the execution time of a function,
    and save the logs to a specified text file.

    Args:
        log_file (str): Path to the log file where execution times will be saved.
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            start_time = time.time()
            func_name = func.__name__
            print(f"Starting {func_name}...")
            
            # Execute the wrapped function
            result = func(*args, **kwargs)
            
            end_time = time.time()
            duration = end_time - start_time
            log_message = f"{time.strftime('%Y-%m-%d %H:%M:%S')} - {func_name} took {duration:.4f} seconds.\n"
            
            # Log the timing information to the specified log file
            os.makedirs(os.path.dirname(log_file), exist_ok=True)
            with open(log_file, "a") as log:
                log.write(log_message)
            
            print(f"Finished {func_name} in {duration:.4f} seconds.")
            return result
        return wrapper
    return decorator