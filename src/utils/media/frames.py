# Local video inspection with PyAV: what a file contains (probe) and which frames are
# worth showing a vision model (sample_frames). Both are CPU-bound; run them in an executor.
import time

import av
import cv2
import numpy as np


def probe(path: str) -> dict:
    with av.open(path) as c:
        video = next((s for s in c.streams if s.type == "video"), None)
        audio = next((s for s in c.streams if s.type == "audio"), None)
        return {
            "duration": round(c.duration / av.time_base, 2) if c.duration else None,
            "width": video.codec_context.width if video else None,
            "height": video.codec_context.height if video else None,
            "has_video": video is not None,
            "has_audio": audio is not None,
        }


def sample_frames(path: str, *, fps: float = 2.0, scan_fps: float = 10.0, change_threshold: float = 0.002,
                  max_frames: int = 150, max_side: int = 1280, jpeg_quality: int = 90) -> tuple[list[dict], dict]:
    # Decodes the video in order (no seeking) and keeps a frame when one is due at `fps`,
    # when the picture changed enough since the last kept frame (a new caption, a cut), and
    # always the first and last frames, since title cards and punchlines live there.
    # Timestamps come from each frame's pts, so variable-frame-rate video stays correct.
    # Over max_frames, frames are thinned evenly across the video.
    # -> ([{"t", "reason", "jpeg", "size"}], stats)
    step, scan_step = 1 / fps, 1 / scan_fps
    kept, last_small, last_t, next_due, next_scan = [], None, -1e9, 0.0, 0.0
    decoded, final, rotation = 0, None, 0
    started = time.monotonic()
    with av.open(path) as c:
        stream = c.streams.video[0]
        stream.thread_type = "AUTO"
        for frame in c.decode(stream):
            decoded += 1
            t = float(frame.time or 0.0)
            rotation = getattr(frame, "rotation", 0) or rotation
            final = (frame, t)
            if t < next_scan and last_small is not None:
                continue
            next_scan = t + scan_step
            small_h = max(2, round(480 * frame.height / frame.width / 2) * 2)
            small = frame.reformat(width=480, height=small_h, format="gray").to_ndarray()
            reason = None
            if last_small is None:
                reason = "start"
            else:
                changed = np.count_nonzero(cv2.absdiff(small, last_small) > 30) / small.size
                if changed >= change_threshold and t - last_t >= 0.2:
                    reason = "change"
                elif t >= next_due and (changed > 0.0002 or t - last_t >= 3.0):
                    reason = "regular"  # skip exact repeats of a static screen, but keep some coverage
            if t >= next_due:
                next_due = t + step
            if reason:
                kept.append(_encode(frame, t, reason, max_side, jpeg_quality))
                last_small, last_t = small, t
        if final and final[1] - last_t > 0.1:
            kept.append(_encode(final[0], final[1], "end", max_side, jpeg_quality))
    dropped = 0
    if len(kept) > max_frames:
        keep = sorted(set(np.linspace(0, len(kept) - 1, max_frames).round().astype(int)))
        dropped = len(kept) - len(keep)
        kept = [kept[i] for i in keep]
    if rotation:
        # Not handled yet (no test clip had it); frames would come out sideways.
        print(f"[media] video has rotation metadata ({rotation}); frames are not rotated")
    return kept, {"decoded": decoded, "decode_s": round(time.monotonic() - started, 2), "dropped": dropped}


def _encode(frame, t, reason, max_side, jpeg_quality):
    img = frame.to_ndarray(format="bgr24")
    h, w = img.shape[:2]
    if max_side and max(h, w) > max_side:
        scale = max_side / max(h, w)
        img = cv2.resize(img, (round(w * scale), round(h * scale)), interpolation=cv2.INTER_AREA)
    _, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, jpeg_quality])
    return {"t": t, "reason": reason, "jpeg": buf.tobytes(), "size": f"{img.shape[1]}x{img.shape[0]}"}
