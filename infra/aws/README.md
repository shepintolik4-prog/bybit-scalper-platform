# Развёртывание на AWS (институциональный контур)

Целевая схема: **Application Load Balancer → ECS Fargate (API) → RDS PostgreSQL**, образы в **ECR**, секреты в **Secrets Manager**, наблюдаемость через **Datadog Agent** (sidecar или Daemon) + **Sentry DSN** в task definition.

## 1. Подготовка образов

```bash
# из корня bybit-scalper-platform
aws ecr get-login-password --region <REGION> | docker login --username AWS --password-stdin <ACCOUNT>.dkr.ecr.<REGION>.amazonaws.com
docker build -t bybit-scalper-api ./backend
docker tag bybit-scalper-api:latest <ACCOUNT>.dkr.ecr.<REGION>.amazonaws.com/bybit-scalper-api:latest
docker push <ACCOUNT>.dkr.ecr.<REGION>.amazonaws.com/bybit-scalper-api:latest
```

Фронт (статика) — отдельно на **S3 + CloudFront** или **Vercel** (`VITE_API_BASE` на URL ALB).

## 2. База данных

- Создайте **RDS PostgreSQL** (Multi-AZ при проде), security group: вход **только** от SG ECS tasks.
- Строка подключения в Secrets Manager: `DATABASE_URL=postgresql+psycopg2://user:pass@host:5432/scalper`.

## 3. Секреты

В **Secrets Manager** (или SSM Parameter Store):

- `BYBIT_API_KEY`, `BYBIT_API_SECRET`
- `SENTRY_DSN_BACKEND`
- `API_SECRET`
- `DATABASE_URL`

Ссылка на секреты в task definition (`secrets` в контейнере ECS).

## 4. ECS Fargate

- Кластер ECS, сервис **Fargate**, desired count ≥ 2 для HA.
- Task definition: см. черновик `ecs/task-definition.json` (замените плейсхолдеры account/region/image).
- **Переменные окружения:** как в `.env.example`, плюс `METRICS_ENABLED=true`, `DD_AGENT_HOST` = localhost если Datadog sidecar, или IP Node при DaemonSet на EC2 (реже на Fargate).
- **Health checks ALB:** path `/api/health` (interval 30s), target group stickiness при необходимости.

### Datadog на Fargate

Варианты:

1. **Sidecar** контейнер `gcr.io/datadoghq/agent:7` в том же task; `DD_AGENT_HOST=localhost`, `DD_DOGSTATSD_PORT=8125`.
2. **Datadog Lambda / Forwarder** для логов; метрики DogStatsD — через sidecar.

Установите `DD_API_KEY`, `DD_SITE`, `ECS_FARGATE` metadata (см. официальную документацию Datadog ECS).

## 5. Сеть

- VPC с **private subnets** для ECS + RDS; **public subnets** для ALB.
- NAT Gateway для исходящего трафика контейнеров (Bybit API).

## 6. Terraform (опционально)

Каталог `terraform/` — минимальный каркас (`versions.tf`, `variables.tf`, `ecs.tf`): VPC data sources, ECR, ECS cluster/service. Не запускается «из коробки» без заполнения `terraform.tfvars` и бэкенда state (S3 + DynamoDB). Используйте как шаблон для DevOps.

## 6. Масштабирование

- **Горизонтальное:** увеличить `desired_count`, CPU/memory в task definition.
- **Отдельный worker:** второй task definition с `command: python -m app.worker` (когда появится очередь) или тот же образ с `ROLE=worker`.

## 7. Чеклист перед продом

- [ ] RDS encryption at rest, backup window
- [ ] Secrets rotation policy
- [ ] ALB HTTPS (ACM certificate)
- [ ] WAF на ALB (опционально)
- [ ] Sentry release tracking + environment
- [ ] Datadog dashboards: latency, errors, RDS CPU
