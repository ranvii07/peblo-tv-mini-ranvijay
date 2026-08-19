"""Cloudflare R2 storage backend — deliberately an unimplemented stub.

This is written out rather than left to the imagination because the challenge asks what
would change to move from local disk to R2. The answer is: this file, and nothing else.

R2 is S3-compatible, so the implementation is boto3 against R2's endpoint:

    self._s3 = boto3.client(
        "s3",
        endpoint_url=f"https://{account_id}.r2.cloudflarestorage.com",
        aws_access_key_id=access_key_id,
        aws_secret_access_key=secret_access_key,
        region_name="auto",            # R2 ignores region but boto3 requires one
    )

    put    -> self._s3.put_object(Bucket=..., Key=key, Body=data, ContentType=content_type)
    get    -> self._s3.get_object(Bucket=..., Key=key)["Body"].read()
    exists -> head_object, catching ClientError 404
    delete -> delete_object   (deleting a missing key is already a no-op in S3)
    public_url -> f"{public_base_url}/{key}"  — the bucket's public r2.dev domain or a
                  custom domain bound to it.

The two semantics the publish job relies on both hold on R2, which is why no call site
changes:

* **Per-object atomic PUT.** S3-compatible object writes are all-or-nothing; there is no
  torn read. On local disk this is achieved with a temp file plus `os.replace`.
* **Read-after-write consistency.** R2 provides it for new objects, so the pointer flip
  in the publish transaction can safely reference a key written moments earlier.

Operationally the only other change is that `/media/{key}` stops proxying bytes and
either redirects to `public_url` or is dropped entirely in favour of the bucket domain,
which also takes image traffic off the API.

Not implemented because there is no R2 account to verify it against, and shipping
untested cloud credentials handling would be worse than shipping an honest stub.
"""

from __future__ import annotations


class R2Storage:
    def __init__(
        self,
        account_id: str,
        bucket: str,
        access_key_id: str,
        secret_access_key: str,
        public_base_url: str = "",
    ):
        self.account_id = account_id
        self.bucket = bucket
        self.access_key_id = access_key_id
        self.secret_access_key = secret_access_key
        self.public_base_url = public_base_url

    def _unimplemented(self, op: str) -> NotImplementedError:
        return NotImplementedError(
            f"R2Storage.{op} is not implemented in this take-home. "
            "Set STORAGE_BACKEND=local to use LocalDiskStorage. "
            "See the module docstring for the boto3 implementation this would carry."
        )

    def put(self, key: str, data: bytes, content_type: str) -> None:
        raise self._unimplemented("put")

    def get(self, key: str) -> bytes:
        raise self._unimplemented("get")

    def exists(self, key: str) -> bool:
        raise self._unimplemented("exists")

    def delete(self, key: str) -> None:
        raise self._unimplemented("delete")

    def public_url(self, key: str) -> str:
        return f"{self.public_base_url.rstrip('/')}/{key}"
