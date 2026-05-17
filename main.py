import os
import json
import time
import re
from pathlib import Path
from typing import List, Optional

import google.generativeai as genai
from pydantic import BaseModel, Field
from PIL import Image
from rapidfuzz import fuzz

API_KEY_POOL = [
    "AIzaSyA9H7SAR7QOrfVhHRryHvROqgqnPqRD3yo"
]

current_key_index = 0

def get_current_key() -> str:
    return API_KEY_POOL[current_key_index]

def rotate_api_key() -> bool:
    global current_key_index
    next_index = current_key_index + 1
    if next_index >= len(API_KEY_POOL):
        print("[ERROR] All API keys have been exhausted. No more keys to rotate.")
        return False
    current_key_index = next_index
    print(f"[KEY POOL] Rotated to API key index {current_key_index}.")
    return True

def configure_api() -> None:
    key = get_current_key()
    genai.configure(api_key=key)
    print(f"[KEY POOL] Configured with API key index {current_key_index}.")
    
class ReceiptItem(BaseModel):
    name: str = Field(description="ชื่อสินค้าหรือบริการในใบเสร็จ")
    price: float = Field(description="ราคาของสินค้าหรือบริการนั้น เป็นตัวเลขทศนิยม")

class ReceiptData(BaseModel):
    vendor_name: Optional[str] = Field(
        description="ชื่อร้านค้า บริษัท หรือผู้ให้บริการที่ออกใบเสร็จ ถ้าหาไม่เจอให้เป็น null"
    )
    transaction_date: Optional[str] = Field(
        description="วันที่ทำรายการในรูปแบบ YYYY-MM-DD เป็น ค.ศ. เท่านั้น ถ้าเป็น พ.ศ. ตัวอย่างเช่น ค.ศ.2026 -> พ.ศ.2569  ให้แปลงก่อน ถ้าหาไม่เจอให้เป็น null"
    )
    items: List[ReceiptItem] = Field(
        description="รายการสินค้าหรือบริการทั้งหมดที่ปรากฏในใบเสร็จ"
    )
    total_amount: float = Field(
        description="ยอดเงินรวมสุทธิทั้งหมด (Grand Total) เป็นตัวเลขทศนิยม"
    )
    
