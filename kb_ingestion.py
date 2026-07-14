"""Safe, small-scale knowledge-base ingestion for the local demo."""

from __future__ import annotations

from html.parser import HTMLParser
from io import BytesIO
import ipaddress
import socket
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from pypdf import PdfReader

MAX_PDF_BYTES = 10 * 1024 * 1024
MAX_WEB_BYTES = 2 * 1024 * 1024
MAX_TEXT_CHARS = 100_000


class IngestionError(ValueError):
    pass


class _TextOnly(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript", "svg"}:
            self.skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "svg"} and self.skip_depth:
            self.skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self.skip_depth:
            self.parts.append(data)


def pdf_text(data: bytes) -> str:
    if not data:
        raise IngestionError("The PDF is empty.")
    if len(data) > MAX_PDF_BYTES:
        raise IngestionError("PDF is larger than the 10 MB demo limit.")
    try:
        reader = PdfReader(BytesIO(data))
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
    except Exception as error:
        raise IngestionError(f"Could not read this PDF: {error}") from error
    text = " ".join(text.split())
    if not text:
        raise IngestionError("No selectable text was found. Scanned PDFs need OCR before indexing.")
    return text[:MAX_TEXT_CHARS]


def _is_public_host(host: str) -> bool:
    if host.lower() in {"localhost", "localhost.localdomain"}:
        return False
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(host, None)}
        return bool(addresses) and all(not ipaddress.ip_address(address).is_private and
                                      not ipaddress.ip_address(address).is_loopback and
                                      not ipaddress.ip_address(address).is_link_local and
                                      not ipaddress.ip_address(address).is_reserved
                                      for address in addresses)
    except socket.gaierror:
        return False


def webpage_text(url: str) -> str:
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or not _is_public_host(parsed.hostname):
        raise IngestionError("Use a public http(s) URL. Local, private-network, and invalid addresses are blocked.")
    try:
        request = Request(parsed.geturl(), headers={"User-Agent": "Local-RAG-KB-Demo/1.0"})
        with urlopen(request, timeout=12) as response:  # nosec B310: host is validated above
            content_type = response.headers.get_content_type()
            if content_type not in {"text/html", "text/plain"}:
                raise IngestionError("The link must return an HTML or text page.")
            raw = response.read(MAX_WEB_BYTES + 1)
            if len(raw) > MAX_WEB_BYTES:
                raise IngestionError("Web page is larger than the 2 MB demo limit.")
            content = raw.decode(response.headers.get_content_charset() or "utf-8", errors="replace")
    except IngestionError:
        raise
    except Exception as error:
        raise IngestionError(f"Could not fetch the link: {error}") from error
    if content_type == "text/plain":
        text = content
    else:
        parser = _TextOnly()
        parser.feed(content)
        text = " ".join(parser.parts)
    text = " ".join(text.split())
    if not text:
        raise IngestionError("No readable text was found at this link.")
    return text[:MAX_TEXT_CHARS]
