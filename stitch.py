from PIL import Image


def stitch_tiles(tile_results: list[dict], width: int, height: int, output_path: str) -> None:
    width = max(1, int(width))
    height = max(1, int(height))

    base = Image.new("RGBA", (width, height), (0, 0, 0, 0))

    ordered = sorted(
        tile_results,
        key=lambda t: (
            int(t.get("core_min_y", t.get("min_y", 0))),
            int(t.get("core_min_x", t.get("min_x", 0))),
            str(t.get("tile_id", "")),
        ),
    )

    for tile in ordered:
        tile_img = Image.open(tile["path"]).convert("RGBA")

        min_x = int(tile["min_x"])
        min_y = int(tile["min_y"])
        core_min_x = int(tile.get("core_min_x", min_x))
        core_max_x = int(tile.get("core_max_x", int(tile["max_x"])))
        core_min_y = int(tile.get("core_min_y", min_y))
        core_max_y = int(tile.get("core_max_y", int(tile["max_y"])))

        left = max(0, core_min_x - min_x)
        right = left + max(1, core_max_x - core_min_x)
        top = max(0, core_min_y - min_y)
        bottom = top + max(1, core_max_y - core_min_y)

        crop = tile_img.crop((left, top, right, bottom))
        base.alpha_composite(crop, (core_min_x, core_min_y))

    base.save(output_path)
