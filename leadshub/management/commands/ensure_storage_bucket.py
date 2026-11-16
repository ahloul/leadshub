"""Create the uploads bucket on a local S3-compatible server if it's missing.

For ``make storage-up`` and ``make docker-up`` (RustFS). Production buckets
are created with the infrastructure. Does nothing when uploads are stored on
disk. Waits for the server to start.
"""

import time

from botocore.exceptions import ClientError, EndpointConnectionError
from django.core.files.storage import storages
from django.core.management.base import BaseCommand, CommandError
from storages.backends.s3 import S3Storage

_MISSING = {"404", "NoSuchBucket", "NotFound"}


class Command(BaseCommand):
    help = "Create the uploads bucket on an S3-compatible server if it is missing."

    def add_arguments(self, parser):
        parser.add_argument(
            "--wait", type=int, default=30, help="Seconds to wait for the server (default 30)."
        )

    def handle(self, *args, **options):
        storage = storages["default"]
        if not isinstance(storage, S3Storage):
            self.stdout.write("Uploads are stored on disk (MEDIA_ROOT); there is no bucket to create.")
            return
        client = storage.connection.meta.client
        bucket = storage.bucket_name
        deadline = time.monotonic() + options["wait"]
        while True:
            try:
                client.head_bucket(Bucket=bucket)
            except EndpointConnectionError as error:
                if time.monotonic() >= deadline:
                    raise CommandError(
                        "The storage server didn't answer. Is it running? (make storage-up)"
                    ) from error
                time.sleep(1)
                continue
            except ClientError as error:
                if error.response.get("Error", {}).get("Code") not in _MISSING:
                    raise CommandError(f"Can't use bucket {bucket}: {error}") from error
                client.create_bucket(Bucket=bucket)
                self.stdout.write(self.style.SUCCESS(f"Created bucket {bucket}."))
                return
            self.stdout.write(f"Bucket {bucket} is ready.")
            return
