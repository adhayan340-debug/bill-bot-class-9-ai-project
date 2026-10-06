from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

import cv2


DEFAULT_STREAM = "http://192.168.4.1:81/stream"


def main() -> None:
    parser = argparse.ArgumentParser(description="Capture product images from an ESP32-CAM stream")
    parser.add_argument("--stream", default=DEFAULT_STREAM, help="ESP32-CAM MJPEG stream URL")
    parser.add_argument("--output", default="dataset/raw", help="Folder for captured images")
    args = parser.parse_args()

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    camera = cv2.VideoCapture(args.stream)

    if not camera.isOpened():
        raise RuntimeError(f"Could not open ESP32-CAM stream: {args.stream}")

    print("Press s to save an image, q to quit.")
    print(f"Saving images to: {output_dir.resolve()}")

    try:
        while True:
            ok, frame = camera.read()
            if not ok:
                print("Could not read a frame from the stream.")
                break

            cv2.imshow("Capture Images", frame)
            key = cv2.waitKey(1) & 0xFF

            if key == ord("q"):
                break
            if key == ord("s"):
                filename = output_dir / f"image_{datetime.now():%Y%m%d_%H%M%S_%f}.jpg"
                cv2.imwrite(str(filename), frame)
                print(f"Saved {filename}")
    finally:
        camera.release()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
