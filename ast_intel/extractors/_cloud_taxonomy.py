"""Cloud infrastructure taxonomy — maps IaC types and SDK clients to categories.

This module is the **single source of truth** for classifying cloud resources.
Both the code-side detector (``_cloud_clients.py``) and the IaC classifier
reference these tables, ensuring consistent categorization across the two
signal sources.

The taxonomy answers: given an IaC resource type (e.g. ``aws_sqs_queue``) or a
code-level SDK client (e.g. ``CosmosClient``), what is its normalized
``(provider, service, category)``?
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

__all__: list[str] = [
    "CATEGORIES",
    "CloudResourceInfo",
    "classify_client",
    "classify_iac_type",
]


# ---------------------------------------------------------------------------
# region:    --- Category enum (as a frozenset for validation)
# ---------------------------------------------------------------------------

CATEGORIES: Final[frozenset[str]] = frozenset({
    "database",
    "cache",
    "queue",
    "topic",
    "stream",
    "storage",
    "secret",
    "other",
})


# ---------------------------------------------------------------------------
# region:    --- Data class
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CloudResourceInfo:
    """Normalized classification of a cloud resource."""

    provider: str
    """Cloud provider: 'azure', 'aws', 'gcp', or 'generic'."""

    service: str
    """Specific service name (e.g. 'Cosmos DB', 'SQS', 'Redis')."""

    category: str
    """One of CATEGORIES: database, cache, queue, topic, stream, storage, secret, other."""


# ---------------------------------------------------------------------------
# region:    --- IaC resource-type classification
# ---------------------------------------------------------------------------

# Terraform resource_type → CloudResourceInfo
_IAC_TERRAFORM: dict[str, CloudResourceInfo] = {
    # --- AWS: database ---
    "aws_rds_instance": CloudResourceInfo("aws", "RDS", "database"),
    "aws_rds_cluster": CloudResourceInfo("aws", "RDS", "database"),
    "aws_rds_cluster_instance": CloudResourceInfo("aws", "RDS", "database"),
    "aws_db_instance": CloudResourceInfo("aws", "RDS", "database"),
    "aws_dynamodb_table": CloudResourceInfo("aws", "DynamoDB", "database"),
    "aws_dynamodb_global_table": CloudResourceInfo("aws", "DynamoDB", "database"),
    "aws_redshift_cluster": CloudResourceInfo("aws", "Redshift", "database"),
    "aws_neptune_cluster": CloudResourceInfo("aws", "Neptune", "database"),
    "aws_docdb_cluster": CloudResourceInfo("aws", "DocumentDB", "database"),
    # --- AWS: cache ---
    "aws_elasticache_cluster": CloudResourceInfo("aws", "ElastiCache", "cache"),
    "aws_elasticache_replication_group": CloudResourceInfo("aws", "ElastiCache", "cache"),
    # --- AWS: queue ---
    "aws_sqs_queue": CloudResourceInfo("aws", "SQS", "queue"),
    # --- AWS: topic ---
    "aws_sns_topic": CloudResourceInfo("aws", "SNS", "topic"),
    # --- AWS: stream ---
    "aws_kinesis_stream": CloudResourceInfo("aws", "Kinesis", "stream"),
    "aws_kinesis_firehose_delivery_stream": CloudResourceInfo("aws", "Kinesis Firehose", "stream"),
    "aws_msk_cluster": CloudResourceInfo("aws", "MSK", "stream"),
    # --- AWS: storage ---
    "aws_s3_bucket": CloudResourceInfo("aws", "S3", "storage"),
    "aws_efs_file_system": CloudResourceInfo("aws", "EFS", "storage"),
    # --- AWS: secret ---
    "aws_secretsmanager_secret": CloudResourceInfo("aws", "Secrets Manager", "secret"),
    "aws_ssm_parameter": CloudResourceInfo("aws", "SSM Parameter Store", "secret"),
    "aws_kms_key": CloudResourceInfo("aws", "KMS", "secret"),
    # --- Azure: database ---
    "azurerm_cosmosdb_account": CloudResourceInfo("azure", "Cosmos DB", "database"),
    "azurerm_cosmosdb_sql_database": CloudResourceInfo("azure", "Cosmos DB", "database"),
    "azurerm_cosmosdb_sql_container": CloudResourceInfo("azure", "Cosmos DB", "database"),
    "azurerm_cosmosdb_mongo_database": CloudResourceInfo("azure", "Cosmos DB", "database"),
    "azurerm_mssql_server": CloudResourceInfo("azure", "Azure SQL", "database"),
    "azurerm_mssql_database": CloudResourceInfo("azure", "Azure SQL", "database"),
    "azurerm_mysql_flexible_server": CloudResourceInfo("azure", "MySQL", "database"),
    "azurerm_postgresql_flexible_server": CloudResourceInfo("azure", "PostgreSQL", "database"),
    "azurerm_storage_table": CloudResourceInfo("azure", "Table Storage", "database"),
    # --- Azure: cache ---
    "azurerm_redis_cache": CloudResourceInfo("azure", "Redis", "cache"),
    "azurerm_redis_enterprise_cluster": CloudResourceInfo("azure", "Redis Enterprise", "cache"),
    # --- Azure: queue ---
    "azurerm_servicebus_queue": CloudResourceInfo("azure", "Service Bus", "queue"),
    "azurerm_storage_queue": CloudResourceInfo("azure", "Storage Queue", "queue"),
    # --- Azure: topic ---
    "azurerm_servicebus_topic": CloudResourceInfo("azure", "Service Bus", "topic"),
    "azurerm_eventgrid_topic": CloudResourceInfo("azure", "Event Grid", "topic"),
    "azurerm_eventgrid_system_topic": CloudResourceInfo("azure", "Event Grid", "topic"),
    # --- Azure: stream ---
    "azurerm_eventhub": CloudResourceInfo("azure", "Event Hub", "stream"),
    "azurerm_eventhub_namespace": CloudResourceInfo("azure", "Event Hub", "stream"),
    # --- Azure: storage ---
    "azurerm_storage_account": CloudResourceInfo("azure", "Blob Storage", "storage"),
    "azurerm_storage_container": CloudResourceInfo("azure", "Blob Storage", "storage"),
    "azurerm_storage_blob": CloudResourceInfo("azure", "Blob Storage", "storage"),
    # --- Azure: secret ---
    "azurerm_key_vault": CloudResourceInfo("azure", "Key Vault", "secret"),
    "azurerm_key_vault_secret": CloudResourceInfo("azure", "Key Vault", "secret"),
    "azurerm_key_vault_key": CloudResourceInfo("azure", "Key Vault", "secret"),
    "azurerm_key_vault_certificate": CloudResourceInfo("azure", "Key Vault", "secret"),
    # --- GCP: database ---
    "google_sql_database_instance": CloudResourceInfo("gcp", "Cloud SQL", "database"),
    "google_sql_database": CloudResourceInfo("gcp", "Cloud SQL", "database"),
    "google_spanner_instance": CloudResourceInfo("gcp", "Spanner", "database"),
    "google_spanner_database": CloudResourceInfo("gcp", "Spanner", "database"),
    "google_bigtable_instance": CloudResourceInfo("gcp", "Bigtable", "database"),
    "google_firestore_database": CloudResourceInfo("gcp", "Firestore", "database"),
    # --- GCP: cache ---
    "google_redis_instance": CloudResourceInfo("gcp", "Memorystore", "cache"),
    # --- GCP: topic ---
    "google_pubsub_topic": CloudResourceInfo("gcp", "Pub/Sub", "topic"),
    "google_pubsub_subscription": CloudResourceInfo("gcp", "Pub/Sub", "topic"),
    # --- GCP: storage ---
    "google_storage_bucket": CloudResourceInfo("gcp", "Cloud Storage", "storage"),
    # --- GCP: secret ---
    "google_secret_manager_secret": CloudResourceInfo("gcp", "Secret Manager", "secret"),
    "google_kms_crypto_key": CloudResourceInfo("gcp", "Cloud KMS", "secret"),
}

# Bicep / ARM resource type prefix → CloudResourceInfo
# Matched by prefix (e.g. "Microsoft.DocumentDB/databaseAccounts" starts with key).
_IAC_BICEP_PREFIX: dict[str, CloudResourceInfo] = {
    # database
    "Microsoft.DocumentDB/databaseAccounts": CloudResourceInfo("azure", "Cosmos DB", "database"),
    "Microsoft.Sql/servers": CloudResourceInfo("azure", "Azure SQL", "database"),
    "Microsoft.DBforMySQL": CloudResourceInfo("azure", "MySQL", "database"),
    "Microsoft.DBforPostgreSQL": CloudResourceInfo("azure", "PostgreSQL", "database"),
    # cache
    "Microsoft.Cache/redis": CloudResourceInfo("azure", "Redis", "cache"),
    "Microsoft.Cache/redisEnterprise": CloudResourceInfo("azure", "Redis Enterprise", "cache"),
    # queue / topic (Service Bus)
    "Microsoft.ServiceBus/namespaces/queues": CloudResourceInfo("azure", "Service Bus", "queue"),
    "Microsoft.ServiceBus/namespaces/topics": CloudResourceInfo("azure", "Service Bus", "topic"),
    "Microsoft.ServiceBus/namespaces": CloudResourceInfo("azure", "Service Bus", "queue"),
    # topic (Event Grid)
    "Microsoft.EventGrid/topics": CloudResourceInfo("azure", "Event Grid", "topic"),
    "Microsoft.EventGrid/systemTopics": CloudResourceInfo("azure", "Event Grid", "topic"),
    # stream (Event Hub)
    "Microsoft.EventHub/namespaces": CloudResourceInfo("azure", "Event Hub", "stream"),
    # storage
    "Microsoft.Storage/storageAccounts": CloudResourceInfo("azure", "Blob Storage", "storage"),
    # secret
    "Microsoft.KeyVault/vaults": CloudResourceInfo("azure", "Key Vault", "secret"),
}

# ---------------------------------------------------------------------------
# region:    --- Code SDK-client classification
# ---------------------------------------------------------------------------

# Client class/function name → CloudResourceInfo
# Keys are case-sensitive class names (or function patterns for boto3).
_CLIENT_MAP: dict[str, CloudResourceInfo] = {
    # --- Azure SDK (.NET / Python / Java / TS) ---
    "CosmosClient": CloudResourceInfo("azure", "Cosmos DB", "database"),
    "CosmosDatabase": CloudResourceInfo("azure", "Cosmos DB", "database"),
    "CosmosContainer": CloudResourceInfo("azure", "Cosmos DB", "database"),
    "BlobServiceClient": CloudResourceInfo("azure", "Blob Storage", "storage"),
    "BlobContainerClient": CloudResourceInfo("azure", "Blob Storage", "storage"),
    "BlobClient": CloudResourceInfo("azure", "Blob Storage", "storage"),
    "ServiceBusClient": CloudResourceInfo("azure", "Service Bus", "queue"),
    "ServiceBusSender": CloudResourceInfo("azure", "Service Bus", "queue"),
    "ServiceBusReceiver": CloudResourceInfo("azure", "Service Bus", "queue"),
    "ServiceBusProcessor": CloudResourceInfo("azure", "Service Bus", "queue"),
    "QueueClient": CloudResourceInfo("azure", "Storage Queue", "queue"),
    "QueueServiceClient": CloudResourceInfo("azure", "Storage Queue", "queue"),
    "ConnectionMultiplexer": CloudResourceInfo("azure", "Redis", "cache"),
    "RedisCache": CloudResourceInfo("azure", "Redis", "cache"),
    "SecretClient": CloudResourceInfo("azure", "Key Vault", "secret"),
    "KeyClient": CloudResourceInfo("azure", "Key Vault", "secret"),
    "CertificateClient": CloudResourceInfo("azure", "Key Vault", "secret"),
    "TableServiceClient": CloudResourceInfo("azure", "Table Storage", "database"),
    "TableClient": CloudResourceInfo("azure", "Table Storage", "database"),
    "EventGridPublisherClient": CloudResourceInfo("azure", "Event Grid", "topic"),
    "EventHubProducerClient": CloudResourceInfo("azure", "Event Hub", "stream"),
    "EventHubConsumerClient": CloudResourceInfo("azure", "Event Hub", "stream"),
    "EventProcessorClient": CloudResourceInfo("azure", "Event Hub", "stream"),
    # --- Azure DI registrations (C# extension methods) ---
    "AddDbContext": CloudResourceInfo("azure", "Azure SQL", "database"),
    "AddCosmosClient": CloudResourceInfo("azure", "Cosmos DB", "database"),
    "AddStackExchangeRedisCache": CloudResourceInfo("azure", "Redis", "cache"),
    "AddAzureClients": CloudResourceInfo("azure", "Azure SDK", "other"),
    # --- AWS SDK ---
    "AmazonS3Client": CloudResourceInfo("aws", "S3", "storage"),
    "AmazonDynamoDBClient": CloudResourceInfo("aws", "DynamoDB", "database"),
    "AmazonSQSClient": CloudResourceInfo("aws", "SQS", "queue"),
    "AmazonSNSClient": CloudResourceInfo("aws", "SNS", "topic"),
    "AmazonKinesisClient": CloudResourceInfo("aws", "Kinesis", "stream"),
    "AmazonElastiCacheClient": CloudResourceInfo("aws", "ElastiCache", "cache"),
    "AWSSecretsManagerClient": CloudResourceInfo("aws", "Secrets Manager", "secret"),
    # AWS SDK v2 (Java)
    "S3Client": CloudResourceInfo("aws", "S3", "storage"),
    "DynamoDbClient": CloudResourceInfo("aws", "DynamoDB", "database"),
    "SqsClient": CloudResourceInfo("aws", "SQS", "queue"),
    "SnsClient": CloudResourceInfo("aws", "SNS", "topic"),
    "KinesisClient": CloudResourceInfo("aws", "Kinesis", "stream"),
    "SecretsManagerClient": CloudResourceInfo("aws", "Secrets Manager", "secret"),
    # AWS SDK (TS/JS)
    "DynamoDBClient": CloudResourceInfo("aws", "DynamoDB", "database"),
    "SQSClient": CloudResourceInfo("aws", "SQS", "queue"),
    "SNSClient": CloudResourceInfo("aws", "SNS", "topic"),
    # --- GCP SDK ---
    "PublisherClient": CloudResourceInfo("gcp", "Pub/Sub", "topic"),
    "SubscriberClient": CloudResourceInfo("gcp", "Pub/Sub", "topic"),
    "SecretManagerServiceClient": CloudResourceInfo("gcp", "Secret Manager", "secret"),
    # --- Generic (provider-agnostic) ---
    "SqlConnection": CloudResourceInfo("generic", "SQL Database", "database"),
    "SqlClient": CloudResourceInfo("generic", "SQL Database", "database"),
    "NpgsqlConnection": CloudResourceInfo("generic", "PostgreSQL", "database"),
    "MySqlConnection": CloudResourceInfo("generic", "MySQL", "database"),
    "MongoClient": CloudResourceInfo("generic", "MongoDB", "database"),
    "RedisClient": CloudResourceInfo("generic", "Redis", "cache"),
    "Jedis": CloudResourceInfo("generic", "Redis", "cache"),
    "JedisPool": CloudResourceInfo("generic", "Redis", "cache"),
    "Sequelize": CloudResourceInfo("generic", "SQL Database", "database"),
    "PrismaClient": CloudResourceInfo("generic", "SQL Database", "database"),
    "DbContext": CloudResourceInfo("generic", "SQL Database", "database"),
}

# boto3.client('service_name') / boto3.resource('service_name') → CloudResourceInfo
_BOTO3_SERVICE_MAP: dict[str, CloudResourceInfo] = {
    "s3": CloudResourceInfo("aws", "S3", "storage"),
    "dynamodb": CloudResourceInfo("aws", "DynamoDB", "database"),
    "sqs": CloudResourceInfo("aws", "SQS", "queue"),
    "sns": CloudResourceInfo("aws", "SNS", "topic"),
    "kinesis": CloudResourceInfo("aws", "Kinesis", "stream"),
    "firehose": CloudResourceInfo("aws", "Kinesis Firehose", "stream"),
    "secretsmanager": CloudResourceInfo("aws", "Secrets Manager", "secret"),
    "ssm": CloudResourceInfo("aws", "SSM Parameter Store", "secret"),
    "elasticache": CloudResourceInfo("aws", "ElastiCache", "cache"),
    "rds": CloudResourceInfo("aws", "RDS", "database"),
    "redshift": CloudResourceInfo("aws", "Redshift", "database"),
    "kms": CloudResourceInfo("aws", "KMS", "secret"),
}

# Python generic client constructors (module.Class or function call)
_PYTHON_GENERIC_MAP: dict[str, CloudResourceInfo] = {
    "psycopg2.connect": CloudResourceInfo("generic", "PostgreSQL", "database"),
    "psycopg.connect": CloudResourceInfo("generic", "PostgreSQL", "database"),
    "pymongo.MongoClient": CloudResourceInfo("generic", "MongoDB", "database"),
    "redis.Redis": CloudResourceInfo("generic", "Redis", "cache"),
    "redis.StrictRedis": CloudResourceInfo("generic", "Redis", "cache"),
    "redis.asyncio.Redis": CloudResourceInfo("generic", "Redis", "cache"),
    "aioredis.create_redis": CloudResourceInfo("generic", "Redis", "cache"),
    "motor.motor_asyncio.AsyncIOMotorClient": CloudResourceInfo("generic", "MongoDB", "database"),
    "sqlalchemy.create_engine": CloudResourceInfo("generic", "SQL Database", "database"),
    "asyncpg.connect": CloudResourceInfo("generic", "PostgreSQL", "database"),
    "aiomysql.connect": CloudResourceInfo("generic", "MySQL", "database"),
}

# GCP Python SDK patterns (module-qualified)
_GCP_PYTHON_MAP: dict[str, CloudResourceInfo] = {
    "storage.Client": CloudResourceInfo("gcp", "Cloud Storage", "storage"),
    "pubsub_v1.PublisherClient": CloudResourceInfo("gcp", "Pub/Sub", "topic"),
    "pubsub_v1.SubscriberClient": CloudResourceInfo("gcp", "Pub/Sub", "topic"),
    "spanner.Client": CloudResourceInfo("gcp", "Spanner", "database"),
    "bigquery.Client": CloudResourceInfo("gcp", "BigQuery", "database"),
    "firestore.Client": CloudResourceInfo("gcp", "Firestore", "database"),
    "secretmanager.SecretManagerServiceClient": CloudResourceInfo(
        "gcp", "Secret Manager", "secret",
    ),
}


# ---------------------------------------------------------------------------
# region:    --- Public API
# ---------------------------------------------------------------------------


def classify_iac_type(resource_type: str) -> CloudResourceInfo | None:
    """Classify a Terraform resource_type or Bicep/ARM type to a cloud resource.

    For Terraform, matches the exact ``resource_type`` string
    (e.g. ``"aws_sqs_queue"``).  For Bicep/ARM, matches by longest prefix
    (e.g. ``"Microsoft.ServiceBus/namespaces/queues"``).

    Returns ``None`` if the resource type is not a recognized cloud service.
    """
    # Exact match (Terraform)
    info = _IAC_TERRAFORM.get(resource_type)
    if info is not None:
        return info

    # Prefix match (Bicep / ARM)
    best: CloudResourceInfo | None = None
    best_len = 0
    for prefix, info in _IAC_BICEP_PREFIX.items():
        if resource_type.startswith(prefix) and len(prefix) > best_len:
            best = info
            best_len = len(prefix)
    return best


def classify_client(client_name: str) -> CloudResourceInfo | None:
    """Classify a code-level SDK client class/function name to a cloud resource.

    Matches against the ``_CLIENT_MAP`` (class names like ``CosmosClient``,
    ``AmazonS3Client``, ``DbContext``).

    Args:
        client_name: The unqualified class or function name (e.g. ``"CosmosClient"``).

    Returns ``None`` if the client name is not recognized.
    """
    return _CLIENT_MAP.get(client_name)


def classify_boto3_service(service_name: str) -> CloudResourceInfo | None:
    """Classify a boto3 service name (e.g. ``"dynamodb"``, ``"sqs"``).

    Used specifically for Python ``boto3.client('xxx')`` /
    ``boto3.resource('xxx')`` patterns.
    """
    return _BOTO3_SERVICE_MAP.get(service_name)


def classify_python_qualified(qualified_name: str) -> CloudResourceInfo | None:
    """Classify a Python module-qualified client (e.g. ``"redis.Redis"``).

    Matches against ``_PYTHON_GENERIC_MAP`` and ``_GCP_PYTHON_MAP``.
    """
    return _PYTHON_GENERIC_MAP.get(qualified_name) or _GCP_PYTHON_MAP.get(qualified_name)
