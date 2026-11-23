"""Un QR code à imprimer : la valeur d'un champ → une image PNG en `data:` URI (process/impression.md, bloc `qr`).

La MATRICE vient de `qrcode` (pur Python, aucune dépendance : l'encodage, la correction d'erreur Reed-Solomon et le choix du masque
ne s'improvisent pas). L'IMAGE, on l'écrit nous-mêmes — un PNG en niveaux de gris, `zlib` et `struct` : `qrcode` ne sait produire un
PNG qu'avec Pillow ou pypng, deux dépendances pour quarante lignes. Une image et pas un SVG, parce que l'impression ne prend des images
qu'en `data:` raster (`FileService` refuse le SVG) et qu'une image passe telle quelle du navigateur au moteur PDF.

Correction d'erreur M (15 %), celle que la DGI demande pour le QR d'une facture certifiée (spécification SECeF §5), et la zone calme
réglementaire de 4 modules. Chaque module fait `SCALE` pixels : l'image reste nette une fois réduite à quelques centimètres.
"""

from __future__ import annotations

import base64
import struct
import zlib

MAX_QR_TEXT = 2000  # caractères : au-delà, un QR M ne tient plus (version 40 ≈ 2 300 octets) et ne se lirait pas sur une facture
SCALE = 8
BORDER = 4


def matrix(text: str) -> list[list[bool]]:
    """Les modules du QR (True = noir), zone calme comprise."""
    import qrcode
    from qrcode.constants import ERROR_CORRECT_M

    qr = qrcode.QRCode(error_correction=ERROR_CORRECT_M, border=BORDER)
    qr.add_data(text)
    qr.make(fit=True)
    return [[bool(cell) for cell in row] for row in qr.get_matrix()]


def png(modules: list[list[bool]], scale: int = SCALE) -> bytes:
    """Un PNG 8 bits en niveaux de gris : un octet de filtre (0) par ligne, puis un octet par pixel (0 noir, 255 blanc)."""
    size = len(modules) * scale
    raw = bytearray()
    for row in modules:
        line = b"\x00" + b"".join((b"\x00" if dark else b"\xff") * scale for dark in row)
        raw += line * scale

    def chunk(kind: bytes, payload: bytes) -> bytes:
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", size, size, 8, 0, 0, 0, 0)  # largeur, hauteur, 8 bits, gris, compression, filtre, non entrelacé
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + chunk(b"IEND", b"")


def data_uri(text: str) -> str:
    """L'image du QR de `text`, prête pour un `<img src>`."""
    if len(text) > MAX_QR_TEXT:
        raise ValueError(f"un QR imprimé porte {MAX_QR_TEXT} caractères au plus")
    return "data:image/png;base64," + base64.b64encode(png(matrix(text))).decode("ascii")
