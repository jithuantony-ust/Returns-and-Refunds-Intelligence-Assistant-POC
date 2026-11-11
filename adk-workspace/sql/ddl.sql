CREATE SCHEMA IF NOT EXISTS `retail_returns_poc`;

CREATE OR REPLACE TABLE `retail_returns_poc.customers` (
  customer_id STRING,
  region STRING,
  loyalty_tier STRING,
  created_at DATE
);

CREATE OR REPLACE TABLE `retail_returns_poc.orders` (
  order_id STRING,
  customer_id STRING,
  order_date DATE,
  delivered_at DATE,
  payment_status STRING,
  order_total FLOAT64
);

CREATE OR REPLACE TABLE `retail_returns_poc.order_items` (
  order_item_id STRING,
  order_id STRING,
  sku STRING,
  item_category STRING,
  item_price FLOAT64,
  qty INT64,
  final_sale STRING
);

CREATE OR REPLACE TABLE `retail_returns_poc.returns` (
  return_id STRING,
  order_item_id STRING,
  request_date DATE,
  reason_code STRING,
  status STRING,
  requested_refund FLOAT64,
  evidence_attached STRING
);

CREATE OR REPLACE TABLE `retail_returns_poc.refunds` (
  refund_id STRING,
  return_id STRING,
  approved_amount FLOAT64,
  disbursed_amount FLOAT64,
  refund_status STRING,
  updated_at DATE
);

CREATE OR REPLACE TABLE `retail_returns_poc.support_cases` (
  case_id STRING,
  return_id STRING,
  narrative STRING,
  resolution STRING,
  case_status STRING,
  created_at DATE
);
