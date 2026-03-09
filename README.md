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

Envoy is a mature, open-source L7 proxy with strong local rate limiting and connection controls. Placing it in front of Kong provides an extra defensive layer against bursts and abusive connection behavior before traffic reaches the API gateway and backend service.

## Notes

- All runtime resources are declarative YAML/Helm templates.
- No managed cloud dependencies are used.
- `ai-usage.md` is intentionally not generated here per assignment instruction.