SYSTEM_PROMPT = """
คุณคือผู้เชี่ยวชาญด้านการอ่านและตรวจสอบใบเสร็จรับเงินและใบแจ้งหนี้
หน้าที่ของคุณคืออ่านเอกสารที่แนบมาและสกัดข้อมูลสำคัญออกมาให้ครบถ้วนและแม่นยำที่สุด

=== กฎที่ต้องปฏิบัติตามอย่างเคร่งครัด ===

[ทั่วไป]
- ห้ามเดาหรือสร้างข้อมูลที่ไม่มีในเอกสารเด็ดขาด
- หากอ่านไม่ออกหรือไม่พบข้อมูลนั้นในเอกสาร ให้ระบุเป็น null เท่านั้น ห้ามใส่ค่าที่ไม่แน่ใจ
- หากภาพเอียง เบลอ หรือลายมือยากอ่าน ให้พยายามอ่านให้ดีที่สุด ส่วนที่ไม่แน่ใจจริง ๆ ให้เป็น null

[ชื่อร้านค้า - vendor_name]
- มักอยู่ที่ส่วนหัวสุดของใบเสร็จ พิมพ์ตัวใหญ่หรือเขียนเด่นชัด
- อาจเป็นชื่อร้าน ชื่อบริษัท หรือชื่อแผนก
- ตัวอย่างที่ถูกต้อง:
    "ร้านข้าวมันไก่ประตูน้ำ"
    "COFFEE AMAZON"
    "บริษัท ABC จำกัด"
- หากมีทั้งชื่อภาษาไทยและอังกฤษ ให้เลือกชื่อภาษาไทย
- หากหาไม่เจอให้เป็น null

[วันที่ - transaction_date]
- ต้องแปลงเป็นรูปแบบ YYYY-MM-DD เสมอ ใช้ ค.ศ. เท่านั้น
- วิธีสังเกตว่าเป็น พ.ศ. หรือ ค.ศ.:
    ถ้าปีขึ้นต้นด้วย 25xx เช่น 2567, 2568 = พ.ศ. ให้ลบ 543
    ถ้าปีขึ้นต้นด้วย 20xx เช่น 2024, 2025 = ค.ศ. ใช้ได้เลย

- ตัวอย่างการแปลง พ.ศ. -> ค.ศ. (ลบ 543):
    25/12/2567 พ.ศ. -> 2567 - 543 = 2024 -> 2024-12-25
    1 ม.ค. 2568 พ.ศ. -> 2568 - 543 = 2025 -> 2025-01-01
    15 ก.พ. 2566 พ.ศ. -> 2566 - 543 = 2023 -> 2023-02-15
    30/06/2565 พ.ศ. -> 2565 - 543 = 2022 -> 2022-06-30
    5 ธ.ค. 2560 พ.ศ. -> 2560 - 543 = 2017 -> 2017-12-05

- ตัวอย่างการแปลง ค.ศ. (ใช้ได้เลย ไม่ต้องลบ):
    31-03-2024 -> 2024-03-31
    March 5, 2024 -> 2024-03-05
    5/3/24 -> 2024-03-05

- เดือนที่เขียนเป็นตัวย่อภาษาไทย:
    ม.ค. = 01, ก.พ. = 02, มี.ค. = 03, เม.ย. = 04
    พ.ค. = 05, มิ.ย. = 06, ก.ค. = 07, ส.ค. = 08
    ก.ย. = 09, ต.ค. = 10, พ.ย. = 11, ธ.ค. = 12

- เดือนที่เขียนเป็นตัวย่อภาษาอังกฤษ:
    Jan=01, Feb=02, Mar=03, Apr=04, May=05, Jun=06
    Jul=07, Aug=08, Sep=09, Oct=10, Nov=11, Dec=12

- หากเขียนแค่ปี 2 หลัก:
    ถ้าเป็น 67, 68 = พ.ศ. ย่อ -> แปลงเป็น 2567, 2568 แล้วลบ 543
        ตัวอย่าง: 25/12/67 -> 2567 - 543 = 2024 -> 2024-12-25
    ถ้าเป็น 24, 25 = ค.ศ. ย่อ -> แปลงเป็น 2024, 2025 ใช้ได้เลย
        ตัวอย่าง: 5/3/24 -> 2024-03-05

- หากอ่านวันที่ไม่ออกเลย หรือไม่มีในเอกสาร ให้เป็น null ห้ามเดา

[รายการสินค้า - items]
- ให้สกัดทุกรายการที่ปรากฏในใบเสร็จ ห้ามข้ามรายการ
- ชื่อสินค้า (name) ให้คัดลอกตามที่ปรากฏในเอกสาร ไม่ต้องแปลหรือแก้ไข
- ราคาสินค้า (price) ต้องเป็นตัวเลขทศนิยมเท่านั้น
    ตัวอย่าง: "60 บาท" -> 60.0
    ตัวอย่าง: "1,250.00" -> 1250.0
    ตัวอย่าง: "฿350" -> 350.0
- หากรายการหนึ่งมีหลายชิ้น เช่น "กาแฟ x2 = 100" ให้บันทึกเป็น 1 รายการ ราคา 100.0
- หากอ่านชื่อสินค้าไม่ออก ให้ใส่ name เป็น null และใส่ราคาที่อ่านได้
- หากอ่านราคาสินค้าไม่ออก ให้ใส่ price เป็น 0.0

[ยอดรวมสุทธิ - total_amount]
- คือตัวเลขสุดท้ายที่ลูกค้าต้องชำระ ไม่ใช่ยอดก่อนหักส่วนลดหรือก่อนภาษี
- คำที่บ่งบอก: Total, Grand Total, ยอดรวม, ยอดสุทธิ, ยอดชำระ, รวมทั้งสิ้น, NET TOTAL
- ต้องเป็นตัวเลขทศนิยมเท่านั้น ห้ามมีสัญลักษณ์สกุลเงิน
    ตัวอย่าง: "รวม 350.00 บาท" -> 350.0
    ตัวอย่าง: "Total: 1,200" -> 1200.0
- หากมีทั้ง subtotal และ total ให้เลือก total เสมอ
- หากมีภาษีมูลค่าเพิ่ม (VAT) รวมอยู่ด้วย ให้ใช้ยอดที่รวม VAT แล้ว
- หากอ่านยอดรวมไม่ออกเลย ให้คำนวณจากผลรวมของ items แทน
"""

