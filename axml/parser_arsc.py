from __future__ import annotations

import collections
import io
import logging
from collections import defaultdict
from struct import unpack
from typing import BinaryIO, Union
from xml.sax.saxutils import escape

from axml.constants import *
from axml.exceptions import ResParserError
from axml.formatters import format_value, complexToFloat
from axml.stringblock import StringBlock




class ARSCParser:
    """
    Parser for resource.arsc files

    The ARSC File is, like the binary XML format, a chunk based format.
    Both formats are actually identical but use different chunks in order to store the data.

    The most outer chunk in the ARSC file is a chunk of type `RES_TABLE_TYPE`.
    Inside this chunk is a StringPool and at least one package.

    Each package is a chunk of type `RES_TABLE_PACKAGE_TYPE`.
    It contains again many more chunks.
    """

    def __init__(self, raw_buff: bytes) -> None:
        """
        :param bytes raw_buff: the raw bytes of the file
        """
        self.buff = io.BufferedReader(io.BytesIO(raw_buff))
        self.buff_size = self.buff.raw.getbuffer().nbytes

        if self.buff_size < 8 or self.buff_size > 0xFFFFFFFF:
            raise ResParserError(
                "Invalid file size {} for a resources.arsc file!".format(
                    self.buff_size
                )
            )

        self.analyzed = False
        self._resolved_strings = None
        self.packages = defaultdict(list)
        self.values = {}
        self.resource_values = defaultdict(defaultdict)
        self.resource_configs = defaultdict(lambda: defaultdict(set))
        self.resource_keys = defaultdict(lambda: defaultdict(defaultdict))
        self.stringpool_main = None

        # First, there is a ResTable_header.
        self.header = ARSCHeader(self.buff, expected_type=RES_TABLE_TYPE)

        # More sanity checks...
        if self.header.header_size != 12:
            logging.warning(
                "The ResTable_header has an unexpected header size! Expected 12 bytes, got {}.".format(
                    self.header.header_size
                )
            )

        if self.header.size > self.buff_size:
            raise ResParserError(
                "The file seems to be truncated. Refuse to parse the file! Filesize: {}, declared size: {}".format(
                    self.buff_size, self.header.size
                )
            )

        if self.header.size < self.buff_size:
            logging.warning(
                "The Resource file seems to have data appended to it. Filesize: {}, declared size: {}".format(
                    self.buff_size, self.header.size
                )
            )

        # The ResTable_header contains the packageCount, i.e. the number of ResTable_package
        self.packageCount = unpack('<I', self.buff.read(4))[0]

        # Even more sanity checks...
        if self.packageCount < 1:
            logging.warning(
                "The number of packages is smaller than one. There should be at least one package!"
            )

        logging.debug(
            "Parsed ResTable_header with {} package(s) inside.".format(
                self.packageCount
            )
        )

        # skip to the start of the first chunk's data, skipping trailing header bytes (there should be none)
        self.buff.seek(self.header.start + self.header.header_size)

        # Now parse the data:
        # We should find one ResStringPool_header and one or more ResTable_package chunks inside
        while self.buff.tell() <= self.header.end - ARSCHeader.SIZE:
            res_header = ARSCHeader(self.buff)

            if res_header.end > self.header.end:
                # this inner chunk crosses the boundary of the table chunk
                logging.warning(
                    "Invalid chunk found! It is larger than the outer chunk: %s",
                    res_header,
                )
                break

            if res_header.type == RES_STRING_POOL_TYPE:
                # There should be only one StringPool per resource table.
                if self.stringpool_main:
                    logging.warning(
                        "Already found a ResStringPool_header, but there should be only one! Will not parse the Pool again."
                    )
                else:
                    self.stringpool_main = StringBlock(self.buff, res_header)
                    logging.debug(
                        "Found the main string pool: %s", self.stringpool_main
                    )

            elif res_header.type == RES_TABLE_PACKAGE_TYPE:
                if len(self.packages) > self.packageCount:
                    raise ResParserError(
                        "Got more packages ({}) than expected ({})".format(
                            len(self.packages), self.packageCount
                        )
                    )

                current_package = ARSCResTablePackage(self.buff, res_header)
                package_name = current_package.get_name()

                # After the Header, we have the resource type symbol table
                self.buff.seek(
                    current_package.header.start + current_package.typeStrings
                )
                type_sp_header = ARSCHeader(
                    self.buff, expected_type=RES_STRING_POOL_TYPE
                )
                mTableStrings = StringBlock(self.buff, type_sp_header)

                # Next, we should have the resource key symbol table
                self.buff.seek(
                    current_package.header.start + current_package.keyStrings
                )
                key_sp_header = ARSCHeader(
                    self.buff, expected_type=RES_STRING_POOL_TYPE
                )
                mKeyStrings = StringBlock(self.buff, key_sp_header)

                # Add them to the dict of read packages
                self.packages[package_name].append(current_package)
                self.packages[package_name].append(mTableStrings)
                self.packages[package_name].append(mKeyStrings)

                pc = PackageContext(
                    current_package,
                    self.stringpool_main,
                    mTableStrings,
                    mKeyStrings,
                )
                logging.debug("Constructed a PackageContext: %s", pc)

                # skip to the first header in this table package chunk
                # FIXME is this correct? We have already read the first two sections!
                # self.buff.set_idx(res_header.start + res_header.header_size)
                # this looks more like we want: (???)
                # FIXME it looks like that the two string pools we have read might not be concatenated to each other,
                # thus jumping to the sum of the sizes might not be correct...
                next_idx = (
                    res_header.start
                    + res_header.header_size
                    + type_sp_header.size
                    + key_sp_header.size
                )

                if next_idx != self.buff.tell():
                    # If this happens, we have a testfile ;)
                    logging.error("This looks like an odd resources.arsc file!")
                    logging.error(
                        "Please report this error including the file you have parsed!"
                    )
                    logging.error(
                        "next_idx = {}, current buffer position = {}".format(
                            next_idx, self.buff.tell()
                        )
                    )
                    logging.error(
                        "Please open a issue at https://github.com/androguard/androguard/issues"
                    )
                    logging.error("Thank you!")

                self.buff.seek(next_idx)

                # Read all other headers
                while self.buff.tell() <= res_header.end - ARSCHeader.SIZE:
                    pkg_chunk_header = ARSCHeader(self.buff)
                    logging.debug("Found a header: {}".format(pkg_chunk_header))
                    if (
                        pkg_chunk_header.start + pkg_chunk_header.size
                        > res_header.end
                    ):
                        # we are way off the package chunk; bail out
                        break

                    self.packages[package_name].append(pkg_chunk_header)

                    if pkg_chunk_header.type == RES_TABLE_TYPE_SPEC_TYPE:
                        self.packages[package_name].append(
                            ARSCResTypeSpec(self.buff, pc)
                        )

                    elif pkg_chunk_header.type == RES_TABLE_TYPE_TYPE:
                        # Parse a RES_TABLE_TYPE
                        # http://androidxref.com/9.0.0_r3/xref/frameworks/base/tools/aapt2/format/binary/BinaryResourceParser.cpp#311
                        start_of_chunk = self.buff.tell() - 8
                        expected_end_of_chunk = (
                            start_of_chunk + pkg_chunk_header.size
                        )
                        a_res_type = ARSCResType(self.buff, pc)
                        self.packages[package_name].append(a_res_type)
                        self.resource_configs[package_name][a_res_type].add(
                            a_res_type.config
                        )

                        logging.debug("Config: {}".format(a_res_type.config))

                        entries = []
                        FLAG_OFFSET16 = 0x02
                        NO_ENTRY_16 = 0xFFFF
                        NO_ENTRY_32 = 0xFFFFFFFF
                        expected_entries_start = (
                            start_of_chunk + a_res_type.entriesStart
                        )

                        # Helper function to convert 16-bit offset to 32-bit
                        def offset_from16(off16):
                            return (
                                NO_ENTRY_16
                                if off16 == NO_ENTRY_16
                                else off16 * 4
                            )

                        for i in range(0, a_res_type.entryCount):
                            current_package.mResId = (
                                current_package.mResId & 0xFFFF0000 | i
                            )
                            # Check if FLAG_OFFSET16 is set
                            if a_res_type.flags & FLAG_OFFSET16:
                                # Read as 16-bit offset
                                offset_16 = unpack('<H', self.buff.read(2))[0]
                                offset = offset_from16(offset_16)
                                if offset == NO_ENTRY_16:
                                    continue
                            else:
                                # Read as 32-bit offset
                                offset = unpack('<I', self.buff.read(4))[0]
                                if offset == NO_ENTRY_32:
                                    continue
                            entries.append((offset, current_package.mResId))

                        self.packages[package_name].append(entries)

                        base_offset = self.buff.tell()
                        if base_offset + ((4 - (base_offset % 4)) % 4) != expected_entries_start:
                            # FIXME: seems like I am missing 2 bytes here in some cases, though it does not affect the result
                            logging.warning(
                                "Something is off here! We are not where the entries should start."
                            )
                        base_offset = expected_entries_start
                        for entry_offset, res_id in entries:
                            if entry_offset != -1:
                                ate = ARSCResTableEntry(
                                    self.buff,
                                    base_offset + entry_offset,
                                    expected_end_of_chunk,
                                    res_id,
                                    pc,
                                )
                                self.packages[package_name].append(ate)
                                if ate.is_weak():
                                    # FIXME we are not sure how to implement the FLAG_WEAK!
                                    # We saw the following: There is just a single Res_value after the ARSCResTableEntry
                                    # and then comes the next ARSCHeader.
                                    # Therefore we think this means all entries are somehow replicated?
                                    # So we do some kind of hack here. We set the idx to the entry again...
                                    # Now we will read all entries!
                                    # Not sure if this is a good solution though
                                    self.buff.seek(ate.start)
                    elif pkg_chunk_header.type == RES_TABLE_LIBRARY_TYPE:
                        logging.warning(
                            "RES_TABLE_LIBRARY_TYPE chunk is not supported"
                        )
                    else:
                        # Unknown / not-handled chunk type
                        logging.warning(
                            "Unknown chunk type encountered inside RES_TABLE_PACKAGE: %s",
                            pkg_chunk_header,
                        )

                    # skip to the next chunk
                    self.buff.seek(pkg_chunk_header.end)
            else:
                # Unknown / not-handled chunk type
                logging.warning(
                    "Unknown chunk type encountered: %s", res_header
                )

            # move to the next resource chunk
            self.buff.seek(res_header.end)

    def _analyse(self):
        if self.analyzed:
            return

        self.analyzed = True

        for package_name in self.packages:
            self.values[package_name] = {}

            nb = 3
            while nb < len(self.packages[package_name]):
                header = self.packages[package_name][nb]
                if isinstance(header, ARSCHeader):
                    if header.type == RES_TABLE_TYPE_TYPE:
                        a_res_type = self.packages[package_name][nb + 1]

                        locale = a_res_type.config.get_language_and_region()

                        c_value = self.values[package_name].setdefault(
                            locale, {"public": []}
                        )

                        entries = self.packages[package_name][nb + 2]
                        nb_i = 0
                        for entry, res_id in entries:
                            if entry != -1:
                                ate = self.packages[package_name][
                                    nb + 3 + nb_i
                                ]

                                self.resource_values[ate.mResId][
                                    a_res_type.config
                                ] = ate
                                self.resource_keys[package_name][
                                    a_res_type.get_type()
                                ][ate.get_value()] = ate.mResId

                                if ate.get_index() != -1:
                                    c_value["public"].append(
                                        (
                                            a_res_type.get_type(),
                                            ate.get_value(),
                                            ate.mResId,
                                        )
                                    )

                                if a_res_type.get_type() not in c_value:
                                    c_value[a_res_type.get_type()] = []

                                if a_res_type.get_type() == "string":
                                    c_value["string"].append(
                                        self.get_resource_string(ate)
                                    )

                                elif a_res_type.get_type() == "id":
                                    if (
                                        not ate.is_complex()
                                        and not ate.is_compact()
                                    ):
                                        c_value["id"].append(
                                            self.get_resource_id(ate)
                                        )

                                elif a_res_type.get_type() == "bool":
                                    if (
                                        not ate.is_complex()
                                        and not ate.is_compact()
                                    ):
                                        c_value["bool"].append(
                                            self.get_resource_bool(ate)
                                        )

                                elif a_res_type.get_type() == "integer":
                                    if ate.is_compact():
                                        c_value["integer"].append(ate.data)
                                    else:
                                        c_value["integer"].append(
                                            self.get_resource_integer(ate)
                                        )

                                elif a_res_type.get_type() == "color":
                                    if not ate.is_compact():
                                        c_value["color"].append(
                                            self.get_resource_color(ate)
                                        )

                                elif a_res_type.get_type() == "dimen":
                                    if not ate.is_compact():
                                        c_value["dimen"].append(
                                            self.get_resource_dimen(ate)
                                        )

                                nb_i += 1
                        nb += (
                            3 + nb_i - 1
                        )  # -1 to account for the nb+=1 on the next line
                nb += 1

    def get_resource_string(self, ate: ARSCResTableEntry) -> list:
        return [ate.get_value(), ate.get_key_data()]

    def get_resource_id(self, ate: ARSCResTableEntry) -> list[str]:
        x = [ate.get_value()]
        if ate.key.get_data() == 0:
            x.append("false")
        elif ate.key.get_data() == 1:
            x.append("true")
        return x

    def get_resource_bool(self, ate: ARSCResTableEntry) -> list[str]:
        x = [ate.get_value()]
        if ate.key.get_data() == 0:
            x.append("false")
        elif ate.key.get_data() == -1:
            x.append("true")
        return x

    def get_resource_integer(self, ate: ARSCResTableEntry) -> list:
        return [ate.get_value(), ate.key.get_data()]

    def get_resource_color(self, ate: ARSCResTableEntry) -> list:
        entry_data = ate.key.get_data()
        return [
            ate.get_value(),
            "#{:02x}{:02x}{:02x}{:02x}".format(
                ((entry_data >> 24) & 0xFF),
                ((entry_data >> 16) & 0xFF),
                ((entry_data >> 8) & 0xFF),
                (entry_data & 0xFF),
            ),
        ]

    def get_resource_dimen(self, ate: ARSCResTableEntry) -> list:
        try:
            return [
                ate.get_value(),
                "{}{}".format(
                    complexToFloat(ate.key.get_data()),
                    DIMENSION_UNITS[ate.key.get_data() & COMPLEX_UNIT_MASK],
                ),
            ]
        except IndexError:
            logging.debug(
                "Out of range dimension unit index for {}: {}".format(
                    complexToFloat(ate.key.get_data()),
                    ate.key.get_data() & COMPLEX_UNIT_MASK,
                )
            )
            return [ate.get_value(), ate.key.get_data()]

    # FIXME
    def get_resource_style(self, ate: ARSCResTableEntry) -> list:
        return ["", ""]

    def get_packages_names(self) -> list[str]:
        """
        Retrieve a list of all package names, which are available
        in the given resources.arsc.
        """
        return list(self.packages.keys())

    def get_locales(self, package_name: str) -> list[str]:
        """
        Retrieve a list of all available locales in a given packagename.

        :param package_name: the package name to get locales of
        :returns: a list of locale strings
        """
        self._analyse()
        return list(self.values[package_name].keys())

    def get_types(
        self, package_name: str, locale: str = '\x00\x00'
    ) -> list[str]:
        """
        Retrieve a list of all types which are available in the given
        package and locale.

        :param package_name: the package name to get types of
        :param locale: the locale to get types of (default: '\x00\x00')
        :returns: a list of type strings
        """
        self._analyse()
        return list(self.values[package_name][locale].keys())

    def get_public_resources(
        self, package_name: str, locale: str = '\x00\x00'
    ) -> bytes:
        """
        Get the XML (as string) of all resources of type 'public'.

        The public resources table contains the IDs for each item.

        :param package_name: the package name to get the resources for
        :param locale: the locale to get the resources for (default: '\x00\x00')
        :returns: the public xml bytes
        """

        self._analyse()

        buff = '<?xml version="1.0" encoding="utf-8"?>\n'
        buff += '<resources>\n'

        try:
            for i in self.values[package_name][locale]["public"]:
                buff += (
                    '<public type="{}" name="{}" id="0x{:08x}" />\n'.format(
                        i[0], i[1], i[2]
                    )
                )
        except KeyError:
            pass

        buff += '</resources>\n'

        return buff.encode('utf-8')

    def get_string_resources(
        self, package_name: str, locale: str = '\x00\x00'
    ) -> bytes:
        """
        Get the XML (as string) of all resources of type 'string'.

        Read more about string resources:
        <https://developer.android.com/guide/topics/resources/string-resource.html>

        :param package_name: the package name to get the resources for
        :param locale: the locale to get the resources for (default: '\x00\x00')
        :returns: the string xml bytes
        """
        self._analyse()

        buff = '<?xml version="1.0" encoding="utf-8"?>\n'
        buff += '<resources>\n'

        try:
            for i in self.values[package_name][locale]["string"]:
                if any(map(i[1].__contains__, '<&>')):
                    value = '<![CDATA[%s]]>' % i[1]
                else:
                    value = i[1]
                buff += '<string name="{}">{}</string>\n'.format(i[0], value)
        except KeyError:
            pass

        buff += '</resources>\n'

        return buff.encode('utf-8')

    def get_strings_resources(self) -> bytes:
        """
        Get the XML (as string) of all resources of type 'string'.
        This is a combined variant, which has all locales and all package names
        stored.

        :returns: the string, locales, and package name xml bytes
        """
        self._analyse()

        buff = '<?xml version="1.0" encoding="utf-8"?>\n'

        buff += "<packages>\n"
        for package_name in self.get_packages_names():
            buff += "<package name=\"%s\">\n" % package_name

            for locale in self.get_locales(package_name):
                buff += "<locale value=%s>\n" % repr(locale)

                buff += '<resources>\n'
                try:
                    for i in self.values[package_name][locale]["string"]:
                        buff += '<string name="{}">{}</string>\n'.format(
                            i[0], escape(i[1])
                        )
                except KeyError:
                    pass

                buff += '</resources>\n'
                buff += '</locale>\n'

            buff += "</package>\n"

        buff += "</packages>\n"

        return buff.encode('utf-8')

    def get_id_resources(
        self, package_name: str, locale: str = '\x00\x00'
    ) -> bytes:
        """
        Get the XML (as string) of all resources of type 'id'.

        Read more about ID resources:
        <https://developer.android.com/guide/topics/resources/more-resources.html#Id>

        :param package_name: the package name to get the resources for
        :param locale: the locale to get the resources for (default: '\x00\x00')

        :returns: the id resources xml bytes
        """
        self._analyse()

        buff = '<?xml version="1.0" encoding="utf-8"?>\n'
        buff += '<resources>\n'

        try:
            for i in self.values[package_name][locale]["id"]:
                if len(i) == 1:
                    buff += '<item type="id" name="%s"/>\n' % (i[0])
                else:
                    buff += '<item type="id" name="{}">{}</item>\n'.format(
                        i[0], escape(i[1])
                    )
        except KeyError:
            pass

        buff += '</resources>\n'

        return buff.encode('utf-8')

    def get_bool_resources(
        self, package_name: str, locale: str = '\x00\x00'
    ) -> bytes:
        """
        Get the XML (as string) of all resources of type 'bool'.

        Read more about bool resources:
        <https://developer.android.com/guide/topics/resources/more-resources.html#Bool>

        :param package_name: the package name to get the resources for
        :param locale: the locale to get the resources for (default: '\x00\x00')

        :returns: the bool resources xml bytes
        """
        self._analyse()

        buff = '<?xml version="1.0" encoding="utf-8"?>\n'
        buff += '<resources>\n'

        try:
            for i in self.values[package_name][locale]["bool"]:
                buff += '<bool name="{}">{}</bool>\n'.format(i[0], i[1])
        except KeyError:
            pass

        buff += '</resources>\n'

        return buff.encode('utf-8')

    def get_integer_resources(
        self, package_name: str, locale: str = '\x00\x00'
    ) -> bytes:
        """
        Get the XML (as string) of all resources of type 'integer'.

        Read more about integer resources:
        <https://developer.android.com/guide/topics/resources/more-resources.html#Integer>

        :param package_name: the package name to get the resources for
        :param locale: the locale to get the resources for (default: '\x00\x00')

        :returns: the integer resources xml bytes
        """
        self._analyse()

        buff = '<?xml version="1.0" encoding="utf-8"?>\n'
        buff += '<resources>\n'

        try:
            for i in self.values[package_name][locale]["integer"]:
                buff += '<integer name="{}">{}</integer>\n'.format(i[0], i[1])
        except KeyError:
            pass

        buff += '</resources>\n'

        return buff.encode('utf-8')

    def get_color_resources(
        self, package_name: str, locale: str = '\x00\x00'
    ) -> bytes:
        """
        Get the XML (as string) of all resources of type 'color'.

        Read more about color resources:
        <https://developer.android.com/guide/topics/resources/more-resources.html#Color>

        :param package_name: the package name to get the resources for
        :param locale: the locale to get the resources for (default: '\x00\x00')

        :returns: the color resources xml bytes
        """
        self._analyse()

        buff = '<?xml version="1.0" encoding="utf-8"?>\n'
        buff += '<resources>\n'

        try:
            for i in self.values[package_name][locale]["color"]:
                buff += '<color name="{}">{}</color>\n'.format(i[0], i[1])
        except KeyError:
            pass

        buff += '</resources>\n'

        return buff.encode('utf-8')

    def get_dimen_resources(
        self, package_name: str, locale: str = '\x00\x00'
    ) -> bytes:
        """
        Get the XML (as string) of all resources of type 'dimen'.

        Read more about Dimension resources:
        <https://developer.android.com/guide/topics/resources/more-resources.html#Dimension>

        :param package_name: the package name to get the resources for
        :param locale: the locale to get the resources for (default: '\x00\x00')

        :returns: the dimen resource xml bytes
        """
        self._analyse()

        buff = '<?xml version="1.0" encoding="utf-8"?>\n'
        buff += '<resources>\n'

        try:
            for i in self.values[package_name][locale]["dimen"]:
                buff += '<dimen name="{}">{}</dimen>\n'.format(i[0], i[1])
        except KeyError:
            pass

        buff += '</resources>\n'

        return buff.encode('utf-8')

    def get_id(
        self, package_name: str, rid: int, locale: str = '\x00\x00'
    ) -> tuple:
        """
        Returns the tuple `(resource_type, resource_name, resource_id)`
        for the given resource_id.

        :param package_name: package name to query
        :param rid: the resource_id
        :param locale: specific locale
        :returns: tuple of (resource_type, resource_name, resource_id)
        """
        self._analyse()

        try:
            for i in self.values[package_name][locale]["public"]:
                if i[2] == rid:
                    return i
        except KeyError:
            pass
        return None, None, None

    class ResourceResolver:
        """
        Resolves resources by ID and configuration.
        This resolver deals with complex resources as well as with references.
        """

        def __init__(
            self,
            axml: ARSCParser,
            config: Union[ARSCResTableConfig, None] = None,
        ) -> None:
            """
            :param ARSCParser axml: A resource parser
            :param ARSCResTableConfig config: The desired configuration or None to resolve all.
            """
            self.resources = axml
            self.wanted_config = config

        def resolve(self, res_id: int) -> list[tuple[ARSCResTableConfig, str]]:
            """
            the given ID into the Resource and returns a list of matching resources.

            :param int res_id: numerical ID of the resource
            :returns: a list of tuples of (ARSCResTableConfig, str)
            """
            result = []
            self._resolve_into_result(result, res_id, self.wanted_config)
            return result

        def _resolve_into_result(self, result, res_id, config):
            # First: Get all candidates
            configs = self.resources.get_res_configs(res_id, config)

            for config, ate in configs:
                # deconstruct them and check if more candidates are generated
                self.put_ate_value(result, ate, config)

        def put_ate_value(
            self,
            result: list,
            ate: ARSCResTableEntry,
            config: ARSCResTableConfig,
        ) -> None:
            """
            Put a [ARSCResTableEntry][androguard.core.axml.ARSCResTableEntry] into the list of results
            :param result: results array
            :param ate:
            :param config:
            """
            if ate.is_complex():
                complex_array = []
                result.append((config, complex_array))
                for _, item in ate.item.items:
                    self.put_item_value(
                        complex_array, item, config, ate, complex_=True
                    )
            elif ate.is_compact():
                self.put_item_value(
                    result,
                    ate.data,
                    config,
                    ate,
                    complex_=False,
                    compact_=True,
                )
            else:
                self.put_item_value(
                    result, ate.key, config, ate, complex_=False
                )

        def put_item_value(
            self,
            result: list,
            item: Union[ARSCResStringPoolRef, int],
            config: ARSCResTableConfig,
            parent: ARSCResTableEntry,
            complex_: bool,
            compact_: bool = False,
        ) -> None:
            """
            Put the tuple ([ARSCResTableConfig][androguard.core.axml.ARSCResTableConfig], resolved string) into the result set

            :param result: the result set
            :param item:
            :param config:
            :param parent: the originating entry
            :param complex_: True if the originating `ARSCResTableEntry` was complex
            :param bool compact_: True if the originating `ARSCResTableEntry` was compact
            """
            if isinstance(item, ARSCResStringPoolRef):
                if item.is_reference():
                    res_id = item.get_data()
                    if res_id:
                        # Infinite loop detection:
                        # TODO should this stay here or should be detect the loop much earlier?
                        if res_id == parent.mResId:
                            logging.warning(
                                "Infinite loop detected at resource item {}. It references itself!".format(
                                    parent
                                )
                            )
                            return

                        self._resolve_into_result(
                            result, item.get_data(), self.wanted_config
                        )
                else:
                    if complex_:
                        result.append(item.format_value())
                    else:
                        result.append((config, item.format_value()))
            else:
                if compact_:
                    result.append(
                        (config, parent.parent.stringpool_main.getString(item))
                    )

    def get_resolved_res_configs(
        self, rid: int, config: Union[ARSCResTableConfig, None] = None
    ) -> list[tuple[ARSCResTableConfig, str]]:
        """
        Return a list of resolved resource IDs with their corresponding configuration.
        It has a similar return type as [get_res_configs][androguard.core.axml.ARSCParser.get_res_configs] but also handles complex entries
        and references.
        Also instead of returning [ARSCResTableConfig][androguard.core.axml.ARSCResTableConfig] in the tuple, the actual values are resolved.

        This is the preferred way of resolving resource IDs to their resources.

        :param rid: the numerical ID of the resource
        :param config: the desired configuration or None to retrieve all
        :return: A list of tuples of (`ARSCResTableConfig`, str)
        """
        resolver = ARSCParser.ResourceResolver(self, config)
        return resolver.resolve(rid)

    def get_resolved_strings(self) -> list[str]:
        self._analyse()
        if self._resolved_strings:
            return self._resolved_strings

        r = {}
        for package_name in self.get_packages_names():
            r[package_name] = {}
            k = {}

            for locale in self.values[package_name]:
                v_locale = locale
                if v_locale == '\x00\x00':
                    v_locale = 'DEFAULT'

                r[package_name][v_locale] = {}

                try:
                    for i in self.values[package_name][locale]["public"]:
                        if i[0] == 'string':
                            r[package_name][v_locale][i[2]] = None
                            k[i[1]] = i[2]
                except KeyError:
                    pass

                try:
                    for i in self.values[package_name][locale]["string"]:
                        if i[0] in k:
                            r[package_name][v_locale][k[i[0]]] = i[1]
                except KeyError:
                    pass

        self._resolved_strings = r
        return r

    def get_res_configs(
        self,
        rid: int,
        config: Union[ARSCResTableConfig, None] = None,
        fallback: bool = True,
    ) -> list[ARSCResTableConfig]:
        """
        Return the resources found with the ID `rid` and select
        the right one based on the configuration, or return all if no configuration was set.

        But we try to be generous here and at least try to resolve something:
        This method uses a fallback to return at least one resource (the first one in the list)
        if more than one items are found and the default config is used and no default entry could be found.

        This is usually a bad sign (i.e. the developer did not follow the android documentation:
        <https://developer.android.com/guide/topics/resources/localization.html#failing2)>
        In practise an app might just be designed to run on a single locale and thus only has those locales set.

        You can disable this fallback behaviour, to just return exactly the given result.

        :param rid: resource id as int
        :param config: a config to resolve from, or None to get all results
        :param fallback: Enable the fallback for resolving default configuration (default: True)
        :return: a list of `ARSCResTableConfig`
        """
        self._analyse()

        if not rid:
            raise ValueError("'rid' should be set")
        if not isinstance(rid, int):
            raise ValueError("'rid' must be an int")

        if rid not in self.resource_values:
            logging.warning(
                "The requested rid '0x{:08x}' could not be found in the list of resources.".format(
                    rid
                )
            )
            return []

        res_options = self.resource_values[rid]
        if len(res_options) > 1 and config:
            if config in res_options:
                return [(config, res_options[config])]
            elif fallback and config == ARSCResTableConfig.default_config():
                logging.warning(
                    "No default resource config could be found for the given rid '0x{:08x}', using fallback!".format(
                        rid
                    )
                )
                return [list(self.resource_values[rid].items())[0]]
            else:
                return []
        else:
            return list(res_options.items())

    def get_string(
        self, package_name: str, name: str, locale: str = '\x00\x00'
    ) -> Union[str, None]:
        self._analyse()

        try:
            for i in self.values[package_name][locale]["string"]:
                if i[0] == name:
                    return i
        except KeyError:
            return None

    def get_res_id_by_key(self, package_name, resource_type, key):
        try:
            return self.resource_keys[package_name][resource_type][key]
        except KeyError:
            return None

    def get_items(self, package_name):
        self._analyse()
        return self.packages[package_name]

    def get_type_configs(self, package_name, type_name=None):
        if package_name is None:
            package_name = self.get_packages_names()[0]
        result = collections.defaultdict(list)

        for res_type, configs in list(
            self.resource_configs[package_name].items()
        ):
            if res_type.get_package_name() == package_name and (
                type_name is None or res_type.get_type() == type_name
            ):
                result[res_type.get_type()].extend(configs)

        return result

    @staticmethod
    def parse_id(name: str) -> tuple[str, str]:
        """
        Resolves an id from a binary XML file in the form `@[package:]DEADBEEF`
        and returns a tuple of package name and resource id.
        If no package name was given, i.e. the ID has the form `@DEADBEEF`,
        the package name is set to None.

        :raises ValueError: if the id is malformed.

        :param name: the string of the resource, as in the binary XML file
        :return: a tuple of (resource_id, package_name).
        """

        if not name.startswith('@'):
            raise ValueError(
                "Not a valid resource ID, must start with @: '{}'".format(name)
            )

        # remove @
        name = name[1:]

        package = None
        if ':' in name:
            package, res_id = name.split(':', 1)
        else:
            res_id = name

        if len(res_id) != 8:
            raise ValueError(
                "Numerical ID is not 8 characters long: '{}'".format(res_id)
            )

        try:
            return int(res_id, 16), package
        except ValueError:
            raise ValueError("ID is not a hex ID: '{}'".format(res_id))

    def get_resource_xml_name(
        self, r_id: int, package: Union[str, None] = None
    ) -> str:
        """
        Returns the XML name for a resource, including the package name if package is `None`.
        A full name might look like `@com.example:string/foobar`
        Otherwise the name is only looked up in the specified package and is returned without
        the package name.
        The same example from about without the package name will read as `@string/foobar`.

        If the ID could not be found, `None` is returned.

        A description of the XML name can be found here:
        <https://developer.android.com/guide/topics/resources/providing-resources#ResourcesFromXml>

        :param r_id: numerical ID if the resource
        :param package: package name
        :return: XML name identifier
        """
        if package:
            resource, name, i_id = self.get_id(package, r_id)
            if not i_id:
                return None
            return "@{}/{}".format(resource, name)
        else:
            for p in self.get_packages_names():
                r, n, i_id = self.get_id(p, r_id)
                if i_id:
                    # found the resource in this package
                    package = p
                    resource = r
                    name = n
                    break
            if not package:
                return None
            else:
                return "@{}:{}/{}".format(package, resource, name)

