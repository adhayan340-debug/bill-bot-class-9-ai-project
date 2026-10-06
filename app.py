from __future__ import annotations

import argparse
import csv
import functools
import hmac
import json
import math
import os
import secrets
import shutil
import subprocess
import sys
import threading
import time
from collections import Counter, deque
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
from dotenv import load_dotenv
from flask import Flask, Response, jsonify, redirect, render_template, request, send_from_directory, session, url_for
from ultralytics import YOLO
from werkzeug.utils import secure_filename
try:
    from authlib.integrations.flask_client import OAuth
except ImportError:
    OAuth = None

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")
RAW_DIR = BASE_DIR / "dataset" / "raw"
LABELLED_DIR = BASE_DIR / "dataset" / "labelled"
OUTPUT_DIR = BASE_DIR / "billing_output"
PRICES_PATH = BASE_DIR / "prices.json"
DEFAULT_STREAM = "http://192.168.4.1:81/stream"
DEFAULT_LABEL_MODEL = "yolo11n.pt"
DATASET_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
MAX_DATASET_UPLOAD_BYTES = 20 * 1024 * 1024
DEFAULT_GOOGLE_ADMIN_EMAIL = "adhayan340@gmail.com"

# Add more local admin accounts here. For production, set each password through
# an environment variable instead of keeping it in source code.
ADMIN_USERS = {
    "Adhayan Das": os.environ.get("ADHAYAN_ADMIN_PASSWORD"),
}
ADMIN_PANEL_LOCAL_ONLY = os.environ.get("ADMIN_PANEL_LOCAL_ONLY", "false").strip().lower() in {"1", "true", "yes", "on"}

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY") or secrets.token_hex(32)

oauth = OAuth(app) if OAuth is not None else None
google = None
if oauth is not None and os.environ.get("GOOGLE_CLIENT_ID") and os.environ.get("GOOGLE_CLIENT_SECRET"):
    google = oauth.register(
        name="google",
        client_id=os.environ["GOOGLE_CLIENT_ID"],
        client_secret=os.environ["GOOGLE_CLIENT_SECRET"],
        server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
        client_kwargs={"scope": "openid email profile"},
    )


def allowed_google_email() -> str:
    return os.environ.get("GOOGLE_ALLOWED_EMAIL", DEFAULT_GOOGLE_ADMIN_EMAIL).strip().lower()


def is_local_request() -> bool:
    return request.remote_addr in {"127.0.0.1", "::1"}


