from .celery_app import celery_app


@celery_app.task(name="process_file")
def process_file(file_id: str) -> dict:
    return {"status": "queued", "fileId": file_id}
