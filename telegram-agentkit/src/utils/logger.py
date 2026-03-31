import logging
import sys

def setup_logging():
    logging.basicConfig(
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        level=logging.INFO,
        handlers=[
            logging.FileHandler("data/bot.log"),
            logging.StreamHandler(sys.stdout)
        ]
    )
