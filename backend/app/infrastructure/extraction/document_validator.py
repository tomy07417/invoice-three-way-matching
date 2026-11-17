"""Bounded, content-aware validation only; PDFs are never rendered."""
from hashlib import sha256
from io import BytesIO
import re
import warnings

from PIL import Image
from pypdf import PdfReader

from app.application.dtos.invoice_documents import DocumentMetadata, ValidatedDocument
from app.application.ports.invoice_documents import DocumentValidationError


class LocalDocumentValidator:
    def __init__(self, *, max_bytes: int = 8 * 1024 * 1024,
                 max_pages: int = 5, max_pixels: int = 16_000_000):
        for value in (max_bytes, max_pages, max_pixels):
            if type(value) is not int or value <= 0:
                raise ValueError("Document limits must be positive integers")
        self.max_bytes = max_bytes
        self.max_pages = max_pages
        self.max_pixels = max_pixels

    def validate(self, file_name: str, content_type: str, content: bytes) -> ValidatedDocument:
        if not isinstance(content, bytes) or not 0 < len(content) <= self.max_bytes:
            raise DocumentValidationError("invalid_size", "Document size is not allowed")
        name = re.sub(r"[^\w. -]", "_", file_name.replace("\\", "/").split("/")[-1])[-200:]
        extension = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        types = {"pdf": "application/pdf", "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg"}
        if extension not in types or content_type != types[extension]:
            raise DocumentValidationError("unsupported_type", "Document type is not supported")
        # Raise outside the parser's exception handler: even implicit exception
        # context must not retain parser messages or document fragments.
        error = None
        pages, width, height = 1, None, None
        try:
            if content_type == "application/pdf":
                if not content.startswith(b"%PDF-") or not content.rstrip().endswith(b"%%EOF"):
                    raise ValueError()
                reader = PdfReader(BytesIO(content), strict=True)
                if reader.is_encrypted:
                    raise ValueError()
                pages = len(reader.pages)
                if pages == 0:
                    raise ValueError()
                if pages > self.max_pages:
                    error = ("page_limit", "Document exceeds the page limit")
            else:
                # Pillow checks dimensions at open, before full pixel decoding.
                with warnings.catch_warnings():
                    warnings.simplefilter("error", Image.DecompressionBombWarning)
                    with Image.open(BytesIO(content)) as image:
                        expected = "PNG" if content_type == "image/png" else "JPEG"
                        if image.format != expected:
                            raise ValueError()
                        width, height = image.size
                        if width * height > self.max_pixels:
                            error = ("pixel_limit", "Document exceeds the pixel limit")
                        else:
                            image.verify()
                    if error is None:
                        with Image.open(BytesIO(content)) as image:
                            image.load()
        except Exception:
            error = ("invalid_document", "Document is malformed, encrypted or unreadable")
        if error is not None:
            raise DocumentValidationError(*error)
        return ValidatedDocument(content, DocumentMetadata(
            name, content_type, len(content), sha256(content).hexdigest(), pages, width, height))
