import binascii
import re
from typing import Any, Iterator, List, Tuple, Union

import lxml.etree as etree

from axml.arsc.parser import ARSCParser
from axml.axml import AXMLParser
from axml.helper.logging import LOGGER
from axml.utils.constants import END_DOCUMENT, END_TAG, START_TAG, TEXT
from axml.utils.formatters import format_value

NS_ANDROID_URI = 'http://schemas.android.com/apk/res/android'
NS_ANDROID = '{{{}}}'.format(NS_ANDROID_URI)  # Namespace as used by etree

def namespace(name: str) -> str:
    """
    return the name including the Android namespace URI
    """
    return NS_ANDROID + name

class AXMLPrinter:
    """
    Converter for AXML Files into a lxml ElementTree, which can easily be
    converted into XML.

    A Reference Implementation can be found at http://androidxref.com/9.0.0_r3/xref/frameworks/base/tools/aapt/XMLNode.cpp
    """

    __charrange = None
    __replacement = None

    def __init__(self, raw_buff: bytes):
        LOGGER.debug("AXMLPrinter")

        self.axml: AXMLParser = AXMLParser(raw_buff)

        self.root: etree.Element | None = None
        self.packerwarning: bool = False
        cur = []

        while self.axml.is_valid():
            _type = next(self.axml)
            LOGGER.debug("DEBUG ARSC TYPE {}".format(_type))

            if _type == START_TAG:
                if not self.axml.name:  # Check if the name is empty
                    LOGGER.debug("Empty tag name, skipping to next element")
                    continue  # Skip this iteration
                uri = self._print_namespace(self.axml.namespace)
                uri, name = self._fix_name(uri, self.axml.name)
                tag = "{}{}".format(uri, name)

                comment = self.axml.comment
                if comment:
                    if self.root is None:
                        LOGGER.warning(
                            "Can not attach comment with content '{}' without root!".format(
                                comment
                            )
                        )
                    else:
                        cur[-1].append(etree.Comment(comment))

                LOGGER.debug(
                    "START_TAG: {} (line={})".format(
                        tag, self.axml.m_lineNumber
                    )
                )

                try:
                    elem = etree.Element(tag, nsmap=self.axml.nsmap)
                except ValueError as e:
                    LOGGER.error(e)
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

                    LOGGER.debug(
                        "found an attribute: {}{}='{}'".format(
                            uri, name, value.encode("utf-8")
                        )
                    )
                    if "{}{}".format(uri, name) in elem.attrib:
                        LOGGER.warning(
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
                        LOGGER.error(
                            "No more elements available to attach to! Is the XML malformed?"
                        )
                        break
                    cur[-1].append(elem)
                cur.append(elem)

            if _type == END_TAG:
                if not cur:
                    LOGGER.warning(
                        "Too many END_TAG! No more elements available to attach to!"
                    )
                else:
                    if not self.axml.name:  # Check if the name is empty
                        LOGGER.debug(
                            "Empty tag name at END_TAG, skipping to next element"
                        )
                        continue

                name = self.axml.name
                uri = self._print_namespace(self.axml.namespace)
                tag = "{}{}".format(uri, name)
                if cur[-1].tag != tag:
                    LOGGER.warning(
                        "Closing tag '{}' does not match current stack! At line number: {}. Is the XML malformed?".format(
                            self.axml.name, self.axml.m_lineNumber
                        )
                    )
                cur.pop()
            if _type == TEXT:
                LOGGER.debug("TEXT for {}".format(cur[-1]))
                cur[-1].text = self.axml.text
            if _type == END_DOCUMENT:
                # Check if all namespace mappings are closed
                if len(self.axml.namespaces) > 0:
                    LOGGER.warning(
                        "Not all namespace mappings were closed! Malformed AXML?"
                    )
                break

        self._init_fields()

    def _init_fields(self):
        self.androidversion = {}
        self.uses_permissions = []

        self.package = self.get_attribute_value("manifest", "package")

        self.androidversion["Code"] = self.get_attribute_value(
            "manifest", "versionCode"
        )
        self.androidversion["Name"] = self.get_attribute_value(
            "manifest", "versionName"
        )
        self.permissions = list(
            set(self.get_all_attribute_value("uses-permission", "name"))
        )

        for uses_permission in self.find_tags("uses-permission"):
            self.uses_permissions.append(
                [
                    self.get_value_from_tag(uses_permission, "name"),
                    self.get_permission_maxsdk(uses_permission),
                ]
            )

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
        if self.root is not None:
            return etree.tostring(
                self.root, encoding="utf-8", pretty_print=pretty
            )
        return b''

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

    def _fix_name(self, prefix: str, name: str) -> tuple[str, str]:
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
            LOGGER.warning(
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
            LOGGER.info(
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
                LOGGER.info(
                    "Prefix '{}' is in namespace mapping, assume that it is a prefix."
                )
                prefix = self._print_namespace(
                    self.axml.nsmap[embedded_prefix]
                )
                name = new_name
            else:
                # Print out an extra warning
                LOGGER.warning(
                    "Confused: name contains a unknown namespace prefix: '{}'. "
                    "This is either a broken AXML file or some attempt to break stuff.".format(
                        name
                    )
                )
        if not re.match(r"^[a-zA-Z0-9._-]*$", name):
            LOGGER.warning(
                "Name '{}' contains invalid characters!".format(name)
            )
            self.packerwarning = True
            name = re.sub(r"[^a-zA-Z0-9._-]", "_", name)

        return prefix, name

    def _fix_value(self, value: str):
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
                '^[\u0020-\ud7ff\u0009\u000a\u000d\ue000-\ufffd\U00010000-\U0010ffff]*$'
            )
            self.__replacement = re.compile(
                '[^\u0020-\ud7ff\u0009\u000a\u000d\ue000-\ufffd\U00010000-\U0010ffff]'
            )

        # Reading string until \x00. This is the same as aapt does.
        if "\x00" in value:
            self.packerwarning = True
            LOGGER.warning(
                "Null byte found in attribute value at position {}: "
                "Value(hex): '{}'".format(
                    value.find("\x00"), binascii.hexlify(value.encode("utf-8"))
                )
            )
            value = value[: value.find("\x00")]

        if not self.__charrange.match(value):
            LOGGER.warning(
                "Invalid character in value found. Replacing with '_'."
            )
            self.packerwarning = True
            value = self.__replacement.sub('_', value)
        return value

    def _print_namespace(self, uri: str):
        if uri != "":
            uri = "{{{}}}".format(uri)
        return uri

    def is_tag_matched(self, tag: etree.Element, **attribute_filter) -> bool:
        r"""
        Return `True` if the attributes matches in attribute filter.

        An attribute filter is a dictionary containing: {attribute_name: value}.
        This function will return `True` if and only if all attributes have the same value.
        This function allows to set the dictionary via kwargs, thus you can filter like this:

        Example:

            >>> a.is_tag_matched(tag, name="foobar", other="barfoo")

        This function uses a fallback for attribute searching. It will by default use
        the namespace variant but fall back to the non-namespace variant.
        Thus specifiying `{"name": "foobar"}` will match on `<bla name="foobar" \>`
        as well as on `<bla android:name="foobar" \>`.

        :param tag: specify the tag element
        :param attribute_filter: specify the attribute filter as dictionary

        :returns: `True` if the attributes matches in attribute filter, else `False`
        """
        if len(attribute_filter) <= 0:
            return True
        for attr, value in attribute_filter.items():
            _value = self.get_value_from_tag(tag, attr)
            if _value != value:
                return False
        return True

    def find_tags(self, tag_name: str, **attribute_filter) -> list[str]:
        """
        Return a list of all the matched tags

        :param str tag_name: specify the tag name

        :returns: the matched tags
        """
        xml = self.get_xml_obj()
        if xml is None:
            return []

        if xml.tag == tag_name:
            if self.is_tag_matched(xml.tag, **attribute_filter):
                return [xml]
            return []
        tags = set()
        tags.update(xml.findall(".//" + tag_name))

        # https://github.com/androguard/androguard/pull/1053
        # permission declared using tag <android:uses-permission...
        tags.update(xml.findall(".//" + NS_ANDROID + tag_name))
        return [
            tag for tag in tags if self.is_tag_matched(tag, **attribute_filter)
        ]

    def format_value(self, value):
        """
        Format a value with packagename, if not already set.
        For example, the name `'.foobar'` will be transformed into `'package.name.foobar'`.

        Names which do not contain any dots are assumed to be packagename-less as well:
        `foobar` will also transform into `package.name.foobar`.

        :param value: string value to format with the packaname
        :returns: the formatted package name
        """
        if value and self.package:
            v_dot = value.find(".")
            if v_dot == 0:
                # Dot at the start
                value = self.package + value
            elif v_dot == -1:
                # Not a single dot
                value = self.package + "." + value
        return value

    def get_all_attribute_value(
        self,
        tag_name: str,
        attribute: str,
        format_value: bool = True,
        **attribute_filter,
    ) -> Iterator[str]:
        """
        Yields all the attribute values in xml files which match with the tag name and the specific attribute

        :param str tag_name: specify the tag name
        :param str attribute: specify the attribute
        :param bool format_value: specify if the value needs to be formatted with packagename

        :returns: the attribute values
        """
        tags = self.find_tags(tag_name, **attribute_filter)
        for tag in tags:
            value = tag.get(namespace(attribute)) or tag.get(attribute)
            if value is not None:
                if format_value:
                    yield self.format_value(value)
                else:
                    yield value

    def get_attribute_value(
        self,
        tag_name: str,
        attribute: str,
        format_value: bool = False,
        **attribute_filter,
    ) -> str | None:
        """
        Return the attribute value in xml files which matches the tag name and the specific attribute

        :param str tag_name: specify the tag name
        :param str attribute: specify the attribute
        :param bool format_value: specify if the value needs to be formatted with packagename

        :returns: the attribute value
        """

        for value in self.get_all_attribute_value(
            tag_name, attribute, format_value, **attribute_filter
        ):
            if value is not None:
                return value

    def get_value_from_tag(
        self, tag: etree.Element, attribute: str
    ) -> Union[str, None]:
        """
        Return the value of the android prefixed attribute in a specific tag.

        This function will always try to get the attribute with a `android:` prefix first,
        and will try to return the attribute without the prefix, if the attribute could not be found.
        This is useful for some broken `AndroidManifest.xml`, where no android namespace is set,
        but could also indicate malicious activity (i.e. wrongly repackaged files).
        A warning is printed if the attribute is found without a namespace prefix.

        If you require to get the exact result you need to query the tag directly:

        Example:

            >>> from lxml.etree import Element
            >>> tag = Element('bar', nsmap={'android': 'http://schemas.android.com/apk/res/android'})
            >>> tag.set('{http://schemas.android.com/apk/res/android}foobar', 'barfoo')
            >>> tag.set('name', 'baz')
            # Assume that `a` is some APK object
            >>> a.get_value_from_tag(tag, 'name')
            'baz'
            >>> tag.get('name')
            'baz'
            >>> tag.get('foobar')
            None
            >>> a.get_value_from_tag(tag, 'foobar')
            'barfoo'

        :param lxml.etree.Element tag: specify the tag element
        :param str attribute: specify the attribute name
        :returns: the attribute's value, or None if the attribute is not present
        """

        # TODO: figure out if both android:name and name tag exist which one to give preference:
        # currently we give preference for the namespace one and fallback to the un-namespaced
        value = tag.get(namespace(attribute))
        if value is None:
            value = tag.get(attribute)

            if value:
                # If value is still None, the attribute could not be found, thus is not present
                LOGGER.warning(
                    "Failed to get the attribute '{}' on tag '{}' with namespace. "
                    "But found the same attribute without namespace!".format(
                        attribute, tag.tag
                    )
                )
        return value

    def get_permission_maxsdk(self, item):
        maxSdkVersion = None
        try:
            maxSdkVersion = int(self.get_value_from_tag(item, "maxSdkVersion"))
        except ValueError:
            LOGGER.warning(
                str(maxSdkVersion)
                + ' is not a valid value for <uses-permission> maxSdkVersion'
            )
        except TypeError:
            pass
        return maxSdkVersion

    def get_max_sdk_version(self) -> str:
        """
        Return the `android:maxSdkVersion` attribute

        :returns: the `android:maxSdkVersion` attribute
        """
        return self.get_attribute_value("uses-sdk", "maxSdkVersion")

    def get_min_sdk_version(self) -> str:
        """
        Return the `android:minSdkVersion` attribute

        :returns: the `android:minSdkVersion` attribute
        """
        return self.get_attribute_value("uses-sdk", "minSdkVersion")

    def get_target_sdk_version(self) -> str:
        """
        Return the `android:targetSdkVersion` attribute

        :returns: the `android:targetSdkVersion` attribute
        """
        return self.get_attribute_value("uses-sdk", "targetSdkVersion")
    
    def get_effective_target_sdk_version(self) -> int:
        """
        Return the effective `targetSdkVersion`, always returns int > 0.

        If the `targetSdkVersion` is not set, it defaults to 1.  This is
        set based on defaults as defined in:
        <https://developer.android.com/guide/topics/manifest/uses-sdk-element.html>

        :returns: the effective `targetSdkVersion`
        """
        target_sdk_version = self.get_target_sdk_version()
        if not target_sdk_version:
            target_sdk_version = self.get_min_sdk_version()
        try:
            return int(target_sdk_version)
        except (ValueError, TypeError):
            return 1
        
    def get_libraries(self) -> list[str]:
        """
        Return the `android:name` attributes for libraries

        :returns: the `android:name` attributes
        """
        return list(self.get_all_attribute_value("uses-library", "name"))

    def get_features(self) -> list[str]:
        """
        Return a list of all `android:names` found for the tag `uses-feature`
        in the `AndroidManifest.xml`

        :returns: the `android:names` found
        """
        return list(self.get_all_attribute_value("uses-feature", "name"))

    def is_wearable(self) -> bool:
        """
        Checks if this application is build for wearables by
        checking if it uses the feature 'android.hardware.type.watch'
        See: https://developer.android.com/training/wearables/apps/creating.html for more information.

        Not every app is setting this feature (not even the example Google provides),
        so it might be wise to not 100% rely on this feature.

        :returns: `True` if wearable, `False` otherwise
        """
        return 'android.hardware.type.watch' in self.get_features()

    def is_leanback(self) -> bool:
        """
        Checks if this application is build for TV (Leanback support)
        by checkin if it uses the feature 'android.software.leanback'

        :returns: `True` if leanback feature is used, `False` otherwise
        """
        return 'android.software.leanback' in self.get_features()

    def is_androidtv(self) -> bool:
        """
        Checks if this application does not require a touchscreen,
        as this is the rule to get into the TV section of the Play Store
        See: https://developer.android.com/training/tv/start/start.html for more information.

        :returns: `True` if 'android.hardware.touchscreen' is not required, `False` otherwise
        """
        return (
            self.get_attribute_value(
                'uses-feature',
                'name',
                required="false",
                name="android.hardware.touchscreen",
            )
            == "android.hardware.touchscreen"
        )