from __future__ import annotations

from io import BytesIO

import qrcode
from qrcode.image.svg import SvgPathImage


def build_qr_svg(payload: str) -> str:
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=2,
    )
    qr.add_data(payload)
    qr.make(fit=True)
    image = qr.make_image(
        image_factory=SvgPathImage,
        fill_color="#172033",
        back_color="#ffffff",
    )
    output = BytesIO()
    image.save(output)
    return output.getvalue().decode("utf-8")
