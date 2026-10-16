"""Private Supabase Storage operations; credentials stay server-side."""

import os
from urllib.parse import quote

import httpx


class SupabaseStorageError(RuntimeError):
    pass


def _configuration() -> tuple[str, str, str]:
    base_url = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
    service_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    bucket = os.getenv("SUPABASE_STORAGE_BUCKET", "documents").strip() or "documents"
    if not base_url or not service_key:
        raise RuntimeError("Hosted PDF storage requires SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY.")
    if os.getenv("APP_ENV", "").lower() == "production" and not base_url.startswith("https://"):
        raise RuntimeError("Hosted Supabase Storage must use HTTPS in production.")
    if bucket != "documents":
        raise RuntimeError("SUPABASE_STORAGE_BUCKET must name the existing private 'documents' bucket.")
    return base_url, service_key, bucket


def validate_configuration() -> None:
    _configuration()


def _headers(key: str, content_type: str | None = None) -> dict[str, str]:
    headers = {"apikey": key, "Authorization": f"Bearer {key}"}
    if content_type:
        headers["Content-Type"] = content_type
    return headers


def _request(method: str, url: str, headers: dict[str, str], **kwargs) -> httpx.Response:
    try:
        with httpx.Client(timeout=httpx.Timeout(45.0, connect=10.0), follow_redirects=False) as client:
            response = client.request(method, url, headers=headers, **kwargs)
            response.raise_for_status()
            return response
    except httpx.HTTPStatusError as exc:
        raise SupabaseStorageError(f"Supabase Storage returned HTTP {exc.response.status_code}.") from None
    except httpx.RequestError:
        raise SupabaseStorageError("Supabase Storage could not be reached.") from None


def upload_pdf(owner_id: str, document_id: str, content: bytes) -> str:
    base_url, key, bucket = _configuration()
    storage_key = f"{owner_id}/{document_id}.pdf"
    url = f"{base_url}/storage/v1/object/{quote(bucket, safe='')}/{quote(storage_key, safe='/')}"
    headers = _headers(key, "application/pdf")
    headers["x-upsert"] = "false"
    _request("POST", url, headers, content=content)
    return storage_key


def download_pdf(storage_key: str) -> bytes:
    base_url, key, bucket = _configuration()
    url = f"{base_url}/storage/v1/object/authenticated/{quote(bucket, safe='')}/{quote(storage_key, safe='/')}"
    return _request("GET", url, _headers(key)).content


def delete_pdf(storage_key: str) -> None:
    base_url, key, bucket = _configuration()
    url = f"{base_url}/storage/v1/object/{quote(bucket, safe='')}"
    _request("DELETE", url, _headers(key, "application/json"), json={"prefixes": [storage_key]})
