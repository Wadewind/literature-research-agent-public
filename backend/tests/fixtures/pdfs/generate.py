"""生成 Parser 契约测试用的合成 PDF Fixtures。

运行方式：``uv run python tests/fixtures/pdfs/generate.py``

产出（全部合成，不含版权内容，可提交）：
- ``text_two_pages.pdf``：两页 Helvetica 文本，验证多页文本提取与页级定位；
- ``blank.pdf``：一页空白，验证 ``possibly_scanned`` 警告；
- ``corrupted.pdf``：有 PDF 魔数但结构损坏，验证输入错误分类与降级；
- ``encrypted.pdf``：口令加密，验证加密文件的分类与降级。
"""

from pathlib import Path

from pypdf import PdfWriter

FIXTURE_DIR = Path(__file__).parent


def _text_pdf(path: Path, page_texts: list[str]) -> None:
    """手写最小 PDF 对象结构，生成带可提取文本的多页 PDF。"""
    objects: list[bytes] = []

    # 1: Catalog, 2: Pages, 3: Font, 之后每页两个对象（Page + Content）
    page_ids = [4 + i * 2 for i in range(len(page_texts))]
    kids = " ".join(f"{pid} 0 R" for pid in page_ids)
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {len(page_texts)} >>".encode())
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    for page_id, text in zip(page_ids, page_texts, strict=True):
        stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 3 0 R >> >> "
            f"/Contents {page_id + 1} 0 R >>".encode()
        )
        objects.append(
            b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream"
        )

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref_start = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_start}\n%%EOF\n"
    ).encode()
    path.write_bytes(bytes(out))


def _blank_pdf(path: Path) -> None:
    """用 pypdf 生成一页空白 PDF。"""
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    with path.open("wb") as fh:
        writer.write(fh)


def _corrupted_pdf(path: Path) -> None:
    """生成有 PDF 魔数但结构损坏的文件。"""
    path.write_bytes(b"%PDF-1.4\nthis is not a valid pdf body at all \xff\xfe\x00\n%%EOF\n")


def _encrypted_pdf(path: Path, password: str = "secret") -> None:
    """用 pypdf 生成口令加密的单页 PDF。"""
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    writer.encrypt(password)
    with path.open("wb") as fh:
        writer.write(fh)


def main() -> None:
    """重新生成全部 Fixtures（确定性输出）。"""
    _text_pdf(FIXTURE_DIR / "text_two_pages.pdf", ["Hello Page One", "Hello Page Two"])
    _blank_pdf(FIXTURE_DIR / "blank.pdf")
    _corrupted_pdf(FIXTURE_DIR / "corrupted.pdf")
    _encrypted_pdf(FIXTURE_DIR / "encrypted.pdf")


if __name__ == "__main__":
    main()
