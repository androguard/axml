class ResParserError(Exception):
    """Exception for the parsers"""

    pass


class NoDecoderFoundError(Exception):
    """Raised when no decoder is registered for a given type."""

    def __init__(self, decoder_type: str):
        message = f"No decoder found for type: '{decoder_type}'! Please open a bug report."
        super().__init__(message)
        self.decoder_type = decoder_type
