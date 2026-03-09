# Secure API Platform using Kong on Minikube

This repository implements a fully local, self-managed secure API platform on Ubuntu 24.04 using:

- Minikube (Kubernetes cluster)
- Kong Gateway (API Gateway, DB-less mode)
- Envoy (front proxy for DDoS-style protection controls)
- Flask microservice with SQLite user store
- Helm charts for deployment and configuration

## Architecture Overview

Request path:

1. Client calls Envoy (`NodePort` exposed from Minikube).
2. Envoy forwards to Kong Proxy service.
3. Kong applies plugins (JWT, IP rate limit, IP whitelist, Lua pre-function logic).
4. Kong routes to `user-service` in Kubernetes.
5. `user-service` validates business logic and returns response.

## API Request Flow

Client -> Envoy -> Kong -> user-service

- Public APIs (bypass auth): `GET /health`, `GET /verify`
- Auth API: `POST /login`
- Protected API: `GET /users` (JWT required)

## JWT Authentication Flow

1. User logs in via `POST /login` with username/password.
2. Service validates SQLite user and returns JWT.
3. Client sends `Authorization: Bearer <token>` to protected APIs.
4. Kong JWT plugin verifies signature and `exp` claim before forwarding.

## Authentication Bypass Strategy

Kong declarative routes are split by path:

- Public routes: `/health`, `/verify`, `/login` (no JWT plugin)
- Protected route: `/users` (JWT + rate limit + IP whitelist)

## Repository Structure

```text
.
├── microservice/
│   ├── app/
│   │   └── app.py
│   ├── Dockerfile
│   ├── requirements.txt
│   └── sqlite.db
├── helm/
│   ├── user-service/
│   │   ├── Chart.yaml
│   │   ├── values.yaml
│   │   └── templates/
│   └── kong/
│       ├── Chart.yaml
│       ├── values.yaml
│       ├── kong.yaml
│       ├── plugins/
│       │   └── custom.lua
│       └── templates/
├── k8s/
│   └── deployment.yaml
└── README.md
```

## Prerequisites (Ubuntu 24.04)

Install tools:

```bash
sudo apt-get update
sudo apt-get install -y curl apt-transport-https ca-certificates gnupg

# kubectl
curl -LO "https://dl.k8s.io/release/$(curl -L -s https://dl.k8s.io/release/stable.txt)/bin/linux/amd64/kubectl"
sudo install -o root -g root -m 0755 kubectl /usr/local/bin/kubectl

# Minikube
curl -LO https://storage.googleapis.com/minikube/releases/latest/minikube-linux-amd64
sudo install minikube-linux-amd64 /usr/local/bin/minikube

# Helm
curl https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash
```

Ensure Docker is installed and running.

## 1. Start Kubernetes with Minikube

```bash
minikube start --driver=docker --cpus=4 --memory=8192
kubectl get nodes
```

## 2. Build and load microservice image into Minikube

```bash
cd microservice
docker build -t user-service:local .
minikube image load user-service:local
cd ..
```

## 3. Deploy user-service via Helm

```bash
helm upgrade --install user-service ./helm/user-service \
  --set image.repository=user-service \
  --set image.tag=local \
  --set jwt.secret='change-me-strong-secret'
```

## 4. Deploy Kong + Envoy via Helm

Use the same JWT secret as the user service.

```bash
helm upgrade --install kong-stack ./helm/kong \
  --set kong.jwt.key='local-dev-key' \
  --set kong.jwt.secret='change-me-strong-secret' \
  --set kong.ipWhitelist[0]='0.0.0.0/0' \
  --set kong.rateLimit.minute=10
```

## 5. Access gateway endpoint

```bash
export GATEWAY_URL="http://$(minikube ip):30080"
echo "$GATEWAY_URL"
```

## 6. Functional tests

### Public APIs (auth bypass)

```bash
curl -i "$GATEWAY_URL/health"
curl -i "$GATEWAY_URL/verify"
```

### Login and get JWT

```bash
TOKEN=$(curl -s -X POST "$GATEWAY_URL/login" \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"admin123"}' | jq -r '.token')

echo "$TOKEN"
```

### Protected API with JWT

```bash
curl -i "$GATEWAY_URL/users" \
  -H "Authorization: Bearer $TOKEN"
```

