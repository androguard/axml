import binascii
import logging
import re

from lxml import etree

from axml.constants import START_TAG, END_TAG, TEXT, END_DOCUMENT
from axml.formatters import format_value
from axml.parser_axml import AXMLParser


class AXMLPrinter:
    """
    Converter for AXML Files into a lxml ElementTree, which can easily be
    converted into XML.

    A Reference Implementation can be found at http://androidxref.com/9.0.0_r3/xref/frameworks/base/tools/aapt/XMLNode.cpp
    """

    __charrange = None
    __replacement = None

    def __init__(self, raw_buff: bytes) -> bytes:
        logging.debug("AXMLPrinter")

        self.axml = AXMLParser(raw_buff)

        self.root = None
        self.packerwarning = False
        cur = []

        while self.axml.is_valid():
            _type = next(self.axml)
            logging.debug("DEBUG ARSC TYPE {}".format(_type))

            if _type == START_TAG:
                if not self.axml.name:  # Check if the name is empty
                    logging.debug("Empty tag name, skipping to next element")
                    continue  # Skip this iteration
                uri = self._print_namespace(self.axml.namespace)
                uri, name = self._fix_name(uri, self.axml.name)
                tag = "{}{}".format(uri, name)

                comment = self.axml.comment
                if comment:
                    if self.root is None:
                        logging.warning(
                            "Can not attach comment with content '{}' without root!".format(
                                comment
                            )
                        )
                    else:
                        cur[-1].append(etree.Comment(comment))

                logging.debug(
                    "START_TAG: {} (line={})".format(
                        tag, self.axml.m_lineNumber
                    )
                )

                try:
                    elem = etree.Element(tag, nsmap=self.axml.nsmap)
                except ValueError as e:
                    logging.error(e)
                    # nsmap= {'<!--': 'http://schemas.android.com/apk/res/android'} | pull/1056
                    if 'Invalid namespace prefix' in str(e):
                        corrected_nsmap = self.clean_and_replace_nsmap(
                            self.axml.nsmap, str(e).split("'")[1]
                        )
                        elem = etree.Element(tag, nsmap=corrected_nsmap)
                    else:
                        raise

                for i in range(self.axml.getAttributeCount()):
                    uri = self._print_namespace(
                        self.axml.getAttributeNamespace(i)
                    )
                    uri, name = self._fix_name(
                        uri, self.axml.getAttributeName(i)
                    )
                    value = self._fix_value(self._get_attribute_value(i))

                    logging.debug(
                        "found an attribute: {}{}='{}'".format(
                            uri, name, value.encode("utf-8")
                        )
                    )
                    if "{}{}".format(uri, name) in elem.attrib:
                        logging.warning(
                            "Duplicate attribute '{}{}'! Will overwrite!".format(
                                uri, name
                            )
                        )
                    elem.set("{}{}".format(uri, name), value)

                if self.root is None:
                    self.root = elem
                else:
                    if not cur:
                        # looks like we lost the root?
                        logging.error(
                            "No more elements available to attach to! Is the XML malformed?"
                        )
                        break
                    cur[-1].append(elem)
                cur.append(elem)

            if _type == END_TAG:
                if not cur:
                    logging.warning(
                        "Too many END_TAG! No more elements available to attach to!"
                    )
                else:
                    if not self.axml.name:  # Check if the name is empty
                        logging.debug(
                            "Empty tag name at END_TAG, skipping to next element"
                        )
                        continue

                name = self.axml.name
                uri = self._print_namespace(self.axml.namespace)
                tag = "{}{}".format(uri, name)
                if cur[-1].tag != tag:
                    logging.warning(
                        "Closing tag '{}' does not match current stack! At line number: {}. Is the XML malformed?".format(
                            self.axml.name, self.axml.m_lineNumber
                        )
                    )
                cur.pop()
            if _type == TEXT:
                logging.debug("TEXT for {}".format(cur[-1]))
                cur[-1].text = self.axml.text
            if _type == END_DOCUMENT:
                # Check if all namespace mappings are closed
                if len(self.axml.namespaces) > 0:
                    logging.warning(
                        "Not all namespace mappings were closed! Malformed AXML?"
                    )
                break

    def clean_and_replace_nsmap(self, nsmap, invalid_prefix):
        correct_prefix = 'android'
        corrected_nsmap = {}
        for prefix, uri in nsmap.items():
            if prefix.startswith(invalid_prefix):
                corrected_nsmap[correct_prefix] = uri
            else:
                corrected_nsmap[prefix] = uri
        return corrected_nsmap

    def get_buff(self) -> bytes:
        """
        Returns the raw XML file without prettification applied.

        :returns: bytes, encoded as UTF-8
        """
        return self.get_xml(pretty=False)

    def get_xml(self, pretty: bool = True) -> bytes:
        """
        Get the XML as an UTF-8 string

        :returns: bytes encoded as UTF-8
        """
        return etree.tostring(self.root, encoding="utf-8", pretty_print=pretty)

    def get_xml_obj(self) -> etree.Element:
        """
        Get the XML as an ElementTree object

        :returns: `lxml.etree.Element` object
        """
        return self.root

    def is_valid(self) -> bool:
        """
        Return the state of the [AXMLParser][androguard.core.axml.AXMLParser].
        If this flag is set to `False`, the parsing has failed, thus
        the resulting XML will not work or will even be empty.

        :returns: `True` if the `AXMLParser` finished parsing, or `False` if an error occurred
        """
        return self.axml.is_valid()

    def is_packed(self) -> bool:
        """
        Returns True if the AXML is likely to be packed

        Packers do some weird stuff and we try to detect it.
        Sometimes the files are not packed but simply broken or compiled with
        some broken version of a tool.
        Some file corruption might also be appear to be a packed file.

        :returns: True if packer detected, False otherwise
        """
        return self.packerwarning or self.axml.packerwarning

    def _get_attribute_value(self, index: int):
        """
        Wrapper function for format_value to resolve the actual value of an attribute in a tag
        :param index: index of the current attribute
        :return: formatted value
        """
        _type = self.axml.getAttributeValueType(index)
        _data = self.axml.getAttributeValueData(index)

        return format_value(
            _type, _data, lambda _: self.axml.getAttributeValue(index)
        )

    def _fix_name(self, prefix, name) -> tuple[str, str]:
        """
        Apply some fixes to element named and attribute names.
        Try to get conform to:
        > Like element names, attribute names are case-sensitive and must start with a letter or underscore.
        > The rest of the name can contain letters, digits, hyphens, underscores, and periods.
        See: <https://msdn.microsoft.com/en-us/library/ms256152(v=vs.110).aspx>

        This function tries to fix some broken namespace mappings.
        In some cases, the namespace prefix is inside the name and not in the prefix field.
        Then, the tag name will usually look like 'android:foobar'.
        If and only if the namespace prefix is inside the namespace mapping and the actual prefix field is empty,
        we will strip the prefix from the attribute name and return the fixed prefix URI instead.
        Otherwise replacement rules will be applied.

        The replacement rules work in that way, that all unwanted characters are replaced by underscores.
        In other words, all characters except the ones listed above are replaced.

        :param name: Name of the attribute or tag
        :param prefix: The existing prefix uri as found in the AXML chunk
        :return: a fixed version of prefix and name
        """
        if not name[0].isalpha() and name[0] != "_":
            logging.warning(
                "Invalid start for name '{}'. "
                "XML name must start with a letter.".format(name)
            )
            self.packerwarning = True
            name = "_{}".format(name)
        if (
            name.startswith("android:")
            and prefix == ''
            and 'android' in self.axml.nsmap
        ):
            # Seems be a common thing...
            logging.info(
                "Name '{}' starts with 'android:' prefix but 'android' is a known prefix. Replacing prefix.".format(
                    name
                )
            )
            prefix = self._print_namespace(self.axml.nsmap['android'])
            name = name[len("android:") :]
            # It looks like this is some kind of packer... Not sure though.
            self.packerwarning = True
        elif ":" in name and prefix == '':
            self.packerwarning = True
            embedded_prefix, new_name = name.split(":", 1)
            if embedded_prefix in self.axml.nsmap:
                logging.info(
                    "Prefix '{}' is in namespace mapping, assume that it is a prefix."
                )
                prefix = self._print_namespace(
                    self.axml.nsmap[embedded_prefix]
                )
                name = new_name
            else:
                # Print out an extra warning
                logging.warning(
                    "Confused: name contains a unknown namespace prefix: '{}'. "
                    "This is either a broken AXML file or some attempt to break stuff.".format(
                        name
                    )
                )
        if not re.match(r"^[a-zA-Z0-9._-]*$", name):
            logging.warning(
                "Name '{}' contains invalid characters!".format(name)
            )
            self.packerwarning = True
            name = re.sub(r"[^a-zA-Z0-9._-]", "_", name)

        return prefix, name

    def _fix_value(self, value):
        """
        Return a cleaned version of a value
        according to the specification:
        > Char	   ::=   	#x9 | #xA | #xD | [#x20-#xD7FF] | [#xE000-#xFFFD] | [#x10000-#x10FFFF]

        See <https://www.w3.org/TR/xml/#charsets>

        :param value: a value to clean
        :return: the cleaned value
        """
        if not self.__charrange or not self.__replacement:
            self.__charrange = re.compile(
                '^[\u0020-\uD7FF\u0009\u000A\u000D\uE000-\uFFFD\U00010000-\U0010FFFF]*$'
            )
            self.__replacement = re.compile(
                '[^\u0020-\uD7FF\u0009\u000A\u000D\uE000-\uFFFD\U00010000-\U0010FFFF]'
            )

        # Reading string until \x00. This is the same as aapt does.
        if "\x00" in value:
            self.packerwarning = True
            logging.warning(
                "Null byte found in attribute value at position {}: "
                "Value(hex): '{}'".format(
                    value.find("\x00"), binascii.hexlify(value.encode("utf-8"))
                )
            )
            value = value[: value.find("\x00")]

        if not self.__charrange.match(value):
            logging.warning(
                "Invalid character in value found. Replacing with '_'."
            )
            self.packerwarning = True
            value = self.__replacement.sub('_', value)
        return value

    def _print_namespace(self, uri):
        if uri != "":
            uri = "{{{}}}".format(uri)
        return uri
