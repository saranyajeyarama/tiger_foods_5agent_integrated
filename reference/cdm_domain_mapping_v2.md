# CDM Domain Mapping — v2

The deck commits to 6 Common Data Model (CDM) domains. This document is the canonical mapping from CDM domain → `tiger_semantic` views → consuming agents → tools.

## The 6 CDM domains

| # | Domain | What it contains |
|---|---|---|
| 1 | **Sales & Orders** | Open and historical sales orders, OTIF events, CFR aggregates, chargebacks |
| 2 | **Inventory** | Finished goods on-hand by plant × SKU × date, batch/lot with FEFO, shelf life, safety stock |
| 3 | **Supply & Production** | Confirmed production orders, BOM, plant capacity, scheduled runs, hold reasons |
| 4 | **Procurement** | Inbound raw material orders, vendor performance, ETAs, delays |
| 5 | **Master Data** | dim_customer (with MRSL, OTIF target, fine rate), dim_material, dim_carrier, dim_calendar_day |
| 6 | **Retail Signals** | (v3) Retailer DC inventory + store inventory (initial v3); POS velocity + promo calendar (deferred beyond initial v3) |

## Domain → views mapping

### Sales & Orders
| View | Description | Maturity |
|---|---|---|
| `tiger_semantic.fct_sales_orders` | Open and historical orders | v1 |
| `tiger_semantic.fct_otif` | OTIF event log with fine exposure | v1 |
| `tiger_semantic.fct_delivery` | Actual delivery timing, freight cost, transit days | v1 |
| `tiger_semantic.agg_cfr_weekly` | CFR by customer × SKU × week | v1 |
| `tiger_semantic.agg_otif_customer_quarter` | Quarterly OTIF by customer | v1 |
| `tiger_semantic.fct_forecast_accuracy` | Forecast vs actual by lag | v1 |

### Inventory
| View | Description | Maturity |
|---|---|---|
| `tiger_semantic.fct_inventory_movements` | FG on-hand by plant × SKU × date, batch with earliest expiry | v1 |
| `tiger_semantic.agg_capacity_utilization` | Safety stock days-of-cover vs target | v1 |

### Supply & Production
| View | Description | Maturity |
|---|---|---|
| `tiger_semantic.fct_production_orders` | Confirmed production orders, status, hold reasons | v1 |
| `tiger_semantic.dim_plant_storage_location` | Plant master, DC outbound capacity | v1 |

### Procurement
| View | Description | Maturity |
|---|---|---|
| `tiger_semantic.fct_inventory_movements` (filtered) | Inbound RM with vendor and ETA | v1 (filtered view of inventory) |
| `tiger_semantic.dim_material` | BOM relationships RM → FG | v1 |

### Master Data
| View | Description | Maturity |
|---|---|---|
| `tiger_semantic.dim_customer` | Customer master incl. MRSL, OTIF target, fine rate | v1 |
| `tiger_semantic.dim_material` | Material master incl. shelf life | v1 |
| `tiger_semantic.dim_carrier` | Carrier master incl. performance baselines | v1 |
| `tiger_semantic.dim_calendar_day` | Calendar with promo flags | v1 (promo flags v3 via TPM) |

### Retail Signals (v3 dependency)
| View | Description | Maturity |
|---|---|---|
| `tiger_semantic.fct_retail_dc_inventory` | Retailer DC on-hand, DOS by SKU × retailer × DC × snapshot | **v3** — initial release, not yet loaded |
| `tiger_semantic.fct_retail_store_inventory` | Retailer store on-hand + OOS flag by SKU × retailer × store × snapshot | **v3** — initial release, not yet loaded |
| `tiger_semantic.fct_retail_velocity` | POS depletion by SKU × retailer × week | **deferred** beyond initial v3 |
| `tiger_semantic.dim_promotion` | TPM promo calendar | **deferred** beyond initial v3 |

The two inventory views call `tiger_semantic.resolve_external_to_internal(...)` (the crosswalk TVF) in their view DDL to translate retailer-side keys (retailer_item_number, ean_upc) to Tiger Foods MATNR. Agents never see external keys. Full DDL contract in `retail_data_gap_v2.md`.

## Agent → domain consumption map

| Agent | Sales & Orders | Inventory | Supply & Production | Procurement | Master Data | Retail Signals |
|---|---|---|---|---|---|---|
| Customer Supply Agent | ✓ (orders) |  |  |  | ✓ (customer rules) |  |
| Supply Planning Agent |  | ✓ | ✓ | ✓ | ✓ (material) |  |
| Demand Planning Agent | ✓ (history, accuracy) |  |  |  | ✓ (calendar) | ✓ (v3 velocity) |
| Transportation Agent | ✓ (OTIF, delivery, chargebacks) |  |  |  | ✓ (carrier, customer) |  |
| Retail Intelligence Agent | ✓ (history) | ✓ (shelf life) |  |  | ✓ (customer MRSL) | ✓ (v3 inventory, velocity) |

