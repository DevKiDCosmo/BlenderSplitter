from pathlib import Path
import argparse
from PIL import Image


def create_trans_image(input_file: str) -> Path:
    src = Path(input_file)

    if not src.exists() or not src.is_file():
        raise FileNotFoundError(f"Datei nicht gefunden: {src}")

    img = Image.open(src).convert("RGBA")
    pixels = img.getdata()

    new_pixels = []
    for r, g, b, a in pixels:
        if (r, g, b) == (0, 0, 0):  # exakt #000000
            new_pixels.append((r, g, b, 0))  # transparent
        else:
            new_pixels.append((r, g, b, a))

    img.putdata(new_pixels)

    out = src.with_name(f"{src.stem}_trans.png")
    img.save(out)
    return out


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Macht alle exakten schwarzen Pixel (#000000) transparent."
    )
    parser.add_argument("datei", help="Pfad zur Eingabedatei (Bild)")
    args = parser.parse_args()

    output_file = create_trans_image(args.datei)
    print(f"Neue Datei erstellt: {output_file}")


if __name__ == "__main__":
    main()