### Protected API without JWT (should fail)

```bash
curl -i "$GATEWAY_URL/users"
```

## 7. Security tests

### Rate limiting test (Kong, per IP)

```bash
for i in $(seq 1 12); do
  curl -s -o /dev/null -w "%{http_code}\n" \
    "$GATEWAY_URL/users" -H "Authorization: Bearer $TOKEN"
done
```

Expected: first requests succeed, then `429` after configured threshold.

### IP whitelisting test

Set a restrictive CIDR and redeploy:

```bash
helm upgrade --install kong-stack ./helm/kong \
  --set kong.jwt.key='local-dev-key' \
  --set kong.jwt.secret='change-me-strong-secret' \
  --set kong.ipWhitelist[0]='127.0.0.1/32'
```

Expected: calls from non-whitelisted source are blocked (`403`).

### DDoS protection test (Envoy)

Envoy protects Kong with:

- local token bucket rate limit
- connection/pending request circuit breaker thresholds
- max requests per connection

Run a burst test:

```bash
for i in $(seq 1 200); do
  curl -s -o /dev/null -w "%{http_code}\n" "$GATEWAY_URL/health" &
done
wait
```

Expected: under burst conditions, some requests are throttled/limited by Envoy controls.

## Custom Kong Lua Logic

`helm/kong/plugins/custom.lua` is version-controlled and loaded through Kong DB-less configuration (`helm/kong/templates/kong-configmap.yaml`).

Current behavior:

- injects `x-client-ip` header to upstream request
- injects `x-platform: secure-api-platform` header to upstream request

## Why Envoy for DDoS-style protection

### Reason for Choosing Envoy

**Envoy Proxy** was selected as the DDoS protection solution for the following reasons:

1. **Cloud-Native & Kubernetes-Native**
   - Built specifically for modern microservices architectures
   - Native integration with Kubernetes service discovery
   - Lightweight and designed for sidecar/front-proxy patterns

2. **Multi-Layer Protection**
   - **Local Rate Limiting**: Token bucket algorithm with configurable refill rates
   - **Circuit Breaking**: Connection and pending request limits prevent resource exhaustion
   - **Connection Management**: Max requests per connection prevents keep-alive abuse
   - **Automatic Retry/Timeout Controls**: Prevents cascading failures

3. **Production-Ready & Battle-Tested**
   - Used by companies like Lyft, AWS (App Mesh), Google (Traffic Director)
   - CNCF graduated project with strong community support
   - Extensive observability with metrics and tracing built-in

4. **Self-Managed & Open Source**
   - No external dependencies or cloud vendor lock-in
   - Runs entirely locally on Minikube
   - Fully declarative configuration via YAML

5. **Complements Kong**
   - Layer separation: Envoy protects infrastructure, Kong protects business logic
   - Envoy handles connection-level abuse before Kong processes HTTP semantics
   - Performance optimization: cheap connection filtering before expensive JWT validation

**Alternatives Considered:**
- **nginx/ModSecurity**: Requires more complex configuration, less cloud-native
- **Istio**: Too heavyweight for this use case (full service mesh overhead)
- **Cloud WAF services**: Not self-managed, requires external infrastructure

### Integration with Kong and Kubernetes

**Architecture Flow:**
```
Internet/Client
    ↓
[NodePort 30080] ← Kubernetes Service (envoy-front)
    ↓
Envoy Proxy Pod (Layer 4-7 protection)
    ↓
Kong Proxy Service (ClusterIP)
    ↓
Kong Gateway Pod (JWT, rate-limit, IP whitelist)
    ↓
User Service (ClusterIP)
    ↓
Flask Microservice Pod
```

**Kubernetes Integration:**

1. **Service Discovery**
   ```yaml
   # Envoy config references Kong service
   clusters:
     - name: kong_proxy
       type: LOGICAL_DNS
       load_assignment:
         endpoints:
           - lb_endpoints:
             - endpoint:
                 address:
                   socket_address:
                     address: kong-proxy.default.svc.cluster.local
                     port_value: 80
   ```
   Envoy uses Kubernetes DNS to discover Kong pods dynamically.

2. **Deployment Strategy**
   - **Envoy**: Exposed via NodePort (30080) as the single entry point
   - **Kong**: ClusterIP service, only accessible from within cluster
   - **User-service**: ClusterIP, isolated from direct external access