class PackageContext:
    def __init__(
        self,
        current_package: ARSCResTablePackage,
        stringpool_main: StringBlock,
        mTableStrings: StringBlock,
        mKeyStrings: StringBlock,
    ) -> None:
        """
        :param current_package:
        :param stringpool_main:
        :param mTableStrings:
        :param mKeyStrings:
        """
        self.stringpool_main = stringpool_main
        self.mTableStrings = mTableStrings
        self.mKeyStrings = mKeyStrings
        self.current_package = current_package

    def get_mResId(self) -> int:
        return self.current_package.mResId

    def set_mResId(self, mResId: int) -> None:
        self.current_package.mResId = mResId

    def get_package_name(self) -> str:
        return self.current_package.get_name()

    def __repr__(self):
        return "<PackageContext {}, {}, {}, {}>".format(
            self.current_package,
            self.stringpool_main,
            self.mTableStrings,
            self.mKeyStrings,
        )


class ARSCHeader:
    """
    Object which contains a Resource Chunk.
    This is an implementation of the `ResChunk_header`.

    It will throw an [ResParserError][androguard.core.axml.ResParserError] if the header could not be read successfully.

    It is not checked if the data is outside the buffer size nor if the current
    chunk fits into the parent chunk (if any)!

    The parameter `expected_type` can be used to immediately check the header for the type or raise a [ResParserError][androguard.core.axml.ResParserError].
    This is useful if you know what type of chunk must follow.

    See http://androidxref.com/9.0.0_r3/xref/frameworks/base/libs/androidfw/include/androidfw/ResourceTypes.h#196
    """

    # This is the minimal size such a header must have. There might be other header data too!
    SIZE = 2 + 2 + 4

    def __init__(
        self,
        buff: BinaryIO,
        expected_type: Union[int, None] = None,
        possible_types: Union[set[int], None] = None,
    ) -> None:
        """
        :raises ResParserError: if header malformed
        :param buff: the buffer set to the position where the header starts.
        :param int expected_type: the type of the header which is expected.
        """
        self.start = buff.tell()
        # Make sure we do not read over the buffer:
        if buff.raw.getbuffer().nbytes < self.start + self.SIZE:
            raise ResParserError(
                "Can not read over the buffer size! Offset={}".format(
                    self.start
                )
            )

        # Checking for dummy data between elements
        if possible_types:
            while True:
                cur_pos = buff.tell()
                self._type, self._header_size, self._size = unpack(
                    '<HHL', buff.read(self.SIZE)
                )

                # cases where packers set the EndNamespace with zero size: check we are the end and add the prefix + uri
                if self._size < self.SIZE and (
                    buff.raw.getbuffer().nbytes
                    == cur_pos + self._header_size + 4 + 4
                ):
                    self._size = 24

                if cur_pos == 0 or (
                    self._type in possible_types
                    and self._header_size >= self.SIZE
                    and self._size > self.SIZE
                ):
                    break
                buff.seek(cur_pos)
                buff.read(1)
                logging.warning(
                    "Appears that dummy data are found between elements!"
                )
        else:
            self._type, self._header_size, self._size = unpack(
                '<HHL', buff.read(self.SIZE)
            )

        if expected_type and self._type != expected_type:
            raise ResParserError(
                "Header type is not equal the expected type: Got 0x{:04x}, wanted 0x{:04x}".format(
                    self._type, expected_type
                )
            )

        # Assert that the read data will fit into the chunk.
        # The total size must be equal or larger than the header size
        if self._header_size < self.SIZE:
            raise ResParserError(
                "declared header size is smaller than required size of {}! Offset={}".format(
                    self.SIZE, self.start
                )
            )
        if self._size < self.SIZE:
            raise ResParserError(
                "declared chunk size is smaller than required size of {}! Offset={}".format(
                    self.SIZE, self.start
                )
            )
        if self._size < self._header_size:
            raise ResParserError(
                "declared chunk size ({}) is smaller than header size ({})! Offset={}".format(
                    self._size, self._header_size, self.start
                )
            )

    @property
    def type(self) -> int:
        """
        Type identifier for this chunk
        """
        return self._type

    @property
    def header_size(self) -> int:
        """
        Size of the chunk header (in bytes).  Adding this value to
        the address of the chunk allows you to find its associated data
        (if any).
        """
        return self._header_size

    @property
    def size(self) -> int:
        """
        Total size of this chunk (in bytes).  This is the chunkSize plus
        the size of any data associated with the chunk.  Adding this value
        to the chunk allows you to completely skip its contents (including
        any child chunks).  If this value is the same as chunkSize, there is
        no data associated with the chunk.
        """
        return self._size

    @property
    def end(self) -> int:
        """
        Get the absolute offset inside the file, where the chunk ends.
        This is equal to `ARSCHeader.start + ARSCHeader.size`.
        """
        return self.start + self.size

    def __repr__(self):
        return "<ARSCHeader idx='0x{:08x}' type='{}' header_size='{}' size='{}'>".format(
            self.start, self.type, self.header_size, self.size
        )


