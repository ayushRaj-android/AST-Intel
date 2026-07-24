"""Tests for the cloud infrastructure taxonomy.

Covers IaC type classification (Terraform + Bicep/ARM), SDK client
classification, boto3 service mapping, and Python qualified-name mapping.
"""

from __future__ import annotations

import pytest

from ast_intel.extractors._cloud_taxonomy import (
    CATEGORIES,
    CloudResourceInfo,
    classify_boto3_service,
    classify_client,
    classify_iac_type,
    classify_python_qualified,
)

# ---------------------------------------------------------------------------
# region:    --- Category validation
# ---------------------------------------------------------------------------


class TestCategories:
    def test_known_categories(self) -> None:
        expected = {
            "database", "cache", "queue", "topic",
            "stream", "storage", "secret", "other",
        }
        assert expected == CATEGORIES


# endregion


# ---------------------------------------------------------------------------
# region:    --- IaC Terraform classification
# ---------------------------------------------------------------------------


class TestTerraformClassification:
    @pytest.mark.parametrize(
        ("resource_type", "provider", "category"),
        [
            ("aws_sqs_queue", "aws", "queue"),
            ("aws_dynamodb_table", "aws", "database"),
            ("aws_s3_bucket", "aws", "storage"),
            ("aws_sns_topic", "aws", "topic"),
            ("aws_kinesis_stream", "aws", "stream"),
            ("aws_elasticache_cluster", "aws", "cache"),
            ("aws_secretsmanager_secret", "aws", "secret"),
            ("aws_rds_instance", "aws", "database"),
            ("azurerm_cosmosdb_account", "azure", "database"),
            ("azurerm_redis_cache", "azure", "cache"),
            ("azurerm_servicebus_queue", "azure", "queue"),
            ("azurerm_servicebus_topic", "azure", "topic"),
            ("azurerm_eventhub", "azure", "stream"),
            ("azurerm_eventgrid_topic", "azure", "topic"),
            ("azurerm_storage_account", "azure", "storage"),
            ("azurerm_key_vault", "azure", "secret"),
            ("azurerm_mssql_server", "azure", "database"),
            ("google_sql_database_instance", "gcp", "database"),
            ("google_pubsub_topic", "gcp", "topic"),
            ("google_storage_bucket", "gcp", "storage"),
            ("google_redis_instance", "gcp", "cache"),
            ("google_secret_manager_secret", "gcp", "secret"),
            ("google_spanner_instance", "gcp", "database"),
        ],
    )
    def test_known_terraform_types(
        self, resource_type: str, provider: str, category: str,
    ) -> None:
        info = classify_iac_type(resource_type)
        assert info is not None
        assert info.provider == provider
        assert info.category == category

    def test_unknown_terraform_type_returns_none(self) -> None:
        assert classify_iac_type("aws_lambda_function") is None
        assert classify_iac_type("azurerm_linux_virtual_machine") is None
        assert classify_iac_type("random_pet") is None


# endregion


# ---------------------------------------------------------------------------
# region:    --- IaC Bicep/ARM classification (prefix match)
# ---------------------------------------------------------------------------


class TestBicepClassification:
    @pytest.mark.parametrize(
        ("bicep_type", "service", "category"),
        [
            ("Microsoft.DocumentDB/databaseAccounts", "Cosmos DB", "database"),
            ("Microsoft.DocumentDB/databaseAccounts/sqlDatabases", "Cosmos DB", "database"),
            ("Microsoft.Cache/redis", "Redis", "cache"),
            ("Microsoft.Cache/redisEnterprise/databases", "Redis Enterprise", "cache"),
            ("Microsoft.ServiceBus/namespaces/queues", "Service Bus", "queue"),
            ("Microsoft.ServiceBus/namespaces/topics", "Service Bus", "topic"),
            ("Microsoft.ServiceBus/namespaces", "Service Bus", "queue"),
            ("Microsoft.EventGrid/topics", "Event Grid", "topic"),
            ("Microsoft.EventHub/namespaces/eventhubs", "Event Hub", "stream"),
            ("Microsoft.Storage/storageAccounts", "Blob Storage", "storage"),
            ("Microsoft.Storage/storageAccounts/blobServices", "Blob Storage", "storage"),
            ("Microsoft.KeyVault/vaults", "Key Vault", "secret"),
            ("Microsoft.KeyVault/vaults/secrets", "Key Vault", "secret"),
            ("Microsoft.Sql/servers/databases", "Azure SQL", "database"),
        ],
    )
    def test_known_bicep_prefixes(
        self, bicep_type: str, service: str, category: str,
    ) -> None:
        info = classify_iac_type(bicep_type)
        assert info is not None, f"Expected match for {bicep_type}"
        assert info.provider == "azure"
        assert info.service == service
        assert info.category == category

    def test_longest_prefix_wins(self) -> None:
        # "Microsoft.ServiceBus/namespaces/queues" is longer than
        # "Microsoft.ServiceBus/namespaces" — should match queue not just namespace.
        info = classify_iac_type("Microsoft.ServiceBus/namespaces/queues")
        assert info is not None
        assert info.category == "queue"

        info_topic = classify_iac_type("Microsoft.ServiceBus/namespaces/topics")
        assert info_topic is not None
        assert info_topic.category == "topic"

    def test_unknown_bicep_type_returns_none(self) -> None:
        assert classify_iac_type("Microsoft.Compute/virtualMachines") is None
        assert classify_iac_type("Microsoft.Network/virtualNetworks") is None


# endregion


# ---------------------------------------------------------------------------
# region:    --- SDK client classification
# ---------------------------------------------------------------------------