3. **Configuration Management**
   - ConfigMap `envoy-config` holds declarative Envoy YAML
   - Mounted as volume in Envoy pod at `/etc/envoy/envoy.yaml`
   - Hot reload possible via SIGHUP or pod restart

4. **Scalability**
   - Both Envoy and Kong deployments can be scaled independently:
     ```bash
     kubectl scale deploy envoy-front --replicas=3
     kubectl scale deploy kong --replicas=3
     ```

**Kong Integration:**

1. **Layer Separation**
   - **Envoy (L4-L7)**: Connection limits, rate buckets, circuit breakers
   - **Kong (L7)**: JWT validation, business rate limits (per user/IP), routing

2. **Header Propagation**
   - Envoy adds headers like `x-envoy-upstream-service-time`
   - Kong's custom Lua adds `x-client-ip`, `x-platform`
   - All headers forwarded to backend for logging/debugging

3. **Failure Handling**
   - If Envoy circuit breaker opens → immediate 503 to client
   - If Kong rate limit triggered → 429 with Kong headers
   - If JWT invalid → 401 from Kong
   - Clear separation of failure reasons for troubleshooting

### Demonstrating Basic Protection Behavior

#### 1. Baseline Performance (No Load)

```bash
export GATEWAY_URL="http://$(minikube ip):30080"

# Normal request
time curl -s "$GATEWAY_URL/health"
```

**Expected**: `200 OK`, response time ~10-50ms

#### 2. Envoy Circuit Breaker Protection

**Current Configuration:**
```bash
kubectl get configmap envoy-config -o yaml | grep -A 5 "circuit_breakers"
```

Shows:
- `max_connections: 100`
- `max_pending_requests: 100`

**Test:** Exceed connection limits
```bash
# Generate 200 concurrent connections (exceeds max_connections=100)
for i in $(seq 1 200); do
  curl -s -o /dev/null -w "%{http_code}\n" "$GATEWAY_URL/health" &
done > /tmp/circuit_test.txt
wait

# Analyze results
echo "Status Code Distribution:"
sort /tmp/circuit_test.txt | uniq -c | sort -rn
```

**Expected Behavior:**
- First ~100 requests: `200` (within circuit breaker limit)
- Remaining requests: `503` or connection failures (circuit breaker opened)
- Kong and backend never see the rejected traffic

**Evidence of Protection:**
```bash
# Kong should show fewer requests than total sent
kubectl logs deploy/kong --tail=100 | grep -c "GET /health"
# Compare with 200 requests sent - many were blocked by Envoy
```

#### 3. Envoy Local Rate Limiting

**Current Configuration:**
```bash
kubectl get configmap envoy-config -o yaml | grep -A 8 "token_bucket"
```

Shows:
- `max_tokens: 120`
- `fill_interval: 60s`
- Allows 120 requests per minute

**Test:** Exceed rate limit
```bash
# Send 150 requests rapidly (exceeds 120/min limit)
start_time=$(date +%s)
for i in $(seq 1 150); do
  CODE=$(curl -s -o /dev/null -w "%{http_code}" "$GATEWAY_URL/health")
  echo "$i,$CODE,$(date +%s)"
done > /tmp/rate_test.csv

end_time=$(date +%s)
echo "Test duration: $((end_time - start_time)) seconds"

# Count status codes
cut -d',' -f2 /tmp/rate_test.csv | sort | uniq -c
```

**Expected Behavior:**
- First ~120 requests: `200` (tokens available)
- After token depletion: `429` or immediate rejections
- After 60 seconds: tokens refill, requests succeed again

**Verify Token Bucket Refill:**
```bash
# Wait for bucket refill
sleep 60

# New requests should succeed
for i in $(seq 1 10); do
  curl -s -o /dev/null -w "Request $i: %{http_code}\n" "$GATEWAY_URL/health"
done
```

#### 4. Connection Per-Request Limits

**Current Configuration:**
- `max_requests_per_connection: 50`

**Test:** Send sustained requests on same connection
```bash
# curl reuses connection with --keepalive-time
time curl -s "$GATEWAY_URL/health" \
  --keepalive-time 60 \
  --parallel --parallel-max 60 \
  $(printf " $GATEWAY_URL/health%.0s" {1..60})
```