class ARSCResTablePackage:
    """
    A `ResTable_package`

    See http://androidxref.com/9.0.0_r3/xref/frameworks/base/libs/androidfw/include/androidfw/ResourceTypes.h#861
    """

    def __init__(self, buff: BinaryIO, header: ARSCHeader) -> None:
        self.header = header
        self.start = buff.tell()
        self.id = unpack('<I', buff.read(4))[0]
        self.name = buff.read(256)
        self.typeStrings = unpack('<I', buff.read(4))[0]
        self.lastPublicType = unpack('<I', buff.read(4))[0]
        self.keyStrings = unpack('<I', buff.read(4))[0]
        self.lastPublicKey = unpack('<I', buff.read(4))[0]
        self.mResId = self.id << 24

    def get_name(self) -> str:
        name = self.name.decode("utf-16", 'replace')
        name = name[: name.find("\x00")]
        return name


class ARSCResTypeSpec:
    """
    See http://androidxref.com/9.0.0_r3/xref/frameworks/base/libs/androidfw/include/androidfw/ResourceTypes.h#1327
    """

    def __init__(
        self, buff: BinaryIO, parent: Union[PackageContext, None] = None
    ) -> None:
        self.start = buff.tell()
        self.parent = parent
        self.id = unpack('<B', buff.read(1))[0]
        self.res0 = unpack('<B', buff.read(1))[0]
        self.res1 = unpack('<H', buff.read(2))[0]
        # TODO: https://github.com/androguard/androguard/issues/1014 | Properly account for the cases where res0/1 are not zero
        try:
            if self.res0 != 0:
                logging.warning("res0 must be zero!")
            if self.res1 != 0:
                logging.warning("res1 must be zero!")
            self.entryCount = unpack('<I', buff.read(4))[0]

            self.typespec_entries = []
            for i in range(0, self.entryCount):
                self.typespec_entries.append(unpack('<I', buff.read(4))[0])
        except Exception as e:
            logging.error(e)


