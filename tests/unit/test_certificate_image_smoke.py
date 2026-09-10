"""Exercise the same offline image contract used by the packaged executable."""
from certificate_image_smoke import run_image_smoke_test


def test_certificate_image_formats_produce_recognition_requests():
    run_image_smoke_test()
