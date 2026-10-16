"""Ground clicks: screenshot↔screen mapping, Vision OCR, Accessibility lookup."""

from __future__ import annotations

import io
from typing import Any


def map_image_to_screen(
    x: float,
    y: float,
    *,
    img_w: int,
    img_h: int,
    screen_w: int,
    screen_h: int,
) -> tuple[int, int]:
    """Map pixel coords in the model-visible screenshot to PyAutoGUI logical screen coords."""
    if img_w <= 0 or img_h <= 0:
        raise ValueError("Invalid screenshot size for coordinate mapping.")
    real_x = int(round(float(x) * float(screen_w) / float(img_w)))
    real_y = int(round(float(y) * float(screen_h) / float(img_h)))
    real_x = max(0, min(screen_w - 1, real_x))
    real_y = max(0, min(screen_h - 1, real_y))
    return real_x, real_y


def _pil_to_cgimage(image: Any) -> Any:
    from Cocoa import NSBitmapImageRep
    from PIL import Image

    if not isinstance(image, Image.Image):
        raise TypeError("Expected PIL Image")
    buf = io.BytesIO()
    image.convert("RGB").save(buf, format="PNG")
    data = buf.getvalue()
    rep = NSBitmapImageRep.alloc().initWithData_(data)
    if rep is None:
        raise RuntimeError("Failed to create NSBitmapImageRep for OCR.")
    cg = rep.CGImage()
    if cg is None:
        raise RuntimeError("Failed to get CGImage for OCR.")
    return cg


def ocr_find_text(image: Any, query: str, *, limit: int = 12) -> list[dict[str, Any]]:
    """Find text boxes in a PIL image using Apple Vision. Coords are top-left image pixels."""
    import Vision
    from Foundation import NSDictionary
    from Quartz import CGImageGetHeight, CGImageGetWidth

    query_norm = " ".join((query or "").lower().split())
    if not query_norm:
        raise RuntimeError("click_text requires a nonempty query.")

    cg = _pil_to_cgimage(image)
    img_w = int(CGImageGetWidth(cg))
    img_h = int(CGImageGetHeight(cg))

    request = Vision.VNRecognizeTextRequest.alloc().init()
    request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    request.setUsesLanguageCorrection_(True)

    handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(cg, NSDictionary.dictionary())
    ok, error = handler.performRequests_error_([request], None)
    if not ok:
        raise RuntimeError(f"Vision OCR failed: {error}")

    hits: list[dict[str, Any]] = []
    for observation in request.results() or []:
        candidates = observation.topCandidates_(3)
        if not candidates:
            continue
        best = candidates[0]
        text = str(best.string())
        conf = float(best.confidence())
        box = observation.boundingBox()  # normalized, origin bottom-left
        cx = (box.origin.x + box.size.width / 2.0) * img_w
        cy_bottom = (box.origin.y + box.size.height / 2.0) * img_h
        cy = img_h - cy_bottom  # top-left origin
        text_norm = " ".join(text.lower().split())
        score = 0.0
        if query_norm == text_norm:
            score = 100.0 + conf
        elif query_norm in text_norm:
            score = 80.0 + conf
        elif text_norm in query_norm and text_norm:
            score = 60.0 + conf
        else:
            # token overlap
            qt, tt = set(query_norm.split()), set(text_norm.split())
            if qt and tt and qt & tt:
                score = 40.0 * (len(qt & tt) / max(len(qt), 1)) + conf
        if score <= 0:
            continue
        hits.append(
            {
                "text": text,
                "confidence": conf,
                "score": score,
                "x": int(round(cx)),
                "y": int(round(cy)),
                "img_w": img_w,
                "img_h": img_h,
                "box": {
                    "x": box.origin.x,
                    "y": box.origin.y,
                    "w": box.size.width,
                    "h": box.size.height,
                },
            }
        )
    hits.sort(key=lambda h: h["score"], reverse=True)
    return hits[:limit]


