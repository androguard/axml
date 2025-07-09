
# Androguard's axml

This is a library for handling the AXML file format.  "AXML" is the informal
common name for the compiled binary XML data format used in Android app files.
The Android Open Source Project does not seem to have named the format, other
than referring to is as "binary XML" or "compiled XML".  So AXML stands for
Android XML. The file format is based on compiling XML source into a binary
format based on [protobuf](). There are a number of different Android XML file
types that are compiled to AXML, these are generically known as [Android
Resources](https://developer.android.com/guide/topics/resources/available-resources).
All of these files are included in the APK's ZIP package with the file extension
`.xml` even though they are actually AXML and not XML.

Some specific data files, like String Resources and Style Resources, are instead
compiled into a single file `resources.arsc` in its own data format, known as
ASRC.  AXML files often refer to values that are in `resources.arsc`.

The entry point for an app is the "[app
manifest](https://developer.android.com/guide/topics/manifest/manifest-element)"
defines the essential data points that every app must have, like Package Name
and Version Code, and includes lots of other metadata that describe the
app. Every Android app file (APK) must include
[`AndroidManifest.xml`](https://developer.android.com/guide/topics/manifest/manifest-intro),
which in the APK is the compiled binary AXML format, not XML, despite the file
extension.  The source code files for the binary app manifest file are also
called `AndroidManifest.xml`, but they are actually XML.  There can be
[multiple](https://developer.android.com/build/manage-manifests) source files,
but there is only ever one single compiled binary `AndroidManifest.xml` that is
valid in the APK.

https://developer.android.com/guide/topics/manifest/manifest-intro#reference

## Current status

 - Passing androguard tests for axml and arsc.

## Next steps to reach an "up to par" milestone

 - pyproject.toml/setup.py
 - workflows for testing/building
 - ?

#### Structure

~~~~
axml/
├── axml/
│   ├── __init__.py       # Expose the public API (parse_axml, AXMLParser, AXMLPrinter)
│   ├── constants.py      # All constants (chunk types, flag values...)
│   ├── exceptions.py     # Custom exceptions (like ResParserError)
│   ├── stringblock.py    # The StringBlock class and its helper functions (_decode8, _decode16...)
│   ├── parser_arsc.py    # The resources parser class and related parsing functions
│   ├── parser_axml.py    # The AXMLParser class and related parsing functions
│   ├── printer.py        # The AXMLPrinter class for converting parsed AXML into an ElementTree
│   └── formatters.py     # Helper functions like format_value and any formatting utilities
│   ├── resources
│   │   ├── __init__.py
│   │   ├── public.json
│   │   ├── public.py
│   │   └── public.xml
├── tests/
│   └── test_*.py    # Unit tests for each module
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

## AXML binary format

Some references about the binary AXML format:

* [_aapt2_](https://developer.android.com/tools/aapt2) compiles XML to protobuf-based AXML
* [_aapt2_ source code](https://android.googlesource.com/platform/frameworks/base/+/master/tools/aapt2)
* [_aapt_ source code](https://android.googlesource.com/platform/frameworks/base/+/master/tools/aapt)
* The binary format for `AndroidManifest.xml` is defined in [`ApkInfo.proto`](https://android.googlesource.com/platform/frameworks/base/+/refs/heads/main/tools/aapt2/ApkInfo.proto).

![Android binary XML](https://raw.githubusercontent.com/senswrong/AndroidBinaryXml/main/AndroidBinaryXml.png)

<!-- back up URL in case the one above goes away
![Android binary XML](https://github.com/user-attachments/assets/6439a13a-5a50-4f32-b106-c70c9fb9acf1)
-->