def admin_required(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if ADMIN_PANEL_LOCAL_ONLY and not is_local_request():
            if request.path.startswith("/api/"):
                return jsonify({"ok": False, "error": "Admin panel is available only on the laptop"}), 403
            return redirect(url_for("billing_page"))
        if not session.get("admin_user"):
            if request.path.startswith("/api/"):
                return jsonify({"ok": False, "error": "Admin login required"}), 401
            return redirect(url_for("admin_login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


class CameraService:
    def __init__(self) -> None:
        self.url = DEFAULT_STREAM
        self.lock = threading.Lock()
        self.frame = None
        self.jpeg = None
        self.version = 0
        self.running = False
        self.thread: threading.Thread | None = None
        self.capture: cv2.VideoCapture | None = None

    def start(self, url: str) -> None:
        self.stop()
        self.url = url.strip() or DEFAULT_STREAM
        self.running = True
        self.thread = threading.Thread(target=self._read_loop, daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.running = False
        if self.capture is not None:
            self.capture.release()
            self.capture = None

    def _read_loop(self) -> None:
        while self.running:
            self.capture = cv2.VideoCapture(self.url)
            self.capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            if not self.capture.isOpened():
                self.capture.release()
                self.capture = None
                time.sleep(2)
                continue
            while self.running:
                ok, frame = self.capture.read()
                if not ok or frame is None:
                    break
                encoded, buffer = cv2.imencode(".jpg", frame)
                if not encoded:
                    continue
                with self.lock:
                    self.frame = frame
                    self.jpeg = buffer.tobytes()
                    self.version += 1
            self.capture.release()
            self.capture = None
            time.sleep(1)

    def get_frame(self) -> tuple[object | None, int]:
        with self.lock:
            return (None if self.frame is None else self.frame.copy(), self.version)

    def get_jpeg(self) -> bytes | None:
        with self.lock:
            return self.jpeg


class BillingService:
    def __init__(self, camera: CameraService) -> None:
        self.camera = camera
        self.lock = threading.Lock()
        self.running = False
        self.thread: threading.Thread | None = None
        self.model: YOLO | None = None
        self.model_path = ""
        self.confidence = 0.55
        self.items: Counter[str] = Counter()
        self.fallback_tracks: dict[str, list[tuple[int, float, float]]] = {}
        self.next_fallback_id = 0
        self.annotated_jpeg: bytes | None = None
        self.last_error = ""
        self.last_version = -1
        self.receipt = ""
        self.finalized = False

    def start(self, model_path: str, confidence: float) -> None:
        self.stop()
        with self.lock:
            self.items.clear()
            self.fallback_tracks.clear()
            self.next_fallback_id = 0
            self.receipt = ""
            self.finalized = False
            self.annotated_jpeg = None
            self.last_error = ""
            self.model_path = model_path
            self.confidence = confidence
            self.running = True
        self.thread = threading.Thread(target=self._detect_loop, daemon=True)
        self.thread.start()

    def stop(self) -> None:
        with self.lock:
            self.running = False

    def reset(self) -> None:
        with self.lock:
            self.items.clear()
            self.fallback_tracks.clear()
            self.next_fallback_id = 0
            self.receipt = ""
            self.finalized = False

    def _detect_loop(self) -> None:
        try:
            self.model = YOLO(self.model_path)
            while self.running:
                frame, version = self.camera.get_frame()
                if frame is None or version == self.last_version:
                    time.sleep(0.02)
                    continue
                self.last_version = version
                result = self.model.track(frame, persist=True, tracker="bytetrack.yaml", conf=self.confidence, verbose=False)[0]
                annotated = result.plot()
                if result.boxes is not None and len(result.boxes) > 0:
                    class_ids = result.boxes.cls.int().cpu().tolist()
                    boxes = result.boxes.xyxy.cpu().tolist()
                    track_ids = result.boxes.id.int().cpu().tolist() if result.boxes.id is not None else [None] * len(class_ids)
                    with self.lock:
                        visible_items: Counter[str] = Counter()
                        for index, (class_id, track_id) in enumerate(zip(class_ids, track_ids)):
                            name = self._class_name(class_id)
                            if track_id is None:
                                x1, y1, x2, y2 = boxes[index]
                                center_x = (x1 + x2) / 2.0
                                center_y = (y1 + y2) / 2.0
                                track_id = self._fallback_track_id(name, center_x, center_y)
                            visible_items[name] += 1
                        self.items = visible_items
                else:
                    with self.lock:
                        self.items.clear()
                ok, buffer = cv2.imencode(".jpg", annotated)
                if ok:
                    with self.lock:
                        self.annotated_jpeg = buffer.tobytes()
        except Exception as error:
            with self.lock:
                self.last_error = str(error)
                self.running = False

    def _fallback_track_id(self, name: str, center_x: float, center_y: float) -> int:
        tracks = self.fallback_tracks.setdefault(name, [])
        nearest_index = None
        nearest_distance = float("inf")
        for index, (_, previous_x, previous_y) in enumerate(tracks):
            distance = ((center_x - previous_x) ** 2 + (center_y - previous_y) ** 2) ** 0.5
            if distance < nearest_distance:
                nearest_index = index
                nearest_distance = distance
        if nearest_index is not None and nearest_distance <= 120:
            track_id = tracks[nearest_index][0]
            tracks[nearest_index] = (track_id, center_x, center_y)
            return track_id
        track_id = self.next_fallback_id
        self.next_fallback_id += 1
        tracks.append((track_id, center_x, center_y))
        return track_id

    def _class_name(self, class_id: int) -> str:
        if isinstance(self.model.names, dict):
            return str(self.model.names.get(class_id, class_id))
        return str(self.model.names[class_id])

    def snapshot(self) -> dict:
        prices = load_prices()
        with self.lock:
            items = dict(self.items)
            return {"running": self.running, "finalized": self.finalized, "items": items, "total_items": sum(items.values()), "total": sum(prices.get(name, 0.0) * count for name, count in items.items()), "error": self.last_error, "model": self.model_path, "confidence": self.confidence, "receipt": self.receipt}

    def finalize(self) -> str:
        prices = load_prices()
        with self.lock:
            lines = ["COUNTERTOP VISION BILL", "=" * 24]
            if not self.items:
                lines.append("No products detected")
                total = 0.0
            else:
                for name, count in sorted(self.items.items()):
                    amount = prices.get(name, 0.0) * count
                    lines.append(f"{name} x{count}  {amount:.2f}")
                total = sum(prices.get(name, 0.0) * count for name, count in self.items.items())
            lines.extend(["-" * 24, f"TOTAL  {total:.2f}"])
            self.receipt = "\n".join(lines)
            self.running = False
            self.finalized = True
            return self.receipt


class TrainingService:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.process: subprocess.Popen[str] | None = None
        self.logs: deque[str] = deque(maxlen=100)
        self.started_at = ""
        self.epochs = 0
        self.run_name = ""

    def start(self, data: str, model: str, epochs: int, image_size: int, batch: int, name: str) -> None:
        with self.lock:
            if self.process is not None and self.process.poll() is None:
                raise RuntimeError("Training is already running")
            command = [
                sys.executable,
                "-u",
                str(BASE_DIR / "train_model.py"),
                "--data", data,
                "--model", model,
                "--epochs", str(epochs),
                "--image-size", str(image_size),
                "--batch", str(batch),
                "--name", name,
            ]
            self.logs.clear()
            self.started_at = datetime.now().isoformat(timespec="seconds")
            self.epochs = epochs
            self.run_name = name
            self.process = subprocess.Popen(
                command,
                cwd=BASE_DIR,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
            threading.Thread(target=self._read_output, daemon=True).start()

    def _read_output(self) -> None:
        process = self.process
        if process is None or process.stdout is None:
            return
        for line in process.stdout:
            print(line.rstrip(), flush=True)
            with self.lock:
                self.logs.append(line.rstrip())
        process.wait()

    def snapshot(self) -> dict:
        with self.lock:
            code = None if self.process is None else self.process.poll()
            running = code is None and self.process is not None
            started_at = self.started_at
            epochs = self.epochs
            run_name = self.run_name
            logs = list(self.logs)

        current_epoch = 0
        run_directory = ""
        runs_root = BASE_DIR / "runs" / "detect"
        if run_name and runs_root.exists():
            candidates = sorted(
                (path for path in runs_root.glob(f"{run_name}*") if (path / "results.csv").exists()),
                key=lambda path: path.stat().st_mtime,
                reverse=True,
            )
            if candidates:
                run_directory = str(candidates[0].relative_to(BASE_DIR)).replace("\\", "/")
                try:
                    with (candidates[0] / "results.csv").open("r", newline="", encoding="utf-8") as file:
                        rows = list(csv.DictReader(file))
                    if rows:
                        current_epoch = int(float(rows[-1].get("epoch", 0)))
                except (OSError, ValueError):
                    current_epoch = 0

        progress = 100 if code == 0 else min(99, round((current_epoch / epochs) * 100)) if epochs else 0
        return {
            "running": running,
            "return_code": code,
            "started_at": started_at,
            "logs": logs,
            "current_epoch": current_epoch,
            "total_epochs": epochs,
            "progress_percent": progress,
            "run_directory": run_directory,
        }


camera = CameraService()
billing = BillingService(camera)
training = TrainingService()


def load_prices() -> dict[str, float]:
    if not PRICES_PATH.exists():
        return {}
    with PRICES_PATH.open("r", encoding="utf-8") as file:
        return {str(name): float(value) for name, value in json.load(file).items()}


def resolve_model_path(requested_model: str) -> Path:
    model_path = Path(requested_model)
    if not model_path.is_absolute():
        model_path = BASE_DIR / model_path
    if model_path.exists():
        return model_path
    if requestedi_model == "best.pt":
        completed_runs = sorted(
            (path for path in (BASE_DIR / "runs" / "detect").glob("*")),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        ) if (BASE_DIR / "runs" / "detect").exists() else []
        for run_path in completed_runs:
            fallback = run_path / "weights" / "best.pt"
            if fallback.exists():
                return fallback
    return model_path


def product_class_id(product_name: str) -> int:
    product_names = list(load_prices())
    try:
        return product_names.index(product_name)
    except ValueError as error:
        raise ValueError("Select a product from the price book before labeling") from error


def ensure_training_dataset_layout(dataset_root: str | Path | None = None) -> Path:
    root = Path(dataset_root) if dataset_root is not None else BASE_DIR / "dataset"
    for relative_path in ("images/train", "images/val", "labels/train", "labels/val"):
        (root / relative_path).mkdir(parents=True, exist_ok=True)
    return root


def save_yolo_annotation_file(
    path: str | Path,
    image_width: int,
    image_height: int,
    boxes: list[tuple[int, float, float, float, float]] | list[tuple[int, int, int, int, int]],
) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    if image_width <= 0 or image_height <= 0:
        raise ValueError("Image width and height must be greater than zero")

    lines: list[str] = []
    for class_id, x1, y1, x2, y2 in boxes:
        x_center = ((float(x1) + float(x2)) / 2.0) / float(image_width)
        y_center = ((float(y1) + float(y2)) / 2.0) / float(image_height)
        box_width = (float(x2) - float(x1)) / float(image_width)
        box_height = (float(y2) - float(y1)) / float(image_height)
        lines.append(
            f"{int(class_id)} {x_center:.6f} {y_center:.6f} {max(box_width, 0.0):.6f} {max(box_height, 0.0):.6f}"
        )

    target.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    return target


def dataset_split_paths(split: str) -> tuple[Path, Path]:
    if split not in {"train", "val"}:
        raise ValueError("Dataset split must be train or val")
    root = ensure_training_dataset_layout(BASE_DIR / "dataset")
    return root / "images" / split, root / "labels" / split


def valid_dataset_filename(filename: str) -> bool:
    return Path(filename).name == filename and Path(filename).suffix.lower() in DATASET_IMAGE_EXTENSIONS


def read_yolo_boxes(label_path: Path, image_width: int, image_height: int) -> list[dict]:
    boxes = []
    if not label_path.exists():
        return boxes
    for line in label_path.read_text(encoding="utf-8").splitlines():
        values = line.split()
        if len(values) != 5:
            continue
        try:
            class_id = int(values[0])
            x_center, y_center, width, height = map(float, values[1:])
        except ValueError:
            continue
        boxes.append({
            "class_id": class_id,
            "x1": (x_center - width / 2) * image_width,
            "y1": (y_center - height / 2) * image_height,
            "x2": (x_center + width / 2) * image_width,
            "y2": (y_center + height / 2) * image_height,
        })
    return boxes


def frame_stream() -> Response:
    def generate():
        while True:
            jpeg = billing.annotated_jpeg if billing.snapshot()["running"] else camera.get_jpeg()
            if jpeg is not None:
                yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + jpeg + b"\r\n"
            time.sleep(0.04)

    return Response(generate(), mimetype="multipart/x-mixed-replace; boundary=frame")


@app.get("/")
def index():
    return redirect("/billing")


@app.get("/billing")
def billing_page():
    return render_template("billing.html", stream_url=camera.url, active="billing")


@app.get("/capture")
@admin_required
def capture_page():
    return render_template("capture.html", stream_url=camera.url, prices=load_prices(), active="capture")


@app.get("/train")
@admin_required
def train_page():
    return render_template("train.html", prices=load_prices(), active="train")


@app.get("/prices")
@admin_required
def prices_page():
    return render_template("prices.html", prices=load_prices(), active="prices")


@app.get("/admin")
@admin_required
def admin_dashboard():
    return render_template("admin.html", stream_url=camera.url, active="admin")


@app.get("/admin/login")
def admin_login():
    google_enabled = google is not None and bool(allowed_google_email())
    setup_error = "" if not ADMIN_PANEL_LOCAL_ONLY or is_local_request() else "Admin login is locked to the laptop. Set ADMIN_PANEL_LOCAL_ONLY=false to allow trusted devices on the same network."
    oauth_error = "" if google_enabled else "Add GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET to the project .env file, then restart Flask."
    login_enabled = not ADMIN_PANEL_LOCAL_ONLY or is_local_request()
    return render_template("login.html", google_enabled=google_enabled and login_enabled, local_login_enabled=login_enabled, setup_error=setup_error, oauth_error=oauth_error, next_path=request.args.get("next", "/train"))


@app.post("/admin/login/local")
def local_admin_login():
    if ADMIN_PANEL_LOCAL_ONLY and not is_local_request():
        return jsonify({"ok": False, "error": "Local admin login is available only on the laptop"}), 403
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")
    expected_password = ADMIN_USERS.get(username)
    if expected_password is None or not hmac.compare_digest(password, expected_password):
        return render_template("login.html", google_enabled=google is not None, local_login_enabled=True, error="Invalid username or password.", next_path=request.form.get("next", "/train")), 401
    session.clear()
    session["admin_user"] = username
    session["admin_auth"] = "local"
    return redirect(request.form.get("next") or "/train")


@app.get("/auth/google")
def google_login():
    if ADMIN_PANEL_LOCAL_ONLY and not is_local_request():
        return redirect(url_for("billing_page"))
    if google is None:
        return render_template("login.html", google_enabled=False, local_login_enabled=True, oauth_error="Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET before using Google sign-in.", next_path="/train"), 503
    return google.authorize_redirect(url_for("google_callback", _external=True))


@app.get("/auth/google/callback")
def google_callback():
    if google is None:
        return redirect(url_for("admin_login"))
    token = google.authorize_access_token()
    user = token.get("userinfo", {})
    if user.get("email_verified") is not True:
        return render_template("login.html", google_enabled=True, local_login_enabled=True, error="Google did not verify this email address.", next_path="/train"), 403
    if not allowed_google_email() or user.get("email", "").lower() != allowed_google_email():
        return render_template("login.html", google_enabled=True, local_login_enabled=True, error="Only the configured admin Google account is authorized.", next_path="/train"), 403
    session["admin_user"] = user.get("email", "google-admin")
    session["admin_auth"] = "google"
    return redirect("/train")


@app.get("/admin/logout")
def admin_logout():
    session.clear()
    return redirect(url_for("billing_page"))


@app.get("/video_feed")
def video_feed():
    return frame_stream()


@app.post("/api/camera/start")
@admin_required
def start_camera():
    payload = request.get_json(silent=True) or {}
    camera.start(str(payload.get("url", DEFAULT_STREAM)))
    return jsonify({"ok": True, "url": camera.url})


@app.post("/api/capture")
@admin_required
def capture_image():
    frame, _ = camera.get_frame()
    if frame is None:
        return jsonify({"ok": False, "error": "No camera frame available"}), 503
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    path = RAW_DIR / f"image_{datetime.now():%Y%m%d_%H%M%S_%f}.jpg"
    cv2.imwrite(str(path), frame)
    return jsonify({"ok": True, "file": str(path.relative_to(BASE_DIR)).replace("\\", "/")})


@app.post("/api/capture-label")
@admin_required
def capture_and_label_image():
    payload = request.get_json(silent=True) or {}
    frame, _ = camera.get_frame()
    if frame is None:
        return jsonify({"ok": False, "error": "No camera frame available"}), 503

    requested_model = str(payload.get("model", "best.pt"))
    product_name = str(payload.get("product", "")).strip()
    if not product_name:
        return jsonify({"ok": False, "error": "Choose a product label before saving a trainable image"}), 400
    try:
        product_id = product_class_id(product_name)
    except ValueError as error:
        return jsonify({"ok": False, "error": str(error)}), 400
    model_path = resolve_model_path(requested_model)
    warning = ""
    if not model_path.exists():
        if requested_model == "best.pt":
            model_path = BASE_DIR / DEFAULT_LABEL_MODEL
            warning = "best.pt is not trained yet; using yolo11n.pt for general object labels."
        else:
            return jsonify({"ok": False, "error": f"Model not found: {model_path.name}"}), 400

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    LABELLED_DIR.mkdir(parents=True, exist_ok=True)
    dataset_root = ensure_training_dataset_layout(BASE_DIR / "dataset")
    image_train_dir = dataset_root / "images" / "train"
    label_train_dir = dataset_root / "labels" / "train"

    raw_path = RAW_DIR / f"image_{datetime.now():%Y%m%d_%H%M%S_%f}.jpg"
    cv2.imwrite(str(raw_path), frame)
    dataset_image_path = image_train_dir / raw_path.name
    cv2.imwrite(str(dataset_image_path), frame)

    try:
        labelled = YOLO(str(model_path))(frame, verbose=False)[0]
    except Exception as error:
        return jsonify({"ok": False, "error": f"Could not load {model_path.name}: {error}"}), 400

    labelled_path = LABELLED_DIR / raw_path.name
    cv2.imwrite(str(labelled_path), labelled.plot())

    labels = [product_name]
    detected_boxes: list[tuple[float, float, float, float]] = []
    if labelled.boxes is not None:
        for box in labelled.boxes:
            x1, y1, x2, y2 = map(float, box.xyxy[0].cpu().tolist())
            detected_boxes.append((x1, y1, x2, y2))

    label_boxes = [(product_id, *box) for box in detected_boxes]

    label_path = label_train_dir / f"{raw_path.stem}.txt"
    save_yolo_annotation_file(
        label_path,
        image_width=frame.shape[1],
        image_height=frame.shape[0],
        boxes=label_boxes,
    )

    return jsonify({
        "ok": True,
        "raw_file": str(raw_path.relative_to(BASE_DIR)).replace("\\", "/"),
        "dataset_image_file": str(dataset_image_path.relative_to(BASE_DIR)).replace("\\", "/"),
        "label_file": str(label_path.relative_to(BASE_DIR)).replace("\\", "/"),
        "labelled_file": str(labelled_path.relative_to(BASE_DIR)).replace("\\", "/"),
        "labels": labels,
        "product": product_name,
        "boxes": len(label_boxes),
        "model": model_path.name,
        "warning": warning,
    })


@app.get("/api/dataset")
@admin_required
def dataset_list():
    dataset_root = ensure_training_dataset_layout(BASE_DIR / "dataset")
    items = []
    known_names = set()
    for split in ("train", "val"):
        image_dir, label_dir = dataset_split_paths(split)
        for image_path in sorted(image_dir.iterdir()):
            if not image_path.is_file() or image_path.suffix.lower() not in DATASET_IMAGE_EXTENSIONS:
                continue
            known_names.add(image_path.name)
            label_path = label_dir / f"{image_path.stem}.txt"
            items.append({
                "split": split,
                "filename": image_path.name,
                "url": url_for("dataset_image", split=split, filename=image_path.name),
                "annotated": label_path.exists() and label_path.stat().st_size > 0,
            })

    raw_dir = dataset_root / "raw"
    if raw_dir.exists():
        for image_path in sorted(raw_dir.iterdir()):
            if image_path.is_file() and image_path.name not in known_names and image_path.suffix.lower() in DATASET_IMAGE_EXTENSIONS:
                items.append({
                    "split": "raw",
                    "filename": image_path.name,
                    "url": url_for("dataset_image", split="raw", filename=image_path.name),
                    "annotated": False,
                })
    items.sort(key=lambda item: (("train", "val", "raw").index(item["split"]), item["filename"]))
    return jsonify({"ok": True, "items": items, "classes": [
        {"id": index, "name": name} for index, name in enumerate(load_prices())
    ]})


@app.get("/api/dataset/annotations/<split>/<path:filename>")
@admin_required
def dataset_get_annotations(split: str, filename: str):
    if not valid_dataset_filename(filename):
        return jsonify({"ok": False, "error": "Invalid dataset image name"}), 400
    try:
        image_dir, label_dir = dataset_split_paths(split)
    except ValueError as error:
        return jsonify({"ok": False, "error": str(error)}), 400
    image_path = image_dir / filename
    if not image_path.is_file():
        return jsonify({"ok": False, "error": "Dataset image not found"}), 404
    image = cv2.imread(str(image_path))
    if image is None:
        return jsonify({"ok": False, "error": "Could not read dataset image"}), 400
    height, width = image.shape[:2]
    boxes = read_yolo_boxes(label_dir / f"{image_path.stem}.txt", width, height)
    return jsonify({"ok": True, "boxes": boxes, "width": width, "height": height})


@app.get("/api/dataset/image/<split>/<path:filename>")
@admin_required
def dataset_image(split: str, filename: str):
    if not valid_dataset_filename(filename):
        return jsonify({"ok": False, "error": "Invalid dataset image name"}), 400
    if split == "raw":
        image_dir = BASE_DIR / "dataset" / "raw"
    elif split in {"train", "val"}:
        image_dir, _ = dataset_split_paths(split)
    else:
        return jsonify({"ok": False, "error": "Invalid dataset split"}), 400
    return send_from_directory(image_dir, filename)


@app.post("/api/dataset/upload")
@admin_required
def dataset_upload():
    split = request.form.get("split", "train")
    try:
        image_dir, label_dir = dataset_split_paths(split)
    except ValueError as error:
        return jsonify({"ok": False, "error": str(error)}), 400

    pending = []
    for uploaded in request.files.getlist("files"):
        filename = secure_filename(uploaded.filename or "")
        if not filename or Path(filename).suffix.lower() not in DATASET_IMAGE_EXTENSIONS:
            return jsonify({"ok": False, "error": "Choose image files (JPG, PNG, BMP, or WEBP)"}), 400
        content = uploaded.stream.read(MAX_DATASET_UPLOAD_BYTES + 1)
        if len(content) > MAX_DATASET_UPLOAD_BYTES:
            return jsonify({"ok": False, "error": f"{filename} exceeds the 20 MB upload limit"}), 413
        try:
            decoded = cv2.imdecode(np.frombuffer(content, dtype=np.uint8), cv2.IMREAD_COLOR)
        except cv2.error:
            decoded = None
        if decoded is None:
            return jsonify({"ok": False, "error": f"{filename} is not a readable image"}), 400
        pending.append((filename, content))
    if not pending:
        return jsonify({"ok": False, "error": "Choose at least one image to add"}), 400

    added = []
    for filename, content in pending:
        destination = image_dir / filename
        suffix = destination.suffix
        stem = destination.stem
        index = 1
        while destination.exists():
            destination = image_dir / f"{stem}_{index}{suffix}"
            index += 1
        destination.write_bytes(content)
        (label_dir / f"{destination.stem}.txt").touch()
        added.append(destination.name)
    return jsonify({"ok": True, "added": added})


@app.post("/api/dataset/import-raw")
@admin_required
def dataset_import_raw():
    payload = request.get_json(silent=True) or {}
    split = str(payload.get("split", "train"))
    filename = str(payload.get("filename", ""))
    if not valid_dataset_filename(filename):
        return jsonify({"ok": False, "error": "Invalid source image name"}), 400
    try:
        image_dir, label_dir = dataset_split_paths(split)
    except ValueError as error:
        return jsonify({"ok": False, "error": str(error)}), 400
    source = BASE_DIR / "dataset" / "raw" / filename
    if not source.is_file():
        return jsonify({"ok": False, "error": "Raw image not found"}), 404
    destination = image_dir / filename
    if destination.exists():
        return jsonify({"ok": False, "error": "An image with that name already exists in this split"}), 409
    shutil.copy2(source, destination)
    (label_dir / f"{destination.stem}.txt").touch()
    return jsonify({"ok": True, "filename": destination.name, "split": split})


@app.post("/api/dataset/annotations")
@admin_required
def dataset_save_annotations():
    payload = request.get_json(silent=True) or {}
    split = str(payload.get("split", ""))
    filename = str(payload.get("filename", ""))
    if not valid_dataset_filename(filename):
        return jsonify({"ok": False, "error": "Invalid dataset image name"}), 400
    try:
        image_dir, label_dir = dataset_split_paths(split)
    except ValueError as error:
        return jsonify({"ok": False, "error": str(error)}), 400
    image_path = image_dir / filename
    if not image_path.is_file():
        return jsonify({"ok": False, "error": "Dataset image not found"}), 404
    image = cv2.imread(str(image_path))
    if image is None:
        return jsonify({"ok": False, "error": "Could not read dataset image"}), 400
    height, width = image.shape[:2]
    classes = list(load_prices())
    boxes = payload.get("boxes")
    if not isinstance(boxes, list) or len(boxes) > 500:
        return jsonify({"ok": False, "error": "Boxes must be a list with at most 500 items"}), 400

    label_boxes = []
    try:
        for box in boxes:
            class_id = int(box["class_id"])
            x1, y1, x2, y2 = (float(box[key]) for key in ("x1", "y1", "x2", "y2"))
            if class_id < 0 or class_id >= len(classes):
                raise ValueError("Choose a valid product class for every box")
            if not all(math.isfinite(value) for value in (x1, y1, x2, y2)):
                raise ValueError("Box coordinates must be finite numbers")
            if x1 < 0 or y1 < 0 or x2 > width or y2 > height or x2 <= x1 or y2 <= y1:
                raise ValueError("Every box must have positive size and stay inside the image")
            label_boxes.append((class_id, x1, y1, x2, y2))
    except (KeyError, TypeError, ValueError) as error:
        return jsonify({"ok": False, "error": str(error) or "Invalid box data"}), 400

    label_path = label_dir / f"{image_path.stem}.txt"
    save_yolo_annotation_file(label_path, width, height, label_boxes)
    return jsonify({"ok": True, "boxes": len(label_boxes)})


@app.delete("/api/dataset/<split>/<path:filename>")
@admin_required
def dataset_delete(split: str, filename: str):
    if not valid_dataset_filename(filename):
        return jsonify({"ok": False, "error": "Invalid dataset image name"}), 400
    if split == "raw":
        image_dir = BASE_DIR / "dataset" / "raw"
        label_dir = None
    elif split in {"train", "val"}:
        image_dir, label_dir = dataset_split_paths(split)
    else:
        return jsonify({"ok": False, "error": "Invalid dataset split"}), 400
    image_path = image_dir / filename
    if not image_path.is_file():
        return jsonify({"ok": False, "error": "Dataset image not found"}), 404
    image_path.unlink()
    if label_dir is not None:
        (label_dir / f"{image_path.stem}.txt").unlink(missing_ok=True)
    return jsonify({"ok": True})


@app.get("/api/stats")
def stats():
    raw_images = len(list(RAW_DIR.glob("*.jpg"))) if RAW_DIR.exists() else 0
    labelled_images = len(list(LABELLED_DIR.glob("*.jpg"))) if LABELLED_DIR.exists() else 0
    return jsonify({
        "camera": {"url": camera.url, "connected": camera.get_jpeg() is not None},
        "raw_images": raw_images,
        "labelled_images": labelled_images,
        "model_exists": resolve_model_path("best.pt").exists(),
        "billing": billing.snapshot(),
        "training": training.snapshot(),
        "prices": load_prices(),
    })


@app.post("/api/training/start")
@admin_required
def start_training():
    payload = request.get_json(silent=True) or {}
    try:
        training.start(
            str(payload.get("data", "dataset/data.yaml")),
            str(payload.get("model", "yolo11n.pt")),
            int(payload.get("epochs", 50)),
            int(payload.get("image_size", 640)),
            int(payload.get("batch", 8)),
            str(payload.get("name", "product_detector")),
        )
        return jsonify({"ok": True})
    except (ValueError, RuntimeError, FileNotFoundError) as error:
        return jsonify({"ok": False, "error": str(error)}), 400


@app.post("/api/billing/start")
def start_billing():
    payload = request.get_json(silent=True) or {}
    model = str(payload.get("model", "best.pt"))
    model_path = resolve_model_path(model)
    if not model_path.exists():
        return jsonify({"ok": False, "error": f"Model not found: {model}. Train a model first."}), 400
    billing.start(str(model_path), float(payload.get("confidence", 0.55)))
    return jsonify({"ok": True})


@app.post("/api/billing/stop")
def stop_billing():
    billing.stop()
    return jsonify({"ok": True})


@app.post("/api/billing/reset")
def reset_billing():
    billing.reset()
    return jsonify({"ok": True})


@app.post("/api/billing/finalize")
def finalize_billing():
    payload = request.get_json(silent=True) or {}
    if payload.get("lid_closed") is not True:
        return jsonify({"ok": False, "error": "Confirm that the lid is closed before creating the bill."}), 400
    receipt = billing.finalize()
    return jsonify({"ok": True, "receipt": receipt})


@app.post("/api/prices")
@admin_required
def update_prices():
    payload = request.get_json(silent=True) or {}
    prices = payload.get("prices", {})
    if not isinstance(prices, dict):
        return jsonify({"ok": False, "error": "Prices must be an object"}), 400
    clean_prices = {str(name): float(value) for name, value in prices.items()}
    with PRICES_PATH.open("w", encoding="utf-8") as file:
        json.dump(clean_prices, file, indent=2)
    return jsonify({"ok": True, "prices": clean_prices})


@app.get("/files/<path:filename>")
def files(filename: str):
    return send_from_directory(BASE_DIR, filename)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Auto Billing Flask dashboard")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=5000)
    args = parser.parse_args()
    camera.start(DEFAULT_STREAM)
    app.run(host=args.host, port=args.port, threaded=True)