def _ax_get(element: Any, attr: str) -> Any:
    from ApplicationServices import AXUIElementCopyAttributeValue

    err, value = AXUIElementCopyAttributeValue(element, attr, None)
    if err != 0:
        return None
    return value


def _ax_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def _ax_point_size(pos: Any, size: Any) -> tuple[float, float, float, float] | None:
    from ApplicationServices import AXValueGetValue, kAXValueCGPointType, kAXValueCGSizeType
    from Quartz import CGPoint, CGSize

    pt = CGPoint()
    sz = CGSize()
    if not AXValueGetValue(pos, kAXValueCGPointType, pt):
        return None
    if not AXValueGetValue(size, kAXValueCGSizeType, sz):
        return None
    return float(pt.x), float(pt.y), float(sz.width), float(sz.height)


def ax_find_elements(query: str, *, role: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
    """Search the focused app's accessibility tree for title/label matches. Screen coords = PyAutoGUI space."""
    from ApplicationServices import (
        AXUIElementCreateSystemWide,
        kAXChildrenAttribute,
        kAXDescriptionAttribute,
        kAXFocusedApplicationAttribute,
        kAXPositionAttribute,
        kAXRoleAttribute,
        kAXSizeAttribute,
        kAXTitleAttribute,
        kAXValueAttribute,
    )
    import Quartz

    query_norm = " ".join((query or "").lower().split())
    if not query_norm:
        raise RuntimeError("click_element requires a nonempty query.")

    system = AXUIElementCreateSystemWide()
    app = _ax_get(system, kAXFocusedApplicationAttribute)
    if app is None:
        raise RuntimeError(
            "No focused application for Accessibility lookup. "
            "Enable Accessibility for the God worker (Terminal/uv/python)."
        )

    role_filter = (role or "").strip().lower() or None
    hits: list[dict[str, Any]] = []
    main = Quartz.CGDisplayBounds(Quartz.CGMainDisplayID())
    screen_h = float(main.size.height)

    def walk(el: Any, depth: int = 0) -> None:
        if depth > 25 or len(hits) >= limit * 3:
            return
        title = _ax_str(_ax_get(el, kAXTitleAttribute))
        desc = _ax_str(_ax_get(el, kAXDescriptionAttribute))
        value = _ax_str(_ax_get(el, kAXValueAttribute))
        el_role = _ax_str(_ax_get(el, kAXRoleAttribute))
        label = " ".join(p for p in (title, desc, value) if p).strip()
        label_norm = " ".join(label.lower().split())
        if label_norm and (role_filter is None or role_filter in el_role.lower()):
            score = 0.0
            if query_norm == label_norm:
                score = 100.0
            elif query_norm in label_norm:
                score = 80.0
            elif label_norm in query_norm:
                score = 60.0
            else:
                qt, lt = set(query_norm.split()), set(label_norm.split())
                if qt & lt:
                    score = 40.0 * (len(qt & lt) / max(len(qt), 1))
            if score > 0:
                pos = _ax_get(el, kAXPositionAttribute)
                size = _ax_get(el, kAXSizeAttribute)
                if pos is not None and size is not None:
                    parsed = _ax_point_size(pos, size)
                    if parsed is not None:
                        px, py, sw, sh = parsed
                        # AX uses bottom-left origin; PyAutoGUI uses top-left.
                        cx = int(round(px + sw / 2.0))
                        cy = int(round(screen_h - (py + sh / 2.0)))
                        hits.append(
                            {
                                "label": label,
                                "role": el_role,
                                "score": score,
                                "x": cx,
                                "y": cy,
                                "width": int(sw),
                                "height": int(sh),
                            }
                        )
        children = _ax_get(el, kAXChildrenAttribute) or []
        for child in children:
            walk(child, depth + 1)

    walk(app)
    hits.sort(key=lambda h: h["score"], reverse=True)
    return hits[:limit]
