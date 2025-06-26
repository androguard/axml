import logging

# Configure a default handler only if no one else has
if not logging.root.handlers:
    handler = logging.StreamHandler()
    fmt     = "%(asctime)s [%(name)s] %(levelname)s: %(message)s"
    handler.setFormatter(logging.Formatter(fmt))
    logging.root.setLevel(logging.INFO)
    logging.root.addHandler(handler)