# -*- coding: utf-8 -*-
'''
Created on 23-September-2024 13:55
Project: ambient-machine 
@author: Pranjal Bhaskare
@email: pranjalab@neophyte.live
'''

# logger.py

import logging
import os
from datetime import datetime

class Logger:
    def __init__(self, log_name='orchestrator', log_dir='../../logs', verbose=False):
        """
        Initializes the Logger.

        Args:
        - log_name (str): Name of the log file.
        - log_dir (str): Directory where log files will be stored.
        - verbose (bool): If True, logs will also be printed to the console.
        """
        self.verbose = verbose
        # Create logs directory if it doesn't exist
        if not os.path.exists(log_dir):
            os.makedirs(log_dir)
        # Create a logger
        self.logger = logging.getLogger(log_name)
        self.logger.setLevel(logging.DEBUG)

        # Create handlers
        log_file = os.path.join(log_dir, f"{log_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")
        file_handler = logging.FileHandler(log_file)
        file_handler.setLevel(logging.DEBUG)

        if self.verbose:
            stream_handler = logging.StreamHandler()
            stream_handler.setLevel(logging.DEBUG)
            # Add handlers to the logger
            self.logger.addHandler(stream_handler)

        # Create formatters and add them to the handlers
        formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
        file_handler.setFormatter(formatter)
        if self.verbose:
            stream_handler.setFormatter(formatter)

        # Add the file handler
        self.logger.addHandler(file_handler)

    def info(self, message):
        """
        Logs an info message.

        Args:
        - message (str): The message to log.
        """
        self.logger.info(message)

    def debug(self, message):
        """
        Logs a debug message.

        Args:
        - message (str): The message to log.
        """
        self.logger.debug(message)

    def warning(self, message):
        """
        Logs a warning message.

        Args:
        - message (str): The message to log.
        """
        self.logger.warning(message)

    def error(self, message):
        """
        Logs an error message.

        Args:
        - message (str): The message to log.
        """
        self.logger.error(message)

    def critical(self, message):
        """
        Logs a critical message.

        Args:
        - message (str): The message to log.
        """
        self.logger.critical(message)
