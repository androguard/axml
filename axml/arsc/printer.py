import binascii
import re

import lxml.etree as etree

from axml.arsc import ARSCParser
from axml.helper.logging import LOGGER
from axml.utils.exceptions import NoDecoderFoundError


class ARSCPrinter:
    # TODO: be able to dump all locales of a specific type
    # TODO: be able to recreate the structure of files when developing
    # TODO: res folder with all the XML files

    def __init__(self, raw_buff: bytes) -> None:
        self.arsc = ARSCParser(raw_buff)

    def get_xml(
        self, pack: str = None, table_type: str = None, lcl: str = None
    ) -> bytes:
        package = pack or self.arsc.get_packages_names()[0]
        ttype = table_type or "public"
        locale = lcl or '\x00\x00'

        if not hasattr(self.arsc, "get_{}_resources".format(ttype)):
            raise NoDecoderFoundError(ttype)

        get_table_type_resources = getattr(
            self.arsc, "get_" + ttype + "_resources"
        )(package, locale)

        buff = etree.tostring(
            etree.fromstring(get_table_type_resources),
            pretty_print=True,
            encoding="UTF-8",
        )
        return buff