class ARSCResType:
    """
    This is a `ResTable_type` without it's `ResChunk_header`.
    It contains a `ResTable_config`

    See http://androidxref.com/9.0.0_r3/xref/frameworks/base/libs/androidfw/include/androidfw/ResourceTypes.h#1364
    """

    def __init__(
        self, buff: BinaryIO, parent: Union[PackageContext, None] = None
    ) -> None:
        self.start = buff.tell()
        self.parent = parent

        self.id = unpack('<B', buff.read(1))[0]
        # TODO there is now FLAG_SPARSE: http://androidxref.com/9.0.0_r3/xref/frameworks/base/libs/androidfw/include/androidfw/ResourceTypes.h#1401
        (self.flags,) = unpack('<B', buff.read(1))
        self.reserved = unpack('<H', buff.read(2))[0]
        if self.reserved != 0:
            raise ResParserError("reserved must be zero!")
        self.entryCount = unpack('<I', buff.read(4))[0]
        self.entriesStart = unpack('<I', buff.read(4))[0]

        self.mResId = (0xFF000000 & self.parent.get_mResId()) | self.id << 16
        self.parent.set_mResId(self.mResId)

        self.config = ARSCResTableConfig(buff)

        logging.debug("Parsed {}".format(self))

    def get_type(self) -> str:
        return self.parent.mTableStrings.getString(self.id - 1)

    def get_package_name(self) -> str:
        return self.parent.get_package_name()

    def __repr__(self):
        return (
            "<ARSCResType(start=0x%x, id=0x%x, flags=0x%x, entryCount=%d, entriesStart=0x%x, mResId=0x%x, %s)>"
            % (
                self.start,
                self.id,
                self.flags,
                self.entryCount,
                self.entriesStart,
                self.mResId,
                "table:" + self.parent.mTableStrings.getString(self.id - 1),
            )
        )


