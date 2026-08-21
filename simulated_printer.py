#!/usr/bin/env python3
import json
import logging
import time
import sys
import httpx
import pika
from app.config import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger("simulated_printer")


def process_message(ch, method, properties, body):
    try:
        data = json.loads(body.decode("utf-8"))
        print_job_id = data.get("print_job_id")
        attendee_id = data.get("attendee_id")
        attendee_name = data.get("attendee_name")

        logger.info("Received print job %s for attendee #%s (%s)", print_job_id, attendee_id, attendee_name)

        # Simulate physical printing delay
        time.sleep(0.5)
        logger.info("Badge printed successfully for job %s. Dispatching webhook confirmation...", print_job_id)

        # Dispatch webhook HTTP POST request
        webhook_url = f"{settings.API_BASE_URL}/webhook/print-confirmation"
        payload = {
            "print_job_id": print_job_id,
            "result": "success"
        }

        response = httpx.post(webhook_url, json=payload, timeout=10.0)

        if response.status_code == 200:
            logger.info("Webhook acknowledged HTTP 200 for job %s. Acknowledging RabbitMQ message.", print_job_id)
            ch.basic_ack(delivery_tag=method.delivery_tag)
        else:
            logger.error("Webhook returned status %s for job %s. Nacking message.", response.status_code, print_job_id)
            ch.basic_nack(delivery_tag=method.delivery_tag, requeue=True)
    except Exception as exc:
        logger.error("Error processing print job message: %s", exc)
        ch.basic_nack(delivery_tag=method.delivery_tag, requeue=True)


def start_worker():
    logger.info("Starting Simulated Printer Worker...")
    logger.info("Connecting to RabbitMQ: %s", settings.RABBITMQ_URL)

    parameters = pika.URLParameters(settings.RABBITMQ_URL)
    connection = pika.BlockingConnection(parameters)
    channel = connection.channel()

    # Declare quorum queue
    channel.queue_declare(
        queue=settings.RABBITMQ_QUEUE,
        durable=True,
        arguments={"x-queue-type": "quorum"}
    )
    # Fair dispatch: process 1 message at a time per worker
    channel.basic_qos(prefetch_count=1)

    channel.basic_consume(
        queue=settings.RABBITMQ_QUEUE,
        on_message_callback=process_message,
        auto_ack=False
    )

    logger.info("Simulated Printer Worker listening on queue '%s'...", settings.RABBITMQ_QUEUE)
    try:
        channel.start_consuming()
    except KeyboardInterrupt:
        logger.info("Stopping worker...")
        channel.stop_consuming()
    finally:
        connection.close()


if __name__ == "__main__":
    start_worker()
