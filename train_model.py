from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from ultralytics import YOLO


def ensure_dataset_yaml(data_path: Path) -> None:
    prices_path = Path("prices.json")
    if not prices_path.exists():
        raise FileNotFoundError(
            "Cannot create the dataset config because prices.json is missing. "
            "Open Prices in the admin panel and add at least one product first."
        )

    with prices_path.open("r", encoding="utf-8") as file:
        product_names = list(json.load(file).keys())
    if not product_names:
        raise FileNotFoundError(
            "Cannot create the dataset config because the price book is empty. "
            "Open Prices, add each product name and price, then capture images with "
            "a selected Product label."
        )

    data_path.parent.mkdir(parents=True, exist_ok=True)
    for relative_path in ("images/train", "images/val", "labels/train", "labels/val"):
        (data_path.parent / relative_path).mkdir(parents=True, exist_ok=True)

    names = "\n".join(f"  {index}: {name}" for index, name in enumerate(product_names))
    dataset_root = data_path.parent.resolve().as_posix()
    data_path.write_text(
        f"path: '{dataset_root}'\n"
        "train: images/train\n"
        "val: images/val\n"
        "nc: " + str(len(product_names)) + "\n"
        f"names:\n{names}\n",
        encoding="utf-8",
    )
    print(f"Created {data_path} from prices.json")


def prepare_validation_split(data_path: Path) -> None:
    dataset_root = data_path.parent
    train_images = sorted(
        path for path in (dataset_root / "images" / "train").iterdir()
        if path.suffix.lower() in {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
    )
    val_images = sorted(
        path for path in (dataset_root / "images" / "val").iterdir()
        if path.suffix.lower() in {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
    )
    if val_images:
        return
    if len(train_images) < 2:
        raise FileNotFoundError(
            "At least two captured images are required to create train and validation sets. "
            "Capture more labeled product images, then train again."
        )

    validation_count = max(1, len(train_images) // 5)
    for image_path in train_images[-validation_count:]:
        label_path = dataset_root / "labels" / "train" / f"{image_path.stem}.txt"
        if not label_path.exists():
            raise FileNotFoundError(
                f"Missing YOLO label for {image_path.name}: {label_path}. "
                "Capture the image again with a Product label."
            )
        shutil.move(str(image_path), str(dataset_root / "images" / "val" / image_path.name))
        shutil.move(str(label_path), str(dataset_root / "labels" / "val" / label_path.name))
    print(f"Prepared validation split with {validation_count} images")


def validate_dataset(data_path: Path) -> None:
    dataset_root = data_path.parent
    required = [
        dataset_root / "images" / "train",
        dataset_root / "images" / "val",
        dataset_root / "labels" / "train",
        dataset_root / "labels" / "val",
    ]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise FileNotFoundError(
            "The YOLO dataset is incomplete. Create these folders and export "
            f"annotated YOLO files first: {', '.join(missing)}"
        )
    train_images = list((dataset_root / "images" / "train").glob("*"))
    val_images = list((dataset_root / "images" / "val").glob("*"))
    if not train_images or not val_images:
        raise FileNotFoundError(
            "The YOLO dataset needs at least one image in both images/train and images/val. "
            "Capture more labeled product images and train again."
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Train a YOLO product detector")
    parser.add_argument("--data", default="dataset/data.yaml", help="YOLO dataset YAML file")
    parser.add_argument("--model", default="yolo11n.pt", help="Starting YOLO model")
    parser.add_argument("--epochs", type=int, default=50)
    parser.add_argument("--image-size", type=int, default=640)
    parser.add_argument("--batch", type=int, default=8)
    parser.add_argument("--name", default="product_detector")
    args = parser.parse_args()

    data_path = Path(args.data)
    ensure_dataset_yaml(data_path)
    prepare_validation_split(data_path)
    validate_dataset(data_path)

    model = YOLO(args.model)
    model.train(
        data=str(data_path),
        epochs=args.epochs,
        imgsz=args.image_size,
        batch=args.batch,
        name=args.name,
    )

    run_directory = Path(model.trainer.save_dir)
    trained_weights = run_directory / "weights" / "best.pt"
    if not trained_weights.exists():
        raise FileNotFoundError(f"Training finished but best weights were not found at {trained_weights}")
    shutil.copy2(trained_weights, Path("best.pt"))
    print(f"Training complete. Active billing model: {Path('best.pt').resolve()}")


if __name__ == "__main__":
    main()