class ARSCResTableConfig:
    """
    ARSCResTableConfig contains the configuration for specific resource selection.
    This is used on the device to determine which resources should be loaded
    based on different properties of the device like locale or displaysize.

    See the definition of `ResTable_config` in
    http://androidxref.com/9.0.0_r3/xref/frameworks/base/libs/androidfw/include/androidfw/ResourceTypes.h#911
    """

    @classmethod
    def default_config(cls):
        if not hasattr(cls, 'DEFAULT'):
            cls.DEFAULT = ARSCResTableConfig(None)
        return cls.DEFAULT

    def __init__(self, buff: Union[BinaryIO, None] = None, **kwargs) -> None:
        if buff is not None:
            self.start = buff.tell()

            # uint32_t
            self.size = unpack('<I', buff.read(4))[0]

            # union: uint16_t mcc, uint16_t mnc
            # 0 means any
            self.imsi = unpack('<I', buff.read(4))[0]

            # uint32_t as chars \0\0 means any
            # either two 7bit ASCII representing the ISO-639-1 language code
            # or a single 16bit LE value representing ISO-639-2 3 letter code
            self.locale = unpack('<I', buff.read(4))[0]

            # struct of:
            # uint8_t orientation
            # uint8_t touchscreen
            # uint16_t density
            self.screenType = unpack('<I', buff.read(4))[0]

            if self.size >= 20:
                # struct of
                # uint8_t keyboard
                # uint8_t navigation
                # uint8_t inputFlags
                # uint8_t inputPad0
                self.input = unpack('<I', buff.read(4))[0]
            else:
                logging.debug(
                    "This file does not have input flags! size={}".format(
                        self.size
                    )
                )
                self.input = 0

            if self.size >= 24:
                # struct of
                # uint16_t screenWidth
                # uint16_t screenHeight
                self.screenSize = unpack('<I', buff.read(4))[0]
            else:
                logging.debug(
                    "This file does not have screenSize! size={}".format(
                        self.size
                    )
                )
                self.screenSize = 0

            if self.size >= 28:
                # struct of
                # uint16_t sdkVersion
                # uint16_t minorVersion  which should be always 0, as the meaning is not defined
                self.version = unpack('<I', buff.read(4))[0]
            else:
                logging.debug(
                    "This file does not have version! size={}".format(
                        self.size
                    )
                )
                self.version = 0

            # The next three fields seems to be optional
            if self.size >= 32:
                # struct of
                # uint8_t screenLayout
                # uint8_t uiMode
                # uint16_t smallestScreenWidthDp
                (self.screenConfig,) = unpack('<I', buff.read(4))
            else:
                logging.debug(
                    "This file does not have a screenConfig! size={}".format(
                        self.size
                    )
                )
                self.screenConfig = 0

            if self.size >= 36:
                # struct of
                # uint16_t screenWidthDp
                # uint16_t screenHeightDp
                (self.screenSizeDp,) = unpack('<I', buff.read(4))
            else:
                logging.debug(
                    "This file does not have a screenSizeDp! size={}".format(
                        self.size
                    )
                )
                self.screenSizeDp = 0

            if self.size >= 40:
                self.localeScript = buff.read(4)

            if self.size >= 44:
                self.localeVariant = buff.read(8)

            if self.size >= 52:
                # struct of
                # uint8_t screenLayout2
                # uint8_t colorMode
                # uint16_t screenConfigPad2
                (self.screenConfig2,) = unpack("<I", buff.read(4))
            else:
                logging.debug(
                    "This file does not have a screenConfig2! size={}".format(
                        self.size
                    )
                )
                self.screenConfig2 = 0

            self.exceedingSize = self.size - (buff.tell() - self.start)
            if self.exceedingSize > 0:
                logging.debug("Skipping padding bytes!")
                self.padding = buff.read(self.exceedingSize)

        else:
            self.start = 0
            self.size = 0
            self.imsi = ((kwargs.pop('mcc', 0) & 0xFFFF) << 0) + (
                (kwargs.pop('mnc', 0) & 0xFFFF) << 16
            )

            temp_locale = kwargs.pop('locale', 0)
            if isinstance(temp_locale, str):
                self.set_language_and_region(temp_locale)
            else:
                self.locale = temp_locale

            for char_ix, char in kwargs.pop('locale', "")[0:4]:
                self.locale += ord(char) << (char_ix * 8)

            self.screenType = (
                ((kwargs.pop('orientation', 0) & 0xFF) << 0)
                + ((kwargs.pop('touchscreen', 0) & 0xFF) << 8)
                + ((kwargs.pop('density', 0) & 0xFFFF) << 16)
            )

            self.input = (
                ((kwargs.pop('keyboard', 0) & 0xFF) << 0)
                + ((kwargs.pop('navigation', 0) & 0xFF) << 8)
                + ((kwargs.pop('inputFlags', 0) & 0xFF) << 16)
                + ((kwargs.pop('inputPad0', 0) & 0xFF) << 24)
            )

            self.screenSize = (
                (kwargs.pop('screenWidth', 0) & 0xFFFF) << 0
            ) + ((kwargs.pop('screenHeight', 0) & 0xFFFF) << 16)

            self.version = ((kwargs.pop('sdkVersion', 0) & 0xFFFF) << 0) + (
                (kwargs.pop('minorVersion', 0) & 0xFFFF) << 16
            )

            self.screenConfig = (
                ((kwargs.pop('screenLayout', 0) & 0xFF) << 0)
                + ((kwargs.pop('uiMode', 0) & 0xFF) << 8)
                + ((kwargs.pop('smallestScreenWidthDp', 0) & 0xFFFF) << 16)
            )

            self.screenSizeDp = (
                (kwargs.pop('screenWidthDp', 0) & 0xFFFF) << 0
            ) + ((kwargs.pop('screenHeightDp', 0) & 0xFFFF) << 16)

            # TODO add this some day...
            self.screenConfig2 = 0

            self.exceedingSize = 0

    def _unpack_language_or_region(self, char_in, char_base):
        char_out = ""
        if char_in[0] & 0x80:
            first = char_in[1] & 0x1F
            second = ((char_in[1] & 0xE0) >> 5) + ((char_in[0] & 0x03) << 3)
            third = (char_in[0] & 0x7C) >> 2
            char_out += chr(first + char_base)
            char_out += chr(second + char_base)
            char_out += chr(third + char_base)
        else:
            if char_in[0]:
                char_out += chr(char_in[0])
            if char_in[1]:
                char_out += chr(char_in[1])
        return char_out

    def _pack_language_or_region(self, char_in: str) -> list[int]:
        char_out = [0x00, 0x00]
        if len(char_in) != 2:
            return char_out
        char_out[0] = ord(char_in[0])
        char_out[1] = ord(char_in[1])
        return char_out

    def set_language_and_region(self, language_region):
        try:
            language, region = language_region.split("-r")
        except ValueError:
            language, region = language_region, None
        language_bytes = self._pack_language_or_region(language)
        if region:
            region_bytes = self._pack_language_or_region(region)
        else:
            region_bytes = [0x00, 0x00]
        self.locale = (
            language_bytes[0]
            | (language_bytes[1] << 8)
            | (region_bytes[0] << 16)
            | (region_bytes[1] << 24)
        )

    def get_language_and_region(self) -> str:
        """
        Returns the combined language+region string or \x00\x00 for the default locale
        :returns: the combined language and region string
        """
        if self.locale != 0:
            _language = self._unpack_language_or_region(
                [
                    self.locale & 0xFF,
                    (self.locale & 0xFF00) >> 8,
                ],
                ord('a'),
            )
            _region = self._unpack_language_or_region(
                [
                    (self.locale & 0xFF0000) >> 16,
                    (self.locale & 0xFF000000) >> 24,
                ],
                ord('0'),
            )
            return (_language + "-r" + _region) if _region else _language
        return "\x00\x00"

    def get_config_name_friendly(self) -> str:
        """
        Here for legacy reasons.

        use [get_qualifier][androguard.core.axml.ARSCResTableConfig.get_qualifier] instead.
        :returns: the qualifier string
        """
        return self.get_qualifier()

    def get_qualifier(self) -> str:
        """
        Return resource name qualifier for the current configuration.
        for example

        * `ldpi-v4`
        * `hdpi-v4`

        All possible qualifiers are listed in table 2 of <https://developer.android.com/guide/topics/resources/providing-resources>

        You can find how android process this at [ResourceTypes 3243](http://aospxref.com/android-13.0.0_r3/xref/frameworks/base/libs/androidfw/ResourceTypes.cpp#3243)

        :return: the resource name qualifer string
        """
        res = []

        mcc = self.imsi & 0xFFFF
        mnc = (self.imsi & 0xFFFF0000) >> 16
        if mcc != 0:
            res.append("mcc%d" % mcc)
        if mnc != 0:
            res.append("mnc%d" % mnc)

        if self.locale != 0:
            res.append(self.get_language_and_region())

        screenLayout = self.screenConfig & 0xFF
        if (screenLayout & MASK_LAYOUTDIR) != 0:
            if screenLayout & MASK_LAYOUTDIR == LAYOUTDIR_LTR:
                res.append("ldltr")
            elif screenLayout & MASK_LAYOUTDIR == LAYOUTDIR_RTL:
                res.append("ldrtl")
            else:
                res.append("layoutDir_%d" % (screenLayout & MASK_LAYOUTDIR))

        smallestScreenWidthDp = (self.screenConfig & 0xFFFF0000) >> 16
        if smallestScreenWidthDp != 0:
            res.append("sw%ddp" % smallestScreenWidthDp)

        screenWidthDp = self.screenSizeDp & 0xFFFF
        screenHeightDp = (self.screenSizeDp & 0xFFFF0000) >> 16
        if screenWidthDp != 0:
            res.append("w%ddp" % screenWidthDp)
        if screenHeightDp != 0:
            res.append("h%ddp" % screenHeightDp)

        if (screenLayout & MASK_SCREENSIZE) != SCREENSIZE_ANY:
            if screenLayout & MASK_SCREENSIZE == SCREENSIZE_SMALL:
                res.append("small")
            elif screenLayout & MASK_SCREENSIZE == SCREENSIZE_NORMAL:
                res.append("normal")
            elif screenLayout & MASK_SCREENSIZE == SCREENSIZE_LARGE:
                res.append("large")
            elif screenLayout & MASK_SCREENSIZE == SCREENSIZE_XLARGE:
                res.append("xlarge")
            else:
                res.append(
                    "screenLayoutSize_%d" % (screenLayout & MASK_SCREENSIZE)
                )
        if (screenLayout & MASK_SCREENLONG) != 0:
            if screenLayout & MASK_SCREENLONG == SCREENLONG_NO:
                res.append("notlong")
            elif screenLayout & MASK_SCREENLONG == SCREENLONG_YES:
                res.append("long")
            else:
                res.append(
                    "screenLayoutLong_%d" % (screenLayout & MASK_SCREENLONG)
                )

        screenLayout2 = self.screenConfig2 & 0xFF
        if (screenLayout2 & MASK_SCREENROUND) != 0:
            if screenLayout2 & MASK_SCREENROUND == SCREENROUND_NO:
                res.append("notround")
            elif screenLayout2 & MASK_SCREENROUND == SCREENROUND_YES:
                res.append("round")
            else:
                res.append(
                    "screenRound_%d" % (screenLayout2 & MASK_SCREENROUND)
                )

        colorMode = (self.screenConfig2 & 0xFF00) >> 8
        if (colorMode & MASK_WIDE_COLOR_GAMUT) != 0:
            if colorMode & MASK_WIDE_COLOR_GAMUT == WIDE_COLOR_GAMUT_NO:
                res.append("nowidecg")
            elif colorMode & MASK_WIDE_COLOR_GAMUT == WIDE_COLOR_GAMUT_YES:
                res.append("widecg")
            else:
                res.append(
                    "wideColorGamut_%d" % (colorMode & MASK_WIDE_COLOR_GAMUT)
                )

        if (colorMode & MASK_HDR) != 0:
            if colorMode & MASK_HDR == HDR_NO:
                res.append("lowdr")
            elif colorMode & MASK_HDR == HDR_YES:
                res.append("highdr")
            else:
                res.append("hdr_%d" % (colorMode & MASK_HDR))

        orientation = self.screenType & 0xFF
        if orientation != ORIENTATION_ANY:
            if orientation == ORIENTATION_PORT:
                res.append("port")
            elif orientation == ORIENTATION_LAND:
                res.append("land")
            elif orientation == ORIENTATION_SQUARE:
                res.append("square")
            else:
                res.append("orientation_%d" % orientation)

        uiMode = (self.screenConfig & 0xFF00) >> 8
        if (uiMode & MASK_UI_MODE_TYPE) != UI_MODE_TYPE_ANY:
            ui_mode = uiMode & MASK_UI_MODE_TYPE
            if ui_mode == UI_MODE_TYPE_DESK:
                res.append("desk")
            elif ui_mode == UI_MODE_TYPE_CAR:
                res.append("car")
            elif ui_mode == UI_MODE_TYPE_TELEVISION:
                res.append("television")
            elif ui_mode == UI_MODE_TYPE_APPLIANCE:
                res.append("appliance")
            elif ui_mode == UI_MODE_TYPE_WATCH:
                res.append("watch")
            elif ui_mode == UI_MODE_TYPE_VR_HEADSET:
                res.append("vrheadset")
            else:
                res.append("uiModeType_%d" % ui_mode)

        if (uiMode & MASK_UI_MODE_NIGHT) != 0:
            if uiMode & MASK_UI_MODE_NIGHT == UI_MODE_NIGHT_NO:
                res.append("notnight")
            elif uiMode & MASK_UI_MODE_NIGHT == UI_MODE_NIGHT_YES:
                res.append("night")
            else:
                res.append("uiModeNight_%d" % (uiMode & MASK_UI_MODE_NIGHT))

        density = (self.screenType & 0xFFFF0000) >> 16
        if density != DENSITY_DEFAULT:
            if density == DENSITY_LOW:
                res.append("ldpi")
            elif density == DENSITY_MEDIUM:
                res.append("mdpi")
            elif density == DENSITY_TV:
                res.append("tvdpi")
            elif density == DENSITY_HIGH:
                res.append("hdpi")
            elif density == DENSITY_XHIGH:
                res.append("xhdpi")
            elif density == DENSITY_XXHIGH:
                res.append("xxhdpi")
            elif density == DENSITY_XXXHIGH:
                res.append("xxxhdpi")
            elif density == DENSITY_NONE:
                res.append("nodpi")
            elif density == DENSITY_ANY:
                res.append("anydpi")
            else:
                res.append("%ddpi" % (density))

        touchscreen = (self.screenType & 0xFF00) >> 8
        if touchscreen != TOUCHSCREEN_ANY:
            if touchscreen == TOUCHSCREEN_NOTOUCH:
                res.append("notouch")
            elif touchscreen == TOUCHSCREEN_FINGER:
                res.append("finger")
            elif touchscreen == TOUCHSCREEN_STYLUS:
                res.append("stylus")
            else:
                res.append("touchscreen_%d" % touchscreen)

        keyboard = self.input & 0xFF
        navigation = (self.input & 0xFF00) >> 8
        inputFlags = (self.input & 0xFF0000) >> 16

        if inputFlags & MASK_KEYSHIDDEN != 0:
            input_flags = inputFlags & MASK_KEYSHIDDEN
            if input_flags == KEYSHIDDEN_NO:
                res.append("keysexposed")
            elif input_flags == KEYSHIDDEN_YES:
                res.append("keyshidden")
            elif input_flags == KEYSHIDDEN_SOFT:
                res.append("keyssoft")

        if keyboard != KEYBOARD_ANY:
            if keyboard == KEYBOARD_NOKEYS:
                res.append("nokeys")
            elif keyboard == KEYBOARD_QWERTY:
                res.append("qwerty")
            elif keyboard == KEYBOARD_12KEY:
                res.append("12key")
            else:
                res.append("keyboard_%d" % keyboard)

        if inputFlags & MASK_NAVHIDDEN != 0:
            input_flags = inputFlags & MASK_NAVHIDDEN
            if input_flags == NAVHIDDEN_NO:
                res.append("navexposed")
            elif input_flags == NAVHIDDEN_YES:
                res.append("navhidden")
            else:
                res.append("inputFlagsNavHidden_%d" % input_flags)

        if navigation != NAVIGATION_ANY:
            if navigation == NAVIGATION_NONAV:
                res.append("nonav")
            elif navigation == NAVIGATION_DPAD:
                res.append("dpad")
            elif navigation == NAVIGATION_TRACKBALL:
                res.append("trackball")
            elif navigation == NAVIGATION_WHEEL:
                res.append("wheel")
            else:
                res.append("navigation_%d" % navigation)

        screenSize = self.screenSize
        if screenSize != 0:
            screenWidth = self.screenSize & 0xFFFF
            screenHeight = (self.screenSize & 0xFFFF0000) >> 16
            res.append("%dx%d" % (screenWidth, screenHeight))

        version = self.version
        if version != 0:
            sdkVersion = self.version & 0xFFFF
            minorVersion = (self.version & 0xFFFF0000) >> 16
            res.append("v%d" % sdkVersion)
            if minorVersion != 0:
                res.append(".%d" % minorVersion)

        return "-".join(res)

    def get_language(self) -> str:
        x = self.locale & 0x0000FFFF
        return chr(x & 0x00FF) + chr((x & 0xFF00) >> 8)

    def get_country(self) -> str:
        x = (self.locale & 0xFFFF0000) >> 16
        return chr(x & 0x00FF) + chr((x & 0xFF00) >> 8)

    def get_density(self) -> str:
        x = (self.screenType >> 16) & 0xFFFF
        return x

    def is_default(self) -> bool:
        """
        Test if this is a default resource, which matches all

        This is indicated that all fields are zero.
        :returns: True if default, False otherwise
        """
        return all(map(lambda x: x == 0, self._get_tuple()))

    def _get_tuple(self):
        return (
            self.imsi,
            self.locale,
            self.screenType,
            self.input,
            self.screenSize,
            self.version,
            self.screenConfig,
            self.screenSizeDp,
            self.screenConfig2,
        )

    def __hash__(self):
        return hash(self._get_tuple())

    def __eq__(self, other):
        return self._get_tuple() == other._get_tuple()

    def __repr__(self):
        return "<ARSCResTableConfig '{}'={}>".format(
            self.get_qualifier(), repr(self._get_tuple())
        )



