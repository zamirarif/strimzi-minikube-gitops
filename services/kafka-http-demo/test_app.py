import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from fastapi import HTTPException
from kafka.errors import KafkaError

from app import ProduceRequest, publish_message


class PublishMessageTests(unittest.TestCase):
    def test_publishes_message_and_returns_record_location(self) -> None:
        producer = Mock()
        producer.send.return_value.get.return_value = SimpleNamespace(
            topic="test-topic",
            partition=2,
            offset=7,
        )

        result = publish_message(
            producer, ProduceRequest(message="hello Kafka", key="demo-1")
        )

        producer.send.assert_called_once_with(
            "test-topic", key=b"demo-1", value={"message": "hello Kafka"}
        )
        self.assertEqual(
            result, {"topic": "test-topic", "partition": 2, "offset": 7}
        )

    def test_reports_kafka_failure_as_service_unavailable(self) -> None:
        producer = Mock()
        producer.send.return_value.get.side_effect = KafkaError("broker unavailable")

        with self.assertRaises(HTTPException) as raised:
            publish_message(producer, ProduceRequest(message="hello Kafka"))

        self.assertEqual(raised.exception.status_code, 503)


if __name__ == "__main__":
    unittest.main()
