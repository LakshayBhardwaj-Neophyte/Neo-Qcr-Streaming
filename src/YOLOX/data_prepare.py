import os
import json
import shutil
from pathlib import Path
from PIL import Image
from tqdm import tqdm

ROOT = "/mnt/storage/data/interns_data/RT-DETR/NeoQcr_patch_detection_training/Dataset_4_class_merged"

OUT_ROOT = os.path.join(ROOT, "COCO")

CLASS_NAMES = [
    "clean",
    "blur",
    "glare",
    "occlusion"
]

os.makedirs(os.path.join(OUT_ROOT, "annotations"), exist_ok=True)
os.makedirs(os.path.join(OUT_ROOT, "train2017"), exist_ok=True)
os.makedirs(os.path.join(OUT_ROOT, "val2017"), exist_ok=True)


def convert_split(split):

    image_dir = os.path.join(ROOT, split, "images")
    label_dir = os.path.join(ROOT, split, "labels")

    coco = {
        "images": [],
        "annotations": [],
        "categories": []
    }

    for i, name in enumerate(CLASS_NAMES):
        coco["categories"].append({
            "id": i,
            "name": name,
            "supercategory": "object"
        })

    ann_id = 1
    img_id = 1

    dst_img_dir = os.path.join(
        OUT_ROOT,
        "train2017" if split == "train" else "val2017"
    )

    image_files = []

    for ext in ("*.jpg", "*.jpeg", "*.png", "*.bmp", "*.webp"):
        image_files.extend(Path(image_dir).glob(ext))

    for img_path in tqdm(sorted(image_files), desc=split):

        try:
            with Image.open(img_path) as img:
                W, H = img.size
        except:
            continue

        shutil.copy2(
            str(img_path),
            os.path.join(dst_img_dir, img_path.name)
        )

        coco["images"].append({
            "id": img_id,
            "file_name": img_path.name,
            "width": W,
            "height": H
        })

        label_file = os.path.join(
            label_dir,
            img_path.stem + ".txt"
        )

        if os.path.exists(label_file):

            with open(label_file) as f:

                for line in f:

                    parts = line.strip().split()

                    if len(parts) != 5:
                        continue

                    cls, xc, yc, bw, bh = map(float, parts)

                    cls = int(cls)

                    x = (xc - bw/2) * W
                    y = (yc - bh/2) * H
                    w = bw * W
                    h = bh * H

                    # clip to image
                    x = max(0, x)
                    y = max(0, y)

                    if x + w > W:
                        w = W - x

                    if y + h > H:
                        h = H - y

                    if w <= 1 or h <= 1:
                        continue

                    coco["annotations"].append({
                        "id": ann_id,
                        "image_id": img_id,
                        "category_id": cls,
                        "bbox": [
                            float(x),
                            float(y),
                            float(w),
                            float(h)
                        ],
                        "area": float(w * h),
                        "iscrowd": 0
                    })

                    ann_id += 1

        img_id += 1

    out_json = os.path.join(
        OUT_ROOT,
        "annotations",
        f"instances_{split}2017.json"
    )

    with open(out_json, "w") as f:
        json.dump(coco, f)

    print(
        f"{split}: "
        f"{len(coco['images'])} images, "
        f"{len(coco['annotations'])} annotations"
    )


convert_split("train")
convert_split("val")

print("\nDone.")