def resize_image(image_path: str, max_size: int = 1024) -> Image.Image:
    try:
        img = Image.open(image_path)
        img.thumbnail((max_size, max_size))
        print(f"[RESIZE] Image resized to fit within {max_size}x{max_size} px. Final size: {img.size}")
        return img
    except FileNotFoundError:
        raise FileNotFoundError(f"[ERROR] Image file not found: {image_path}")
    except Exception as e:
        raise RuntimeError(f"[ERROR] Failed to resize image '{image_path}': {e}")

def extract_receipt_data(image_path: str, retries: int = 2) -> ReceiptData:
    global current_key_index

    img = resize_image(image_path)

    wait = 3  # เริ่มรอ 3 วิ

    for attempt in range(retries):
        try:
            configure_api()
            model = genai.GenerativeModel("gemini-2.5-flash")

            print(f"[EXTRACT] attempt {attempt + 1}...")

            response = model.generate_content(
                [SYSTEM_PROMPT, img],
                generation_config=genai.GenerationConfig(
                    temperature=0,
                    response_mime_type="application/json",
                    response_schema=ReceiptData,
                ),
            )

            #กัน response ว่าง / เพี้ยน
            if not response.text:
                raise ValueError("Empty response from model")

            parsed = ReceiptData.model_validate_json(response.text)

            print("[EXTRACT] ✅ Success")
            return parsed

        except Exception as e:
            err = str(e)

            print(f"[ERROR] attempt {attempt+1}: {err}")

            # handle rate limit + server error
            if any(x in err for x in ["429", "503", "ResourceExhausted"]):

                # 🔁 พยายาม rotate key (ถ้ามี)
                rotated = rotate_api_key()

                if rotated:
                    print(f"[KEY] switched to index {current_key_index}")
                else:
                    print("[KEY] no more keys, will retry with same key")

                print(f"[WAIT] sleeping {wait} sec...")
                time.sleep(wait)

                wait *= 2  # exponential backoff

            else:
                # error อื่น → ไม่ควร retry มั่ว
                if attempt == retries - 1:
                    raise RuntimeError(
                        f"[ERROR] Failed after {retries} attempts: {e}"
                    )
                time.sleep(3)

    raise RuntimeError(
        f"[ERROR] Extraction failed after {retries} attempts for '{image_path}'"
    )
    
def save_output_json(data: ReceiptData, image_path: str) -> str:
    try:
        image_name = Path(image_path).stem
        output_path = f"{image_name}.json"

        output_dict = data.model_dump()

        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(output_dict, f, ensure_ascii=False, indent=2)

        print(f"[SAVE] JSON saved to: {output_path}")
        return output_path

    except Exception as e:
        raise RuntimeError(f"[ERROR] Failed to save JSON output for '{image_path}': {e}")
    
def print_result_as_text(data: ReceiptData) -> None:
    try:
        print("=" * 50)
        print("EXTRACTION RESULT")
        print("=" * 50)
        print(f"Vendor Name      : {data.vendor_name if data.vendor_name else 'Not found'}")
        print(f"Transaction Date : {data.transaction_date if data.transaction_date else 'Not found'}")
        print(f"Total Amount     : {data.total_amount}")
        print("-" * 50)
        print("Items:")
        if data.items:
            for i, item in enumerate(data.items, start=1):
                print(f"  {i}. {item.name} — {item.price}")
        else:
            print("  No items found.")
        print("=" * 50)
    except Exception as e:
        raise RuntimeError(f"[ERROR] Failed to print result as text: {e}")

SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}

