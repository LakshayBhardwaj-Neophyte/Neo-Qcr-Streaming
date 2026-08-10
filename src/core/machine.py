# machine.py

from abc import ABC, abstractmethod

class Machine(ABC):
    """
    Abstract base class for all machines in the pipeline.

    Defines the interface that all machines must implement.
    """

    def __init__(self, config, logger=None):
        """
        Initializes the Machine with configuration and logger.

        Args:
            config (dict): Configuration parameters for the machine.
            logger (Logger, optional): Logger instance for logging events. Defaults to None.
        """
        self.config = config
        self.logger = logger
        self.output_data = {}
        self.data_store = None  # Initialize data store if needed
        self.on_start()

    @abstractmethod
    def get_data(self, input_data):
        """
        Retrieves input data for processing.

        Args:
            input_data (dict): Dictionary containing the input data for the machine.

        Returns:
            Any: The data to be processed.
        """
        pass

    @abstractmethod
    def process(self, data):
        """
        Processes the input data.

        Args:
            data (Any): The data to process.

        Returns:
            Any: The processed data.
        """
        pass

    @abstractmethod
    def send_data(self, processed_data):
        """
        Sends the processed data to the next module.

        Args:
            processed_data (Any): The data after processing.

        Returns:
            dict: The data to be stored in the orchestrator's data_dict.
        """
        pass

    @abstractmethod
    def store_data(self):
        """
        Stores data if necessary.

        This method can be used to store data to a database or file system.
        """
        pass

    def on_start(self):
        """
        Actions to perform when the machine starts.

        This method is called at the beginning of the machine's lifecycle.
        """
        if self.logger:
            self.logger.info(f"{self.__class__.__name__} started.")
        else:
            print(f"{self.__class__.__name__} started.")

    def on_finish(self):
        """
        Actions to perform when the machine finishes.

        This method is called at the end of the machine's lifecycle.
        """
        if self.logger:
            self.logger.info(f"{self.__class__.__name__} Machine finished.")
        else:
            print(f"{self.__class__.__name__} finished.")
