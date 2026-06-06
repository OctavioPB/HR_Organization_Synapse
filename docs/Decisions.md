# Engineering Decisions — Organizational Synapse & Knowledge Risk

This document records every non-trivial engineering decision made in this project, explains the reasoning behind each choice, and notes the alternatives that were considered and rejected. It is written to support a technical presentation of the system and to serve as a reference for future maintainers.

---

## 1. Problem Framing

### Decision: Graph-Based HR Intelligence, Not Survey or Sentiment Analysis

**What was decided.** The system models the organization as a weighted directed graph where nodes are employees and edges are metadata events (who sent a message to whom, who assigned a ticket to whom, who invited whom to a meeting). No message content is ever ingested.

**Why.** HR tools traditionally rely on surveys or manager intuition, both of which are slow, subjective, and gameable. Collaboration metadata is a continuous, passive signal: it does not require employee participation, it cannot be faked at scale, and it reflects real working relationships rather than self-reported ones.

The graph abstraction is the right data model because organizational risk is fundamentally a structural property. A single employee can be a critical information bridge between two departments without anyone realizing it — including that employee. Graph centrality metrics make this structural role measurable.

**Alternative considered.** Natural language processing on message content (sentiment analysis, topic modeling). Rejected because: it violates employee privacy, requires storing content at rest, creates regulatory liability under GDPR, and does not scale cleanly across languages and communication styles. Metadata is both safer and more objective.

---

## 2. Containerization

### Decision: Docker Compose as the Local Runtime

**What was decided.** All infrastructure services (Kafka, PostgreSQL, Neo4j, Airflow, Redis, Prometheus, Grafana) run inside Docker containers orchestrated by a single `docker-compose.yml`. The application itself (FastAPI API + Vite frontend) runs outside Docker during development to allow hot-reload without rebuilding images.

**Why.** A single `docker compose up -d` command reproduces the full infrastructure stack on any machine that has Docker installed. This eliminates the "works on my machine" problem for the database, message broker, and monitoring stack.

Running the application outside Docker during development means that `uvicorn --reload` and `vite dev` respond instantly to file changes. If the application were containerized for development, every Python change would require a volume mount with complex permission handling, or worse, a full image rebuild.

**Alternative considered.** Running services directly on the host machine (native PostgreSQL, native Redis). Rejected because it requires every developer to install, configure, and version-manage each service independently. This does not scale across a team and makes onboarding expensive.

**Alternative considered.** Kubernetes (k3s or minikube) for local development. Rejected as over-engineered for a single-developer or small-team context. Kubernetes adds meaningful operational overhead (pod specs, services, ingress rules) that provides no benefit until horizontal scaling is required.

### Decision: External Ports Differ from Internal Ports for PostgreSQL and Redis

**What was decided.** PostgreSQL is mapped `5433:5432` (host:container). Redis is mapped `6380:6379`.

**Why.** Developers who already have PostgreSQL or Redis installed natively on their machine occupy port 5432 and 6379. Offsetting the host port avoids a port conflict that would prevent `docker compose up` from starting. The application's `.env` file is configured to connect to the offset ports when running outside Docker.

---

## 3. Message Streaming — Apache Kafka

### Decision: Kafka as the Ingestion Bus

**What was decided.** Apache Kafka (`apache/kafka:3.7.0`) is the message broker for all collaboration metadata events. Producers write events to the `collaboration.events.raw` topic. A consumer reads from that topic and writes edges to PostgreSQL.

**Why.** Kafka provides durable, ordered, replayable event storage. If the downstream consumer (the PostgreSQL writer) is temporarily unavailable, events are not lost — they remain in the Kafka log and are consumed when the consumer recovers. This decoupling means the ingestion producers do not need to know anything about the database schema.

Replayability is particularly important here: if the graph schema changes (e.g., a new edge attribute is added), the raw event log can be replayed from the beginning to reconstruct the entire history rather than backfilling from scratch.

**Alternative considered.** Writing directly from producers to PostgreSQL. Rejected because it creates tight coupling: if the database is overloaded or migrating, events are dropped. It also makes it impossible to add a second consumer (e.g., a real-time anomaly detector) without modifying the producers.

**Alternative considered.** RabbitMQ. Rejected because RabbitMQ is a message queue, not a log. Once a message is consumed, it is gone. The replayability of Kafka is a first-class requirement for this system.

### Decision: KRaft Mode (No Zookeeper)

**What was decided.** Kafka is configured with `KAFKA_PROCESS_ROLES: broker,controller`, which means Kafka manages its own cluster metadata internally using the Raft consensus protocol (KRaft). Zookeeper is not deployed.

**Why.** Zookeeper was Kafka's external dependency for cluster coordination until Kafka 2.8. In KRaft mode (stable from Kafka 3.3), Kafka manages its own metadata. Removing Zookeeper eliminates one container, one JVM process, one port to manage, and one potential failure point. For a single-broker development setup, there is no reason to add Zookeeper.

**Specific configuration.** `KAFKA_NODE_ID: 1`, `KAFKA_CONTROLLER_QUORUM_VOTERS: 1@localhost:9093`, `CLUSTER_ID: MkU3OEVBNTcwNTJENDM2Qk`. The cluster ID is pre-set rather than auto-generated to make the container idempotent — restarting the container does not produce a new cluster ID, which would invalidate existing topic metadata.

### Decision: Six Partitions, Auto Topic Creation

**What was decided.** `KAFKA_NUM_PARTITIONS: 6`, `KAFKA_AUTO_CREATE_TOPICS_ENABLE: true`.

**Why.** Partitions are the unit of parallelism in Kafka. Six partitions means up to six consumers can read in parallel without coordination. For a single-node development setup this has no immediate effect, but it avoids a painful migration if the system scales to multiple consumers later. Auto topic creation is enabled to avoid requiring an explicit topic-creation step on first run.

### Decision: Metadata-Only Event Schema

**What was decided.** The `CollaborationEvent` Pydantic schema captures only: `source_employee_id`, `target_employee_id`, `channel` (slack/email/jira/calendar/github), `direction` (sent/mentioned/invited/assigned/reviewed), `department_source`, `department_target`, `timestamp`, `weight`. No message content, subject lines, or file contents are ever present.

**Why.** This is a hard privacy constraint. Edge metadata (who interacts with whom) is the only signal needed for graph analysis. Storing content would: (1) create GDPR Article 15/17 obligations that metadata does not, (2) vastly increase storage requirements, (3) open the system to misuse as a surveillance tool. By making the schema incapable of holding content, the constraint is enforced at the data model level, not just by policy.

---

## 4. Data Storage

### Decision: PostgreSQL as the Primary Relational Store

**What was decided.** PostgreSQL 15 stores all processed data: employees, raw_events, graph_snapshots, risk_scores, alerts, churn_risk_scores, temporal_anomaly_scores, employee_knowledge, knowledge_risk_scores, succession_recommendations, and org_health_scores.

**Why.** PostgreSQL is the industry-standard open-source relational database. It supports JSONB (for flexible fields like `affected_entities` and `component_scores`), native UUID types, TIMESTAMPTZ (timezone-aware timestamps), and ON CONFLICT upsert semantics — all of which are used in this schema. PostgreSQL's `pg_isready` utility makes health checking in Docker straightforward.

**Why not MySQL.** PostgreSQL's JSONB support is more mature and performs better for the semi-structured fields in this schema. PostgreSQL also has better support for complex analytical queries (window functions, lateral joins) that will be needed as the analytics layer matures.

**Why not a time-series database (TimescaleDB, InfluxDB).** The graph_snapshots and risk_scores tables do have a time dimension, but the query patterns are mixed: point lookups by employee, range scans by date, and joins between tables. A pure time-series database is optimized only for range scans and does not handle relational queries cleanly. PostgreSQL handles both.

### Decision: Schema-Per-Tenant Multi-Tenancy

**What was decided.** Each tenant gets an isolated PostgreSQL schema (`tenant_{slug}`). The public schema holds only the tenant registry, API keys, and billing data. All application tables (employees, raw_events, risk_scores, etc.) exist inside the tenant schema. The API sets `search_path` to the tenant schema on every connection.

**Why.** Schema isolation is the most robust multi-tenancy model for analytics workloads. It prevents cross-tenant data leakage without application-level `WHERE tenant_id = ?` clauses on every query. It allows per-tenant backup and restore. It allows different tenants to have different schema versions during migrations. The cost is that provisioning a new tenant requires executing DDL; this is acceptable because tenant onboarding is an infrequent, admin-triggered operation.

**Alternative considered.** Row-level security (RLS) with a shared table and a `tenant_id` column. Rejected because RLS policies are invisible to application code and can be accidentally bypassed by superuser connections. They also complicate index design and query planning. For an analytics system handling organizational data, the blast radius of a RLS misconfiguration is unacceptable.

**Alternative considered.** Separate database per tenant. Rejected because it requires a separate connection pool per tenant and makes cross-tenant reporting (future feature) impossible without federation.

### Decision: API Key Stored as SHA-256 Hash

**What was decided.** When a tenant API key is provisioned, the raw key is shown once and not stored. Only the SHA-256 hash of the key is stored in `tenant_api_keys`. On every API call, the presented key is hashed and compared to the stored hash.

