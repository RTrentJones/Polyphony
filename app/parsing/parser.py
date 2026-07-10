"""Document parsing utilities for various file formats"""

import docx
import PyPDF2
from bs4 import BeautifulSoup
import os


class DocumentParser:
    """Parse various document formats into plain text"""

    SUPPORTED_FORMATS = [".txt", ".docx", ".pdf", ".html", ".htm"]

    def parse_document(self, file_path: str) -> str:
        """
        Parse document based on extension

        Args:
            file_path: Path to the document file

        Returns:
            Full text content as string

        Raises:
            ValueError: If file format is not supported
            FileNotFoundError: If file doesn't exist
        """
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"File not found: {file_path}")

        ext = os.path.splitext(file_path)[1].lower()

        if ext not in self.SUPPORTED_FORMATS:
            raise ValueError(
                f"Unsupported file type: {ext}. "
                f"Supported formats: {', '.join(self.SUPPORTED_FORMATS)}"
            )

        if ext == ".docx":
            return self._parse_docx(file_path)
        elif ext == ".pdf":
            return self._parse_pdf(file_path)
        elif ext == ".txt":
            return self._parse_txt(file_path)
        elif ext in [".html", ".htm"]:
            return self._parse_html(file_path)
        else:
            raise ValueError(f"Unsupported file type: {ext}")

    def _parse_docx(self, file_path: str) -> str:
        """Parse DOCX file"""
        try:
            doc = docx.Document(file_path)
            paragraphs = [para.text for para in doc.paragraphs if para.text.strip()]
            return "\n\n".join(paragraphs)
        except Exception as e:
            raise ValueError(f"Error parsing DOCX file: {e}")

    def _parse_pdf(self, file_path: str) -> str:
        """Parse PDF file"""
        try:
            text = []
            with open(file_path, "rb") as file:
                reader = PyPDF2.PdfReader(file)
                for page in reader.pages:
                    page_text = page.extract_text()
                    if page_text:
                        text.append(page_text)

            if not text:
                raise ValueError("No text could be extracted from PDF")

            return "\n\n".join(text)
        except Exception as e:
            raise ValueError(f"Error parsing PDF file: {e}")

    def _parse_txt(self, file_path: str) -> str:
        """Parse TXT file"""
        try:
            with open(file_path, "r", encoding="utf-8") as file:
                return file.read()
        except UnicodeDecodeError:
            # Try with different encoding
            try:
                with open(file_path, "r", encoding="latin-1") as file:
                    return file.read()
            except Exception as e:
                raise ValueError(f"Error parsing TXT file: {e}")

    def _parse_html(self, file_path: str) -> str:
        """Parse HTML file"""
        try:
            with open(file_path, "r", encoding="utf-8") as file:
                soup = BeautifulSoup(file.read(), "html.parser")

                # Remove script and style elements
                for script in soup(["script", "style"]):
                    script.decompose()

                # Get text
                text = soup.get_text()

                # Clean up whitespace
                lines = (line.strip() for line in text.splitlines())
                chunks = (
                    phrase.strip() for line in lines for phrase in line.split("  ")
                )
                text = "\n".join(chunk for chunk in chunks if chunk)

                return text
        except Exception as e:
            raise ValueError(f"Error parsing HTML file: {e}")

    def get_word_count(self, text: str) -> int:
        """Get word count from text"""
        return len(text.split())

    def get_paragraph_count(self, text: str) -> int:
        """Get paragraph count from text"""
        paragraphs = [p for p in text.split("\n\n") if p.strip()]
        return len(paragraphs)