def get_image_paths(folder_path: str) -> List[str]:
    try:
        folder = Path(folder_path)
        if not folder.exists():
            raise FileNotFoundError(f"[ERROR] Folder not found: {folder_path}")
        if not folder.is_dir():
            raise NotADirectoryError(f"[ERROR] Path is not a folder: {folder_path}")

        image_paths = sorted([
            str(p) for p in folder.iterdir()
            if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS
        ])

        if not image_paths:
            raise FileNotFoundError(f"[ERROR] No supported image files found in: {folder_path}")

        print(f"[DATASET] Found {len(image_paths)} image(s) in '{folder_path}':")
        for p in image_paths:
            print(f"  - {p}")
        return image_paths

    except (FileNotFoundError, NotADirectoryError):
        raise
    except Exception as e:
        raise RuntimeError(f"[ERROR] Failed to scan folder '{folder_path}': {e}")


def run_pipeline(image_path: str) -> ReceiptData:
    try:
        print(f"\n[PIPELINE] Starting pipeline for: {image_path}")

        if not os.path.exists(image_path):
            raise FileNotFoundError(f"[ERROR] Input image not found: {image_path}")

        extracted = extract_receipt_data(image_path)
        save_output_json(extracted, image_path)
        print_result_as_text(extracted)

        print(f"[PIPELINE] Pipeline completed for: {image_path}")
        return extracted

    except Exception as e:
        raise RuntimeError(f"[ERROR] Pipeline failed for '{image_path}': {e}")


def run_dataset(folder_path: str) -> dict:
    image_paths = get_image_paths(folder_path)
    results = {}
    failed = []

    for i, image_path in enumerate(image_paths, start=1):
        print(f"\n{'=' * 60}")
        print(f"[DATASET] Processing {i}/{len(image_paths)}: {image_path}")
        print(f"{'=' * 60}")

        max_retry = 1
        success = False

        for attempt in range(max_retry):
            try:
                result = run_pipeline(image_path)
                results[image_path] = result
                success = True
                break

            except Exception as e:
                err = str(e)
                
                # STOP ถ้า quota หมด
                if "quota" in err.lower():
                    raise RuntimeError("QUOTA_EXCEEDED_STOP")

                print(f"[RETRY] attempt {attempt+1}/{max_retry} failed")

                # handle 429 / 503
                if "429" in err or "503" in err:
                    wait = 5 * (2 ** attempt)   # exponential backoff
                    print(f"[WAIT] {wait} seconds before retry...")
                    time.sleep(wait)
                else:
                    print(f"[ERROR] {e}")
                    break

        if not success:
            print(f"[FAIL] Skipping '{image_path}'")
            failed.append(image_path)

        # ✅ delay ปกติระหว่างรูป
        time.sleep(15)

    print(f"\n[DATASET] Done. Success: {len(results)}, Failed: {len(failed)}")

    if failed:
        print("[DATASET] Failed files:")
        for f in failed:
            print(f"  - {f}")

    return results

# --- Run full dataset ---
if __name__ == "__main__":
    DATASET_FOLDER = "C:\Hackathon\Data"
    all_results = run_dataset(DATASET_FOLDER)

