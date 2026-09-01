"""Parse grocery receipts from S3 with Textract AnalyzeExpense."""
from __future__ import annotations

import argparse
import json
import os
import re
import time
from collections import defaultdict
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError

SUPPORTED = {".pdf", ".jpg", ".jpeg", ".png"}
MONEY = Decimal("0.01")


def decimal_value(value: Any) -> Decimal | None:
    if value is None:
        return None
    cleaned = re.sub(r"[^0-9.\-]", "", str(value).replace(",", ""))
    if not cleaned or cleaned in {".", "-", "-."}:
        return None
    try:
        return Decimal(cleaned).quantize(MONEY, rounding=ROUND_HALF_UP)
    except InvalidOperation:
        return None


def json_safe(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {key: json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [json_safe(item) for item in value]
    return value


def text_field(fields: list[dict[str, Any]], *names: str) -> str | None:
    wanted = {name.upper() for name in names}
    for field in fields:
        field_type = str(field.get("Type", {}).get("Text", "")).upper()
        field_label = str(field.get("LabelDetection", {}).get("Text", "")).strip().upper().rstrip(":")
        if field_type in wanted or field_label in wanted:
            value = field.get("ValueDetection", {}).get("Text") or field.get("LabelDetection", {}).get("Text")
            if value:
                return str(value).strip()
    return None


def value_field(fields: list[dict[str, Any]], *names: str) -> Decimal | None:
    return decimal_value(text_field(fields, *names))


def normalized_name(name: str) -> str:
    name = re.sub(r"[^A-Za-z0-9 &'/-]", "", name).strip()
    return re.sub(r"\s+", " ", name).title() or "Unknown item"


def normalized_date(value: str | None) -> str | None:
    if not value:
        return None
    for pattern in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%d/%m/%Y", "%d-%b-%Y", "%d-%B-%Y"):
        try:
            return datetime.strptime(value.strip(), pattern).date().isoformat()
        except ValueError:
            continue
    return value.strip()


def receipt_id(key: str) -> str:
    stem = Path(key).stem.lower()
    return re.sub(r"[^a-z0-9]+", "-", stem).strip("-") or "receipt"


def expense_documents(response: dict[str, Any]) -> list[dict[str, Any]]:
    return response.get("ExpenseDocuments", [])


def analyze_image(textract: Any, bucket: str, key: str) -> dict[str, Any]:
    return textract.analyze_expense(Document={"S3Object": {"Bucket": bucket, "Name": key}})


def analyze_pdf(textract: Any, bucket: str, key: str, poll_seconds: float) -> dict[str, Any]:
    started = textract.start_expense_analysis(DocumentLocation={"S3Object": {"Bucket": bucket, "Name": key}})
    job_id = started["JobId"]
    pages: list[dict[str, Any]] = []
    next_token = None
    while True:
        request = {"JobId": job_id}
        if next_token:
            request["NextToken"] = next_token
        response = textract.get_expense_analysis(**request)
        status = response.get("JobStatus")
        if status == "FAILED":
            raise RuntimeError(f"Textract job {job_id} failed: {response.get('StatusMessage', 'no reason returned')}")
        if status == "IN_PROGRESS":
            time.sleep(poll_seconds)
            continue
        pages.extend(expense_documents(response))
        next_token = response.get("NextToken")
        if not next_token:
            return {"JobId": job_id, "JobStatus": status, "ExpenseDocuments": pages}


def normalize(textract_response: dict[str, Any], source_key: str, parsed_at: str) -> dict[str, Any]:
    documents = expense_documents(textract_response)
    fields = documents[0].get("SummaryFields", []) if documents else []
    items: list[dict[str, Any]] = []
    for document in documents:
        for group in document.get("LineItemGroups", []):
            for line in group.get("LineItems", []):
                line_fields = line.get("LineItemExpenseFields", [])
                name = text_field(line_fields, "ITEM", "DESCRIPTION", "PRODUCT_NAME", "NAME")
                if not name:
                    # Some retailers return the description as an OTHER field with a label.
                    for field in line_fields:
                        label = str(field.get("LabelDetection", {}).get("Text", "")).lower()
                        value = field.get("ValueDetection", {}).get("Text")
                        if value and any(word in label for word in ("item", "description", "product", "name")):
                            name = str(value).strip()
                            break
                name = name or text_field(line_fields, "PRODUCT_CODE") or "Unknown item"
                quantity = value_field(line_fields, "QUANTITY") or Decimal("1")
                unit_price = value_field(line_fields, "UNIT_PRICE")
                total_price = value_field(line_fields, "PRICE", "LINE_TOTAL", "AMOUNT", "TOTAL")
                items.append({
                    "name": normalized_name(name),
                    "quantity": float(quantity),
                    "unit_price": float(unit_price) if unit_price is not None else None,
                    "total_price": float(total_price) if total_price is not None else None,
                    "raw_text": " | ".join(
                        str(field.get("ValueDetection", {}).get("Text", ""))
                        for field in line_fields
                        if field.get("ValueDetection", {}).get("Text")
                    ).strip(" |"),
                })
    return {
        "receipt_id": receipt_id(source_key),
        "source_key": source_key,
        "parsed_at": parsed_at,
        "vendor": text_field(fields, "VENDOR_NAME", "VENDOR"),
        "date": normalized_date(text_field(fields, "INVOICE_RECEIPT_DATE", "RECEIPT_DATE", "DATE")),
        "subtotal": float(value_field(fields, "SUBTOTAL", "NET_AMOUNT") or 0),
        "tax": float(value_field(fields, "TAX", "TOTAL_TAX", "VAT", "VAT_AMOUNT") or 0),
        "total": float(value_field(fields, "TOTAL", "AMOUNT_DUE", "GRAND_TOTAL") or 0),
        "items": items,
    }


def dashboard_summary(parsed_receipts: list[dict[str, Any]]) -> dict[str, Any]:
    products: dict[str, list[dict[str, Any]]] = defaultdict(list)
    receipts = []
    for receipt in parsed_receipts:
        receipts.append({key: receipt.get(key) for key in ("receipt_id", "date", "vendor", "subtotal", "tax", "total")})
        for item in receipt.get("items", []):
            products[item["name"]].append({**item, "date": receipt.get("date"), "vendor": receipt.get("vendor")})
    product_rows = []
    for name, purchases in products.items():
        priced = [Decimal(str(item["total_price"])) for item in purchases if item.get("total_price") is not None]
        latest = max(purchases, key=lambda item: item.get("date") or "")
        product_rows.append({
            "name": name,
            "latest_price": float(Decimal(str(latest["total_price"])).quantize(MONEY)) if latest.get("total_price") is not None else None,
            "average_price": float((sum(priced, Decimal("0")) / len(priced)).quantize(MONEY)) if priced else None,
            "total_spent": float(sum(priced, Decimal("0")).quantize(MONEY)),
            "last_bought": latest.get("date"),
            "vendor": latest.get("vendor"),
            "purchase_count": len(purchases),
        })
    return {"generated_at": datetime.now(timezone.utc).isoformat(), "receipts": sorted(receipts, key=lambda row: row.get("date") or "", reverse=True), "products": sorted(product_rows, key=lambda row: row["name"])}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="reprocess receipts with existing parsed output")
    parser.add_argument("--poll-seconds", type=float, default=3, help="seconds between PDF Textract status checks")
    args = parser.parse_args()
    bucket = os.environ["GROCERY_BUCKET"]
    raw_prefix = os.getenv("RAW_PREFIX", "receipts/raw/").rstrip("/") + "/"
    textract_prefix = os.getenv("TEXTRACT_PREFIX", "receipts/textract/").rstrip("/") + "/"
    parsed_prefix = os.getenv("PARSED_PREFIX", "receipts/parsed/").rstrip("/") + "/"
    web_data_dir = Path(os.getenv("WEB_DATA_DIR", "web/data"))
    session = boto3.Session(profile_name=os.getenv("AWS_PROFILE") or None, region_name=os.getenv("AWS_REGION"))
    s3, textract = session.client("s3"), session.client("textract")
    try:
        objects = s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=raw_prefix)
        keys = [obj["Key"] for page in objects for obj in page.get("Contents", []) if Path(obj["Key"]).suffix.lower() in SUPPORTED]
        parsed_receipts = []
        for key in sorted(keys):
            rid = receipt_id(key)
            parsed_key = parsed_prefix + rid + ".json"
            try:
                existing = s3.get_object(Bucket=bucket, Key=parsed_key)
                parsed = json.loads(existing["Body"].read())
                if not args.force:
                    parsed_receipts.append(parsed)
                    print(f"Skipping {key}; parsed output exists")
                    continue
            except s3.exceptions.NoSuchKey:
                pass
            print(f"Processing s3://{bucket}/{key}")
            response = analyze_pdf(textract, bucket, key, args.poll_seconds) if Path(key).suffix.lower() == ".pdf" else analyze_image(textract, bucket, key)
            parsed = normalize(response, key, datetime.now(timezone.utc).isoformat())
            s3.put_object(Bucket=bucket, Key=textract_prefix + rid + ".json", Body=json.dumps(json_safe(response)).encode(), ContentType="application/json")
            s3.put_object(Bucket=bucket, Key=parsed_key, Body=json.dumps(parsed).encode(), ContentType="application/json")
            parsed_receipts.append(parsed)
        web_data_dir.mkdir(parents=True, exist_ok=True)
        # Only this aggregate is public: it contains no source keys, invoice data, or OCR text.
        (web_data_dir / "grocery-summary.json").write_text(json.dumps(dashboard_summary(parsed_receipts), indent=2) + "\n", encoding="utf-8")
        print(f"Wrote sanitized dashboard data for {len(parsed_receipts)} receipt(s)")
    except (BotoCoreError, ClientError) as error:
        raise RuntimeError(f"AWS processing failed: {error}") from error


if __name__ == "__main__":
    main()
