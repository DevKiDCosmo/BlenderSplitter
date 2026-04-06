import hashlib
import math


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


def tile_target_for_workers(worker_count: int, tile_coefficient: int = 1) -> int:
    worker_count = max(1, int(worker_count))
    tile_coefficient = max(1, int(tile_coefficient))

    # Use a "good" rectangular base split derived from active nodes.
    # Examples: 2 -> 2 tiles, 3 -> 4 tiles, 4 -> 4 tiles.
    gx, gy = grid_for_worker_count(worker_count)
    base_tiles = max(1, int(gx) * int(gy))
    multiplier = 2 ** max(0, tile_coefficient - 1)
    return base_tiles * multiplier


def grid_for_tile_count(tile_count: int, res_x: int, res_y: int) -> tuple[int, int]:
    tile_count = max(1, int(tile_count))
    res_x = max(1, int(res_x))
    res_y = max(1, int(res_y))

    target_aspect = float(res_x) / float(res_y)
    best = (tile_count, 1)
    best_score = float("inf")

    for tiles_x in range(1, tile_count + 1):
        if tile_count % tiles_x != 0:
            continue
        tiles_y = tile_count // tiles_x
        aspect = float(tiles_x) / float(tiles_y)
        score = abs(math.log(max(1e-9, aspect / target_aspect)))
        if score < best_score:
            best = (tiles_x, tiles_y)
            best_score = score

    return best


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
    camera = scene.camera
    camera_name = camera.name if camera else "none"
    camera_loc = [0.0, 0.0, 0.0]
    camera_rot = [0.0, 0.0, 0.0]
    if camera is not None:
        try:
            camera_loc = [round(float(v), 6) for v in camera.location]
        except Exception:
            camera_loc = [0.0, 0.0, 0.0]
        try:
            camera_rot = [round(float(v), 6) for v in camera.rotation_euler]
        except Exception:
            camera_rot = [0.0, 0.0, 0.0]

    cycles_samples = int(getattr(scene.cycles, "samples", 0)) if hasattr(scene, "cycles") else 0
    cycles_seed = int(getattr(scene.cycles, "seed", 0)) if hasattr(scene, "cycles") else 0
    eevee_samples = int(getattr(scene.eevee, "taa_render_samples", 0)) if hasattr(scene, "eevee") else 0

    payload = {
        "engine": str(render.engine),
        "camera": camera_name,
        "camera_location": camera_loc,
        "camera_rotation": camera_rot,
        "resolution_x": int(render.resolution_x),
        "resolution_y": int(render.resolution_y),
        "resolution_percentage": int(render.resolution_percentage),
        "pixel_aspect_x": round(float(getattr(render, "pixel_aspect_x", 1.0)), 6),
        "pixel_aspect_y": round(float(getattr(render, "pixel_aspect_y", 1.0)), 6),
        "samples": cycles_samples,
        "seed": cycles_seed,
        "eevee_samples": eevee_samples,
        "frame": int(scene.frame_current),
        "frame_start": int(scene.frame_start),
        "frame_end": int(scene.frame_end),
    }
    sig = hashlib.sha256(str(payload).encode("utf-8")).hexdigest()
    return payload, sig