class TestClientClassification:
    @pytest.mark.parametrize(
        ("client_name", "provider", "category"),
        [
            # Azure
            ("CosmosClient", "azure", "database"),
            ("BlobServiceClient", "azure", "storage"),
            ("ServiceBusClient", "azure", "queue"),
            ("ConnectionMultiplexer", "azure", "cache"),
            ("SecretClient", "azure", "secret"),
            ("EventGridPublisherClient", "azure", "topic"),
            ("EventHubProducerClient", "azure", "stream"),
            ("TableServiceClient", "azure", "database"),
            # AWS
            ("AmazonS3Client", "aws", "storage"),
            ("AmazonDynamoDBClient", "aws", "database"),
            ("AmazonSQSClient", "aws", "queue"),
            ("AmazonSNSClient", "aws", "topic"),
            ("S3Client", "aws", "storage"),
            ("DynamoDbClient", "aws", "database"),
            ("SqsClient", "aws", "queue"),
            # GCP
            ("PublisherClient", "gcp", "topic"),
            ("SecretManagerServiceClient", "gcp", "secret"),
            # Generic
            ("SqlConnection", "generic", "database"),
            ("MongoClient", "generic", "database"),
            ("RedisClient", "generic", "cache"),
            ("JedisPool", "generic", "cache"),
            ("DbContext", "generic", "database"),
            ("Sequelize", "generic", "database"),
        ],
    )
    def test_known_clients(
        self, client_name: str, provider: str, category: str,
    ) -> None:
        info = classify_client(client_name)
        assert info is not None, f"Expected match for {client_name}"
        assert info.provider == provider
        assert info.category == category

    def test_unknown_client_returns_none(self) -> None:
        assert classify_client("HttpClient") is None
        assert classify_client("MyService") is None
        assert classify_client("Logger") is None

    def test_case_sensitive(self) -> None:
        # Must match exact case
        assert classify_client("cosmosclient") is None
        assert classify_client("COSMOSCLIENT") is None
        assert classify_client("CosmosClient") is not None


# endregion


# ---------------------------------------------------------------------------
# region:    --- boto3 service classification
# ---------------------------------------------------------------------------


class TestBoto3Classification:
    @pytest.mark.parametrize(
        ("service_name", "category"),
        [
            ("s3", "storage"),
            ("dynamodb", "database"),
            ("sqs", "queue"),
            ("sns", "topic"),
            ("kinesis", "stream"),
            ("secretsmanager", "secret"),
            ("elasticache", "cache"),
            ("rds", "database"),
            ("kms", "secret"),
        ],
    )
    def test_known_services(self, service_name: str, category: str) -> None:
        info = classify_boto3_service(service_name)
        assert info is not None
        assert info.provider == "aws"
        assert info.category == category

    def test_unknown_boto3_service_returns_none(self) -> None:
        assert classify_boto3_service("lambda") is None
        assert classify_boto3_service("ec2") is None
        assert classify_boto3_service("iam") is None


# endregion


# ---------------------------------------------------------------------------
# region:    --- Python qualified-name classification
# ---------------------------------------------------------------------------


class TestPythonQualifiedClassification:
    @pytest.mark.parametrize(
        ("qualified_name", "provider", "category"),
        [
            ("redis.Redis", "generic", "cache"),
            ("redis.StrictRedis", "generic", "cache"),
            ("psycopg2.connect", "generic", "database"),
            ("pymongo.MongoClient", "generic", "database"),
            ("sqlalchemy.create_engine", "generic", "database"),
            ("storage.Client", "gcp", "storage"),
            ("pubsub_v1.PublisherClient", "gcp", "topic"),
            ("spanner.Client", "gcp", "database"),
            ("bigquery.Client", "gcp", "database"),
        ],
    )
    def test_known_qualified_names(
        self, qualified_name: str, provider: str, category: str,
    ) -> None:
        info = classify_python_qualified(qualified_name)
        assert info is not None, f"Expected match for {qualified_name}"
        assert info.provider == provider
        assert info.category == category

    def test_unknown_qualified_name_returns_none(self) -> None:
        assert classify_python_qualified("os.path.join") is None
        assert classify_python_qualified("json.loads") is None


# endregion


# ---------------------------------------------------------------------------
# region:    --- CloudResourceInfo dataclass
# ---------------------------------------------------------------------------


class TestCloudResourceInfo:
    def test_is_frozen(self) -> None:
        info = CloudResourceInfo("aws", "S3", "storage")
        with pytest.raises(AttributeError):
            info.provider = "azure"  # type: ignore[misc]

    def test_equality(self) -> None:
        a = CloudResourceInfo("aws", "S3", "storage")
        b = CloudResourceInfo("aws", "S3", "storage")
        assert a == b

    def test_all_categories_valid(self) -> None:
        """Every entry in all tables uses a valid category."""
        from ast_intel.extractors._cloud_taxonomy import (
            _BOTO3_SERVICE_MAP,
            _CLIENT_MAP,
            _GCP_PYTHON_MAP,
            _IAC_BICEP_PREFIX,
            _IAC_TERRAFORM,
            _PYTHON_GENERIC_MAP,
        )

        all_tables = [
            _IAC_TERRAFORM, _IAC_BICEP_PREFIX,
            _CLIENT_MAP, _BOTO3_SERVICE_MAP,
            _PYTHON_GENERIC_MAP, _GCP_PYTHON_MAP,
        ]
        for table in all_tables:
            for key, info in table.items():
                assert info.category in CATEGORIES, (
                    f"Invalid category {info.category!r} for key {key!r}"
                )


# endregion
