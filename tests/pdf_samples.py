"""Small deterministic PDF fixtures independent of any PDF reader/renderer."""
from pathlib import Path


def write_pdf(path: Path, streams: list[bytes], *, width: int = 200,
              height: int = 100, rotation: int = 0) -> None:
    """Write pages with standard-font text or graphics and a valid xref table."""
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>", b"",
               b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    kids = []
    for stream in streams:
        page_id = len(objects) + 1
        kids.append(f"{page_id} 0 R")
        objects.append((f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {width} {height}] "
                        f"/Rotate {rotation} /Resources << /Font << /F1 3 0 R >> >> "
                        f"/Contents {page_id+1} 0 R >>").encode())
        objects.append(f"<< /Length {len(stream)} >>\nstream\n".encode()+stream+b"\nendstream")
    objects[1] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(kids)} >>".encode()
    output = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, obj in enumerate(objects, 1):
        offsets.append(len(output))
        output.extend(f"{index} 0 obj\n".encode()+obj+b"\nendobj\n")
    xref = len(output)
    output.extend(f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode())
    for offset in offsets[1:]:
        output.extend(f"{offset:010} 00000 n \n".encode())
    output.extend((f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\n"
                   f"startxref\n{xref}\n%%EOF\n").encode())
    path.write_bytes(output)
