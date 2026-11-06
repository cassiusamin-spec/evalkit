#! python3.14
import os
from pathlib import Path

os.chdir(Path(__file__).resolve().parent)

from dotenv import load_dotenv
load_dotenv()

from inspect_ai import eval
from northwind_task import northwind

eval(northwind(), model="google/gemini-3.5-flash-lite", limit=5, epochs=1, max_connections=2, display="plain")
input("Finished. Press Enter to close...")