class ARSCResTableEntry:
    """
    A `ResTable_entry`.

    See <https://cs.android.com/android/platform/superproject/main/+/main:frameworks/base/libs/androidfw/include/androidfw/ResourceTypes.h;l=1522;drc=442fcb158a5b2e23340b74ce2e29e5e1f5bf9d66;bpv=0;bpt=0>
    """

    # If set, this is a complex entry, holding a set of name/value
    # mappings.  It is followed by an array of ResTable_map structures.
    FLAG_COMPLEX = 1

    # If set, this resource has been declared public, so libraries
    # are allowed to reference it.
    FLAG_PUBLIC = 2

    # If set, this is a weak resource and may be overriden by strong
    # resources of the same name/type. This is only useful during
    # linking with other resource tables.
    FLAG_WEAK = 4

    # If set, this is a compact entry with data type and value directly
    # encoded in this entry
    FLAG_COMPACT = 8

    def __init__(
        self,
        buff: BinaryIO,
        entry_offset: int,
        expected_end_of_chunk: int,
        mResId: int,
        parent: Union[PackageContext, None] = None,
    ) -> None:
        self.start = buff.seek(entry_offset)
        self.mResId = mResId
        self.parent = parent

        self.size = unpack('<H', buff.read(2))[0]
        self.flags = unpack('<H', buff.read(2))[0]
        # This is a ResStringPool_ref
        self.index = unpack('<I', buff.read(4))[0]

        if self.is_complex():
            self.item = ARSCComplex(buff, expected_end_of_chunk, parent)
        elif self.is_compact():
            self.key = self.size
            self.data = self.index
            self.datatype = (self.flags >> 8) & 0xFF
        else:
            # If FLAG_COMPLEX is not set, a Res_value structure will follow
            self.key = ARSCResStringPoolRef(buff, self.parent)

        if self.is_weak():
            logging.debug("Parsed {}".format(self))

    def get_index(self) -> int:
        return self.index

    def get_value(self) -> str:
        return self.parent.mKeyStrings.getString(self.index)

    def get_key_data(self) -> str:
        if self.is_compact():
            return self.parent.stringpool_main.getString(self.key)
        else:
            return self.key.get_data_value()

    def is_public(self) -> bool:
        return (self.flags & self.FLAG_PUBLIC) != 0

    def is_complex(self) -> bool:
        return (self.flags & self.FLAG_COMPLEX) != 0

    def is_compact(self) -> bool:
        return (self.flags & self.FLAG_COMPACT) != 0

    def is_weak(self) -> bool:
        return (self.flags & self.FLAG_WEAK) != 0

    def __repr__(self):
        return "<ARSCResTableEntry idx='0x{:08x}' mResId='0x{:08x}' flags='0x{:02x}' holding={}>".format(
            self.start,
            self.mResId,
            self.flags,
            self.item if self.is_complex() else self.key,
        )


