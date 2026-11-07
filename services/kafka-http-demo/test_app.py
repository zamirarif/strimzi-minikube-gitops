import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fastapi import HTTPException
from kafka.errors import KafkaError

from app import ProduceRequest, kafka_client_config, publish_message


class KafkaClientConfigTests(unittest.TestCase):
    def test_uses_scram_credentials_from_environment(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "KAFKA_SASL_USERNAME": "app-user",
                "KAFKA_SASL_PASSWORD": "test-password",
            },
            clear=True,
        ):
            config = kafka_client_config()

        self.assertEqual(config["security_protocol"], "SASL_PLAINTEXT")
        self.assertEqual(config["sasl_mechanism"], "SCRAM-SHA-512")
        self.assertEqual(config["sasl_plain_username"], "app-user")
        self.assertEqual(config["sasl_plain_password"], "test-password")

    def test_requires_both_scram_credentials(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "must be configured"):
                kafka_client_config()


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
