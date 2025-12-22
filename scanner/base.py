import pandas as pd
from abc import ABC, abstractmethod

class BaseScanner(ABC):
    def __init__(self):
        self.limit = 250

    @abstractmethod
    def scan(self, **kwargs) -> list:
        pass
