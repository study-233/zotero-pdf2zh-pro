class PSException(Exception):  # noqa: N818 - keep pdfminer-compatible exception names
    pass


class PSTypeError(PSException):
    pass


class PDFException(PSException):
    pass


class PDFTypeError(PDFException, TypeError):
    pass


class PDFKeyError(PDFException, KeyError):
    pass
