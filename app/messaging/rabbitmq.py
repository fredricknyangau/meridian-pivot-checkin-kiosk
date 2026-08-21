import pika
from app.config import settings


def get_rabbitmq_connection() -> pika.BlockingConnection:
    parameters = pika.URLParameters(settings.RABBITMQ_URL)
    return pika.BlockingConnection(parameters)


def declare_queue(channel: pika.adapters.blocking_connection.BlockingChannel) -> None:
    channel.queue_declare(queue=settings.RABBITMQ_QUEUE, durable=True)
