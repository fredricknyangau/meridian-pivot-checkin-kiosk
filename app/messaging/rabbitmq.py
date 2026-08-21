import json
import logging
import pika
from app.config import settings

logger = logging.getLogger(__name__)


class RabbitMQPublishError(Exception):
    """Raised when publishing a message to RabbitMQ fails."""
    pass


def publish_print_job_sync(print_job_id: str, attendee_id: int, attendee_name: str) -> None:
    """Synchronously publish a print job payload to RabbitMQ quorum queue.
    
    This function performs blocking network I/O and should be executed in a
    thread pool (e.g. via asyncio.to_thread).
    """
    payload = {
        "print_job_id": print_job_id,
        "attendee_id": attendee_id,
        "attendee_name": attendee_name,
    }

    try:
        parameters = pika.URLParameters(settings.RABBITMQ_URL)
        connection = pika.BlockingConnection(parameters)
        try:
            channel = connection.channel()
            # Declare quorum queue with durable=True
            channel.queue_declare(
                queue=settings.RABBITMQ_QUEUE,
                durable=True,
                arguments={"x-queue-type": "quorum"}
            )
            # Publish message with persistent delivery mode
            channel.basic_publish(
                exchange="",
                routing_key=settings.RABBITMQ_QUEUE,
                body=json.dumps(payload),
                properties=pika.BasicProperties(
                    delivery_mode=pika.DeliveryMode.Persistent
                )
            )
            logger.info("Successfully published print job %s for attendee %s to queue %s",
                        print_job_id, attendee_id, settings.RABBITMQ_QUEUE)
        finally:
            if connection.is_open:
                connection.close()
    except Exception as exc:
        logger.error("Failed to publish print job %s to RabbitMQ: %s", print_job_id, str(exc))
        raise RabbitMQPublishError(f"RabbitMQ publish failed for job {print_job_id}") from exc
