import io
import struct
import unittest
import warnings
import zlib
from uuid import uuid4

from PIL import Image

from app.services.comfy_annotation import (
    ComfyAnnotationClient,
    ComfyAnnotationError,
    prepare_annotation_image,
)


class PreparedAnnotationImageTests(unittest.TestCase):
    @staticmethod
    def _png_chunk(kind: bytes, data: bytes) -> bytes:
        checksum = zlib.crc32(kind + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", checksum)

    def test_exif_orientation_is_applied_and_metadata_is_removed(self) -> None:
        source = Image.new("RGB", (2, 3), "red")
        exif = Image.Exif()
        exif[274] = 6
        encoded = io.BytesIO()
        source.save(encoded, format="JPEG", exif=exif)

        with Image.open(io.BytesIO(encoded.getvalue())) as loaded:
            prepared = prepare_annotation_image(loaded)

        self.assertEqual((prepared.width, prepared.height), (3, 2))
        self.assertEqual(prepared.media_type, "image/jpeg")
        with Image.open(io.BytesIO(prepared.data)) as normalized:
            self.assertEqual(normalized.size, (3, 2))
            self.assertEqual(dict(normalized.getexif()), {})
            self.assertNotIn("icc_profile", normalized.info)

    def test_transparency_uses_metadata_free_png(self) -> None:
        source = Image.new("RGBA", (4, 5), (10, 20, 30, 64))
        source.info["comment"] = "must not cross the boundary"

        prepared = prepare_annotation_image(source)

        self.assertEqual(prepared.media_type, "image/png")
        with Image.open(io.BytesIO(prepared.data)) as normalized:
            self.assertEqual(normalized.mode, "RGB")
            self.assertEqual(normalized.getpixel((0, 0)), (194, 196, 199))
            self.assertNotIn("comment", normalized.info)

    def test_animated_input_must_be_reduced_explicitly(self) -> None:
        encoded = io.BytesIO()
        first = Image.new("RGB", (2, 2), "red")
        second = Image.new("RGB", (2, 2), "blue")
        first.save(
            encoded,
            format="GIF",
            save_all=True,
            append_images=[second],
        )

        with Image.open(io.BytesIO(encoded.getvalue())) as animated:
            with self.assertRaises(ComfyAnnotationError) as raised:
                prepare_annotation_image(animated)

        self.assertEqual(raised.exception.code, "invalid-image")

    def test_oversized_header_is_rejected_before_pixel_decode(self) -> None:
        ihdr = struct.pack(">IIBBBBB", 10_000, 5_000, 8, 2, 0, 0, 0)
        encoded = (
            b"\x89PNG\r\n\x1a\n"
            + self._png_chunk(b"IHDR", ihdr)
            + self._png_chunk(b"IEND", b"")
        )
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", Image.DecompressionBombWarning)
            source = Image.open(io.BytesIO(encoded))
        with source:
            with self.assertRaises(ComfyAnnotationError) as raised:
                prepare_annotation_image(source)

        self.assertEqual(raised.exception.code, "input-limit-exceeded")

    def test_client_rejects_unallowlisted_profile_before_socket_io(self) -> None:
        client = ComfyAnnotationClient.__new__(ComfyAnnotationClient)
        client.socket_path = None
        client.timeout_seconds = 1

        with self.assertRaises(ComfyAnnotationError) as raised:
            client.annotate(
                attempt_id=uuid4(),
                profile_id="arbitrary-workflow",
                image=Image.new("RGB", (1, 1)),
            )

        self.assertEqual(raised.exception.code, "unknown-profile")
        self.assertEqual(
            raised.exception.message,
            "annotation profile is not supported by this client",
        )


if __name__ == "__main__":
    unittest.main()