class ARSCComplex:
    """
    This is actually a `ResTable_map_entry`

    It contains a set of {name: value} mappings, which are of type `ResTable_map`.
    A `ResTable_map` contains two items: `ResTable_ref` and `Res_value`.

    See [ResourceTypes.h 1485](http://androidxref.com/9.0.0_r3/xref/frameworks/base/libs/androidfw/include/androidfw/ResourceTypes.h#1485) for `ResTable_map_entry`
    and [ResourceTypes.h 1498](http://androidxref.com/9.0.0_r3/xref/frameworks/base/libs/androidfw/include/androidfw/ResourceTypes.h#1498) for `ResTable_map`
    """

    def __init__(
        self,
        buff: BinaryIO,
        expected_end_of_chunk: int,
        parent: Union[PackageContext, None] = None,
    ) -> None:
        self.start = buff.tell()
        self.parent = parent

        self.id_parent = unpack('<I', buff.read(4))[0]
        self.count = unpack('<I', buff.read(4))[0]

        self.items = []
        # Parse self.count number of `ResTable_map`
        # these are structs of ResTable_ref and Res_value
        # ResTable_ref is a uint32_t.
        for i in range(0, self.count):
            if buff.tell() + 4 > expected_end_of_chunk:
                print(
                    f"We are out of bound with this complex entry. Count: {self.count}"
                )
                break
            self.items.append(
                (
                    unpack('<I', buff.read(4))[0],
                    ARSCResStringPoolRef(buff, self.parent),
                )
            )

    def __repr__(self):
        return "<ARSCComplex idx='0x{:08x}' parent='{}' count='{}'>".format(
            self.start, self.id_parent, self.count
        )


