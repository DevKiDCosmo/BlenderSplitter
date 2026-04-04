import hashlib


def overlap_pixels(res_x: int, res_y: int, overlap_percent: float) -> int:
    base = min(int(res_x), int(res_y))
    px = int(base * float(overlap_percent) / 100.0)
    return max(2, px)


def grid_for_worker_count(count: int) -> tuple[int, int]:
    count = max(1, int(count))
    gx = int(count ** 0.5)
    gy = gx
    while gx * gy < count:
        if gx <= gy:
            gx += 1
        else:
            gy += 1
    return gx, gy


def generate_tiles(res_x: int, res_y: int, tiles_x: int, tiles_y: int, overlap: int = 0) -> list[dict]:
    res_x = max(1, int(res_x))
    res_y = max(1, int(res_y))
    tiles_x = max(1, int(tiles_x))
    tiles_y = max(1, int(tiles_y))
    overlap = max(0, int(overlap))

    tile_w = res_x // tiles_x
    tile_h = res_y // tiles_y

    results: list[dict] = []
    tile_id = 0
    for y in range(tiles_y):
        for x in range(tiles_x):
            core_min_x = x * tile_w
            core_max_x = res_x if x == tiles_x - 1 else (x + 1) * tile_w
            core_min_y = y * tile_h
            core_max_y = res_y if y == tiles_y - 1 else (y + 1) * tile_h

            min_x = max(0, core_min_x - overlap)
            max_x = min(res_x, core_max_x + overlap)
            min_y = max(0, core_min_y - overlap)
            max_y = min(res_y, core_max_y + overlap)

            results.append(
                {
                    "id": str(tile_id),
                    "min_x": min_x,
                    "max_x": max_x,
                    "min_y": min_y,
                    "max_y": max_y,
                    "core_min_x": core_min_x,
                    "core_max_x": core_max_x,
                    "core_min_y": core_min_y,
                    "core_max_y": core_max_y,
                }
            )
            tile_id += 1
    return results


def collect_render_signature(scene) -> tuple[dict, str]:
    render = scene.render
    camera_name = scene.camera.name if scene.camera else "none"
    payload = {
        "engine": str(render.engine),
        "camera": camera_name,
        "resolution_x": int(render.resolution_x),
        "resolution_y": int(render.resolution_y),
        "resolution_percentage": int(render.resolution_percentage),
        "samples": int(getattr(scene.cycles, "samples", 0)) if hasattr(scene, "cycles") else 0,
        "frame": int(scene.frame_current),
    }
    sig = hashlib.sha256(str(payload).encode("utf-8")).hexdigest()
    return payload, sig
