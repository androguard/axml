# axml-parser
Android AXML Parser


## In order for everyone to be on the same page and align on the goals of this project the following information is provided:

#### High level suggested structure
~~~~
axml_parser/
├── axml_parser/
│   ├── __init__.py       # Expose the public API (parse_axml, AXMLParser, AXMLPrinter)
│   ├── constants.py      # All constants (chunk types, flag values...)
│   ├── exceptions.py     # Custom exceptions (like ResParserError)
│   ├── stringblock.py    # The StringBlock class and its helper functions (_decode8, _decode16...)
│   ├── parser.py         # The AXMLParser class and related parsing functions
│   ├── printer.py        # The AXMLPrinter class for converting parsed AXML into an ElementTree
│   └── formatters.py     # Helper functions like format_value and any formatting utilities
├── tests/
│   └── test_parser.py    # Unit tests for each module
├── setup.py              # Packaging file
├── pyproject.toml        # Build configuration
└── README.md             # Project description and usage instructions
~~~~

### Goals
 - Write tests early approach, so we can immediately verify breaking changes.
 - Expose a clean public API
 - Standalone capabilities for axml parsing
 - Provide basic documentation


### Coding style
 - Follow [PEP 257](https://peps.python.org/pep-0257/) guidelines using the reStructuredText (reST) format for all docstrings.