**Why.** If the database is compromised, an attacker who reads the `tenant_api_keys` table cannot use the stored values to authenticate. This is the same pattern used for passwords (bcrypt/argon2) but with SHA-256 because API keys are already high-entropy random strings — they do not need the CPU-expensive stretching that password hashing requires.

**Why SHA-256 and not bcrypt.** API keys are 32+ bytes of cryptographically random data. bcrypt is designed to slow down brute-force attacks against low-entropy human passwords. For a 256-bit random key, brute force is computationally infeasible regardless of hash speed. Using bcrypt for API keys adds 100ms+ of CPU per authentication request for no security benefit.

### Decision: Neo4j as a Secondary Graph Store

**What was decided.** Neo4j 5 with the Graph Data Science (GDS) plugin is included. Certain queries (shortest path between two employees, N-hop reachability) are routed to Neo4j first, with a NetworkX fallback if Neo4j is unavailable.

**Why.** NetworkX holds the entire graph in memory and is excellent for metric computation (betweenness centrality, community detection). However, traversal queries like "find the shortest path between Alice and Bob" are naturally expressed in Cypher (Neo4j's query language) and executed efficiently by Neo4j's native graph engine. Neo4j's GDS plugin provides optimized implementations of Dijkstra and BFS that outperform NetworkX for large graphs.

**Why optional fallback.** Neo4j adds operational complexity (a JVM process, 7474/7687 ports, 1–2 GB heap). For development or small organizations, running without Neo4j is acceptable. The graceful fallback to NetworkX means the system degrades gracefully rather than failing.

### Decision: Redis as the API Cache

**What was decided.** Redis 7 (Alpine image, minimal footprint) caches API responses for graph snapshots. The `allkeys-lru` eviction policy with a 256 MB cap ensures Redis never runs out of memory by automatically evicting the least-recently-used keys.

**Why Redis.** Graph metric computation is expensive: betweenness centrality for 200 employees takes 3–5 seconds. HR dashboards are read-heavy: dozens of users may request the same snapshot simultaneously. Caching the computed result in Redis reduces the per-request latency from seconds to milliseconds for cache hits.

**Why `allkeys-lru` and not `volatile-lru`.** `volatile-lru` only evicts keys that have an explicit TTL set. `allkeys-lru` evicts any key. Since all cache keys in this system have a TTL, both policies behave the same in practice — but `allkeys-lru` is a safer default because it prevents Redis from filling up if a key is set without a TTL by accident.

**Why `appendonly yes`.** This enables Redis AOF (Append-Only File) persistence. Without persistence, a Redis restart (or Docker container restart) empties the cache and causes a cold-start latency spike as all requests miss the cache. AOF persistence survives container restarts at the cost of slightly higher disk I/O.

**Cache key format: `org-synapse:v1:{parts}`.** The `v1` prefix is a schema version. If the cached data format changes (e.g., a field is added to graph snapshots), bumping the prefix to `v2` instantly invalidates all old cache entries without needing to explicitly delete them. The `org-synapse:` namespace prefix prevents collisions if Redis is shared with another application.

---

## 5. ETL & Orchestration — Apache Airflow

### Decision: Airflow as the DAG Orchestrator

**What was decided.** Apache Airflow 2.9.0 orchestrates all batch pipelines as DAGs (Directed Acyclic Graphs of tasks): daily graph building, daily churn scoring, weekly succession planning, weekly org health scoring, etc.

**Why.** Airflow provides: (1) a visual UI showing DAG run history, task status, and logs — critical for debugging why the system produced a given risk score on a given day; (2) retry logic with exponential backoff built into the task framework; (3) ExternalTaskSensor to declare cross-DAG dependencies (e.g., churn scoring must not run until graph building completes); (4) configurable scheduling with cron expressions.

**Alternative considered.** Cron jobs (native OS scheduler). Rejected because cron provides no visibility into job history, no retry logic, no dependency management between jobs, and no alerting on failures. If the graph builder DAG fails at 02:00, the churn scorer should not run at 01:30 the next day with stale data. Cron cannot express this dependency.

**Alternative considered.** Celery Beat. Rejected because Celery Beat is a periodic task scheduler, not a DAG orchestrator. It does not support task dependencies, does not have a built-in web UI for pipeline monitoring, and does not have a concept of backfill (re-running historical pipeline runs).

### Decision: LocalExecutor (Not CeleryExecutor or KubernetesExecutor)

**What was decided.** `AIRFLOW__CORE__EXECUTOR: LocalExecutor`. Tasks run as subprocesses on the same machine as the Airflow scheduler.

**Why.** LocalExecutor is correct for a single-machine deployment. CeleryExecutor requires a Celery broker (another Redis or RabbitMQ instance) and multiple worker processes. KubernetesExecutor requires a Kubernetes cluster. Both add significant operational complexity for no benefit until the workload requires distributed computation, which the current pipeline does not.

**When this decision would need revisiting.** If the graph builder DAG must process multiple tenants simultaneously, or if individual DAG tasks take longer than the scheduling interval, LocalExecutor becomes a bottleneck and CeleryExecutor would be the appropriate upgrade.

### Decision: Airflow Installed Only in Docker (Not in requirements.txt)

**What was decided.** Airflow is not listed in `requirements.txt`. It is only installed inside the Airflow Docker containers.

**Why.** Airflow has a notoriously large dependency tree that conflicts with many other Python packages. If Airflow were in `requirements.txt`, installing the project's Python dependencies locally (for the API or for running tests) would pull in hundreds of Airflow-specific packages, slow down `pip install`, and risk dependency conflicts. By isolating Airflow to its container, the local development environment stays lean.

### Decision: DAG Scheduling Times and Cross-DAG Sensors

**What was decided.**
- `graph_builder_dag`: daily at 02:00 UTC
- `churn_gnn_score`: daily at 01:30 UTC, waits for `graph_builder_dag` via `ExternalTaskSensor`
- `succession_dag`: weekly (Sunday) at 04:00 UTC
- `org_health_dag`: weekly schedule, depends on graph metrics

**Why 02:00 UTC.** The graph builder runs during the lowest-traffic window (early morning UTC, which is late night in the Americas and early morning in Europe). This minimizes the impact of the heavy betweenness centrality computation on API response times.

**Why ExternalTaskSensor.** The churn scorer needs the current day's graph metrics as features. If the graph builder fails, the churn scorer must not run with yesterday's metrics and pretend they are today's. ExternalTaskSensor enforces this dependency declaratively in the DAG definition rather than relying on timing assumptions.

---

## 6. Graph Analytics — NetworkX

### Decision: NetworkX as the In-Process Graph Engine

**What was decided.** `networkx>=3.0` is the primary library for graph construction and metric computation. The graph is loaded into memory as a `nx.DiGraph` (directed graph) and converted to undirected (`nx.Graph`) for metrics that require undirected topology.

**Why.** NetworkX is the standard Python graph library with implementations of every algorithm needed: betweenness centrality (Brandes algorithm), clustering coefficient, degree centrality, shortest paths, connected components, and community detection hooks. It integrates natively with Python (no C extension compilation required beyond NumPy) and with Pandas (graph metrics as dictionaries map directly to DataFrame columns).

**Alternative considered.** igraph (C-backed, faster for large graphs). Not used as primary engine because igraph has a different API that would require wrapping, and NetworkX's performance is sufficient for org graphs up to ~5000 nodes. igraph is available as a fallback if performance becomes a constraint.

**Alternative considered.** Querying Neo4j for all metrics. Rejected because Neo4j's GDS plugin has per-algorithm licensing requirements and running heavy computations (betweenness centrality) in Neo4j via Cypher returns results that must then be processed in Python anyway. NetworkX keeps the computation in the same process that writes to PostgreSQL, avoiding serialization overhead.

### Decision: Directed Graph with Undirected Conversion

**What was decided.** Edges are stored as directed (`source → target`). For metrics that are defined on undirected graphs (clustering coefficient, betweenness centrality as a bridge metric), the graph is converted with `G.to_undirected()`.

**Why directed.** Direction matters for organizational analysis: an employee who consistently sends but never receives is exhibiting a different pattern from one who receives but never initiates. Degree centrality is split into `degree_in` and `degree_out` to capture both.

**Why convert to undirected for centrality.** Betweenness centrality in a directed graph measures how often a node lies on the shortest directed path from A to B. But organizational information flows do not require a directed path: if Alice communicates with Bob and Bob communicates with Carol, knowledge can still flow Alice → Carol through Bob, regardless of whether Alice has ever initiated contact with Carol. The undirected interpretation is more relevant for SPOF analysis.

### Decision: Exact vs. Approximate Betweenness Centrality

**What was decided.** For graphs with fewer than `BETWEENNESS_EXACT_THRESHOLD` nodes (default 500), the exact Brandes algorithm is used: `O(n × m)` time where n is nodes and m is edges. For graphs above the threshold, the k-pivot approximation is used with `BETWEENNESS_K_PIVOTS = 200`: `O(k × m)` time with bounded error.

**Why the threshold matters.** Exact betweenness centrality for a 1000-node, 10000-edge graph takes approximately 10 million operations. At 500 nodes (typical HR org), runtime is under 5 seconds and exact values are acceptable. Above 500 nodes, the k-pivot approximation with k=200 pivots produces error bounded by approximately O(1/√k) ≈ 7%, which is acceptable for risk scoring where the output is relative ranking, not an absolute value.

**Why these specific defaults.** 500 nodes covers the vast majority of small-to-midsize organizations (50–500 employees). k=200 pivots is the empirically established value that gives error < 1% for typical organizational graph structures.

### Decision: Edge Weight Inversion for Shortest-Path Metrics

**What was decided.** When computing betweenness centrality with weights (`weight='weight_inv'`), edge weights are inverted: `weight_inv = 1 / frequency_weight`.

**Why.** In a weighted shortest-path algorithm, lower-weight edges are preferred. A high-frequency collaboration edge (weight = 50 interactions/month) should be treated as a strong connection — a short path. Without inversion, the algorithm treats high-frequency edges as long paths, which inverts the organizational meaning: rare communication would appear to be the easiest route, which is the opposite of reality.

### Decision: Louvain Community Detection (python-louvain)

**What was decided.** `python-louvain >= 0.16` (imported as the `community` module) implements the Louvain modularity optimization algorithm for community detection in the collaboration graph.

**Why Louvain.** Louvain is the most widely used community detection algorithm in social network analysis. It optimizes modularity (a measure of how much more densely connected community members are to each other than to random chance), runs in O(n log n) time, and consistently produces meaningful communities in organizational graphs.

**Alternative considered.** Girvan-Newman algorithm (edge betweenness-based removal). Rejected because it runs in O(m² × n) time, making it infeasible for graphs with more than ~200 nodes, and it requires specifying the number of communities in advance.

**Alternative considered.** Label propagation. Not used because it is non-deterministic and produces different results on repeated runs, making it unsuitable for a system where users compare today's output with yesterday's.

### Decision: Silo Detection on Departments, Not Louvain Communities

**What was decided.** The silo detection algorithm groups employees by their `department` attribute rather than by their Louvain community membership. It then computes `isolation_ratio = internal_edges / max(external_edges, 1)` for each department.

**Why departments and not Louvain.** Louvain detects organic communities in the graph — clusters of employees who talk more to each other. But if a team is already siloed, their Louvain community will exactly match their department, making the silo invisible (you would be comparing a team to itself). More importantly, HR decision-makers think in terms of formal departments, not organic clusters. A silo alert that says "Engineering is siloed from Sales" is immediately actionable. An alert that says "community_4 has an isolation ratio of 3.2" requires interpretation.

**Alternative considered.** Detecting silos on Louvain communities and then mapping back to departments. Rejected because the mapping is ambiguous when a department spans multiple communities or when a community spans multiple departments.

### Decision: SPOF Risk Score Formula

**What was decided.**
```
SPOF_score = α × normalized_betweenness
           + β × cross_dept_ratio
           + γ × (1 − clustering)
           − δ × entropy_trend
```
with default weights α=0.4, β=0.3, γ=0.2, δ=0.1 (sum = 1.0).

**Justification of each term.**

`α × normalized_betweenness (0.4)`: Betweenness centrality is the primary indicator of a bridge node. An employee with high betweenness sits on the majority of shortest paths in the organization — they are the most critical information broker. This term has the highest weight because it is the most direct measure of structural dependency.

`β × cross_dept_ratio (0.3)`: An employee who bridges two departments is more critical than one who is central within a single department. If a within-department hub leaves, the department continues to function. If the only cross-department connector leaves, two departments lose their primary communication channel.

`γ × (1 − clustering) (0.2)`: Clustering coefficient measures how interconnected a node's neighbors are. Low clustering means the node's neighbors do not talk to each other directly — they all route through this node. High clustering means neighbors are already connected to each other and can compensate if the node leaves. The `(1 − clustering)` term therefore increases the SPOF score when neighbors are not well-connected.

`−δ × entropy_trend (0.1)`: Entropy trend is the slope of the node's interaction diversity over time. A negative slope (declining entropy) means the employee is progressively interacting with fewer different people — a withdrawal signal. A withdrawing employee's existing connections are degrading, increasing future organizational risk. The minus sign converts a negative slope into a positive risk contribution.

**Why the weights are configurable.** Different organizations have different risk profiles. A startup values cross-department connectors highly. A consulting firm values experts with many client connections. The weights are environment variables (`SPOF_ALPHA`, `SPOF_BETA`, `SPOF_GAMMA`, `SPOF_DELTA`) so the formula can be tuned without code changes.

---

## 7. Machine Learning Layer

### Decision: Isolation Forest for Connectivity Anomaly Detection

**What was decided.** `scikit-learn`'s `IsolationForest` detects abnormal changes in an employee's collaboration patterns (sudden drop in degree, unusual change in betweenness).

**Why Isolation Forest.** Isolation Forest is an unsupervised anomaly detection algorithm that works without labeled examples of what an "anomaly" looks like. This is essential because there is no ground truth dataset of "these employees left and these did not" for this specific organization. Isolation Forest isolates anomalies by randomly partitioning the feature space — anomalous points require fewer splits to isolate because they differ significantly from the majority.

**Alternative considered.** DBSCAN for clustering and treating outlier points as anomalies. Rejected because DBSCAN requires tuning the epsilon and min_samples parameters, which are sensitive to the dimensionality and density of the feature space and require manual calibration per organization.

### Decision: Graph Attention Network (GAT) for Churn Risk

**What was decided.** A Graph Attention Network implemented with `torch-geometric` and `torch-geometric-temporal` predicts churn probability for each employee. The model trains weekly (Sunday 02:00 UTC) and scores daily.

**Why a GNN and not a tabular model.** A traditional ML model (logistic regression, random forest) would treat each employee's features independently. But whether an employee is at churn risk depends not only on their own metrics but also on the state of their neighborhood: if their key collaborators have already left, their isolation is a structural consequence, not an independent event. A Graph Attention Network processes the employee in the context of their neighbors, capturing these structural dependencies.

**Why Graph Attention Network specifically.** The attention mechanism in GAT learns which neighbors are most informative for predicting a node's label. In an organizational graph, not all neighbors are equally relevant — a mentor-mentee relationship may be more predictive of churn than a weekly all-hands connection. Attention weights are learned from data rather than set by hand.

**Why `torch-geometric-temporal`.** The churn signal is temporal: an employee who had 50 interactions in January and 30 in February and 15 in March is exhibiting a different risk pattern than one who consistently has 30. `torch-geometric-temporal` provides temporal graph convolution layers that process graph snapshots as a sequence, capturing this trend.

### Decision: Succession Planning via Structural Compatibility Score

**What was decided.** Succession candidates are scored with:
```
compatibility = w_struct × structural_overlap
              + w_clust  × clustering_score
              + w_domain × domain_overlap
```
with default weights 0.40 / 0.25 / 0.35 (sum = 1.0).

**Justification of each term.**

`structural_overlap (Jaccard similarity of neighbor sets)`: Two employees with many shared neighbors already have overlapping communication networks. If one leaves, the other can cover the relationship without a complete cold-start. Jaccard similarity (intersection/union) normalizes for network size.

`clustering_score`: A candidate with high clustering in the SPOF employee's neighborhood is already embedded in that community. They can absorb the departing employee's role more naturally than someone peripheral to the cluster.

`domain_overlap (knowledge domains)`: Structural connectivity is necessary but not sufficient. The successor must also share relevant knowledge domains (from `employee_knowledge`). A well-connected employee in a different knowledge domain cannot realistically succeed a specialized technical SPOF.

**Why these specific weights.** Structural overlap has the highest weight (0.40) because it is the hardest gap to close: knowledge can be transferred, but rebuilding a relationship network takes years. Domain overlap (0.35) is second because domain expertise is more verifiable and measurable than structural readiness. Clustering (0.25) is a supporting signal that amplifies or moderates the structural overlap assessment.

---

## 8. API Layer — FastAPI

### Decision: FastAPI as the HTTP Framework

**What was decided.** `fastapi>=0.110.0` with `uvicorn[standard]>=0.29.0` (ASGI server).

**Why FastAPI.** FastAPI is an ASGI framework built on Starlette and Pydantic. Three properties make it the correct choice here: (1) Automatic OpenAPI documentation generation from route type annotations — all `/docs` and `/redoc` endpoints are generated without any additional code; (2) Pydantic request/response validation is integrated at the framework level — invalid request bodies return structured 422 errors automatically; (3) native async support allows the WebSocket broadcaster and Redis subscriber to run in the same event loop as the HTTP server.

**Alternative considered.** Flask + Marshmallow. Rejected because Flask is synchronous (WSGI), which means WebSocket support requires a separate server (Flask-SocketIO with Eventlet or gevent) and the event loop cannot be shared with async Redis operations.

**Alternative considered.** Django REST Framework. Rejected for the same synchronous reason, plus DRF's ORM (Django ORM) would conflict with the project's decision to use raw psycopg2 for fine-grained control over query execution.

**Why `uvicorn[standard]` and not plain `uvicorn`.** The `[standard]` extra installs `uvloop` (a faster event loop implementation based on libuv) and `httptools` (a faster HTTP parser). These reduce per-request overhead by roughly 30% on Python 3.11 at no additional configuration cost.

### Decision: Raw psycopg2 Instead of SQLAlchemy ORM

**What was decided.** Database queries are written as raw SQL strings using `psycopg2`'s `RealDictCursor` (which returns rows as dictionaries). SQLAlchemy is listed as a dependency but is only used for lightweight utilities, not as the ORM.

**Why raw SQL.** The queries in this system are analytical: multi-table joins, window functions, date truncation, aggregations, and ON CONFLICT upsert. These are difficult to express cleanly through an ORM's query builder and the generated SQL is often less efficient than hand-written SQL. Raw psycopg2 gives precise control over query text, which is important for a system where query performance directly affects user-visible latency.

**Why `RealDictCursor`.** Standard psycopg2 cursors return rows as tuples, requiring positional indexing (`row[0]`, `row[1]`). `RealDictCursor` returns rows as dictionaries (`row['employee_id']`), which is more readable and robust to column reordering during schema changes.

**Why not asyncpg.** asyncpg is a high-performance async PostgreSQL driver. It was not chosen because the majority of route handlers in this system are synchronous (they compute graph metrics synchronously after fetching data). Adding asyncpg would require converting all route handlers to async, which provides no benefit when the bottleneck is CPU-bound graph computation, not I/O.

### Decision: Middleware Order (CORS → TenantMiddleware)

**What was decided.** CORS middleware is registered before TenantMiddleware in `api/main.py`.

**Why.** Starlette/FastAPI middleware is executed in LIFO order (last registered = first executed on request, first executed on response). By registering CORS last in the code (but first in execution), CORS headers are added to the response before TenantMiddleware can modify or reject it. This ensures that preflight OPTIONS requests from browsers receive correct CORS headers even when the tenant credentials are missing — allowing the browser to make the actual request.

### Decision: Prometheus FastAPI Instrumentator

**What was decided.** `prometheus-fastapi-instrumentator>=6.1.0` is conditionally imported (guarded by a try/except). If installed, it exposes `GET /metrics` in Prometheus text format, tracking request count, latency percentiles (p50/p95/p99), and in-flight request count. `/metrics`, `/health`, and `/` are excluded from tracking.

**Why conditional import.** The instrumentator is an optional dependency. In production it is always present. In unit tests or minimal deployments, it can be omitted without breaking the application. The try/except guard makes the application start successfully in either case.

**Why exclude `/health`.** Health check probes from Docker, load balancers, and the `demo.ps1` script poll `/health` frequently. Including it in the metrics would inflate the request count and skew the latency percentiles with hundreds of trivial requests, making the dashboard harder to read.

### Decision: Prometheus Metrics Disabled for 204 Routes (response_model=None)

**What was decided.** DELETE routes that return HTTP 204 are decorated with `response_model=None`.

**Why technically necessary.** FastAPI 0.115+ infers the response model from the Python return type annotation. A `-> None` annotation is inferred as `NoneType`, which is a truthy class object. FastAPI's internal assertion `if self.response_model: assert is_body_allowed_for_status_code(status_code)` then fails for status 204 because HTTP 204 No Content is defined by RFC 9110 as having no response body. Explicitly passing `response_model=None` (not the default sentinel) bypasses the inference and prevents the assertion from firing.

---

## 9. Real-Time Alerts — WebSocket + Redis Pub/Sub

### Decision: Redis Pub/Sub as the WebSocket Fan-Out Bus

**What was decided.** When an alert is triggered (e.g., a silo is detected by the Airflow DAG), it is published to the Redis channel `org.alerts.live` via `POST /internal/alerts/broadcast`. Each Uvicorn worker process runs an `asyncio` background task that subscribes to this channel. When a message arrives, the subscriber calls `ConnectionManager.broadcast()`, which pushes the message to all WebSocket clients connected to that worker.

**Why Redis pub/sub and not a database poll.** A naive implementation would have WebSocket clients polling `GET /alerts/recent` every few seconds. This creates N×(1/interval) queries per second as the number of clients grows. Redis pub/sub is a push model: a single publish operation fans out to all subscribers instantly, with O(1) database queries regardless of the number of connected clients.

**Why a background asyncio task per worker.** Uvicorn can run with multiple worker processes (`--workers N`) for production deployments. Each worker process has its own memory space and its own `ConnectionManager`. Without Redis pub/sub, only the worker that received the `POST /internal/alerts/broadcast` call would push the alert to its clients — the other workers' clients would miss it. With Redis pub/sub, every worker subscribes to the same channel and all clients receive every alert.

**Why graceful fallback to direct broadcast.** If Redis is unavailable (e.g., during development without Docker), publishing an alert would fail silently. The broadcaster falls back to calling `manager.broadcast()` directly in the same process, which works correctly for single-worker setups.

**The INTERNAL_API_KEY guard.** `POST /internal/alerts/broadcast` is called by Airflow DAG tasks from inside the Docker network. The endpoint is protected by `INTERNAL_API_KEY` header validation. In network-isolated deployments this can be set to empty (disabling auth), but in any internet-facing deployment it must be set to prevent external actors from injecting arbitrary alerts into all connected dashboards.

---

## 10. Natural Language Interface

### Decision: Claude (claude-sonnet-4-6) with Tool Use for NL Queries

**What was decided.** `POST /query/natural` accepts a natural language question ("Who are the top 5 knowledge silos in Engineering?") and returns a structured answer. The answer is generated by Claude (`claude-sonnet-4-6`) using the Anthropic tool-use protocol: Claude calls defined tools to query the database and graph, then synthesizes the results into a human-readable answer.

**Why LLM + tool use rather than NL → SQL.** Direct NL-to-SQL approaches require the model to know the exact schema, handle ambiguous column names, and produce syntactically correct SQL. Tool use is more robust: the tools encapsulate the SQL and expose a clean interface (e.g., `get_risk_scores(min_score=0.7, limit=5)`). Claude does not need to know that risk scores are in the `risk_scores` table or that the SPOF column is called `spof_score`. It just calls the tool with the appropriate parameters and the tool handles the database query.

**Why Claude and not an open-source model.** The tool-use protocol requires a model that can reliably follow a structured API: parse tool definitions, produce correctly formatted tool calls, and integrate multiple tool results into a coherent answer. Claude's tool-use implementation is the most reliable available as of the project's development date.

**Why `asyncio.to_thread` for the synchronous Anthropic client.** The Anthropic Python SDK's synchronous client blocks the calling thread. Calling it directly from an async FastAPI route would block the event loop, preventing other requests from being served during the LLM call (which can take 5–15 seconds). `asyncio.to_thread` runs the synchronous call in a thread pool, releasing the event loop to serve other requests while the LLM generates a response.

---

## 11. Frontend

### Decision: React + Vite (Not Next.js)

**What was decided.** The dashboard is a React 18 single-page application built with Vite 5.3.1.

**Why React.** React's component model maps naturally to the dashboard's structure: `<OrgGraph>`, `<CriticalNodePanel>`, `<SiloAlert>`, and `<OrgHealthCard>` are all independent components that re-render when their data changes. React's ecosystem provides `@tanstack/react-query` for server state management and `react-router-dom` for client-side routing between the dashboard, employee detail, and admin pages.

**Why Vite and not Create React App (CRA).** CRA uses Webpack, which has a cold start time of 15–30 seconds for a project of this size. Vite uses native ES modules in the browser during development, resulting in a cold start under 300ms. Hot module replacement (HMR) updates in under 50ms. For a development-heavy workflow, this is a significant productivity difference.

**Why not Next.js.** Next.js is a server-side rendering (SSR) framework. This dashboard is an internal HR analytics tool — it is not indexed by search engines and does not need SSR for initial page load performance. Adding SSR would require a Node.js server in production, a more complex deployment, and careful handling of the fact that graph visualization libraries (Sigma.js) are browser-only and cannot run on the server.

### Decision: Sigma.js + Graphology for Graph Visualization

**What was decided.** `sigma@3.0.0` renders the organizational graph using WebGL. `graphology@0.25.4` is Sigma's companion graph data structure library. `@react-sigma/core@4.0.3` provides React bindings. `graphology-layout-forceatlas2@0.10.1` computes the force-directed layout (which determines node positions).

**Why Sigma.js and not D3.js.** D3.js force simulation renders each node and edge as an SVG element. SVG is a retained-mode DOM API: for 200 nodes with 1000 edges, the browser must maintain 1200 DOM elements and recompute layout on every frame. This becomes slow (~15 fps) for graphs with 100+ nodes. Sigma.js uses WebGL (immediate-mode GPU rendering), which renders the same graph at 60 fps because the GPU handles the drawing — the browser's DOM is not involved in the render loop.

**Why ForceAtlas2 layout.** ForceAtlas2 is a force-directed layout algorithm developed specifically for social network graphs. Unlike the generic `d3.forceSimulation`, ForceAtlas2 includes a "gravity" force that prevents disconnected components from drifting off-screen, a "LinLog" mode that emphasizes community structure, and a "prevent overlap" option that stops nodes from stacking on top of each other. These properties make it better suited for organizational graphs than a generic spring-charge simulation.

**Why Recharts for charts (not D3.js).** Recharts provides ready-to-use React components for time-series line charts, bar charts, and sparklines. These are used for the risk score trend panel and the org health score history. Building equivalent charts with D3.js would require 100+ lines of manual SVG manipulation per chart. Recharts handles axes, tooltips, legends, and responsiveness automatically.

### Decision: TanStack Query for Server State

**What was decided.** `@tanstack/react-query@5.40.0` manages all API data fetching in the frontend.

**Why.** TanStack Query provides: automatic background refetching (the dashboard updates without page reload), request deduplication (multiple components requesting the same data send only one API call), stale-while-revalidate caching (the UI shows stale data immediately while fetching fresh data), and error/loading state management. Without it, every component would need its own `useState` + `useEffect` + fetch pattern, duplicating logic across the codebase.

### Decision: Vite Proxy for API Calls

**What was decided.** `vite.config.js` proxies all `/api` prefixed requests to `http://localhost:8000`, stripping the `/api` prefix before forwarding.

**Why.** During development, the frontend runs on port 5173 and the API on port 8000. Browser same-origin policy would block direct API calls from 5173 to 8000. The Vite proxy forwards requests from the same origin (5173) to the API, bypassing CORS. This also means the frontend code uses `/api/graph/snapshot` rather than `http://localhost:8000/graph/snapshot`, so the URL is environment-agnostic and works identically in development and production (where both are served from the same origin).

---

## 12. Observability — Prometheus + Grafana

### Decision: Prometheus + Grafana for Metrics and Dashboards

**What was decided.** `prom/prometheus:v2.51.0` scrapes metrics from the FastAPI `/metrics` endpoint every 15 seconds. `grafana/grafana:10.4.0` visualizes those metrics using a pre-configured dashboard provisioned from `monitoring/grafana/dashboards/org_synapse.json`.

**Why Prometheus.** Prometheus is the industry-standard time-series metrics system. Its pull-based model (Prometheus scrapes the API rather than the API pushing to Prometheus) means the API does not need to know where Prometheus is running — it just exposes `/metrics` and Prometheus finds it. The `prometheus-fastapi-instrumentator` library generates the required metrics automatically from the FastAPI request lifecycle.

**Why 15-second scrape interval.** This is the Prometheus default. For a development and demo deployment, 15 seconds provides sufficient resolution to observe latency spikes. A production deployment with strict SLOs might reduce this to 5 seconds, but doing so triples the storage cost.

**Why 15-day retention.** `--storage.tsdb.retention.time=15d` limits Prometheus disk usage. For a demo system, 15 days of metric history is more than sufficient to demonstrate the observability story. Longer retention would require either more disk or a long-term storage backend (Thanos, Cortex).

**Why Grafana provisioning from files.** The dashboard is defined in `monitoring/grafana/dashboards/org_synapse.json` and the data source in `monitoring/grafana/provisioning/datasources/prometheus.yml`. These files are mounted into the container at startup, so Grafana comes up pre-configured without any manual dashboard import step. This makes the observability stack part of the reproducible infrastructure.

**Why `GF_USERS_ALLOW_SIGN_UP: false`.** Grafana is an internal tool. Disabling self-registration means only the initial admin user (configured via `GF_SECURITY_ADMIN_USER` / `GF_SECURITY_ADMIN_PASSWORD`) can access it. This is a minimal but necessary security control for a tool with access to production metrics.

---

## 13. Security & Compliance

### Decision: Employee Consent Flag Gates Graph Computation

**What was decided.** The graph builder (`graph/builder.py`) filters raw events with a JOIN on `employees.consent = true AND employees.active = true`. Employees who have not consented are excluded from the graph entirely — their edges are never loaded into memory, never influence centrality scores, and never appear in risk outputs.

**Why at the query level and not the application level.** Filtering at the SQL query level means consent is enforced before any data reaches Python memory. An application-level filter (load all edges, then filter) would briefly hold non-consenting employees' data in memory, which may be insufficient under strict privacy regulations. The SQL filter prevents the data from leaving the database.

### Decision: Role-Based Access Control at the API Level

**What was decided.** The API design (implemented in `api/deps.py`) distinguishes between three roles: `hr_admin` (sees individual employee scores), `executive` (sees department-level aggregates only), and `analyst` (sees anonymized graph topology). Role resolution is based on the tenant API key's associated role, which is set at provisioning time.

**Why.** Individual SPOF scores and churn risk predictions are sensitive personnel data. An executive who sees that "John Smith has a 0.87 SPOF score and a 0.72 churn probability" could use that information for preemptive personnel actions that are legally problematic. The role model ensures executives see organizational risk ("Engineering has an elevated risk profile") without seeing individual employee scores.

### Decision: GDPR Article 20 Data Export

**What was decided.** `GET /compliance/export/{employee_id}` returns a structured export of all data held about a specific employee: raw event edges (anonymized to source/target IDs), graph snapshots (their metrics over time), risk scores, churn scores, knowledge records, and consent log.

**Why.** GDPR Article 20 gives individuals the right to receive their personal data in a portable format. Even though the system stores only metadata, edge records (who you communicated with and when) qualify as personal data under GDPR. Providing an automated export endpoint reduces compliance overhead: rather than a manual database query each time an employee exercises their GDPR rights, the data controller can use this endpoint.

---

## 14. Synthetic Data Generation

### Decision: A Configurable Synthetic Data Generator for Development and Demo

**What was decided.** `data/synthetic/generate_org_data.py` generates realistic collaboration edge lists for a configurable number of employees over a configurable number of days. The generator models three departments (Engineering, Sales, HR), connector employees with cross-department edges, and optionally a withdrawal pattern (declining edge count over time for a selected employee).

**Why.** The system requires graph data with specific structural properties to demonstrate its value: a bridge node with high betweenness centrality, a siloed department, and a withdrawing employee whose SPOF score rises over time. Real organizational data is not available in a demo or development context. The generator allows every demo to produce the same structural properties while generating different specific employee IDs and timestamps, making each run feel fresh.

**The withdrawal pattern.** The generator decreases one connector employee's edge count by a configurable percentage each week for the last N weeks of the dataset. This causes the employee's entropy trend slope to become increasingly negative, which increases their SPOF score via the `−δ × entropy_trend` term. This demonstrates the system's ability to detect disengagement before it becomes visible to managers.

---

## 15. Configuration Management

### Decision: All Weights and Thresholds as Environment Variables

**What was decided.** Every algorithmically significant constant is an environment variable with a sensible default: `SPOF_ALPHA`, `SPOF_BETA`, `SPOF_GAMMA`, `SPOF_DELTA`, `SILO_THRESHOLD`, `BETWEENNESS_EXACT_THRESHOLD`, `BETWEENNESS_K_PIVOTS`, `SUCCESSION_W_STRUCT`, `SUCCESSION_W_CLUST`, `SUCCESSION_W_DOMAIN`, `HEALTH_W_SILO`, `HEALTH_W_SPOF`, `HEALTH_W_ENTROPY`, `HEALTH_W_FRAG`, `GRAPH_WINDOW_DAYS`, `GRAPH_MIN_EVENTS`, and `CACHE_TTL_SEC`.

**Why.** The correct values for these parameters depend on the specific organization: its size, its communication culture, its industry's churn dynamics. Making them environment variables means: (1) a consultant deploying this system for a 5000-person enterprise can tune betweenness approximation without modifying code; (2) different tenants could in principle have different weight configurations; (3) A/B testing different risk weight configurations is possible without a code deployment.

**Why not a configuration table in the database.** A database configuration table requires a migration every time a new parameter is added, and it requires an API endpoint to update values. Environment variables are simpler for parameters that change infrequently and are set by operators rather than end users.

---

## 16. Dependency and Packaging

### Decision: pyproject.toml + requirements.txt

**What was decided.** `pyproject.toml` declares project metadata (name, version, Python requirement ≥ 3.11). `requirements.txt` lists pinned or minimum-version dependencies for the API and graph layer.

**Why Python 3.11 minimum.** Python 3.11 introduced significant CPython performance improvements (10–60% faster for pure Python code) and `tomllib` as a standard library module. Since graph metric computation is CPU-bound Python code, the 3.11 performance improvements directly reduce betweenness centrality computation time.

**Why `psycopg2-binary` and not `psycopg2`.** `psycopg2` requires a locally installed `libpq` (PostgreSQL client library) to compile from source. `psycopg2-binary` ships a pre-compiled version. For a development environment and Docker-based deployment, binary is the correct choice: it installs without a C toolchain, reducing container build time and eliminating a class of build failures on different operating systems.

---

## 17. HRIS Enrichment Integration

### Decision: Employee Matching by Name/Email Prefix, Not External ID

**What was decided.** `ingestion/connectors/hris_connector.py` matches HR system records to internal employees using a case-insensitive `name` match or an email-prefix `LIKE` query. It does not attempt to map external HRIS identifiers (Workday worker IDs, BambooHR employee numbers) to internal UUIDs.

**Why.** The internal `employees` table is populated from synthetic data or from Kafka-ingested collaboration events — neither source carries the HRIS's own identifier. Adding a `hris_external_id` column and requiring it to be pre-populated before the sync can run creates a chicken-and-egg bootstrapping problem. Name matching is imprecise but acceptable because the update is a `SET tenure_months = %s, ...` with no privacy-sensitive consequences for a mismatch: a failed match simply leaves the HRIS fields as NULL, and the GNN falls back to its 0.0 defaults for those features.

**Alternative considered.** Requiring a pre-mapped `hris_external_id` column. Rejected because it requires a separate configuration step that most demo and development deployments will skip, making the feature effectively non-functional for the majority of users.

**Alternative considered.** Email-based matching using a `work_email` column on `employees`. Rejected because the existing `employees` schema does not store email — the schema only carries `name`, `department`, `role`. Adding email would require a migration and a seeding change, and the name-match approach is sufficient for the current use case.

### Decision: COALESCE Strategy for GNN Feature Activation

**What was decided.** `ml/gnn/feature_builder.py` uses `COALESCE(reporting_level, role_level, 0)` and `COALESCE(pto_days_ytd, pto_days_used, 0)` in the SQL query. The GNN feature matrix (`GNN_IN_FEATURES = 11`) is unchanged; features 0–2 are populated from HRIS data when available and fall back to 0.0 when not.

**Why.** The GNN was already trained to accept 11-feature node vectors with 0.0 defaults for the HRIS features — this is explicitly documented in `ml/gnn/feature_builder.py`'s module docstring. Activating HRIS features therefore requires no model architecture change, no retraining from scratch, and no new feature dimension. The model's next weekly training run will incorporate the new non-zero values automatically.

**Why COALESCE rather than conditional Python logic.** Handling the fallback at the SQL level means the Python feature-building code has no if-branches for "HRIS available vs. not available." The query always returns a value; whether that value is real or 0.0 is the database's concern.

### Decision: ENABLE_HRIS Environment Variable Gate

**What was decided.** The `sync_hris_data` Airflow task checks `ENABLE_HRIS=true` before attempting any HRIS connection. If unset or false, the task logs a skip message and exits cleanly in under 100ms.

**Why.** HRIS credentials (`HRIS_BASE_URL`, `HRIS_CLIENT_ID`, `HRIS_CLIENT_SECRET`) are not present in the default `.env.example`. A Airflow task that attempts an HTTP connection to a non-existent endpoint would fail every day for every deployment that has not configured HRIS integration, adding noise to the Airflow failure log. The boolean gate makes the feature explicitly opt-in — silence is the correct default for unconfigured integrations.

---

## 18. Manager Self-Service Risk View

### Decision: Role Stored on `tenant_api_keys`, Not on `employees`

**What was decided.** The `role` column (`hr_admin`, `executive`, `analyst`, `manager`) is added to the public schema `tenant_api_keys` table, not to the `employees` table. A manager receives a dedicated API key with `role='manager'`.

**Why.** The `employees` table represents organizational people, not authentication principals. An employee who is also a manager in the organizational hierarchy might simultaneously be an `analyst` role user for data access purposes. Conflating the people domain with the auth domain would require a join on every authenticated request to determine whether the authenticated person is themselves an employee — which is operationally complex and not always true (external consultants, automated systems).

**Alternative considered.** A separate `manager_keys` table. Rejected as unnecessary indirection — the existing `tenant_api_keys` table already carries a name and is designed for multiple keys per tenant.

### Decision: `require_role()` as a Dependency Factory, Not Middleware

**What was decided.** Role enforcement is implemented as a FastAPI dependency factory in `api/deps.py`: `require_role("manager", "hr_admin")` returns a `Depends`-compatible callable that raises HTTP 403 if the current role is not in the allowed set. It is applied per-endpoint, not globally in middleware.

**Why.** Middleware runs before routing — it cannot know which endpoint is being called and therefore cannot apply per-endpoint role logic. A dependency factory is the idiomatic FastAPI approach: it participates in the OpenAPI schema generation, appears in `/docs` alongside the endpoint it protects, and can be tested independently of the HTTP layer by calling the dependency directly.

**Alternative considered.** JWT claims-based auth. Rejected because the existing system uses API key + tenant context, not JWTs. Introducing JWTs would require a separate token issuance endpoint and a signing key management story. The API key role approach re-uses the existing auth infrastructure with a single new column.

### Decision: Traffic-Light Abstraction — No Raw Scores Exposed to Managers

**What was decided.** `GET /manager/team` returns `status: "green" | "amber" | "red"` derived from entropy trend and churn probability thresholds. It never returns `spof_score`, `churn_prob`, or `entropy_trend` as numeric values. The threshold computation happens server-side; the client receives only the categorical label.

**Why.** This is a regulatory and access-control decision, not a UX simplification. A manager who receives a numeric `churn_prob = 0.72` for a direct report is in possession of an HR data point that should be access-controlled at the `hr_admin` level. Numeric personnel predictions create legal exposure for managers who act on them directly. The traffic-light abstraction preserves the signal (something is changing) while keeping the quantification inside the `hr_admin` boundary.

**Why compute server-side rather than send scores and redact client-side.** Client-side redaction can be bypassed by reading the raw API response. Server-side computation ensures the numeric values never travel over the wire for manager-role API calls.

---

## 19. New Hire Graph Integration Tracker

### Decision: Cohort Comparison in 30-Day Tenure Bands

**What was decided.** The onboarding integration score computes `degree_centrality_pct` by comparing each new hire's degree against the median of employees in the same 30-day tenure band (`DATE_PART('day', scored_date - hire_date)::int / 30`). A 45-day employee is compared against the 30–60 day cohort, not all employees with tenure ≤ 180 days.

**Why.** Network degree grows naturally over time as employees accumulate connections. Comparing a 14-day employee against a 90-day employee using the same threshold would flag almost every new hire in their first month. The cohort band normalizes for tenure so the metric measures "is this employee integrating at the rate we would expect for their stage?" rather than "is this employee as connected as a typical employee?"

**Why 30-day bands and not a continuous tenure adjustment.** A continuous linear adjustment would require estimating the tenure→degree growth curve per organization, which varies by industry, team size, and onboarding style. 30-day bands are a pragmatic approximation that works without organization-specific calibration.

### Decision: Alert Fires on First `below_cohort_threshold = TRUE` at Day 60+, Not on Every Re-Run

**What was decided.** The `INSERT INTO alerts` uses `ON CONFLICT DO NOTHING`. This means the alert fires once per employee, not every time the daily DAG runs while the employee remains below threshold.

**Why.** An employee who is below the 25th percentile cohort at day 60 and stays there for 30 days would generate 30 duplicate alerts without `ON CONFLICT DO NOTHING`. HR practitioners would tune out a signal that fires daily for the same employee. The intent is to draw attention to the employee once, not to repeatedly notify.

**The limitation.** If an employee recovers (crosses back above the 25th percentile) and then falls below again, the second breach does not re-alert. This is an acceptable trade-off — the more important signal is the initial flag, and a persistent integration failure will surface in the cohort scatter chart regardless of whether a new alert fires.

---

## 20. Reorg Scenario Planner

### Decision: In-Memory Graph Copy for Simulation, Not a Neo4j Branch

**What was decided.** `graph/scenario_simulator.py` loads the current graph as a `nx.DiGraph` via `load_current_graph(conn)`, then calls `G.copy()` before applying operations. All simulation is in-process; no Neo4j mutation occurs.

**Why.** Scenario simulation must be non-destructive and fast. A Neo4j mutation path would require: (1) a Cypher transaction to clone subgraphs or apply virtual removals, (2) rollback handling if the simulation fails midway, (3) concurrent isolation if two simulations run simultaneously. NetworkX in-memory copy-and-mutate has none of these complications. A `G.copy()` on a 500-node graph takes under 10ms; the subsequent metric computation dominates.

**Alternative considered.** A read-only Neo4j "what-if" Cypher query using virtual graph projections (Neo4j GDS). Rejected because GDS virtual projections are a beta feature with inconsistent behavior across Neo4j versions, and the system already handles Neo4j as an optional component with a NetworkX fallback.

### Decision: Operations Stored as JSONB, Not Normalized Rows

**What was decided.** The `reorg_scenarios.operations` column is `JSONB NOT NULL DEFAULT '[]'`. Each operation is a JSON object: `{"op": "remove", "employee_ids": [...]}` or `{"op": "merge_depts", "source_dept": "...", "target_dept": "..."}`.

**Why.** Scenarios have heterogeneous operations — a "remove" operation has a different shape from a "merge_depts" operation. Normalizing this into typed rows would require either a single table with nullable columns for each operation type (wide and sparse) or multiple tables joined at query time. JSONB provides schema flexibility at the cost of no column-level constraints on individual operation fields — an acceptable trade-off given that operations are validated by the Pydantic model before insertion.

**Why `impact_report` is also JSONB.** The impact report structure includes a variable-length list of per-employee SPOF deltas (`spof_top10_delta`). Storing this in a relational schema would require a separate child table and a join on every report read. JSONB keeps the report as a single retrieval.

### Decision: Comparison via Query Parameter, Not a Stored Comparison Object

**What was decided.** `GET /scenarios/compare?ids=id1,id2,id3` accepts a comma-separated list of scenario IDs and returns their `impact_report` fields side by side. The comparison is computed at request time; no "comparison" entity is created in the database.

**Why.** A comparison is a view over two or more scenarios, not an entity with its own lifecycle. Creating a persistent comparison object would require its own table, TTL logic, and cleanup job. The query parameter approach is stateless: any set of computed scenarios can be compared at any time, and the client (the `ScenarioCompareView` component) controls which scenarios are in the comparison by managing its own selection state.

---

## 21. Knowledge Transfer Campaign Planner

### Decision: Three-Phase Structure Mapped to Calendar Weeks, Not Task Buckets

**What was decided.** The transfer plan has three fixed phases: weeks 1–4 (relationship introductions), weeks 5–8 (document review), weeks 9–12 (structural shadowing). The phase boundaries are non-configurable.

**Why.** The three phases correspond to a natural learning progression: first, meet the people you will need to know (social capital investment); then, acquire the codified knowledge (documents and domains); finally, observe the tacit interactions that cannot be documented (meeting patterns). Allowing phase boundaries to be configurable would require a configuration API and would produce plans where the learning sequence is inverted — a candidate reviewing documents before being introduced to the relevant people, for example. Fixed boundaries encode the correct dependency order.

**Why these specific week counts.** 90 days is the standard new-role ramp-up horizon in organizational psychology literature. Dividing it into three 4-week phases gives each phase enough time to produce observable results without making any single phase feel unbounded.

### Decision: Checklist State in `localStorage`, Not API-Persisted

**What was decided.** `TransferPlanPanel.jsx` stores checked/unchecked state in `localStorage` under the key `tp_{plan_id}_{phase_key}`. The backend has no "mark action complete" endpoint.

**Why.** Transfer plan execution is a manager or HR practitioner activity — the person checking off actions is working through a personal workflow, not submitting data back to a system of record. Persisting checklist state server-side would require an authenticated user model (the manager's individual identity, not just the tenant's API key) which is out of scope. `localStorage` is sufficient for the single-user workflow and survives page refreshes.

**The accepted limitation.** If the HR practitioner switches devices or browsers, their progress is lost. For a 90-day plan, this is acceptable — plan regeneration is available on demand, and the underlying plan content is always retrievable from the API.

### Decision: Claude Called Synchronously from Airflow, Not via `asyncio.to_thread`

**What was decided.** `etl/tasks/generate_transfer_plans.py` calls `anthropic.Anthropic().messages.create(...)` synchronously in the Airflow task function.

**Why.** Airflow tasks are executed as Python subprocesses by LocalExecutor — they are not inside an async event loop. `asyncio.to_thread` is only necessary when a blocking synchronous call must be offloaded from an async event loop. In an Airflow task context, there is no event loop to protect; the synchronous SDK call is the correct choice. Using `asyncio.run(asyncio.to_thread(...))` inside an Airflow task would create a new event loop for no purpose and add latency from thread pool management.

---

## 22. Team Composition Optimizer

### Decision: Greedy Set-Cover, Not Exhaustive Combinatorial Search

**What was decided.** `graph/team_optimizer.py` uses a greedy marginal-gain algorithm: at each step, add the candidate who maximally increases the combination of bridge coverage and domain coverage. The algorithm runs once per target team size from `min_size` to `max_size`.

**Why.** Exhaustive search over all combinations of k employees from a pool of N candidates is O(C(N,k)), which for N=100 candidates and k=6 is approximately 1 billion combinations. This is computationally infeasible within an API request timeout. The greedy approximation runs in O(N×k) time — under 50ms for a 100-candidate pool with max size 6 — and produces solutions within a known approximation bound for set-cover problems (1 − 1/e ≈ 63% of optimal).

**Alternative considered.** Integer linear programming (PuLP or scipy.optimize). Rejected because ILP solvers require problem reformulation into linear constraints and produce exact solutions but with unpredictable runtime. For real-time API usage, bounded runtime is more important than global optimality.

**Alternative considered.** Random sampling with multiple restarts (simulated annealing). Rejected because it introduces non-determinism — the same constraint set would produce different results on different API calls, which would confuse users who expect reproducible recommendations.

### Decision: Composite Score Weights (Bridge 0.40, Domain 0.35, Structural Load 0.25)

**What was decided.** The final ranking uses: `0.40 × bridge_coverage + 0.35 × domain_coverage + 0.25 × (1 - structural_load_norm)`.

**Why these weights.** Bridge coverage has the highest weight because bridging is the hardest property to approximate informally — a manager picking a team by availability will naturally select people they know, which means people in their own department. Domain coverage is second because domain gaps produce project failures that are visible and measurable. Structural load (inverse of SPOF concentration) is third because it is a risk-mitigation objective rather than a functional requirement — a high-SPOF team will accomplish the objective but creates organizational fragility.

**Why configurable via future env vars but not yet.** The weights are hardcoded at 0.40/0.35/0.25 because there is no empirical basis for adjusting them without data on which compositions produced successful projects. Once the system accumulates project outcome data (a future feature), the weights can be tuned per organization. Premature configuration without evidence would produce arbitrary variation.

### Decision: Optimization Result Not Persisted

**What was decided.** `POST /teams/optimize` computes results in real time and returns them in the response. No table stores optimization results. Redis caches the result under a hash of the request body with a 600-second TTL.

**Why.** Team compositions are transient recommendations, not organizational decisions. The user explores options, selects one, and exports it. Persisting every optimization run would grow a table with many short-lived rows and no natural foreign key to another entity (a "project" table does not exist). The Redis cache ensures that re-requesting the same constraint set within 10 minutes returns instantly without re-running the algorithm.

---

## 23. Departure Impact Report

### Decision: Four Snapshot Checkpoints (t-90, t-0, t+30, t+60)

**What was decided.** `etl/tasks/generate_departure_report.py` queries graph snapshots at four points: 90 days before departure, the departure date, 30 days after, and 60 days after. `_nearest_snapshot_date()` finds the closest available snapshot to each target date.

**Why these four checkpoints.** `t-90` is the earliest point at which the churn model and SPOF score should have surfaced a signal — 90 days is the prediction horizon of the GNN. If the employee was flagged at t-90, the report can confirm the prediction was accurate. `t-0` is the baseline immediately before departure. `t+30` shows the acute structural impact — the period before any organizational adaptation. `t+60` shows whether the organization is recovering or deteriorating, which is the most actionable signal for the HR team.

**Why `_nearest_snapshot_date()` rather than failing if the exact date is missing.** Graph snapshots are only written on days when the `graph_builder_dag` runs successfully. A departure date that falls on a weekend or public holiday may not have a snapshot. Nearest-available matching preserves the report's value without requiring a snapshot for every calendar day.

### Decision: Departure Detected by Daily Sensor on `deactivated_at`, Not by API Event

**What was decided.** `etl/dags/departure_report_dag.py` runs daily at 06:00 UTC and queries `employees WHERE deactivated_at::date = CURRENT_DATE - 1 AND active = FALSE`. It does not subscribe to an HR system webhook or listen for an API call.

**Why polling over event-driven.** Implementing a departure webhook would require: (a) a new API endpoint to receive the event, (b) authentication for the calling system, (c) queuing logic to prevent lost events if the API is unavailable. A daily poll at 06:00 UTC achieves the same outcome with a maximum 24-hour latency — acceptable for a retrospective analysis report. The polling query is a single indexed scan on `deactivated_at`, taking under 1ms.

**Why the report is generated by an Airflow DAG rather than triggered by the PATCH /employees/{id} endpoint.** Synchronous report generation during an API call would block the response for 15–30 seconds (graph snapshot loading + Claude API call). The async DAG approach separates the departure recording action from the report generation side-effect, following the same pattern as all other ML outputs in the system.

---

## 24. DEI Structural Equity Analytics

### Decision: Demographic Data Provided by HR Team, Not Inferred by the System

**What was decided.** `employee_demographics` is populated exclusively via `POST /equity/import-demographics` — a batch import endpoint called by the HR team with data sourced from their HRIS. The system never derives or infers demographic attributes from graph data, name patterns, or any other signal.

**Why.** Inferring demographic attributes from behavioral or nominal data is both technically unreliable and legally hazardous. In the EU, GDPR Article 9 prohibits processing Special Category Data (which includes data "concerning" a person's characteristics that could reveal protected attributes) without explicit lawful basis. An inference engine would be processing data of unknown sensitivity without the subject's knowledge. The import model transfers legal responsibility to the data controller (the customer's HR team), who holds the data with the appropriate consent and lawful basis.

### Decision: Group Labels Use Abstract Identifiers, Not Real Attribute Values

**What was decided.** The `gender_group` column stores labels like `"group_a"`, `"group_b"` rather than actual demographic values. The `tenure_band` and `level_band` columns use standardized categorical values (`"0-1y"`, `"ic"`, etc.) but not continuous values or free-text.

**Why abstract group labels.** Abstract labels prevent the system from being used to identify individuals whose demographic group is small enough to be uniquely identified. If `gender_group = "non-binary"` applies to only 2 employees in a department, any aggregate statistic for that group could effectively identify those individuals. Abstract labels shift the labeling decision to the HR team, who can choose grouping granularity appropriate for their organization's headcount.

**Why categorical bands for tenure and level.** Continuous values (e.g., exact tenure in days, exact salary) would allow precise cross-referencing with other data sources. Bands provide sufficient analytical resolution (which tenure cohort has lower centrality?) without creating a re-identification risk.

### Decision: All API Outputs Are Group Aggregates; No Individual Demographic Joins

**What was decided.** The `GET /equity/centrality-distribution` endpoint returns only `{ group_value, median_score, p25_score, p75_score, member_count }` per group. The `GET /equity/succession-check/{id}` endpoint returns `{ tenure_band_composition: {}, level_band_composition: {} }` as aggregate percentages. No endpoint returns `{ employee_id, gender_group }` or any similar individual-level demographic data.

**Why.** Exposing individual-level demographic data via the API would make `employee_demographics` a de facto publicly accessible demographic database for the organization's employees, accessible to anyone with an API key. The analytical value of the DEI module is the group-level pattern, not the individual record. Aggregate-only outputs achieve the analytical goal while preventing the table from being misused as a demographic lookup.

---

## 25. Weekly Insights Digest

### Decision: Jinja2 for Email Template Rendering

**What was decided.** The digest email HTML is rendered using `jinja2.Environment` with a `FileSystemLoader` pointing to `etl/templates/`. The template (`digest_email.html`) uses `{{ variable }}` syntax for all dynamic values.

**Why Jinja2.** Jinja2 is already available in the Airflow Docker container (Airflow uses it for DAG templating). Using it for email rendering adds no new dependency to the Airflow image. The alternative — Python string `.format()` or f-strings — is unsafe for HTML generation because it does not escape user-controlled values, creating XSS potential in email clients that render HTML. Jinja2's auto-escape mode handles this correctly.

### Decision: SendGrid over AWS SES or Direct SMTP

**What was decided.** Outbound email uses `sendgrid-python` (`sendgrid.SendGridAPIClient`). AWS SES and direct SMTP were not implemented.

**Why SendGrid.** SendGrid provides a single REST API that works in any deployment environment without AWS credential management or SMTP server configuration. A single `SENDGRID_API_KEY` environment variable is the only configuration required. AWS SES requires AWS IAM credentials, regional configuration, and SES sandbox approval for new accounts. Direct SMTP requires a mail server, TLS certificate handling, and bounce/complaint management. For a demo and early-production system, SendGrid's zero-infrastructure approach reduces setup friction.

**Why the email is skipped silently when `SENDGRID_API_KEY` is unset.** The digest is a best-effort delivery channel, not a critical pipeline step. A missing API key means the customer has not configured email delivery — the appropriate response is to log a warning and continue, not to fail the Airflow DAG run and mark the entire digest pipeline as failed.

### Decision: Slack Block Kit, Not Plain Text Message

**What was decided.** The Slack digest uses the Block Kit JSON payload format with `header`, `section`, `divider`, and `actions` blocks. The payload is sent to a Slack incoming webhook URL via `httpx.post`.

**Why Block Kit.** Slack Block Kit renders structured layouts with visual hierarchy — the health score as a header, metrics as two-column fields, and an interactive "Open Dashboard" button. Plain text messages do not support buttons and collapse all content into undifferentiated text. Block Kit is the appropriate format for a digest that needs to communicate four or five data points clearly in a channel that may be scanned quickly.

**Why webhook, not Slack API with OAuth.** Slack's incoming webhook integration requires no OAuth flow, no token refresh, and no app approval from Slack's review team. A single webhook URL is configured once by the HR team's Slack admin. The OAuth bot approach would require building a full Slack app installation flow — a meaningful engineering investment for a notification channel.

### Decision: Digest Config in `digest_config` Table (Not env vars)

**What was decided.** Email recipients, webhook URL, enable flags, and timezone are stored in a `digest_config` table inside the tenant schema, with one row per tenant. The Airflow DAG reads this table at task runtime.

**Why the database rather than environment variables.** Unlike algorithmic weights (which are the same for all tenants and change rarely), digest configuration is per-tenant and changes frequently — HR admins add and remove recipients, toggle email on/off, change timezones. Environment variables cannot be changed without restarting the Airflow container. A database row can be updated via the Admin panel API with immediate effect on the next digest run.

---

## Summary Table

| Decision | Chosen | Key Reason |
|---|---|---|
| Data model | Weighted directed graph | Organizational risk is structural, not sentiment-based |
| Message broker | Kafka (KRaft) | Durable, replayable event log; no Zookeeper overhead |
| Primary database | PostgreSQL 15 | JSONB, TIMESTAMPTZ, ON CONFLICT, analytical queries |
| Graph store | Neo4j 5 (optional) | Traversal queries; NetworkX fallback for resilience |
| Cache | Redis 7 (allkeys-lru) | Reduce betweenness latency from seconds to milliseconds |
| Orchestrator | Airflow 2.9 (LocalExecutor) | DAG dependencies, retry, UI; LocalExecutor for single machine |
| Graph library | NetworkX 3 | Complete algorithm library; native Python integration |
| Community detection | Louvain (python-louvain) | O(n log n), deterministic enough for reporting |
| Silo detection grouping | Formal departments | HR-actionable; avoids Louvain bias from high-activity nodes |
| Betweenness | Exact < 500 nodes, k-pivot ≥ 500 | Balances accuracy vs. compute time |
| Anomaly detection | Isolation Forest | Unsupervised; no labeled churn data available |
| Churn model | Graph Attention Network | Structural context matters for churn prediction |
| API framework | FastAPI + uvicorn | Async, auto-docs, Pydantic validation, ASGI WebSocket support |
| Database access | Raw psycopg2 (RealDictCursor) | Full SQL control for analytical queries |
| Multi-tenancy | Schema-per-tenant | Isolation without RLS complexity; per-tenant backup/restore |
| API key storage | SHA-256 hash only | Breach-resistant credential storage |
| Real-time alerts | WebSocket + Redis pub/sub | Push model; fan-out across multiple Uvicorn workers |
| NL interface | Claude + tool use | Reliable structured tool calling; no NL-to-SQL brittleness |
| Frontend build | Vite 5 | Sub-300ms cold start vs. CRA/Webpack 15–30s |
| Graph rendering | Sigma.js (WebGL) | 60 fps for 200+ node graphs vs. SVG D3 at ~15 fps |
| Layout algorithm | ForceAtlas2 | Designed for social networks; prevents disconnected drift |
| Server state | TanStack Query | Deduplication, stale-while-revalidate, background refresh |
| Metrics | Prometheus + Grafana | Pull-based; pre-provisioned dashboard; industry standard |
| Consent enforcement | SQL-level filter | Data never enters memory for non-consenting employees |
| HRIS employee matching | Name/email-prefix COALESCE | No external ID pre-mapping required; graceful 0.0 fallback for GNN |
| HRIS feature activation | COALESCE in SQL, fixed GNN_IN_FEATURES=11 | No model architecture change; pre-existing 0.0 defaults absorb missing data |
| HRIS gate | `ENABLE_HRIS` env var (opt-in) | Silent skip prevents daily Airflow failures for unconfigured deployments |
| Manager role storage | `role` column on `tenant_api_keys` | Auth principal ≠ organizational person; avoids people/auth domain collision |
| Role enforcement | `require_role()` dependency factory | Per-endpoint; participates in OpenAPI schema; testable independently |
| Manager score exposure | Traffic-light only (green/amber/red) | Numeric HR predictions are `hr_admin`-scoped; traffic-light preserves signal within access boundary |
| Onboarding cohort bands | 30-day tenure bands | Normalizes for natural degree growth without per-org curve fitting |
| Onboarding alert dedup | `ON CONFLICT DO NOTHING` | One alert per employee per breach; prevents daily duplicate noise |
| Scenario simulation | In-memory NetworkX copy | Non-destructive, fast, no Neo4j mutation or rollback complexity |
| Scenario operations | JSONB on `reorg_scenarios` | Heterogeneous operation shapes; avoids sparse nullable columns or multiple tables |
| Scenario comparison | Query parameter `?ids=...` | Comparison is a view, not an entity; no lifecycle management required |
| Transfer plan phases | Fixed 3-phase 90-day structure | Encodes correct dependency order (social → codified → tacit) |
| Transfer plan checklist state | `localStorage` | Manager workflow, single-user; server persistence would require individual identity model |
| Transfer plan Claude call | Synchronous Anthropic SDK | Airflow tasks have no event loop; `asyncio.to_thread` would be purposeless overhead |
| Team optimizer algorithm | Greedy set-cover | O(N×k) vs O(C(N,k)) exhaustive; bounded runtime for API use; ~63% optimality guarantee |
| Team optimizer composite score | Bridge 0.40 / Domain 0.35 / Load 0.25 | Bridges hardest to achieve informally; domains produce measurable failure; load is risk mitigation not functional requirement |
| Team optimization persistence | Redis cache only (600s TTL) | Compositions are transient recommendations; no natural FK anchor for a results table |
| Departure checkpoints | t-90, t-0, t+30, t+60 | Maps to GNN prediction horizon, departure baseline, acute impact, recovery assessment |
| Departure detection | Daily poll on `deactivated_at` | 24h max latency acceptable for retrospective report; no webhook auth complexity |
| Departure report generation | Async Airflow DAG, not API hook | Decouples departure recording from 15–30s report generation; consistent with all other ML outputs |
| DEI demographic source | HR team import only, never inferred | Inferring protected attributes violates GDPR Article 9; transfers legal responsibility to data controller |
| DEI group labels | Abstract identifiers (`group_a`) | Prevents small-group re-identification; HR team controls granularity |
| DEI API outputs | Group aggregates only | Individual demographic joins would make the table a de facto demographic lookup |
| Email rendering | Jinja2 template | Already available in Airflow container; auto-escapes HTML (XSS safe) |
| Email provider | SendGrid (`sendgrid-python`) | Single API key, no AWS IAM, no SMTP server; zero infrastructure setup |
| Slack digest format | Block Kit JSON | Visual hierarchy + interactive button; plain text has no layout or actions support |
| Digest config storage | `digest_config` DB table | Per-tenant, changes frequently via Admin UI; env vars require container restart |
