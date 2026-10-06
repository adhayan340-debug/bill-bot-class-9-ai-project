from __future__ import annotations

import argparse
import json
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

import cv2
from ultralytics import YOLO


DEFAULT_STREAM = "http://192.168.4.1:81/stream"
DEFAULT_MODEL = "best.pt"
DEFAULT_PRICES = "prices.json"


def load_prices(path: Path) -> dict[str, float]:
    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)
    return {str(name): float(price) for name, price in data.items()}


def get_class_name(names: dict | list, class_id: int) -> str:
    if isinstance(names, dict):
        return str(names.get(class_id, class_id))
    return str(names[class_id])


def save_receipt(items: Counter[str], prices: dict[str, float], output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    receipt_path = output_dir / f"receipt_{datetime.now():%Y%m%d_%H%M%S}.txt"
    total = sum(prices.get(name, 0.0) * count for name, count in items.items())
    lines = ["AUTO BILL", "=" * 32]
    for name, count in sorted(items.items()):
        price = prices.get(name, 0.0)
        lines.append(f"{name:<18} {count:>3} x {price:>8.2f} = {count * price:>8.2f}")
    lines.extend(["=" * 32, f"TOTAL: {total:.2f}"])
    receipt_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return receipt_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Run object detection and billing")
    parser.add_argument("--stream", default=DEFAULT_STREAM)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--prices", default=DEFAULT_PRICES)
    parser.add_argument("--confidence", type=float, default=0.55)
    parser.add_argument("--output", default="billing_output")
    args = parser.parse_args()

    prices = load_prices(Path(args.prices))
    model = YOLO(args.model)
    camera = cv2.VideoCapture(args.stream)
    camera.set(cv2.CAP_PROP_BUFFERSIZE, 1)

    if not camera.isOpened():
        raise RuntimeError(f"Could not open ESP32-CAM stream: {args.stream}")

    billed_ids: set[tuple[str, int]] = set()
    items: Counter[str] = Counter()
    latest_frame = None
    message = "Ready"

    print("Press c to clear, s to save image, b to save receipt, q to quit.")

    try:
        while True:
            ok, frame = camera.read()
            if not ok or frame is None:
                print("Stream frame unavailable; reconnecting...")
                camera.release()
                time.sleep(1)
                camera = cv2.VideoCapture(args.stream)
                continue

            result = model.track(
                frame,
                persist=True,
                tracker="bytetrack.yaml",
                conf=args.confidence,
                verbose=False,
            )[0]
            annotated = result.plot()

            if result.boxes is not None and len(result.boxes) > 0:
                class_ids = result.boxes.cls.int().cpu().tolist()
                track_ids = (
                    result.boxes.id.int().cpu().tolist()
                    if result.boxes.id is not None
                    else [None] * len(class_ids)
                )
                for class_id, track_id in zip(class_ids, track_ids):
                    if track_id is None:
                        continue
                    name = get_class_name(model.names, class_id)
                    identity = (name, track_id)
                    if identity not in billed_ids:
                        billed_ids.add(identity)
                        items[name] += 1

            total = sum(prices.get(name, 0.0) * count for name, count in items.items())
            cv2.putText(
                annotated,
                f"{message} | Items: {sum(items.values())} | Total: {total:.2f}",
                (15, 30),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.7,
                (0, 255, 0),
                2,
                cv2.LINE_AA,
            )
            latest_frame = annotated
            cv2.imshow("Auto Billing", annotated)
            key = cv2.waitKey(1) & 0xFF

            if key == ord("q"):
                break
            if key == ord("c"):
                billed_ids.clear()
                items.clear()
                message = "Bill cleared"
            elif key == ord("s") and latest_frame is not None:
                output_dir = Path(args.output)
                output_dir.mkdir(parents=True, exist_ok=True)
                path = output_dir / f"snapshot_{datetime.now():%Y%m%d_%H%M%S}.jpg"
                cv2.imwrite(str(path), latest_frame)
                message = f"Saved {path.name}"
            elif key == ord("b"):
                path = save_receipt(items, prices, Path(args.output))
                message = f"Saved {path.name}"
    finally:
        camera.release()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
