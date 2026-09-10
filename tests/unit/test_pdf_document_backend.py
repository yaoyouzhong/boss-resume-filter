"""PDF extraction edge cases shared by resumes and certificates."""
from pathlib import Path
from tempfile import TemporaryDirectory

from education_pdf_images import extract_pdf_text, render_preview_page
from tests.pdf_samples import write_pdf


def test_text_crossing_page_edge_is_not_silently_lost():
    with TemporaryDirectory() as directory:
        path = Path(directory) / "edge.pdf"
        write_pdf(path, [b"BT /F1 12 Tf 190 50 Td (ABCDEF) Tj ET"])
        assert "ABCDEF" in extract_pdf_text(path)


def test_invalid_pdf_is_classified_without_native_exception():
    with TemporaryDirectory() as directory:
        path = Path(directory) / "invalid.pdf"
        path.write_bytes(b"invalid")
        for action, expected in ((extract_pdf_text, RuntimeError), (render_preview_page, ValueError)):
            try:
                action(path)
            except expected as error:
                assert "PDF" in str(error)
            else:
                raise AssertionError("Invalid PDF must fail")


def test_encrypted_pdf_requires_decryption():
    import base64

    with TemporaryDirectory() as directory:
        path = Path(directory) / "encrypted.pdf"
        path.write_bytes(base64.b64decode(
            'JVBERi0xLjcKJcK1wrYKJSBXcml0dGVuIGJ5IE11UERGIDEuMjkuMAoKMSAwIG9iago8PC9UeXBlL0NhdGFsb2cvUGFnZXMgMiAwIFIvSW5mbzw8L1Byb2R1Y2VyPEUyNDEwMEMwRTkzREU0RjM2NEU2RDdCODhEN0MyMjFBMEQ0Q0M0NjkxRkY2QUZCNEVDNEQ5RENDMDJDQkZBMzU+Pj4+PgplbmRvYmoKCjIgMCBvYmoKPDwvVHlwZS9QYWdlcy9Db3VudCAxL0tpZHNbNCAwIFJdPj4KZW5kb2JqCgozIDAgb2JqCjw8Pj4KZW5kb2JqCgo0IDAgb2JqCjw8L1R5cGUvUGFnZS9NZWRpYUJveFswIDAgNTk1IDg0Ml0vUm90YXRlIDAvUmVzb3VyY2VzIDMgMCBSL1BhcmVudCAyIDAgUj4+CmVuZG9iagoKeHJlZgowIDUKMDAwMDAwMDAwMCA2NTUzNSBmIAowMDAwMDAwMDQyIDAwMDAwIG4gCjAwMDAwMDAxNzIgMDAwMDAgbiAKMDAwMDAwMDIyNCAwMDAwMCBuIAowMDAwMDAwMjQ1IDAwMDAwIG4gCgp0cmFpbGVyCjw8L1NpemUgNS9Sb290IDEgMCBSL0lEWzxDMzhCQzI5MDY0QzJBQ0MyODYzQUMzOUIwMjQ4QzNCMz48NDEzMDgyMTA2N0UxRkJCMTI4OURDNEMwQThDMTBDNDI+XS9FbmNyeXB0PDwvRmlsdGVyL1N0YW5kYXJkL1IgNi9WIDUvTGVuZ3RoIDI1Ni9QIC00L0VuY3J5cHRNZXRhZGF0YSB0cnVlL1N0bUYvU3RkQ0YvU3RyRi9TdGRDRi9DRjw8L1N0ZENGPDwvQXV0aEV2ZW50L0RvY09wZW4vQ0ZNL0FFU1YzL0xlbmd0aCAzMj4+Pj4vTzwwMzI5NkIxMzUxRThDQTg5NTYyNjI1MUVCRjQ0REZENkIxQjk4MkMyQzMyQzJGQjcwMTExMDBDRDU1MUE3MTI3Q0JCNjQxQUI2QTFCMzU3OTUzMTRDMjYxMDU0RTAxMEE+L1U8MUQ4RjNGMTI1NzlGMzUzMDBDNEJFQ0JBMjIyMzZFNDM0QUM3REFENjdERTE5MDkwQTEwQUM0REZEMkExNUM5ODI3N0VBNTA1QTA3NTE5QUJBOEZCNkNBNUY4Mzg0RTQxPi9PRTw0QjBEQTE1MTZERTI4OTgxMURDOTYxMDg4Mjk0QUJGRTA3OTUwMEU1RTI0N0UzQjNBMjQ2NjQxQkRBMkFBRjU3Pi9VRTxBMjJERENCQjUxRDUzRTVBQkE5MDY5MjFFNDY1QThEMTI2QkNFQzA5ODNBRDBDMzc5MTM3MDdCQ0MxQUE2QkVEPi9QZXJtczwxNkVEMjdFMUMyQzE0NjMyNUNGNzc1MTMwMUNBMjdBQT4+Pj4+CnN0YXJ0eHJlZgozMzYKJSVFT0YK'
        ))
        try:
            extract_pdf_text(path)
        except RuntimeError as error:
            assert "加密" in str(error)
        else:
            raise AssertionError("Encrypted PDF must fail")
