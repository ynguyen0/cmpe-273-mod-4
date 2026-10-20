# Module 4 Assignment: Architecture & Service Boundaries

In this assignment, two independent Python HTTP processes show an Order Service querying an Inventory Service. The application uses  the Python standard library, with in-memory data and JSON over HTTP. Notification is a proposed service boundary. This submission logs an `OrderCreated` event but doesn'y implement a notification worker or message broker.

## Architecture

```mermaid
flowchart LR
    C[Client / curl] -->|Command: POST /orders| O[Order Service :8000]
    O -->|Query: GET /availability| I[Inventory Service :8001]
    I -->|JSON: available true or false| O
    O -->|201 created / 409 unavailable / 503 dependency failure| C
    C -->|Query: GET /orders/id| O
    O --- OD[(Private in-memory orders)]
    I --- ID[(Private sample inventory)]
    O -->|OrderCreated JSON event| L[stdout log: implemented]
    O -.->|Future: publish OrderCreated asynchronously| B[Proposed event broker]
    B -.->|Future: consume event| N[Proposed Notification Service]
    N -.- ND[(Proposed delivery records)]
```

Solid arrows indicate implemented communication. Dotted connections indicate the proposed notification extension. Order and Inventory run in separate OS processes, communicate only through HTTP, and don't share mutable data.

### Responsibilities and data ownership

| Component | Responsibility | Owned data |
| --- | --- | --- |
| Order Service | Validate order submissions, check availability through Inventory, create orders, and expose order lookup. Log an event after creation. | Order IDs, requested item IDs and quantities, order status, and creation timestamps in its private `ORDERS` dictionary. |
| Inventory Service | Answer whether the requested quantity of an item is available. | Item IDs and stock quantities in its private `STOCK` dictionary: `book: 5`, `pen: 0`, `notebook: 10`. Unknown items are unavailable. |
| Notification Service (proposed) | Consume `OrderCreated` events and notify users without delaying order creation. | Notification templates, recipient delivery details, delivery attempts, and delivery status. These are design responsibilities, not implemented data stores. |

These boundaries follow business responsibilities: Order manages the order lifecycle, Inventory is the authority on stock, and Notification manages message delivery. Order stores the item ID as a reference but never reads Inventory's dictionary or owns stock counts. Inventory can change its internal storage without changing Order as long as its API contract remains stable. An asynchronous notification consumer would allow delivery retries independently of order creation; delivery failures would not invalidate an accepted order. The shared `common.py` contains HTTP helpers only and is loaded separately by each process.

### Commands, queries, and events

| Interaction | Classification | Why |
| --- | --- | --- |
| Check whether an item is available | **Query** | Requests information without changing stock: `GET /availability?item_id=book&quantity=2`. |
| Create an order | **Command** | Asks Order to change state: `POST /orders`. It can succeed or be rejected. |
| Order was created | **Event** | Reports a completed fact: `OrderCreated`. It is logged after the order is stored; a future Notification Service could react to it. |

Order lookup is also a query. Inventory's boolean response is the answer to a query, not an event. The synchronous availability check is necessary for this demo's immediate accept/reject decision; notification can happen later.

## Install and run

Requirements: **Python 3.9+** and **curl** (for examples and the integration test). There are **no third-party Python dependencies**, so no `pip install` step is needed. Run commands from the repository root.

Check tools:

```sh
python3 --version
curl --version
```

Start Inventory in terminal 1:

```sh
python3 services/inventory_service.py
```

Start Order in terminal 2:

```sh
python3 services/order_service.py
```

Both servers bind to localhost. Optional configuration: `--port` changes either server's port; `INVENTORY_URL` tells Order where Inventory runs. For example:

```sh
# Terminal 1
python3 services/inventory_service.py --port 9001
# Terminal 2
INVENTORY_URL=http://127.0.0.1:9001 python3 services/order_service.py --port 9000
```

### Requests

From terminal 3, using the default ports:

```sh
# Query Inventory directly.
curl -i 'http://127.0.0.1:8001/availability?item_id=book&quantity=2'

# Create an available order: 201 Created.
curl -i -H 'Content-Type: application/json' \
  -d '{"item_id":"book","quantity":2}' http://127.0.0.1:8000/orders

# Out of stock: 409 Conflict.
curl -i -H 'Content-Type: application/json' \
  -d '{"item_id":"pen","quantity":1}' http://127.0.0.1:8000/orders

# Track an order: replace ORDER_ID with the ID returned by creation.
curl -i http://127.0.0.1:8000/orders/ORDER_ID
```

