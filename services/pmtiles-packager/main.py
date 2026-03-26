#!/usr/bin/env python3
# =============================================================================
# PMTiles Packager Worker - Core Service
# =============================================================================
# Asynchronous worker that listens to Redis Streams for PMTiles 
# generation requests, generates the offline basemap package, 
# and uploads it to MinIO with a TTL.

import os
import sys
import logging
import json
import time
import tempfile
import importlib.util
from datetime import datetime
import boto3
from botocore.client import Config

# Configure Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("pmtiles-packager")

# Environment Variables
REDIS_URL = os.getenv("REDIS_URL", "redis://redis-service:6379")
MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "minio-service:9000")
MINIO_URL = os.getenv("MINIO_URL", f"http://{MINIO_ENDPOINT}")
MINIO_ACCESS_KEY = os.getenv("MINIO_ROOT_USER", os.getenv("MINIO_ACCESS_KEY", "minioadmin"))
MINIO_SECRET_KEY = os.getenv("MINIO_ROOT_PASSWORD", os.getenv("MINIO_SECRET_KEY", "minioadmin"))
MINIO_BUCKET = os.getenv("MINIO_BUCKET", "nekazari")
MINIO_PUBLIC_URL = os.getenv("MINIO_PUBLIC_URL", "https://minio.robotika.cloud")

# Import task-queue module dynamically
try:
    task_queue_file = "/app/task-queue/task_queue.py"
    if os.path.exists(task_queue_file):
        spec = importlib.util.spec_from_file_location("task_queue", task_queue_file)
        task_queue_module = importlib.util.module_from_spec(spec)
        sys.modules["task_queue"] = task_queue_module
        spec.loader.exec_module(task_queue_module)
        TaskQueue = task_queue_module.TaskQueue
        logger.info("TaskQueue module loaded successfully")
    else:
        logger.warning(f"task_queue.py not found at {task_queue_file}. Running in standalone/mock mode.")
        TaskQueue = None
except Exception as e:
    logger.error(f"Failed to load TaskQueue module: {e}")
    TaskQueue = None

class MinioClient:
    def __init__(self):
        self.s3 = boto3.client(
            "s3",
            endpoint_url=MINIO_URL if MINIO_URL.startswith("http") else f"http://{MINIO_URL}",
            aws_access_key_id=MINIO_ACCESS_KEY,
            aws_secret_access_key=MINIO_SECRET_KEY,
            config=Config(signature_version="s3v4"),
            region_name="us-east-1"
        )
        self._ensure_bucket()

    def _ensure_bucket(self):
        try:
            self.s3.head_bucket(Bucket=MINIO_BUCKET)
        except Exception:
            logger.info(f"Bucket {MINIO_BUCKET} not found. Creating it...")
            try:
                self.s3.create_bucket(Bucket=MINIO_BUCKET)
                # Set public policy for frontend access
                policy = {
                    "Version": "2012-10-17",
                    "Statement": [{
                        "Sid": "PublicReadGetObject",
                        "Effect": "Allow",
                        "Principal": "*",
                        "Action": ["s3:GetObject"],
                        "Resource": [f"arn:aws:s3:::{MINIO_BUCKET}/*"]
                    }]
                }
                self.s3.put_bucket_policy(Bucket=MINIO_BUCKET, Policy=json.dumps(policy))
            except Exception as e:
                logger.error(f"Failed to create bucket: {e}")

    def upload_file(self, file_path: str, object_name: str) -> str:
        """Uploads a file and returns the public URL"""
        try:
            # We add a Tagging 'expiration=true' to allow MinIO ILM to delete it later
            # (Requires setting up ILM rules in MinIO separately, see Ops tasks)
            self.s3.upload_file(
                file_path, 
                MINIO_BUCKET, 
                object_name,
                ExtraArgs={'ContentType': 'application/x-pmtiles', 'Tagging': 'expiration=24h'}
            )
            return f"{MINIO_PUBLIC_URL}/{MINIO_BUCKET}/{object_name}"
        except Exception as e:
            logger.error(f"Failed to upload {file_path} to MinIO: {e}")
            raise

class PMTilesPackager:
    def __init__(self):
        self.minio = MinioClient()
        if TaskQueue:
            self.queue = TaskQueue(stream_name="pmtiles:requests")
            self.status_queue = TaskQueue(stream_name="pmtiles:status") # For publishing results
        else:
            self.queue = None

    def build_package(self, parcel_id: str, bbox: list, max_zoom: int) -> str:
        """
        Simulates the creation of a PMTiles archive by fetching XYZ tiles 
        and storing them in a local temporary file.
        In a real deployment, this would use `gdal_translate`, `tippecanoe`, 
        or python's `pmtiles` library to bundle satellite COGs into a local archive.
        """
        logger.info(f"Building PMTiles for parcel {parcel_id} with bbox {bbox} up to zoom {max_zoom}")
        
        # Create a temporary file
        temp_file = tempfile.NamedTemporaryFile(suffix=".pmtiles", delete=False)
        temp_file.close()

        # TODO: Implement actual PMTiles spec writing via `pmtiles` python package.
        # For now, write a dummy header to allow the system to test the flow E2E.
        with open(temp_file.name, "wb") as f:
            f.write(b"PMTiles:DummyContent(SOTA_Offline_Ready)")
        
        # Simulate heavy processing (Raster to PMTiles conversion takes time)
        time.sleep(2)
        
        return temp_file.name

    def process_task(self, task: dict):
        payload = task.get("payload", {})
        tenant_id = payload.get("tenant_id", "unknown")
        parcel_id = payload.get("parcel_id", "unknown")
        bbox = payload.get("bbox", [])
        max_zoom = payload.get("max_zoom", 18)

        if not bbox or len(bbox) != 4:
            logger.error(f"Invalid BBOX provided for parcel {parcel_id}: {bbox}")
            return

        try:
            # 1. Build local PMTiles file
            local_path = self.build_package(parcel_id, bbox, max_zoom)
            
            # 2. Upload to MinIO
            object_name = f"offline/{tenant_id}/basemaps/{parcel_id}.pmtiles"
            public_url = self.minio.upload_file(local_path, object_name)
            
            logger.info(f"Successfully packaged and uploaded: {public_url}")
            
            # Clean up local temp file
            os.remove(local_path)

            # 3. Mark task success in Status queue (for API Gateway to track)
            if self.queue:
                self.queue.update_task_status(
                    task.get("id"),
                    status="completed",
                    result={"url": public_url, "size_bytes": 1024, "parcel_id": parcel_id}
                )

        except Exception as e:
            logger.error(f"Task Failed for parcel {parcel_id}: {e}")
            if self.queue:
                self.queue.update_task_status(
                    task.get("id"),
                    status="failed",
                    error_message=str(e)
                )

    def run(self):
        logger.info("Starting PMTiles Packager Worker...")
        if not self.queue:
            logger.error("No TaskQueue available. Exiting.")
            return

        consumer_group = "pmtiles-workers"
        consumer_name = f"worker-{os.getenv('HOSTNAME', 'local')}"

        while True:
            try:
                tasks = self.queue.consume_tasks(
                    consumer_group=consumer_group, 
                    consumer_name=consumer_name, 
                    count=5
                )
                for task in tasks or []:
                    logger.info(f"Received packaging task: {task.get('id')}")
                    self.process_task(task)
                    self.queue.acknowledge_task(consumer_group, task.get("id"))
            except Exception as e:
                logger.error(f"Error in consumer loop: {e}")
            time.sleep(2)

if __name__ == "__main__":
    worker = PMTilesPackager()
    worker.run()