def load_ground_truth(gt_path: str) -> dict:
    try:
        with open(gt_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        print(f"[METRICS] Ground truth loaded from: {gt_path}")
        return data
    except FileNotFoundError:
        raise FileNotFoundError(f"[ERROR] Ground truth file not found: {gt_path}")
    except Exception as e:
        raise RuntimeError(f"[ERROR] Failed to load ground truth: {e}")


def normalize_text(text) -> str:
    if text is None:
        return ""
    return re.sub(r"\s+", " ", str(text).strip().lower())


def compute_field_accuracy(predicted, ground_truth) -> float:
    pred_str = normalize_text(predicted)
    gt_str = normalize_text(ground_truth)
    if pred_str == "" and gt_str == "":
        return 1.0
    if pred_str == "" or gt_str == "":
        return 0.0
    return fuzz.ratio(pred_str, gt_str) / 100.0


def compute_numeric_accuracy(predicted: float, ground_truth: float, tolerance: float = 0.01) -> float:
    try:
        if ground_truth == 0:
            return 1.0 if predicted == 0 else 0.0
        relative_error = abs(predicted - ground_truth) / abs(ground_truth)
        return max(0.0, 1.0 - relative_error)
    except Exception as e:
        print(f"[WARNING] Numeric accuracy computation error: {e}")
        return 0.0


def compute_items_f1(predicted_items: list, ground_truth_items: list, threshold: float = 80.0) -> dict:
    try:
        tp = 0
        matched_gt = set()

        for pred in predicted_items:
            pred_name = normalize_text(pred.get("name", ""))
            for j, gt in enumerate(ground_truth_items):
                if j in matched_gt:
                    continue
                gt_name = normalize_text(gt.get("name", ""))
                score = fuzz.ratio(pred_name, gt_name)
                if score >= threshold:
                    tp += 1
                    matched_gt.add(j)
                    break

        fp = len(predicted_items) - tp
        fn = len(ground_truth_items) - tp
        precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        recall    = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1        = (2 * precision * recall / (precision + recall)) if (precision + recall) > 0 else 0.0

        return {"precision": precision, "recall": recall, "f1": f1}
    except Exception as e:
        raise RuntimeError(f"[ERROR] Failed to compute items F1: {e}")


def evaluate_single(predicted: ReceiptData, gt_data: dict) -> dict:
    try:
        vendor_acc = compute_field_accuracy(predicted.vendor_name, gt_data.get("vendor_name"))
        date_acc   = compute_field_accuracy(predicted.transaction_date, gt_data.get("transaction_date"))
        total_acc  = compute_numeric_accuracy(predicted.total_amount, gt_data.get("total_amount", 0))

        pred_items = [item.model_dump() for item in predicted.items]
        gt_items   = gt_data.get("items", [])
        items_scores = compute_items_f1(pred_items, gt_items)

        overall = (vendor_acc + date_acc + total_acc + items_scores["f1"]) / 4.0

        return {
            "vendor_name_accuracy":      round(vendor_acc, 4),
            "transaction_date_accuracy": round(date_acc, 4),
            "total_amount_accuracy":     round(total_acc, 4),
            "items_precision":           round(items_scores["precision"], 4),
            "items_recall":              round(items_scores["recall"], 4),
            "items_f1":                  round(items_scores["f1"], 4),
            "overall_score":             round(overall, 4),
        }
    except Exception as e:
        raise RuntimeError(f"[ERROR] evaluate_single failed: {e}")


def evaluate_dataset(all_results: dict, gt_path: str) -> dict:
    try:
        gt_all = load_ground_truth(gt_path)

        per_image_metrics = {}
        skipped = []

        for image_path, predicted in all_results.items():
            image_name = Path(image_path).stem
            if image_name not in gt_all:
                print(f"[WARNING] No ground truth entry for '{image_name}', skipping.")
                skipped.append(image_name)
                continue
            try:
                metrics = evaluate_single(predicted, gt_all[image_name])
                per_image_metrics[image_name] = metrics
                print(f"[METRICS] '{image_name}' -> overall: {metrics['overall_score']:.2%}")
            except Exception as e:
                print(f"[ERROR] Metrics failed for '{image_name}': {e}")
                skipped.append(image_name)

        if not per_image_metrics:
            raise RuntimeError("[ERROR] No metrics could be computed. Check ground truth keys match image names.")

        # Average across all images
        keys = [
            "vendor_name_accuracy", "transaction_date_accuracy",
            "total_amount_accuracy", "items_precision",
            "items_recall", "items_f1", "overall_score",
        ]
        avg_metrics = {
            k: round(sum(m[k] for m in per_image_metrics.values()) / len(per_image_metrics), 4)
            for k in keys
        }

        print(f"\n[METRICS] Evaluated {len(per_image_metrics)} image(s). Skipped: {len(skipped)}")
        return {
            "per_image":  per_image_metrics,
            "average":    avg_metrics,
        }

    except Exception as e:
        raise RuntimeError(f"[ERROR] Dataset evaluation failed: {e}")
    