## Agent → tool → view traceability

This is the auditable trail. Each tool call in a session writes the `view_queried` field to the Firestore run log, so every recommendation traces back to a CDM domain via a named tool and a named view.

### Customer Supply Agent
| Tool | View(s) | CDM domain(s) |
|---|---|---|
| `get_open_sales_orders` | `fct_sales_orders` | Sales & Orders |
| `get_finished_goods_inventory` | `fct_inventory_movements` | Inventory |
| `get_customer_compliance_rules` | `dim_customer` | Master Data |
| `classify_order_vs_forecast` | `fct_forecast_accuracy + fct_sales_orders` | Sales & Orders |
| `get_allocation_history` | `fct_allocation_decisions` | (Decision Capture, not CDM) |

### Supply Planning Agent
| Tool | View(s) | CDM domain(s) |
|---|---|---|
| `get_production_orders` | `fct_production_orders` | Supply & Production |
| `get_finished_goods_inventory` | `fct_inventory_movements` | Inventory |
| `get_raw_materials_status` | `dim_material + fct_inventory_movements` | Master Data, Inventory |
| `get_procurement_orders` | `fct_inventory_movements` (procurement filter) | Procurement |
| `get_safety_stock_position` | `agg_capacity_utilization` | Inventory |
| `get_shelf_life_risk` | `fct_inventory_movements + dim_customer` | Inventory, Master Data |

### Demand Planning Agent
| Tool | View(s) | CDM domain(s) |
|---|---|---|
| `get_order_history` | `fct_sales_orders` | Sales & Orders |
| `classify_order_vs_forecast` | `fct_forecast_accuracy + fct_sales_orders` | Sales & Orders |
| `get_retail_velocity` (v3-deferred) | `fct_retail_velocity` | Retail Signals |
| `get_promotional_calendar` (v3-deferred) | `dim_promotion` | Retail Signals |
| `get_forecast_accuracy` | `fct_forecast_accuracy` | Sales & Orders |

### Transportation Agent
| Tool | View(s) | CDM domain(s) |
|---|---|---|
| `get_otif_performance` | `fct_otif` | Sales & Orders |
| `get_lane_capacity` | `fct_delivery` | Sales & Orders |
| `get_carrier_otp` | `fct_delivery` | Sales & Orders |
| `get_chargeback_risk` | `fct_otif + dim_customer` | Sales & Orders, Master Data |
| `get_transfer_cost_comparison` | `fct_delivery + dim_carrier` | Sales & Orders, Master Data |
| `get_active_alerts` | `fct_otif + fct_delivery + fct_inventory_movements` | Multiple |

### Retail Intelligence Agent
| Tool | View(s) | CDM domain(s) |
|---|---|---|
| `get_retail_dc_inventory` (v3) | `fct_retail_dc_inventory` | Retail Signals |
| `get_retail_store_inventory` (v3) | `fct_retail_store_inventory` | Retail Signals |
| `get_retail_velocity` (v3-deferred) | `fct_retail_velocity` | Retail Signals |
| `get_shelf_life_risk` | `fct_inventory_movements + dim_customer` | Inventory, Master Data |
| `get_customer_compliance_rules` | `dim_customer` | Master Data |
| `get_order_history` | `fct_sales_orders` | Sales & Orders |

## What the DCE captures

When a decision is approved or rejected, `cdm_domains_referenced` is written as a BigQuery `ARRAY<STRING>` containing the deduplicated set of CDM domains that any agent in the session read from. Example: `['Sales & Orders', 'Inventory', 'Master Data']`.

This array supports queries like "which decisions touched the Procurement domain?" and is the seeding metadata for the Phase 2 closed-loop training corpus.

## What's missing in v2 (Retail Signals)

The initial v3 release lands `fct_retail_dc_inventory` and `fct_retail_store_inventory` — both views call the `resolve_external_to_internal` crosswalk TVF in their DDL to translate retailer-side keys to MATNR. `fct_retail_velocity` and `dim_promotion` remain deferred beyond the initial v3 release.

The architectural contract — view DDL, column names, refresh cadence, coverage requirements, volume estimates — is in `retail_data_gap_v2.md`. When the inventory views land, the Retail Intelligence Agent automatically begins returning live `dc_inventory_position` and `store_inventory_position` signals without any agent prompt or code change. The `_retail_view_exists()` check in `adk_tools_v2.py` switches each tool from stub to live mode based on view presence.
