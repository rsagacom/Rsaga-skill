import io
import stat
import unittest
import zipfile

from studio_api.documents import DocumentExtractionError, extract_document, extract_text


def _pdf_with_text(text: str) -> bytes:
    """Build a tiny valid Type1 PDF without adding a PDF generation dependency."""
    stream = f"BT /F1 18 Tf 20 100 Td ({text}) Tj ET\n".encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"endstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    document = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for number, body in enumerate(objects, 1):
        offsets.append(len(document))
        document.extend(f"{number} 0 obj\n".encode("ascii"))
        document.extend(body)
        document.extend(b"\nendobj\n")
    xref = len(document)
    document.extend(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode("ascii"))
    for offset in offsets[1:]:
        document.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    document.extend(
        f"trailer\n<< /Root 1 0 R /Size {len(objects) + 1} >>\nstartxref\n{xref}\n%%EOF\n".encode("ascii")
    )
    return bytes(document)


class DocumentExtractionTests(unittest.TestCase):
    def test_docx_and_epub_extract_text_without_external_parser(self):
        docx_xml = '''<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>第一段</w:t></w:r></w:p><w:p><w:r><w:t>第二段</w:t></w:r></w:p></w:body></w:document>'''.encode()
        docx = io.BytesIO()
        with zipfile.ZipFile(docx, "w") as archive:
            archive.writestr("word/document.xml", docx_xml)
        self.assertEqual(extract_text("story.docx", docx.getvalue()), "第一段\n第二段")

        epub = io.BytesIO()
        with zipfile.ZipFile(epub, "w") as archive:
            archive.writestr(
                "META-INF/container.xml",
                '<?xml version="1.0"?><container xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="OEBPS/package.opf"/></rootfiles></container>',
            )
            archive.writestr(
                "OEBPS/package.opf",
                '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf"><manifest><item id="two" href="chapter2.xhtml" media-type="application/xhtml+xml"/><item id="one" href="chapter1.xhtml" media-type="application/xhtml+xml"/></manifest><spine><itemref idref="one"/><itemref idref="two"/></spine></package>',
            )
            # Deliberately write chapter 2 first; EPUB spine order is authoritative.
            archive.writestr("OEBPS/chapter2.xhtml", "<html><body><p>第二章</p><script>忽略脚本</script></body></html>")
            archive.writestr("OEBPS/chapter1.xhtml", "<html><body><h1>第一章</h1><p>正文内容</p></body></html>")
        self.assertIn("正文内容", extract_text("story.epub", epub.getvalue()))
        ordered = extract_text("story.epub", epub.getvalue())
        self.assertLess(ordered.index("第一章"), ordered.index("第二章"))
        self.assertNotIn("忽略脚本", ordered)
        self.assertEqual(extract_document("story.epub", epub.getvalue()).media_type, "application/epub+zip")

    def test_pdf_extracts_text_and_keeps_pdf_media_type(self):
        extracted = extract_document("story.pdf", _pdf_with_text("Chapter one"))
        self.assertIn("Chapter one", extracted.text)
        self.assertEqual(extracted.media_type, "application/pdf")

    def test_archive_members_are_bounded_and_traversal_is_rejected(self):
        invalid = io.BytesIO()
        with zipfile.ZipFile(invalid, "w") as archive:
            archive.writestr("../word/document.xml", b"not allowed")
        with self.assertRaises(DocumentExtractionError):
            extract_text("story.docx", invalid.getvalue())

        symlink = io.BytesIO()
        with zipfile.ZipFile(symlink, "w") as archive:
            link = zipfile.ZipInfo("word/document.xml")
            link.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(link, b"word/document.xml")
        with self.assertRaises(DocumentExtractionError):
            extract_text("story.docx", symlink.getvalue())


if __name__ == "__main__":
    unittest.main()