class ARSCResStringPoolRef:
    """
    This is actually a `Res_value`
    It holds information about the stored resource value

    See: [ResourceTypes.h 262](http://androidxref.com/9.0.0_r3/xref/frameworks/base/libs/androidfw/include/androidfw/ResourceTypes.h#262)
    """

    def __init__(
        self, buff: BinaryIO, parent: Union[PackageContext, None] = None
    ) -> None:
        self.start = buff.tell()
        self.parent = parent

        (self.size,) = unpack("<H", buff.read(2))
        (self.res0,) = unpack("<B", buff.read(1))
        try:
            if self.res0 != 0:
                logging.warning("res0 must be always zero!")
            self.data_type = unpack('<B', buff.read(1))[0]
            # data is interpreted according to data_type
            self.data = unpack('<I', buff.read(4))[0]
        except Exception as e:
            logging.error(e)

    def get_data_value(self) -> str:
        return self.parent.stringpool_main.getString(self.data)

    def get_data(self) -> int:
        return self.data

    def get_data_type(self) -> bytes:
        return self.data_type

    def get_data_type_string(self) -> str:
        return TYPE_TABLE[self.data_type]

    def format_value(self) -> str:
        """
        Return the formatted (interpreted) data according to `data_type`.
        """
        return format_value(
            self.data_type, self.data, self.parent.stringpool_main.getString
        )

    def is_reference(self) -> bool:
        """
        Returns True if the Res_value is actually a reference to another resource
        """
        return self.data_type == TYPE_REFERENCE

    def __repr__(self):
        return "<ARSCResStringPoolRef idx='0x{:08x}' size='{}' type='{}' data='0x{:08x}'>".format(
            self.start,
            self.size,
            TYPE_TABLE.get(self.data_type, "0x%x" % self.data_type),
            self.data,
        )


def get_arsc_info(arscobj: ARSCParser) -> str:
    """
    Return a string containing all resources packages ordered by packagename, locale and type.

    :param arscobj: [ARSCParser][androguard.core.axml.ARSCParser]
    :return: a string
    """
    buff = ""
    for package in arscobj.get_packages_names():
        buff += package + ":\n"
        for locale in arscobj.get_locales(package):
            buff += "\t" + repr(locale) + ":\n"
            for ttype in arscobj.get_types(package, locale):
                buff += "\t\t" + ttype + ":\n"
                try:
                    tmp_buff = (
                        getattr(arscobj, "get_" + ttype + "_resources")(
                            package, locale
                        )
                        .decode("utf-8", 'replace')
                        .split("\n")
                    )
                    for i in tmp_buff:
                        buff += "\t\t\t" + i + "\n"
                except AttributeError:
                    pass
    return buff