**Expected**: After 50 requests on a single connection, Envoy closes it and forces reconnection, preventing connection exhaustion attacks.

#### 5. Comparative Test: With vs Without Envoy

**Measure Kong directly (bypass Envoy):**
```bash
# Port-forward directly to Kong
kubectl port-forward svc/kong-proxy 8000:80 &
PF_PID=$!

# Burst test directly to Kong
for i in $(seq 1 300); do
  curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8000/health &
done | sort | uniq -c

kill $PF_PID
```

**Then test through Envoy:**
```bash
for i in $(seq 1 300); do
  curl -s -o /dev/null -w "%{http_code}\n" "$GATEWAY_URL/health" &
done | sort | uniq -c
```

**Expected Difference:**
- Direct Kong: Potentially all 300 reach backend (may overwhelm)
- Through Envoy: Mix of 200/429/503 showing traffic shaping

#### 6. Tightened Limits Demo (Clear Proof)

**Deploy strict limits:**
```bash
helm upgrade --install kong-stack ./helm/kong \
  --set envoy.ddos.localRateLimitPerMinute=10 \
  --set envoy.ddos.maxConnections=10 \
  --set envoy.ddos.maxPendingRequests=10 \
  --set envoy.ddos.maxRequestsPerConnection=5 \
  --set kong.jwt.key='local-dev-key' \
  --set kong.jwt.secret='66963a428fecbff608109da79a508c52fe1079005b3321a14f1daebc1cf7a747' \
  --set kong.ipWhitelist[0]='0.0.0.0/0' \
  --set kong.rateLimit.minute=10

kubectl rollout status deploy/envoy-front
```

**Test with tight limits:**
```bash
echo "Sending 30 requests (limit is 10/min)..."
for i in $(seq 1 30); do
  CODE=$(curl -s -o /dev/null -w "%{http_code}" "$GATEWAY_URL/health")
  echo "Request $i: $CODE"
  sleep 0.1
done
```

**Expected:**
- First 10: `200`
- Next 20: `429` (rate limited by Envoy)
- Clear demonstration of protection kicking in

**Restore normal limits:**
```bash
helm upgrade --install kong-stack ./helm/kong \
  --set envoy.ddos.localRateLimitPerMinute=120 \
  --set envoy.ddos.maxConnections=100 \
  --set envoy.ddos.maxPendingRequests=100 \
  --set envoy.ddos.maxRequestsPerConnection=50 \
  --set kong.jwt.key='local-dev-key' \
  --set kong.jwt.secret='66963a428fecbff608109da79a508c52fe1079005b3321a14f1daebc1cf7a747' \
  --set kong.ipWhitelist[0]='0.0.0.0/0' \
  --set kong.rateLimit.minute=10
```

#### 7. Observability

**Monitor Envoy metrics (if admin enabled):**
```bash
# In production, Envoy exposes /stats endpoint
kubectl port-forward deploy/envoy-front 9901:9901 &
curl -s http://localhost:9901/stats | grep -E "ratelimit|circuit|upstream_rq"
```

**Key metrics to watch:**
- `http.local_rate_limit.enabled`: Requests rate-limited
- `circuit_breakers.*.cx_open`: Circuit breaker open events
- `upstream_rq_429`: Rate limit responses

### Summary: Protection Layers

| Layer | Component | Protection Type | Trigger Condition | Response |
|-------|-----------|----------------|-------------------|----------|
| 1 | Envoy | Connection Limit | >100 concurrent connections | 503 / Connection refused |
| 2 | Envoy | Rate Limit | >120 req/min | 429 Too Many Requests |
| 3 | Envoy | Circuit Breaker | >100 pending requests | 503 Service Unavailable |
| 4 | Kong | IP Rate Limit | >10 req/min per IP | 429 Too Many Requests |
| 5 | Kong | JWT Auth | Invalid/missing token | 401 Unauthorized |
| 6 | Kong | IP Whitelist | Source IP not in allow-list | 403 Forbidden |

This multi-layer defense prevents attacks at the earliest possible point, reducing load on downstream components and protecting the backend microservice.

## Notes

- All runtime resources are declarative YAML/Helm templates.
- No managed cloud dependencies are used.
- `ai-usage.md` is intentionally not generated here per assignment instruction.
