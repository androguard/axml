# axml-parser
Android AXML Parser


## In order for everyone to be on the same page and align on the goals of this project the following information is provided:

#### High level structure
~~~~
axml_parser/
├── axml_parser/
│   ├── __init__.py
│   ├── parser.py         # Core logic
│   ├── types.py          # AXML data types
│   ├── utils.py          # Helpers
│   └── exceptions.py     # Any custom exceptions
├── tests/
│   ├── test_parser.py
├── setup.py
├── pyproject.toml
└── README.md
~~~~

### Goals
 - Write tests early approach, so we can immediately verify breaking changes.
 - Expose a clean public API
 - Standalone capabilities for axml parsing
 - Provide basic documentation


### Coding style
 - Follow [PEP 257](https://peps.python.org/pep-0257/) guidelines using the reStructuredText (reST) format for all docstrings.