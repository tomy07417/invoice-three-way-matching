"""Real synthetic documents exercise the public validation boundary."""
from hashlib import sha256
from io import BytesIO

import pytest
from PIL import Image
from pypdf import PdfWriter

from app.application.ports.invoice_documents import DocumentValidationError, DocumentValidator
from app.infrastructure.extraction.document_validator import LocalDocumentValidator


def pdf(pages=1, password=None):
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=72, height=72)
    if password:
        writer.encrypt(password, algorithm="RC4-128")
    stream = BytesIO()
    writer.write(stream)
    return stream.getvalue()


def image(kind="PNG", size=(12, 10)):
    stream = BytesIO()
    Image.new("RGB", size, "white").save(stream, format=kind)
    return stream.getvalue()


@pytest.mark.parametrize("name,mime,content,pages,width,height", [
    ("invoice.pdf", "application/pdf", pdf(2), 2, None, None),
    ("invoice.png", "image/png", image(), 1, 12, 10),
    ("invoice.jpeg", "image/jpeg", image("JPEG"), 1, 12, 10),
    ("invoice.JPG", "image/jpeg", image("JPEG"), 1, 12, 10),
])
def test_valid_documents(name, mime, content, pages, width, height):
    validator: DocumentValidator = LocalDocumentValidator()
    document = validator.validate(name, mime, content)
    assert document.content == content
    assert document.metadata.file_name == name
    assert document.metadata.media_type == mime
    assert document.metadata.byte_size == len(content)
    assert document.metadata.sha256 == sha256(content).hexdigest()
    assert document.metadata.page_count == pages
    assert (document.metadata.pixel_width, document.metadata.pixel_height) == (width, height)


@pytest.mark.parametrize("name,mime,content,code", [
    ("x.pdf", "application/pdf", b"private malformed invoice", "invalid_document"),
    ("x.pdf", "application/pdf", pdf(password="secret"), "invalid_document"),
    ("x.pdf", "application/pdf", pdf()[:-40], "invalid_document"),
    ("x.pdf", "application/pdf", pdf(0), "invalid_document"),
    ("x.png", "image/png", image()[:-15], "invalid_document"),
    ("x.jpg", "image/jpeg", image("JPEG")[:-20], "invalid_document"),
    ("x.png", "image/png", image("JPEG"), "invalid_document"),
    ("x.pdf", "application/pdf", image(), "invalid_document"),
    ("x.png", "application/pdf", image(), "unsupported_type"),
    ("x.gif", "image/gif", image(), "unsupported_type"),
    ("x", "image/png", image(), "unsupported_type"),
    ("x.png", "image/png", b"", "invalid_size"),
])
def test_rejects_invalid_documents_without_leaking_context(name, mime, content, code):
    with pytest.raises(DocumentValidationError) as caught:
        LocalDocumentValidator().validate(name, mime, content)
    assert caught.value.code == code
    assert caught.value.__context__ is None
    assert "private" not in str(caught.value) and "secret" not in str(caught.value)


def test_limits_and_inclusive_boundaries():
    content = image(size=(10, 10))
    assert LocalDocumentValidator(max_bytes=len(content), max_pixels=100).validate(
        "x.png", "image/png", content).content == content
    for validator, name, mime, data, code in [
        (LocalDocumentValidator(max_bytes=len(content)-1), "x.png", "image/png", content, "invalid_size"),
        (LocalDocumentValidator(max_pixels=99), "x.png", "image/png", content, "pixel_limit"),
        (LocalDocumentValidator(max_pages=1), "x.pdf", "application/pdf", pdf(2), "page_limit"),
    ]:
        with pytest.raises(DocumentValidationError) as caught:
            validator.validate(name, mime, data)
        assert caught.value.code == code
    assert LocalDocumentValidator(max_pages=2).validate("x.pdf", "application/pdf", pdf(2)).metadata.page_count == 2


def test_pixel_limit_precedes_decode(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("full decode must not run above the pixel limit")
    monkeypatch.setattr(Image.Image, "load", forbidden)
    with pytest.raises(DocumentValidationError) as caught:
        LocalDocumentValidator(max_pixels=1).validate("x.png", "image/png", image_bytes)
    assert caught.value.code == "pixel_limit"


image_bytes = image()


def test_filename_is_only_sanitized_metadata():
    document = LocalDocumentValidator().validate("../private\\invoice\n.png", "image/png", image())
    assert document.metadata.file_name == "invoice_.png"


@pytest.mark.parametrize("setting", ["max_bytes", "max_pages", "max_pixels"])
@pytest.mark.parametrize("value", [0, -1, True, 1.5])
def test_invalid_configuration(setting, value):
    with pytest.raises(ValueError):
        LocalDocumentValidator(**{setting: value})


def test_defaults_are_bounded():
    validator = LocalDocumentValidator()
    assert (validator.max_bytes, validator.max_pages, validator.max_pixels) == (8 * 1024 * 1024, 5, 16_000_000)
