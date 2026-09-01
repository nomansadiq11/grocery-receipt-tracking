# Pantry Ledger

Private grocery receipt processing with AWS Textract, S3, Docker Compose, and a static GitHub Pages dashboard.

## Architecture

Receipt files in `receipts/raw/` are uploaded to a private S3 bucket. The local Python CLI uses Textract AnalyzeExpense, stores the complete response in `receipts/textract/`, stores normalized private records in `receipts/parsed/`, and writes only sanitized product and spending aggregates to `web/data/grocery-summary.json`. The browser never receives AWS credentials and never reads S3.

S3 prefixes are intentionally separate: `receipts/raw/`, `receipts/textract/`, and `receipts/parsed/`. Raw files, full OCR responses, source keys, and line-item raw text remain private.

## Setup

1. Create the bucket and policy:
	```sh
	aws cloudformation deploy --template-file infra/cloudformation.yml --stack-name grocery-receipts
	aws cloudformation describe-stacks --stack-name grocery-receipts --query 'Stacks[0].Outputs'
	```
	Attach the `ProcessorPolicyArn` output to the IAM user or role used by the local AWS CLI. The policy grants only S3 list/read/write for receipt prefixes and the required Textract actions.
2. Configure AWS CLI credentials locally with `aws configure` (or an AWS profile with equivalent permissions).
3. Copy `.env.example` to `.env`, then fill in `GROCERY_BUCKET` and `AWS_REGION`. Set `AWS_PROFILE` if you do not use the default profile.
4. Build the local processing image:
	```sh
	docker compose build
	```
5. Put PDF, JPG, JPEG, or PNG files into `receipts/raw/`, then upload them:
	```sh
	docker compose run --rm processor python scripts/upload_receipts.py --directory receipts/raw/08-2026
	```
	The current August 2026 folder contains 11 PDF receipts. The uploader stores them under the configured `RAW_PREFIX`.
6. Parse receipts and refresh the public-safe summary:
	```sh
	docker compose run --rm processor python scripts/process_receipts.py
	docker compose run --rm processor python scripts/process_receipts.py --force
	```
	Existing parsed JSON is skipped unless `--force` is supplied. PDFs use the asynchronous Textract flow; images use AnalyzeExpense directly.
7. Preview the dashboard at http://localhost:8081 (set `WEB_PORT` in `.env` to choose another port):
	```sh
	docker compose up web
	```

The Compose processor mounts the project and read-only `~/.aws` credentials into the non-root container. Credentials are never copied into the image or dashboard.

## GitHub Pages

Publish the `web/` directory as the Pages source (for example, with a GitHub Actions Pages workflow). Commit only the cleaned `web/data/grocery-summary.json`; never commit `.env`, receipt files, or private S3 output. The included sample JSON makes the dashboard usable before the first processing run.

## Cost estimate

Textract AnalyzeExpense is approximately $0.01 per page. For example, 10 receipts per month at 2 pages each is about $0.20/month, plus small S3 storage and request charges. Check current AWS pricing for your region before production use.

## Validation

```sh
docker compose build
docker compose run --rm processor python scripts/process_receipts.py --help
docker compose up web
```
