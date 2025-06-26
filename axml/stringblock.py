from __future__ import annotations

import logging
from struct import unpack
from typing import BinaryIO

from axml.constants import UTF8_FLAG
from axml.exceptions import ResParserError


class StringBlock:
    """
    StringBlock is a CHUNK inside an AXML File: `ResStringPool_header`
    It contains all strings, which are used by referencing to ID's

    See http://androidxref.com/9.0.0_r3/xref/frameworks/base/libs/androidfw/include/androidfw/ResourceTypes.h#436
    """

    def __init__(self, buff: BinaryIO, header: "ARSCHeader") -> None:
        """
        :param buff: buffer which holds the string block
        :param header: a instance of [ARSCHeader][androguard.core.axml.ARSCHeader]
        """
        self._cache = {}
        self.header = header
        # We already read the header (which was chunk_type and chunk_size
        # Now, we read the string_count:
        self.stringCount = unpack('<I', buff.read(4))[0]
        # style_count
        self.styleCount = unpack('<I', buff.read(4))[0]

        # flags
        self.flags = unpack('<I', buff.read(4))[0]
        self.m_isUTF8 = (self.flags & UTF8_FLAG) != 0

        # string_pool_offset
        # The string offset is counted from the beginning of the string section
        self.stringsOffset = unpack('<I', buff.read(4))[0]
        # check if the stringCount is correct
        if (
            self.stringsOffset - (self.styleCount * 4 + 28)
        ) / 4 != self.stringCount:
            self.stringCount = int(
                (self.stringsOffset - (self.styleCount * 4 + 28)) / 4
            )

        # style_pool_offset
        # The styles offset is counted as well from the beginning of the string section
        self.stylesOffset = unpack('<I', buff.read(4))[0]

        # Check if they supplied a stylesOffset even if the count is 0:
        if self.styleCount == 0 and self.stylesOffset > 0:
            logging.info(
                "Styles Offset given, but styleCount is zero. "
                "This is not a problem but could indicate packers."
            )

        self.m_stringOffsets = []
        self.m_styleOffsets = []
        self.m_charbuff = ""
        self.m_styles = []

        # Next, there is a list of string following.
        # This is only a list of offsets (4 byte each)
        for i in range(self.stringCount):
            self.m_stringOffsets.append(unpack('<I', buff.read(4))[0])

        # And a list of styles
        # again, a list of offsets
        for i in range(self.styleCount):
            self.m_styleOffsets.append(unpack('<I', buff.read(4))[0])

        # FIXME it is probably better to parse n strings and not calculate the size
        size = self.header.size - self.stringsOffset

        # if there are styles as well, we do not want to read them too.
        # Only read them, if no
        if self.stylesOffset != 0 and self.styleCount != 0:
            size = self.stylesOffset - self.stringsOffset

        if (size % 4) != 0:
            logging.warning("Size of strings is not aligned by four bytes.")

        self.m_charbuff = buff.read(size)

        if self.stylesOffset != 0 and self.styleCount != 0:
            size = self.header.size - self.stylesOffset

            if (size % 4) != 0:
                logging.warning("Size of styles is not aligned by four bytes.")

            for i in range(0, size // 4):
                self.m_styles.append(unpack('<I', buff.read(4))[0])

    def __repr__(self):
        return "<StringPool #strings={}, #styles={}, UTF8={}>".format(
            self.stringCount, self.styleCount, self.m_isUTF8
        )

    def __getitem__(self, idx):
        """
        Returns the string at the index in the string table

        :returns: the string
        """
        return self.getString(idx)

    def __len__(self):
        """
        Get the number of strings stored in this table

        :return: the number of strings
        """
        return self.stringCount

    def __iter__(self):
        """
        Iterable over all strings

        :returns: a generator over all strings
        """
        for i in range(self.stringCount):
            yield self.getString(i)

    def getString(self, idx: int) -> str:
        """
        Return the string at the index in the string table

        :param idx: index in the string table
        :return: the string
        """
        if idx in self._cache:
            return self._cache[idx]

        if idx < 0 or not self.m_stringOffsets or idx >= self.stringCount:
            return ""

        offset = self.m_stringOffsets[idx]

        if self.m_isUTF8:
            self._cache[idx] = self._decode8(offset)
        else:
            self._cache[idx] = self._decode16(offset)

        return self._cache[idx]

    def getStyle(self, idx: int) -> int:
        """
        Return the style associated with the index

        :param idx: index of the style
        :return: the style integer
        """
        return self.m_styles[idx]

    def _decode8(self, offset: int) -> str:
        """
        Decode an UTF-8 String at the given offset

        :param offset: offset of the string inside the data
        :raises ResParserError: if string is not null terminated
        :return: the decoded string
        """
        # UTF-8 Strings contain two lengths, as they might differ:
        # 1) the UTF-16 length
        str_len, skip = self._decode_length(offset, 1)
        offset += skip

        # 2) the utf-8 string length
        encoded_bytes, skip = self._decode_length(offset, 1)
        offset += skip

        # Two checks should happen here:
        # a) offset + encoded_bytes surpassing the string_pool length and
        # b) non-null terminated strings which should be rejected
        # platform/frameworks/base/libs/androidfw/ResourceTypes.cpp#789
        if len(self.m_charbuff) < (offset + encoded_bytes):
            logging.warning(
                f"String size: {offset + encoded_bytes} is exceeding string pool size. Returning empty string."
            )
            return ""
        data = self.m_charbuff[offset : offset + encoded_bytes]

        if self.m_charbuff[offset + encoded_bytes] != 0:
            raise ResParserError(
                "UTF-8 String is not null terminated! At offset={}".format(
                    offset
                )
            )

        return self._decode_bytes(data, 'utf-8', str_len)

    def _decode16(self, offset: int) -> str:
        """
        Decode an UTF-16 String at the given offset

        :param offset: offset of the string inside the data
        :raises ResParserError: if string is not null terminated

        :return: the decoded string
        """
        str_len, skip = self._decode_length(offset, 2)
        offset += skip

        # The len is the string len in utf-16 units
        encoded_bytes = str_len * 2

        # Two checks should happen here:
        # a) offset + encoded_bytes surpassing the string_pool length and
        # b) non-null terminated strings which should be rejected
        # platform/frameworks/base/libs/androidfw/ResourceTypes.cpp#789
        if len(self.m_charbuff) < (offset + encoded_bytes):
            logging.warning(
                f"String size: {offset + encoded_bytes} is exceeding string pool size. Returning empty string."
            )
            return ""

        data = self.m_charbuff[offset : offset + encoded_bytes]

        if (
            self.m_charbuff[
                offset + encoded_bytes : offset + encoded_bytes + 2
            ]
            != b"\x00\x00"
        ):
            raise ResParserError(
                "UTF-16 String is not null terminated! At offset={}".format(
                    offset
                )
            )

        return self._decode_bytes(data, 'utf-16', str_len)

    @staticmethod
    def _decode_bytes(data: bytes, encoding: str, str_len: int) -> str:
        """
        Generic decoding with length check.
        The string is decoded from bytes with the given encoding, then the length
        of the string is checked.
        The string is decoded using the "replace" method.

        :param data: bytes
        :param encoding: encoding name ("utf-8" or "utf-16")
        :param str_len: length of the decoded string
        :return: the decoded bytes
        """
        string = data.decode(encoding, 'replace')
        if len(string) != str_len:
            logging.warning("invalid decoded string length")
        return string

    def _decode_length(self, offset: int, sizeof_char: int) -> tuple[int, int]:
        """
        Generic Length Decoding at offset of string

        The method works for both 8 and 16 bit Strings.
        Length checks are enforced:
        * 8 bit strings: maximum of 0x7FFF bytes (See
        http://androidxref.com/9.0.0_r3/xref/frameworks/base/libs/androidfw/ResourceTypes.cpp#692)
        * 16 bit strings: maximum of 0x7FFFFFF bytes (See
        http://androidxref.com/9.0.0_r3/xref/frameworks/base/libs/androidfw/ResourceTypes.cpp#670)

        :param offset: offset into the string data section of the beginning of
        the string
        :param sizeof_char: number of bytes per char (1 = 8bit, 2 = 16bit)
        :returns: tuple of (length, read bytes)
        """
        sizeof_2chars = sizeof_char << 1
        fmt = "<2{}".format('B' if sizeof_char == 1 else 'H')
        highbit = 0x80 << (8 * (sizeof_char - 1))

        length1, length2 = unpack(
            fmt, self.m_charbuff[offset : (offset + sizeof_2chars)]
        )

        if (length1 & highbit) != 0:
            length = ((length1 & ~highbit) << (8 * sizeof_char)) | length2
            size = sizeof_2chars
        else:
            length = length1
            size = sizeof_char

        # These are true asserts, as the size should never be less than the values
        if sizeof_char == 1:
            assert (
                length <= 0x7FFF
            ), "length of UTF-8 string is too large! At offset={}".format(
                offset
            )
        else:
            assert (
                length <= 0x7FFFFFFF
            ), "length of UTF-16 string is too large!  At offset={}".format(
                offset
            )

        return length, size

    def show(self) -> None:
        """
        Print some information on stdout about the string table
        """
        print(
            "StringBlock(stringsCount=0x%x, "
            "stringsOffset=0x%x, "
            "stylesCount=0x%x, "
            "stylesOffset=0x%x, "
            "flags=0x%x"
            ")"
            % (
                self.stringCount,
                self.stringsOffset,
                self.styleCount,
                self.stylesOffset,
                self.flags,
            )
        )

        if self.stringCount > 0:
            print()
            print("String Table: ")
            for i, s in enumerate(self):
                print("{:08d} {}".format(i, repr(s)))

        if self.styleCount > 0:
            print()
            print("Styles Table: ")
            for i in range(self.styleCount):
                print("{:08d} {}".format(i, repr(self.getStyle(i))))