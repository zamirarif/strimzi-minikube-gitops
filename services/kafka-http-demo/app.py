import json
import logging
import os
import threading
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from kafka import KafkaConsumer, KafkaProducer
from kafka.errors import KafkaError
from pydantic import BaseModel, Field

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("kafka-http-demo")

BOOTSTRAP_SERVERS = os.getenv(
    "KAFKA_BOOTSTRAP_SERVERS", "local-kafka-kafka-bootstrap:9092"
)
TOPIC = os.getenv("KAFKA_TOPIC", "test-topic")
CONSUMER_GROUP = os.getenv("KAFKA_CONSUMER_GROUP", "kafka-http-demo")
KAFKA_RETRY_SECONDS = 5


def kafka_client_config() -> dict[str, str]:
    username = os.getenv("KAFKA_SASL_USERNAME")
    password = os.getenv("KAFKA_SASL_PASSWORD")
    if not username or not password:
        raise RuntimeError("Kafka SASL username and password must be configured")

    return {
        "bootstrap_servers": BOOTSTRAP_SERVERS,
        "security_protocol": "SASL_PLAINTEXT",
        "sasl_mechanism": "SCRAM-SHA-512",
        "sasl_plain_username": username,
        "sasl_plain_password": password,
    }


class ProduceRequest(BaseModel):
    message: str = Field(min_length=1, max_length=100_000)
    key: str | None = Field(default=None, max_length=1_000)


def publish_message(
    producer: KafkaProducer, payload: ProduceRequest
) -> dict[str, Any]:
    try:
        record = producer.send(
            TOPIC,
            key=payload.key.encode("utf-8") if payload.key is not None else None,
            value={"message": payload.message},
        ).get(timeout=10)
    except KafkaError as exc:
        logger.exception("Failed to publish to topic %s", TOPIC)
        raise HTTPException(status_code=503, detail="Kafka is unavailable") from exc

    return {
        "topic": record.topic,
        "partition": record.partition,
        "offset": record.offset,
    }


def consume_messages(
    stop_event: threading.Event, client_config: dict[str, str]
) -> None:
    while not stop_event.is_set():
        consumer: KafkaConsumer | None = None
        try:
            consumer = KafkaConsumer(
                TOPIC,
                **client_config,
                group_id=CONSUMER_GROUP,
                auto_offset_reset="earliest",
                enable_auto_commit=True,
                value_deserializer=lambda value: json.loads(value.decode("utf-8")),
            )
            logger.info("Consuming topic %s as group %s", TOPIC, CONSUMER_GROUP)

            while not stop_event.is_set():
                for records in consumer.poll(timeout_ms=1_000).values():
                    for record in records:
                        logger.info(
                            "Consumed topic=%s partition=%s offset=%s key=%s value=%s",
                            record.topic,
                            record.partition,
                            record.offset,
                            record.key.decode("utf-8") if record.key else None,
                            record.value,
                        )
        except KafkaError:
            if not stop_event.is_set():
                logger.exception("Kafka consumer failed; retrying")
        finally:
            if consumer is not None:
                consumer.close()

        stop_event.wait(KAFKA_RETRY_SECONDS)


@asynccontextmanager
async def lifespan(app: FastAPI):
    stop_event = threading.Event()
    client_config = kafka_client_config()
    producer = KafkaProducer(
        **client_config,
        acks="all",
        retries=5,
        max_block_ms=10_000,
        value_serializer=lambda value: json.dumps(value).encode("utf-8"),
    )
    consumer_thread = threading.Thread(
        target=consume_messages,
        args=(stop_event, client_config),
        name="kafka-consumer",
        daemon=True,
    )
    app.state.producer = producer
    consumer_thread.start()
    logger.info(
        "Service started: bootstrap=%s topic=%s group=%s",
        BOOTSTRAP_SERVERS,
        TOPIC,
        CONSUMER_GROUP,
    )

    try:
        yield
    finally:
        stop_event.set()
        consumer_thread.join(timeout=10)
        producer.close(timeout=10)
        logger.info("Service stopped")


app = FastAPI(title="Kafka HTTP Demo", lifespan=lifespan)


@app.get("/healthz")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/messages", status_code=202)
def produce(payload: ProduceRequest, request: Request) -> dict[str, Any]:
    return publish_message(request.app.state.producer, payload)