`item_id` must be a nonempty string and `quantity` a positive JSON integer. Invalid input returns `400`; an unknown order or route returns `404`. Insufficient stock or an unknown item returns `409`. Failed requests do not create orders or log `OrderCreated` events.

## Observed results and failure proof

The following excerpts come from the actual integration run on **October 19, 2026 (America/Los_Angeles)**. HTTP timestamps in the complete transcript use UTC. Tests choose available ports to avoid interfering with manually running services, which is why the captured ports differ from the defaults above. Full unabridged curl responses, server logs, and event output are saved in [evidence/integration.txt](evidence/integration.txt).

### Successful request

```text
Independent processes: Order PID 90851, Inventory PID 90850

$ curl --silent --show-error --max-time 5 -i -H 'Content-Type: application/json' -d '{"item_id": "book", "quantity": 2}' http://127.0.0.1:57281/orders
HTTP/1.0 201 Created
Content-Type: application/json

{"order_id": "494ae298-8050-4e85-b157-ce5452dde249", "item_id": "book", "quantity": 2, "status": "created", "created_at": "2026-10-20T05:04:42.415157+00:00"}
```

An unavailable item produced:

```text
HTTP/1.0 409 Conflict
{"error": "item_unavailable", "item_id": "pen", "quantity": 1}
```

### Inventory stopped; Order remains running

To reproduce manually, press **Ctrl+C in terminal 1 only**, then submit another order and check Order's health:

```sh
curl -i -H 'Content-Type: application/json' \
  -d '{"item_id":"book","quantity":1}' http://127.0.0.1:8000/orders
curl -i http://127.0.0.1:8000/health
```

The automated test actually terminates the Inventory process and waits for it to exit before submitting the next order. Observed output (nonessential headers omitted):

```text
$ kill -TERM 90850  # Stop only Inventory

$ curl --silent --show-error --max-time 5 -i -H 'Content-Type: application/json' -d '{"item_id": "book", "quantity": 1}' http://127.0.0.1:57281/orders
HTTP/1.0 503 Service Unavailable
Content-Type: application/json

{"error": "inventory_unavailable", "message": "Cannot check inventory. Try again later; no order was created."}

$ curl --silent --show-error --max-time 5 -i http://127.0.0.1:57281/health
HTTP/1.0 200 OK
Content-Type: application/json

{"status": "ok", "service": "order"}
```

Order catches connection failures and timeouts from the Inventory request and returns `503` instead of terminating. The Inventory call uses a two-second socket timeout to limit waiting on an unresponsive dependency. Order's `/health` is a liveness check, so it remains `200` during an Inventory outage; it does not claim dependency readiness. The test also retrieved the previously created order during the outage, restarted Inventory, and successfully created another order while the original Order process remained running.

### Run the verification yourself

```sh
python3 -m unittest discover -s tests -v
```

This launches real processes, exercises HTTP through curl, and checks creation, lookup, out-of-stock items, unknown items, insufficient quantities, invalid quantities, Inventory shutdown, Order liveness, and recovery. It shuts down its own processes afterward and refreshes the evidence transcript. The run used for this submission passed:

```text
test_independent_services_and_dependency_failure (test_services.ServiceTest.test_independent_services_and_dependency_failure) ... ok

Ran 1 test in 0.289s

OK
```

## Scope and limitations

This is an availability-check demonstration, not a complete checkout system. A query does not reserve or decrement inventory, so repeated orders can exceed physical stock; real fulfillment would need an atomic reservation command owned by Inventory and a strategy for coordinating reservation and order state. Orders disappear when Order restarts, and sample stock resets when Inventory restarts. `OrderCreated` is written to stdout only: there is no broker, durable event delivery, recipient information, or actual user notification. A production extension would need durable storage, reliable event publication (for example, an outbox), idempotent consumers, and authentication.

## Reflection

Clear service boundaries give each component a focused responsibility, making the system easier to understand, test, and change. Explicit data ownership establishes which service is authoritative and prevents other services from relying on its internal storage details. Communicating through stable APIs or events lets components evolve independently while making dependency failures visible and manageable. In this application, Inventory owns availability decisions and Order owns order records, so an Inventory outage can be reported without destroying existing orders or crashing Order.
