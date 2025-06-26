from struct import unpack, pack

from axml.constants import RADIX_MULTS, TYPE_STRING, TYPE_ATTRIBUTE, TYPE_REFERENCE, TYPE_FLOAT, \
    TYPE_INT_HEX, TYPE_INT_BOOLEAN, TYPE_DIMENSION, DIMENSION_UNITS, COMPLEX_UNIT_MASK, TYPE_FRACTION, FRACTION_UNITS, \
    TYPE_FIRST_COLOR_INT, TYPE_LAST_COLOR_INT, TYPE_LAST_INT, TYPE_FIRST_INT


def complexToFloat(xcomplex) -> float:
    """
    Convert a complex unit into float
    """
    return float(xcomplex & 0xFFFFFF00) * RADIX_MULTS[(xcomplex >> 4) & 3]

def format_value(
    _type: int, _data: int, lookup_string=lambda ix: "<string>"
) -> str:
    """
    Format a value based on type and data.
    By default, no strings are looked up and `"<string>"` is returned.
    You need to define `lookup_string` in order to actually lookup strings from
    the string table.

    :param _type: The numeric type of the value
    :param _data: The numeric data of the value
    :param lookup_string: A function how to resolve strings from integer IDs
    :returns: the formatted string
    """

    # Function to prepend android prefix for attributes/references from the
    # android library
    fmt_package = lambda x: "android:" if x >> 24 == 1 else ""

    # Function to represent integers
    fmt_int = lambda x: (0x7FFFFFFF & x) - 0x80000000 if x > 0x7FFFFFFF else x

    if _type == TYPE_STRING:
        return lookup_string(_data)

    elif _type == TYPE_ATTRIBUTE:
        return "?{}{:08X}".format(fmt_package(_data), _data)

    elif _type == TYPE_REFERENCE:
        return "@{}{:08X}".format(fmt_package(_data), _data)

    elif _type == TYPE_FLOAT:
        return "%f" % unpack("=f", pack("=L", _data))[0]

    elif _type == TYPE_INT_HEX:
        return "0x%08X" % _data

    elif _type == TYPE_INT_BOOLEAN:
        if _data == 0:
            return "false"
        return "true"

    elif _type == TYPE_DIMENSION:
        return "{:f}{}".format(
            complexToFloat(_data), DIMENSION_UNITS[_data & COMPLEX_UNIT_MASK]
        )

    elif _type == TYPE_FRACTION:
        return "{:f}{}".format(
            complexToFloat(_data) * 100,
            FRACTION_UNITS[_data & COMPLEX_UNIT_MASK],
        )

    elif TYPE_FIRST_COLOR_INT <= _type <= TYPE_LAST_COLOR_INT:
        return "#%08X" % _data

    elif TYPE_FIRST_INT <= _type <= TYPE_LAST_INT:
        return "%d" % fmt_int(_data)

    return "<0x{:X}, type 0x{:02X}>".format(_data, _type)